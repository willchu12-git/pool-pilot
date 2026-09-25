"""
ondilo.py -- pull readings straight off the ICO, so nothing has to be screenshotted.

  py engine/ondilo.py login     # one-time: authorize, print the refresh token
  py engine/ondilo.py pools     # what the account can see (find your pool id)
  py engine/ondilo.py pull      # fetch the latest measures -> readings.jsonl
  py engine/ondilo.py raw       # print what the API returns, no writing

Ondilo's Customer API (https://interop.ondilo.com/docs/api/customer/v1) is
OAuth2 authorization-code. The part that makes it usable from a cron job with no
babysitting: access tokens last an hour, but REFRESH tokens are non-expiring and
are not rotated -- refreshing returns a new access_token and no new refresh
token. So `login` once, drop the refresh token into the ONDILO_REFRESH_TOKEN
secret, and it never needs updating again.

Stdlib only, on purpose. Every other module here runs with nothing installed and
this one shouldn't be the reason the workflow grows a pip step.

Two things worth understanding about how a pulled reading differs from a
screenshot:

  * It is written CONFIRMED. The confirm card exists because OCR can misread a
    decimal point; an API integer has no such failure mode. store.py's sanity
    bounds still apply, and a measure the ICO itself flags `is_valid: false` is
    dropped with its exclusion_reason kept in the note.

  * Its id is the ICO's own `value_time`, not the time we fetched. The ICO
    measures on its own schedule, so polling four times a day over one
    measurement would otherwise create four identical readings. Keying on the
    measurement's timestamp means re-polling collapses onto the same record
    instead of piling up.

The ICO measures temperature, pH, ORP, salt, TDS (plus battery and signal). It
does NOT measure CYA, total alkalinity, free chlorine or borates -- those are
still strip tests you type in, and the checklist still asks for them.
"""
from __future__ import annotations
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import poolcfg  # noqa: E402
import store    # noqa: E402

AUTH_URL = "https://interop.ondilo.com/oauth2/authorize"
TOKEN_URL = "https://interop.ondilo.com/oauth2/token"
API = "https://interop.ondilo.com/api/customer/v1"

# The redirect never has to resolve to anything: the authorization code comes
# back in the URL's query string and `login` reads it off what you paste back.
DEFAULT_REDIRECT = "https://example.com/api"

# what the ICO actually measures
ALL_TYPES = ("temperature", "ph", "orp", "salt", "tds", "battery", "rssi")

# ICO data_type -> the field name store.py keeps it under
FIELD = {"ph": "ph", "orp": "orp_mv", "salt": "salt_ppm", "tds": "tds_ppm",
         "temperature": "water_temp_f"}

TIMEOUT = 30


# --------------------------------------------------------------------- http

def _ctx():
    return ssl.create_default_context()


def _post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path, token, params=None):
    url = API + path
    if params:
        # types[] repeats, so this has to be doseq -- urlencode would otherwise
        # send the Python list repr and the API would 400 on it
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8"))


def _cfg():
    return poolcfg.CONFIG.get("ondilo") or {}


def _client():
    c = _cfg()
    return (c.get("client_id") or "customer_api"), (c.get("client_secret") or "")


# --------------------------------------------------------------------- auth

def authorize_url(redirect_uri=None, state="poolpilot"):
    cid, _ = _client()
    redirect_uri = redirect_uri or _cfg().get("redirect_uri") or DEFAULT_REDIRECT
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": redirect_uri,
        "scope": "api", "state": state})


