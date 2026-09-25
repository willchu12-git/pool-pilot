"""
poolcfg.py -- one place to load config.json.

All paths in config.json are repo-relative, so the SAME config works on my PC
and on the GitHub Actions runner. Point PP_CONFIG at another file to override.

    import poolcfg
    poolcfg.CONFIG["pool"]["gallons"]
    poolcfg.store_path("readings.jsonl")

The one thing worth understanding here is safety_rules(). The rules live in
config.json in machine-readable form, and this module renders them into the
plain-English lines that get injected into EVERY AI prompt. That is deliberate:
there is exactly one list, and no prompt can quietly ship without it.
"""
from __future__ import annotations
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULTS = {
    "paths": {"store": "data/store", "inbox": "inbox", "out": "out", "app": "app"},
    "pool": {"gallons": 18000, "type": "inground_vinyl_liner", "sanitization": "saltwater"},
    "equipment": {"salt_cell": "", "salt_cell_increment_pct": 10, "pump": "", "skimmer": "",
                  "filter": "sand"},
    "targets": {"orp_mv": [600, None], "ph": [7.8, 8.1], "salt_ppm": [2800, 3200],
                "cya_ppm": [30, 50], "borates_ppm": 50, "ta_ppm": [60, 100],
                "water_temp_f": [None, None]},
    "cadence_days": {"reading": 7, "backwash": 14, "skimmer_basket": 3, "salt_check": 30,
                     "cya_check": 30},
    "chemicals": {
        "acid": {"name": "muriatic acid", "strength_pct": 31.45},
        "chlorine": {"name": "liquid chlorine", "strength_pct": 12.5},
        "salt": {"name": "pool salt", "bag_lb": 40},
        "cya": {"name": "stabilizer (cyanuric acid)"},
        "borate": {"name": "boric acid", "boron_pct": 17.5},
    },
    "safety_rules": [],
    "opening": {"steps": []},
    "coach": {"claude_model": "", "timeout_sec": 300, "vision_flags": "--allowedTools Read"},
    "push": {"enabled": False, "ntfy_server": "https://ntfy.sh", "ntfy_topic": ""},
    "app": {"data_repo": "", "title": "Pool Pilot"},
}

