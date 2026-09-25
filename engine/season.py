"""
season.py -- open or closed, and what closing told us about opening.

  py engine/season.py status
  py engine/season.py close --on today
  py engine/season.py open  --on today
  py engine/season.py record pro_visit --cover solid_safety --lowered 18
  py engine/season.py brief          # what the closing record says about spring

Two jobs.

THE SWITCH. `data/store/season.json` says open or closed. Closed means dormant:
checklist.py returns an empty list instead of doses, run_cloud stops polling the
ICO and stops pushing, and the app shows a closed card. That isn't cosmetic --
the ICO spends the winter on a shelf, so any reading it reports is the
temperature of a garage. An app that keeps computing acid doses off that is
worse than an app that says nothing.

THE HANDOVER. This is the part worth caring about. A pool is closed in October
and opened in April, and in between every useful fact about it lives in somebody's
head: what the salt was, which cover went on, where the drain plugs went, what
was already broken. Six months later none of it is there. So closing RECORDS
those facts, and opening READS them back as a briefing -- what to expect, what to
reinstall, what to fix, and which closing numbers are still worth trusting.

The pro does the physical close here. The owner's steps are the ones the pro
can't do for them: capture the last reading before it all goes quiet, and write
down what happened while it's still true.

Records are kept per season in `history`, so "what did we do last year" survives
a reopen.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import poolcfg  # noqa: E402
import store    # noqa: E402

SEASON = "season.json"

OPEN, CLOSED = "open", "closed"

# Roughly how a cover changes what you find in April. Not folklore -- a mesh
# cover is a sieve for snowmelt, so the water it lets in is the water that
# dilutes everything you carefully balanced in October.
COVER_EFFECT = {
    "solid_safety": {
        "label": "solid safety cover",
        "dilution": "low",
        "note": "A solid cover keeps precipitation out, so the numbers you closed at are "
                "mostly still the numbers -- worth testing to confirm, not worth assuming "
                "they collapsed.",
    },
    "mesh": {
        "label": "mesh safety cover",
        "dilution": "high",
        "note": "A mesh cover passes snowmelt and rain straight through all winter, so expect "
                "salt and CYA meaningfully diluted and expect some algae -- mesh passes light "
                "as well as water. Test before adding anything.",
    },
    "tarp": {
        "label": "tarp / water-bag cover",
        "dilution": "medium",
        "note": "A tarp keeps most water out but leaks around the edges and sags. Expect the "
                "chemistry somewhere between where you left it and noticeably diluted.",
    },
    "none": {
        "label": "no cover",
        "dilution": "high",
        "note": "Uncovered all winter: full dilution from rain and snow, full sunlight, and a "
                "lot of debris. Treat every closing number as historical only.",
    },
}

# how a measurement reads in prose, rather than as a store field name
MEASURE_WORD = {"ph": "pH", "orp_mv": "ORP", "salt_ppm": "salt", "cya_ppm": "CYA",
                "ta_ppm": "TA", "fc_ppm": "free chlorine", "tds_ppm": "TDS",
                "borates_ppm": "borates", "water_temp_f": "water temp", "ch_ppm": "calcium"}
MEASURE_ORDER = ("ph", "orp_mv", "salt_ppm", "cya_ppm", "ta_ppm", "fc_ppm",
                 "borates_ppm", "tds_ppm", "ch_ppm", "water_temp_f")


def _water_words(water):
    """'pH 8.3, ORP 558 mV, salt 2,760 ppm' -- in a sensible order, not dict order."""
    out = []
    for k in MEASURE_ORDER:
        v = water.get(k)
        if v is None:
            continue
        num = format(int(v), ",") if v >= 1000 else ("%g" % v)
        unit = {"orp_mv": " mV", "water_temp_f": "°F"}.get(
            k, "" if k == "ph" else " ppm")
        out.append("%s %s%s" % (MEASURE_WORD.get(k, k), num, unit))
    return ", ".join(out)


# What a winter costs CYA regardless of cover -- it degrades slowly on its own.
CYA_WINTER_LOSS_PPM = 5.0

GAL_PER_INCH_PER_SQFT = 0.623


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _today(v=None):
    if isinstance(v, date):
        return v
    return date.today()


def _iso(v):
    return store._iso(v)


def _days(a, b):
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ the file

def _path():
    return poolcfg.store_path(SEASON)


def read():
    data = {"state": OPEN, "closed_at": None, "opened_at": None,
            "closing": {}, "history": [], "updated_at": None}
    if os.path.exists(_path()):
        try:
            data.update(json.load(open(_path(), encoding="utf-8")) or {})
        except (json.JSONDecodeError, OSError):
            pass
    data.setdefault("closing", {})
    data.setdefault("history", [])
    return data


def _write(data):
    data["updated_at"] = _now()
    json.dump(data, open(_path(), "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    return data


def is_closed():
    return read().get("state") == CLOSED


# ------------------------------------------------------------- closing steps

def steps():
    return poolcfg.CONFIG.get("closing", {}).get("steps") or []


def closing_state():
    """Every closing step with whether it's recorded, and whether we can close yet.

    Free order on purpose -- these are things to write down, not a procedure, and
    nothing here depends on anything else here. The single gate is at the end:
    a `required_for_close` step that hasn't been recorded blocks marking the pool
    closed, because a closing record missing the final reading or the cover type
    is the one that's useless in April.
    """
    data = read()
    done = data.get("closing") or {}
    out = []
    for i, s in enumerate(steps()):
        rec = done.get(s["id"]) or {}
        out.append({"id": s["id"], "title": s["title"], "detail": s.get("detail", ""),
                    "kind": s.get("kind", "note"), "n": i + 1,
                    "required": bool(s.get("required_for_close")),
                    "done": bool(rec.get("recorded_at")),
                    "data": {k: v for k, v in rec.items() if k != "recorded_at"},
                    "recorded_at": rec.get("recorded_at")})
    missing = [s["title"] for s in out if s["required"] and not s["done"]]
    return {"state": data.get("state"), "steps": out,
            "done": sum(1 for s in out if s["done"]), "total": len(out),
            "can_close": not missing, "blocking": missing}


def record_step(step_id, **data):
    """Write down one closing fact. Overwrites -- a correction should just win."""
    if step_id not in [s["id"] for s in steps()]:
        raise ValueError("no closing step named %r" % step_id)
    d = read()
    rec = {k: v for k, v in data.items() if v not in (None, "")}
    rec["recorded_at"] = _now()
    d["closing"][step_id] = rec
    _write(d)
    return closing_state()


def clear_step(step_id):
    d = read()
    d["closing"].pop(step_id, None)
    _write(d)
    return closing_state()


# ------------------------------------------------------------- open / close

def close(on=None, note=""):
    """Shut it down for the season. Refuses while a required step is unrecorded."""
    st = closing_state()
    if not st["can_close"]:
        raise ValueError("can't close yet -- still need: %s" % ", ".join(st["blocking"]))
    d = read()
    if d.get("state") == CLOSED:
        return status()
    d["state"] = CLOSED
    d["closed_at"] = _iso(on) or date.today().isoformat()
    d["close_note"] = (note or "").strip()
    # snapshot the water as it stood, so the record survives even if the reading
    # files are later pruned
    r = store.latest_reading(confirmed_only=True)
    d["closing_water"] = {k: r.get(k) for k in store.MEASURES if r and r.get(k) is not None} \
        if r else {}
    if r:
        d["closing_water"]["date"] = r.get("date")
        d["closing_water"]["id"] = r.get("id")
    _write(d)
    return status()


def open_season(on=None):
    """Start fresh. Archives the closing record, then clears the decks.

    The opening wizard is reset, the pool goes live again, and last winter's
    record moves into `history` where opening_brief() reads it. Nothing is
    destroyed -- readings, actions and the closing facts all stay on disk.
    """
    d = read()
    was = d.get("state")
    closed_at = d.get("closed_at")
    if d.get("closing") or closed_at:
        d["history"].append({
            "closed_at": closed_at,
            "opened_at": _iso(on) or date.today().isoformat(),
            "days_closed": _days(closed_at, _iso(on) or date.today().isoformat())
                           if closed_at else None,
            "closing": d.get("closing") or {},
            "closing_water": d.get("closing_water") or {},
            "close_note": d.get("close_note", ""),
        })
    d["state"] = OPEN
    d["opened_at"] = _iso(on) or date.today().isoformat()
    d["closed_at"] = None
    d["closing"] = {}
    d["closing_water"] = {}
    d["close_note"] = ""
    _write(d)
    store.reset_opening()          # the spring wizard starts at step 1 again
    return dict(status(), reopened_from=was)


def status(today=None):
    d = read()
    today = (_today(today)).isoformat()
    return {
        "state": d.get("state", OPEN),
        "closed": d.get("state") == CLOSED,
        "closed_at": d.get("closed_at"),
        "opened_at": d.get("opened_at"),
        "days_closed": _days(d.get("closed_at"), today) if d.get("closed_at") else None,
        "closing_water": d.get("closing_water") or {},
        # formatted here rather than in the app, so there is one place that
        # decides how a measurement reads and the two can't drift
        "closing_water_words": _water_words(d.get("closing_water") or {}),
        "close_note": d.get("close_note", ""),
        "closing": closing_state(),
        "seasons_on_record": len(d.get("history") or []),
    }


# ------------------------------------------------------------- spring brief

def _last_record():
    """The most recent closing on record -- the live one if closed, else the last
    archived one, which is what a freshly-opened pool wants to read."""
    d = read()
    if d.get("state") == CLOSED and d.get("closed_at"):
        return {"closed_at": d["closed_at"], "closing": d.get("closing") or {},
                "closing_water": d.get("closing_water") or {},
                "close_note": d.get("close_note", "")}
    hist = d.get("history") or []
    return hist[-1] if hist else None


def _cover_of(rec):
    pro = (rec.get("closing") or {}).get("pro_visit") or {}
    return COVER_EFFECT.get(pro.get("cover") or "", None), pro


def _dilution_line(rec, days):
    cover, pro = _cover_of(rec)
    water = rec.get("closing_water") or {}
    bits = []
    if cover:
        bits.append(cover["note"])
    lowered = pro.get("lowered_inches")
    sqft = (poolcfg.CONFIG.get("pool") or {}).get("surface_sqft")
    if lowered and sqft:
        gal = float(lowered) * float(sqft) * GAL_PER_INCH_PER_SQFT
        frac = min(0.95, gal / max(1.0, poolcfg.gallons()))
        salt = water.get("salt_ppm")
        if salt:
            bits.append("The water was lowered about %g inches, which is roughly %s gallons "
                        "(%d%% of the pool). Refilling that with fresh water would put salt "
                        "near %s ppm from the %s ppm you closed at -- an estimate to test "
                        "against, not a number to dose from."
                        % (float(lowered), format(int(gal), ","), round(frac * 100),
                           format(int(salt * (1 - frac)), ","), format(int(salt), ",")))
        else:
            bits.append("The water was lowered about %g inches; refilling dilutes everything "
                        "proportionally, so test after a full circulation." % float(lowered))
    elif lowered:
        bits.append("The water was lowered about %g inches. Refilling dilutes salt and CYA "
                    "proportionally -- test after a full circulation rather than trusting "
                    "the closing numbers. (Set pool.surface_sqft in config.json and this "
                    "turns into an actual estimate.)" % float(lowered))
    return bits


def opening_brief(today=None):
    """What last autumn knows that this spring needs. None if nothing was recorded.

    This is the entire reason closing captures anything at all. Six months is
    long enough that nobody remembers which cover went on or where the salt cell
    went, and those are exactly the facts that decide how the opening goes.
    """
    rec = _last_record()
    if not rec:
        return None
    today = (_today(today)).isoformat()
    days = _days(rec.get("closed_at"), today)
    water = rec.get("closing_water") or {}
    cl = rec.get("closing") or {}
    cover, pro = _cover_of(rec)

    expect, reinstall, fix, notes = [], [], [], []

    # what the water should look like
    if water:
        shown = _water_words(water)
        if shown:
            expect.append("You closed on %s at %s." % (rec.get("closed_at"), shown))
    expect += _dilution_line(rec, days)
    cya = water.get("cya_ppm")
    if cya:
        expect.append("CYA degrades on its own over a winter, so expect it a little under the "
                      "%g ppm you closed at even before dilution -- somewhere near %g."
                      % (cya, max(0, cya - CYA_WINTER_LOSS_PPM)))

    # what has to physically go back
    stored = (cl.get("stored") or {})
    for key, label in (("ico", "the ICO"), ("salt_cell", "the salt cell"),
                       ("baskets", "the baskets"), ("ladder", "the ladder and rails"),
                       ("plugs", "the drain plugs")):
        where = stored.get(key)
        if where:
            reinstall.append("%s -- %s" % (label[0].upper() + label[1:], where))
    if stored.get("other"):
        reinstall.append(stored["other"])

    # what was already broken in October
    punch = (cl.get("punch_list") or {}).get("items") or []
    fix += [p for p in punch if str(p).strip()]

    if pro.get("chemicals"):
        chem_note = "Winterizing chemicals that went in: %s." % pro["chemicals"]
        if "copper" in str(pro["chemicals"]).lower():
            chem_note += (" That's copper-based -- watch for staining on the liner as pH "
                          "comes up, and go gently on the pH correction.")
        notes.append(chem_note)
    if pro.get("lines_blown"):
        notes.append("Lines were blown and plugged, so the plugs have to come out before "
                     "the pump is primed.")
    if rec.get("close_note"):
        notes.append(rec["close_note"])
    if (cl.get("pad_photo") or {}).get("image"):
        notes.append("There's a photo of the equipment pad from closing in the timeline -- "
                     "worth looking at before you start turning valves.")

    return {
        "closed_at": rec.get("closed_at"),
        "days_closed": days,
        "cover": cover["label"] if cover else (pro.get("cover") or None),
        "dilution_risk": cover["dilution"] if cover else None,
        "expect": expect,
        "reinstall": reinstall,
        "fix": fix,
        "notes": notes,
        # a close date in the future is a typo, not a negative winter
        "headline": ("Closed %s, %s days ago." % (rec.get("closed_at"), days)
                     if days and days > 0 else "Closed %s." % rec.get("closed_at")),
    }


# --------------------------------------------------------------------- ingest

def ingest(rec):
    """Phone drops: {kind:"season", action:"open"|"close"|"record"|"clear", ...}"""
    action = (rec.get("action") or "").lower()
    if action == "close":
        return "season-close", close(rec.get("on") or rec.get("date"), rec.get("note", ""))
    if action == "open":
        return "season-open", open_season(rec.get("on") or rec.get("date"))
    if action == "clear":
        return "season-clear", clear_step(rec.get("step") or "")
    if action in ("record", "step"):
        data = {k: v for k, v in rec.items()
                if k not in ("kind", "action", "step", "step_id", "id")}
        return "season-record", record_step(rec.get("step") or rec.get("step_id") or "", **data)
    raise ValueError("unknown season action %r" % action)


# ------------------------------------------------------------------------ cli

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "status"
    rest = argv[1:]

    def opt(flag, default=None):
        return rest[rest.index(flag) + 1] if flag in rest and rest.index(flag) + 1 < len(rest) \
            else default

    positional = [a for a in rest if not a.startswith("--")]
    if cmd == "close":
        r = close(opt("--on"), opt("--note", ""))
    elif cmd == "open":
        r = open_season(opt("--on"))
    elif cmd == "record":
        kw = {}
        for i, a in enumerate(rest):
            if a.startswith("--") and a not in ("--on",):
                nxt = rest[i + 1] if i + 1 < len(rest) else ""
                kw[a[2:].replace("-", "_")] = "" if nxt.startswith("--") else nxt
        r = record_step(positional[0] if positional else "", **kw)
    elif cmd == "clear":
        r = clear_step(positional[0] if positional else "")
    elif cmd == "brief":
        r = opening_brief()
    else:
        r = status()
    print(json.dumps(r, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print("! %s" % e)
        sys.exit(1)