# What Ondilo sends back instead of a code, and what it actually means. The raw
# OAuth error names are accurate and tell you nothing about what to do next.
AUTH_ERRORS = {
    "access_denied": (
        "Ondilo refused the sign-in.\n\n"
        "  Nearly always this is the WRONG ONDILO ACCOUNT. There are commonly two:\n"
        "    - the ondilo.com SHOP account, from buying the device\n"
        "    - the ICO MOBILE APP account, which the device is registered to\n"
        "  Only the second one works here, and they are often different.\n\n"
        "  Open the ICO app on your phone, check which email it is signed in as,\n"
        "  and use exactly that. If you are unsure of the password, reset it from\n"
        "  the ICO app and try again."),
    "invalid_request":
        "Ondilo rejected the request itself. Check that config.ondilo.redirect_uri "
        "matches the redirect_uri in the authorize URL.",
    "invalid_client":
        "Ondilo did not recognise the client. Check config.ondilo.client_id "
        "(it should be 'customer_api').",
    "unsupported_response_type":
        "Ondilo rejected the response type. That is a bug in ondilo.py, not "
        "something you did.",
}


def _code_from(pasted):
    """Accept either a bare code or the whole redirect URL pasted back.

    Raises with an explanation when what came back is an OAuth error rather than
    a code -- ?error=access_denied is by far the most common thing to land here,
    and "couldn't find a code" is a uselessly literal thing to say about it.
    """
    pasted = (pasted or "").strip()
    if "?" not in pasted and "code=" not in pasted and "error=" not in pasted:
        return pasted
    q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
    err = (q.get("error") or [""])[0]
    if err:
        desc = (q.get("error_description") or [""])[0]
        detail = ("\n\n  Ondilo also said: " + desc) if desc else ""
        raise RuntimeError("%s\n\n%s%s" % (
            err,
            AUTH_ERRORS.get(err, "Ondilo returned this error and no advice for it."),
            detail))
    return (q.get("code") or [""])[0]


def exchange_code(code, redirect_uri=None):
    """Authorization code -> {access_token, refresh_token}. Run once, by hand."""
    cid, secret = _client()
    data = {"grant_type": "authorization_code", "client_id": cid,
            "code": _code_from(code),
            "redirect_uri": redirect_uri or _cfg().get("redirect_uri") or DEFAULT_REDIRECT}
    if secret:
        data["client_secret"] = secret
    return _post(TOKEN_URL, data)


def access_token(refresh=None):
    """A fresh 1-hour access token. The refresh token is not consumed or rotated.

    Read from the environment first so the real token lives in a GitHub secret
    and never in config.json -- which matters rather a lot here, because this
    repo is public.
    """
    refresh = (refresh or os.environ.get("ONDILO_REFRESH_TOKEN")
               or _cfg().get("refresh_token") or "").strip()
    if not refresh:
        raise RuntimeError("no Ondilo refresh token -- set the ONDILO_REFRESH_TOKEN "
                           "secret, or run `py engine/ondilo.py login` to get one")
    cid, secret = _client()
    data = {"grant_type": "refresh_token", "client_id": cid, "refresh_token": refresh}
    if secret:
        data["client_secret"] = secret
    tok = _post(TOKEN_URL, data)
    if not tok.get("access_token"):
        raise RuntimeError("Ondilo returned no access token: %s" % json.dumps(tok)[:200])
    return tok["access_token"]


# ---------------------------------------------------------------------- api

def pools(token):
    return _get("/pools", token)


def user_units(token):
    return _get("/user/units", token)


def last_measures(token, pool_id, types=None):
    types = list(types or [t for t in ALL_TYPES if t in FIELD])
    return _get("/pools/%s/lastmeasures" % pool_id, token, [("types[]", t) for t in types])


def pool_config(token, pool_id):
    return _get("/pools/%s/configuration" % pool_id, token)


# ------------------------------------------------------------------ mapping

def _c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


# Where the two scales stop overlapping for liquid pool water. Below 32 it
# cannot be Fahrenheit (that is ice); above 40 it cannot be Celsius (that is a
# hot tub, and a hot one). Between them, both readings are physically possible.
MIN_PLAUSIBLE_F = 32.0
MAX_PLAUSIBLE_C = 40.0