# machine-readable rule id -> the sentence a human (or an AI) has to obey.
# Anything not in this map still gets surfaced verbatim, so a rule added to
# config.json is never silently dropped just because nobody wrote prose for it.
RULE_TEXT = {
    "never_pour_undiluted_acid_into_skimmer":
        "NEVER pour undiluted acid into the skimmer. Acid down the skimmer runs neat through "
        "the pump, heater and salt cell and eats them. Dilute it and pour it slowly around the "
        "deep end with the pump running.",
    "always_predissolve_dry_chemicals_in_5gal_bucket":
        "ALWAYS predissolve dry chemicals in a 5-gallon bucket of pool water before they go in. "
        "Undissolved granules sitting on a vinyl liner bleach and weaken it.",
    "never_mix_chlorine_and_acid_in_the_same_bucket":
        "NEVER put chlorine and acid in the same bucket, in either order. That combination "
        "produces chlorine gas.",
    "always_add_acid_to_water_never_water_to_acid":
        "ALWAYS add acid to water, never water to acid -- pouring water into acid can boil and "
        "spit it back at you.",
    "pump_must_be_running_before_adding_anything":
        "The pump must be RUNNING before anything goes in, and keep running afterwards, so a "
        "dose disperses instead of sitting in one corner.",
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _load():
    path = os.environ.get("PP_CONFIG") or os.path.join(ROOT, "config.json")
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    raw = {}
    if os.path.exists(path):
        try:
            raw = json.load(open(path, encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print("! config unreadable (%s) -- using defaults" % e)
    cfg = _merge(DEFAULTS, raw)
    # env overrides so GitHub Secrets never have to live in the repo
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        cfg["push"]["ntfy_topic"] = topic
        cfg["push"]["enabled"] = True
    repo = os.environ.get("PP_DATA_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if repo and "/" not in (cfg["app"].get("data_repo") or ""):
        cfg["app"]["data_repo"] = repo
    cfg["_config_path"] = path
    return cfg


CONFIG = _load()


def _abs(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def store_dir():
    d = _abs(CONFIG["paths"]["store"])
    os.makedirs(d, exist_ok=True)
    return d


def store_path(name):
    return os.path.join(store_dir(), name)


def path_of(key):
    d = _abs(CONFIG["paths"][key])
    os.makedirs(d, exist_ok=True)
    return d


def gallons():
    try:
        g = float(CONFIG["pool"]["gallons"])
    except (TypeError, ValueError):
        g = 0.0
    return g if g > 0 else 18000.0


def targets():
    return CONFIG["targets"]


def band(key):
    """A [low, high] target as a 2-tuple, with None meaning 'open on that side'."""
    t = CONFIG["targets"].get(key)
    if isinstance(t, (list, tuple)) and len(t) == 2:
        return (t[0], t[1])
    return (t, t) if t is not None else (None, None)


def min_gap_hours():
    """The shock/acid separation, pulled out of the safety rule that encodes it."""
    for r in CONFIG.get("safety_rules") or []:
        if str(r).startswith("never_shock_and_acid_same_session_min_gap_hours:"):
            try:
                return float(str(r).split(":", 1)[1])
            except ValueError:
                return 4.0
    return 4.0


def safety_lines():
    """The safety rules as plain English -- one list, injected into every prompt.

    A rule with no prose written for it is still emitted (as its raw id), because
    a rule that silently disappears is worse than an ugly one.
    """
    out = []
    for r in CONFIG.get("safety_rules") or []:
        r = str(r)
        if r.startswith("never_shock_and_acid_same_session_min_gap_hours:"):
            out.append("NEVER put shock and acid in the same session without at least %g hours "
                       "between them, and say the gap out loud in the instructions. Both move "
                       "pH hard in opposite directions, and back to back you cannot tell what "
                       "the water actually did." % min_gap_hours())
        elif r in RULE_TEXT:
            out.append(RULE_TEXT[r])
        else:
            out.append(r.replace("_", " "))
    return out


def safety_block():
    """The prompt section. Same text everywhere Claude is asked anything."""
    lines = safety_lines()
    if not lines:
        return ""
    return "## Safety rules that outrank everything else\n\n" + "\n".join(
        "%d. %s" % (i, s) for i, s in enumerate(lines, 1))


def tzinfo():
    """The pool's local timezone.

    The ICO's API reports measurement times in UTC and GitHub Actions runs in
    UTC, so without this an evening reading is stamped with tomorrow's date and
    shows up in the app as a reading from the future. Falls back to a fixed
    offset for machines with no IANA database, which is why `timezone` should
    stay set -- a fixed offset does not follow daylight saving.
    """
    from datetime import timedelta, timezone as _tz
    name = (CONFIG["pool"].get("timezone") or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            print("! timezone %r unavailable -- using the fixed offset" % name)
    try:
        return _tz(timedelta(hours=float(CONFIG["pool"].get("utc_offset_hours") or 0)))
    except (TypeError, ValueError):
        return _tz(timedelta(0))


def utc_to_local(iso):
    """'2026-09-25 01:38:34' (UTC) -> '2026-09-24T21:38:34' (local)."""
    from datetime import datetime, timezone as _tz
    s = str(iso or "").strip().replace(" ", "T")[:19]
    if not s:
        return s
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return s
    return dt.replace(tzinfo=_tz.utc).astimezone(tzinfo()).replace(tzinfo=None) \
             .isoformat(timespec="seconds")


def title():
    return (CONFIG["app"].get("title") or "Pool Pilot").strip() or "Pool Pilot"


if __name__ == "__main__":
    print(json.dumps(CONFIG, indent=2))
    print("\n" + safety_block())