def _temp_f(value, units):
    """Water temperature as Fahrenheit, whatever the account is set to.

    /user/units reports what the account PREFERS, and the first live pull showed
    that is not always the unit the measurement actually arrives in: a Celsius
    value came through while the preference said Fahrenheit, which stored "24
    degrees F" and had it dropped as impossible. A dropped temperature is a
    harmless failure, but the same mismatch in the other direction would have
    stored a plausible-looking wrong number.

    So the preference is a hint and the magnitude is the check: a pool at 24 is
    not Fahrenheit, a pool at 78 is not Celsius, and where the two disagree,
    physics wins. Only inside the genuinely ambiguous 32-40 band does the stated
    preference get the final say, because there nothing else can decide it.
    """
    pref = str((units or {}).get("temperature") or "").strip().lower()
    said = "F" if pref.startswith("f") else ("C" if pref.startswith("c") else "")

    if value > MAX_PLAUSIBLE_C:
        return value, "F" if said == "F" else "F by magnitude"
    if value < MIN_PLAUSIBLE_F:
        return _c_to_f(value), "C" if said == "C" else "C by magnitude"
    if said == "F":
        return value, "F"
    return _c_to_f(value), "C" if said == "C" else "C assumed"


def _salt_ppm(value):
    """Documented as mg/L, which is 1:1 with ppm. Some accounts report g/L."""
    return (value * 1000.0, "g/L") if 0 < value < 50 else (value, "mg/L")


def to_reading(measures, units=None, pool_id=""):
    """Turn the API's measure list into the kwargs store.add_reading wants.

    Returns (kwargs, notes). A measure the ICO flags invalid is left OUT rather
    than passed through -- `is_valid: false` is the device telling us it doesn't
    believe its own number, and a dose computed from it would be worse than no
    dose at all.
    """
    out, notes, newest = {}, [], None
    for m in measures or []:
        t = m.get("data_type")
        field = FIELD.get(t)
        if not field:
            continue                          # battery / rssi: not water chemistry
        if m.get("is_valid") is False:
            notes.append("%s excluded by the ICO (%s)"
                         % (t, m.get("exclusion_reason") or "no reason given"))
            continue
        v = m.get("value")
        if v is None:
            continue
        if t == "temperature":
            raw = float(v)
            v, src = _temp_f(raw, units)
            if "magnitude" in src:
                notes.append("temperature read as %s (%g) -- the account says %s, but that "
                             "value can't be" % (src[0], raw,
                                                 "F" if src[0] == "C" else "C"))
            elif "assumed" in src:
                notes.append("temperature unit unconfirmed, assumed Celsius")
        elif t == "salt":
            v, src = _salt_ppm(float(v))
            if src == "g/L":
                notes.append("salt looked like g/L, converted to ppm")
        out[field] = round(float(v), 2)
        vt = m.get("value_time")
        if vt and (newest is None or vt > newest):
            newest = vt
    return out, notes, newest


def pull(token=None, pool_id=None, write=True):
    """Fetch the latest measures and file them as one confirmed reading."""
    cfg = _cfg()
    token = token or access_token()
    pool_id = pool_id or cfg.get("pool_id") or ""
    if not pool_id:
        got = pools(token)
        if not got:
            raise RuntimeError("this Ondilo account has no pools on it")
        pool_id = got[0].get("id")
        print("  using pool %s (%s)" % (pool_id, got[0].get("name") or "unnamed"))

    try:
        units = user_units(token)
        print("  units: %s" % json.dumps(units)[:160])
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
        print("  ! couldn't read unit preferences (%s) -- will infer" % e)
        units = {}

    measures = last_measures(token, pool_id, cfg.get("types"))
    vals, notes, newest = to_reading(measures, units, pool_id)
    if not vals:
        print("  ! the ICO returned nothing usable")
        return None

    # The API reports measurement times in UTC. Left alone, an evening reading
    # is filed under tomorrow -- which is exactly what happened on the first
    # live pull, and it showed up in the app as a reading from the future.
    at = poolcfg.utc_to_local(newest) if newest \
        else datetime.now().isoformat(timespec="seconds")
    # Keyed on the ICO's own measurement time, so polling repeatedly over one
    # measurement collapses onto a single record instead of piling up. The API
    # sends "2026-09-25 01:38:34" with a space; normalise it so the id matches
    # the `at` the store ends up holding.
    rid = "ico:" + str(at)[:19].replace(" ", "T")
    shown = ", ".join("%s %s" % (k, v) for k, v in sorted(vals.items()))
    if not write:
        print("  would file %s -> %s" % (rid, shown))
        return {"id": rid, "at": at, **vals}

    # An append-only store collapses duplicates on read, so re-filing the same
    # measurement is harmless -- but the ICO only measures hourly and this polls
    # more often than that, so without this every unchanged poll would add a line
    # forever.
    prior = next((r for r in store.readings(False) if r.get("id") == rid), None)
    if prior and all(prior.get(k) == v for k, v in vals.items()):
        print("  = unchanged since %s, nothing to file" % rid)
        return prior
    rec = store.add_reading(at=at, source="ondilo_api", confirmed=True,
                            note="; ".join(notes)[:200], rid=rid, **vals)
    print("  %s %s -> %s" % ("updated" if prior else "+ new reading", rid, shown))
    for n in notes:
        print("    note: %s" % n)
    if rec.get("dropped"):
        print("    ! out-of-range, dropped: %s" % ", ".join(rec["dropped"]))
    return rec


# ---------------------------------------------------------------------- cli

def _login():
    redirect = _cfg().get("redirect_uri") or DEFAULT_REDIRECT
    print("\n1. Open this in a browser:\n")
    print("   " + authorize_url(redirect))
    print("\n   Sign in with the account your ICO MOBILE APP uses. That is often NOT")
    print("   the same as the ondilo.com shop account you may have bought the device")
    print("   with, and using the shop one is the usual cause of 'access denied'.")
    print("\n2. It then redirects to a page that probably will not load. That is fine --")
    print("   what matters is the address bar. Copy the WHOLE address.")
    print("   (If it says ?error= rather than ?code=, paste it anyway and it will")
    print("    be explained.)\n")
    pasted = input("3. Paste it here: ").strip()
    try:
        code = _code_from(pasted)
    except RuntimeError as e:
        print("\n! %s\n" % e)
        return
    if not code:
        print("! no ?code= in that -- paste the full redirected address")
        return
    tok = exchange_code(code, redirect)
    rt = tok.get("refresh_token")
    if not rt:
        print("! no refresh token came back: %s" % json.dumps(tok)[:300])
        return
    print("\n" + "=" * 68)
    print("ONDILO_REFRESH_TOKEN")
    print(rt)
    print("=" * 68)
    print("\nThis does not expire and is not rotated, so this is the only time")
    print("you have to do this. Add it as a repository SECRET (never config.json --")
    print("this repo is public). Then set ondilo.enabled to true in config.json.\n")
    try:
        ps = pools(tok["access_token"])
        for p in ps:
            print("  pool id %s: %s" % (p.get("id"), p.get("name") or "unnamed"))
        print("\nIf there's more than one, put the right id in config.ondilo.pool_id.")
    except Exception as e:
        print("(couldn't list pools: %s)" % e)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "pull"
    if cmd == "login":
        return _login()
    tok = access_token()
    if cmd == "pools":
        print(json.dumps(pools(tok), indent=2))
    elif cmd == "raw":
        pid = _cfg().get("pool_id") or (pools(tok)[0] or {}).get("id")
        print(json.dumps({"units": user_units(tok),
                          "measures": last_measures(tok, pid)}, indent=2))
    elif cmd in ("pull", "fetch"):
        pull(tok, write="--dry-run" not in argv)
    else:
        print(__doc__.strip())


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
        detail = ""
        if isinstance(e, urllib.error.HTTPError):
            try:
                detail = " -- " + e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
        print("! ondilo: %s%s" % (e, detail))
        sys.exit(1)
