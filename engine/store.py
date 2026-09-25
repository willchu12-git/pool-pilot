"""
store.py -- append-only JSONL storage for everything the pool actually told me.

  data/store/readings.jsonl          one record per water test (ICO screenshot or typed)
  data/store/actions.jsonl           one record per thing I did to the pool
  data/store/opening_progress.json   where I am in the spring opening wizard

Append-only on purpose: nothing is ever destroyed. Correcting a reading appends a
newer record with the same id, and readers COLLAPSE by id (later non-null fields
win), so fixing an OCR mistake Just Works and the original stays on disk.

  reading: {id, at, date, source, ph?, orp_mv?, salt_ppm?, water_temp_f?, cya_ppm?,
            borates_ppm?, ta_ppm?, ch_ppm?, fc_ppm?, confirmed, image?, note?, logged_at}
  action:  {id, at, date, action, amount?, unit?, note?, logged_at}

A reading is UNCONFIRMED until I've looked at what the vision pass read off the
screenshot and said yes. Unconfirmed readings still show up (so I can see what it
thinks it saw) but the checklist refuses to dose off them -- a misread decimal
point on pH is the difference between a quart of acid and a gallon.

Measured values are GROUND TRUTH. Nothing in this app ever invents one.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import poolcfg  # noqa: E402

READINGS = "readings.jsonl"
ACTIONS = "actions.jsonl"
OPENING = "opening_progress.json"

# Every numeric a reading can carry. Anything not in here is dropped rather than
# stored, so a hallucinated field from the vision pass can't reach the checklist.
MEASURES = ("ph", "orp_mv", "salt_ppm", "water_temp_f", "cya_ppm", "borates_ppm",
            "ta_ppm", "ch_ppm", "fc_ppm")

# Sanity bounds. A value outside these is not a reading, it's a misread -- it gets
# dropped with a note rather than quietly dosed against.
SANE = {
    "ph": (5.0, 9.5), "orp_mv": (100, 1000), "salt_ppm": (0, 8000),
    "water_temp_f": (32, 110), "cya_ppm": (0, 200), "borates_ppm": (0, 150),
    "ta_ppm": (0, 400), "ch_ppm": (0, 1200), "fc_ppm": (0, 30),
}

# The quick chips on the Home tab, plus everything the cadence clock watches.
# `unit` is the default the phone pre-fills; `cadence` ties it to config.cadence_days.
ACTION_KINDS = [
    {"key": "added_salt", "label": "Added salt", "unit": "lb", "cadence": "salt_check"},
    {"key": "added_shock", "label": "Added shock", "unit": "gal", "cadence": None},
    {"key": "added_acid", "label": "Added acid", "unit": "fl oz", "cadence": None},
    {"key": "added_cya", "label": "Added stabilizer", "unit": "lb", "cadence": "cya_check"},
    {"key": "added_borate", "label": "Added borates", "unit": "lb", "cadence": None},
    {"key": "backwashed_filter", "label": "Backwashed filter", "unit": "", "cadence": "backwash"},
    {"key": "emptied_skimmer", "label": "Emptied skimmer", "unit": "", "cadence": "skimmer_basket"},
    {"key": "brushed_pool", "label": "Brushed pool", "unit": "", "cadence": None},
    {"key": "vacuumed_pool", "label": "Vacuumed", "unit": "", "cadence": None},
    {"key": "cell_output", "label": "Changed cell output", "unit": "%", "cadence": None},
    {"key": "other", "label": "Something else", "unit": "", "cadence": None},
]
ACTION_LABELS = {a["key"]: a["label"] for a in ACTION_KINDS}
CADENCE_FOR = {a["cadence"]: a["key"] for a in ACTION_KINDS if a["cadence"]}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _iso(v):
    """Accept 'today', 'yesterday', a datetime, or any ISO-ish string -> YYYY-MM-DD."""
    if v in (None, "", "null"):
        return None
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip().lower()
    if s == "today":
        return date.today().isoformat()
    if s == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        return None


def _stamp(v=None):
    """A full timestamp for ordering within a day. Falls back to 'now'."""
    if isinstance(v, (date, datetime)):
        return (v if isinstance(v, datetime) else datetime(v.year, v.month, v.day, 12)) \
            .isoformat(timespec="seconds")
    s = str(v or "").strip()
    if s:
        try:
            return datetime.fromisoformat(s.replace("Z", "").replace(" ", "T")[:19]) \
                .isoformat(timespec="seconds")
        except ValueError:
            d = _iso(s)
            if d:
                return d + "T12:00:00"
    return _now()


def _num(field, v):
    """A measurement, or None. Out-of-range means misread, not measured."""
    if v in (None, "", "null", "--", "n/a", "N/A"):
        return None
    if isinstance(v, str):
        v = v.replace(",", "").replace("ppm", "").replace("mV", "").replace("mv", "") \
             .replace("°F", "").replace("F", "").strip()
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    lo, hi = SANE.get(field, (None, None))
    if lo is not None and not (lo <= n <= hi):
        return None
    return round(n, 2)


def _truthy(v, default=False):
    if v in (None, ""):
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


# ------------------------------------------------------------------ jsonl core

def read_jsonl(name):
    p = poolcfg.store_path(name)
    rows = []
    if os.path.exists(p):
        for i, line in enumerate(open(p, encoding="utf-8"), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print("! skipping malformed line %d in %s" % (i, name))
    return rows


def append_jsonl(name, rec):
    with open(poolcfg.store_path(name), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _collapse(rows, key, order):
    """Later records patch earlier ones with the same key; null/'' never overwrites.

    Booleans are the exception -- `false` is a real answer ("no, I have NOT
    confirmed this reading"), so it has to be able to overwrite a previous true.
    """
    merged = {}
    for r in rows:
        k = r.get(key)
        if not k:
            continue
        cur = merged.setdefault(k, {})
        for f, v in r.items():
            if v not in (None, "", []) or isinstance(v, bool):
                cur[f] = v
            cur.setdefault(f, v)
    return sorted(merged.values(), key=lambda r: (r.get(order) or "", r.get(key) or ""))


# -------------------------------------------------------------------- readings

def readings(confirmed_only=False):
    """Every water test on record, oldest first, one per id."""
    rows = _collapse(read_jsonl(READINGS), "id", "at")
    return [r for r in rows if r.get("confirmed")] if confirmed_only else rows


def latest_reading(confirmed_only=True):
    rows = readings(confirmed_only)
    return rows[-1] if rows else None


def add_reading(at=None, source="manual", confirmed=True, image="", note="",
                rid=None, **measures):
    """Record one water test. Every measurement is optional -- blanks stay blank
    rather than becoming zeros, so a partial test never fakes a full panel."""
    stamp = _stamp(at)
    rec = {
        "id": (rid or stamp).strip() if isinstance(rid or stamp, str) else stamp,
        "at": stamp,
        "date": stamp[:10],
        "source": (source or "manual").strip().lower(),
        "confirmed": _truthy(confirmed, True),
        "image": (image or "").strip(),
        "note": (note or "").strip(),
        "logged_at": _now(),
    }
    dropped = []
    for f in MEASURES:
        raw = measures.get(f)
        val = _num(f, raw)
        if val is None and raw not in (None, "", "null"):
            dropped.append(f)
        rec[f] = val
    if dropped:
        rec["dropped"] = dropped          # kept visible: "we saw this and refused it"
    return append_jsonl(READINGS, rec)


def confirm_reading(rid, **fixes):
    """Mark a reading as checked by a human, optionally correcting what it read.

    Appends rather than edits -- the original OCR guess stays on disk forever,
    which is the only way to ever find out whether the vision pass is any good.

    Two things this refuses to do, both for the same reason (a confirmed reading
    is what the checklist doses off, so it has to mean something):

      * It will not confirm an id that isn't on disk. That happens when a
        confirmation overtakes the screenshot it belongs to, and confirming it
        anyway would mint a "confirmed" reading with no measurements and no date.
      * It will not silently swallow a correction that fails the sanity check.
        _num returns None for an out-of-range value and _collapse ignores None,
        so a fat-fingered "81" for pH used to vanish while the record still
        flipped to confirmed -- leaving the original bad OCR number confirmed and
        doseable. Now the whole confirmation is refused and says which field.
    """
    prior = next((r for r in readings(False) if r.get("id") == rid), None)
    if not prior:
        raise ValueError("no reading with id %r to confirm -- it may not have been "
                         "read yet; try again once the screenshot has been processed" % rid)

    rec = {"id": rid, "confirmed": True, "logged_at": _now(),
           "at": prior.get("at"), "date": prior.get("date")}
    rejected = []
    for f in MEASURES:
        if f not in fixes:
            continue
        raw = fixes[f]
        val = _num(f, raw)
        if val is None and raw not in (None, "", "null"):
            rejected.append("%s=%s" % (f, raw))
            continue
        rec[f] = val
    if rejected:
        lo_hi = ", ".join("%s (expected %g-%g)" % (r.split("=")[0], *SANE[r.split("=")[0]])
                          for r in rejected if r.split("=")[0] in SANE)
        raise ValueError("refusing to confirm: %s is out of range -- %s. Re-enter it; "
                         "nothing was changed." % (", ".join(rejected), lo_hi))
    if fixes.get("note"):
        rec["note"] = str(fixes["note"]).strip()
    return append_jsonl(READINGS, rec)


# --------------------------------------------------------------------- actions

def actions():
    """Everything I've done to the pool, oldest first."""
    return _collapse(read_jsonl(ACTIONS), "id", "at")


def add_action(action, amount=None, unit="", at=None, note="", aid=None):
    """Log one thing I did. `action` is a key from ACTION_KINDS (or free text)."""
    stamp = _stamp(at)
    key = (action or "other").strip().lower().replace(" ", "_")
    try:
        amt = float(amount) if amount not in (None, "", "null") else None
    except (TypeError, ValueError):
        amt = None
    rec = {"id": (aid or stamp) if isinstance(aid or stamp, str) else stamp,
           "at": stamp, "date": stamp[:10],
           "action": key, "label": ACTION_LABELS.get(key, (action or "Something else").strip()),
           "amount": amt, "unit": (unit or "").strip(),
           "note": (note or "").strip(), "logged_at": _now()}
    return append_jsonl(ACTIONS, rec)


def last_action(key, rows=None):
    """The most recent record of doing `key`, or None."""
    rows = rows if rows is not None else actions()
    hits = [a for a in rows if a.get("action") == key]
    return hits[-1] if hits else None


def days_since(key, today=None, rows=None):
    """Whole days since I last did `key`. None if I never have."""
    a = last_action(key, rows)
    if not a or not a.get("date"):
        return None
    today = today or date.today()
    try:
        return (today - date.fromisoformat(a["date"])).days
    except ValueError:
        return None


def recent_actions(days=7, today=None, rows=None):
    """What I've done lately -- the window the checklist checks before dosing again."""
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    return [a for a in (rows if rows is not None else actions()) if (a.get("date") or "") >= cutoff]


# ------------------------------------------------------------------- opening

def opening_steps():
    return poolcfg.CONFIG.get("opening", {}).get("steps") or []


def opening_progress():
    """Where I am in the spring opening. One small JSON file, rewritten in place --
    it's a cursor, not a history, and there's exactly one of it."""
    p = poolcfg.store_path(OPENING)
    data = {"steps": {}, "year": date.today().year, "updated_at": None}
    if os.path.exists(p):
        try:
            data.update(json.load(open(p, encoding="utf-8")) or {})
        except (json.JSONDecodeError, OSError):
            pass
    data.setdefault("steps", {})
    return data


def _write_opening(data):
    data["updated_at"] = _now()
    json.dump(data, open(poolcfg.store_path(OPENING), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    return data


def opening_state():
    """The wizard as the app draws it: every step, locked / current / done.

    Sequential and one-directional. A step unlocks only when the one before it is
    confirmed done, because each step assumes the previous one actually happened --
    salting a pool that's still green is just an expensive way to salt a swamp.
    """
    prog = opening_progress()
    done = prog.get("steps") or {}
    out, unlocked = [], True
    current = None
    for i, s in enumerate(opening_steps()):
        rec = done.get(s["id"]) or {}
        is_done = bool(rec.get("done_at"))
        state = "done" if is_done else ("current" if unlocked else "locked")
        if state == "current" and current is None:
            current = s["id"]
        out.append({"id": s["id"], "title": s["title"], "detail": s.get("detail", ""),
                    "n": i + 1, "state": state,
                    "confirmed_date": rec.get("confirmed_date"),
                    "note": rec.get("note", ""), "done_at": rec.get("done_at")})
        if not is_done:
            unlocked = False
    n_done = sum(1 for s in out if s["state"] == "done")
    return {"year": prog.get("year"), "steps": out, "done": n_done, "total": len(out),
            "current": current, "complete": bool(out) and n_done == len(out),
            "updated_at": prog.get("updated_at")}


def complete_opening_step(step_id, on=None, note=""):
    """Tick one step. Refuses out of order -- that's the whole point of the wizard."""
    st = opening_state()
    step = next((s for s in st["steps"] if s["id"] == step_id), None)
    if not step:
        raise ValueError("no opening step named %r" % step_id)
    if step["state"] == "locked":
        raise ValueError("%s is still locked -- finish %s first"
                         % (step["title"], st["current"] or "the earlier steps"))
    prog = opening_progress()
    prog["steps"][step_id] = {"done_at": _now(),
                              "confirmed_date": _iso(on) or date.today().isoformat(),
                              "note": (note or "").strip()}
    prog["year"] = date.today().year
    _write_opening(prog)
    return opening_state()


def undo_opening_step(step_id):
    """Untick a step, and everything after it -- a later step can't stand on an
    undone earlier one."""
    ids = [s["id"] for s in opening_steps()]
    if step_id not in ids:
        raise ValueError("no opening step named %r" % step_id)
    prog = opening_progress()
    for sid in ids[ids.index(step_id):]:
        prog["steps"].pop(sid, None)
    _write_opening(prog)
    return opening_state()


def reset_opening(year=None):
    """New season, blank wizard. The old one is gone from the cursor but every
    action logged during it is still in actions.jsonl."""
    return _write_opening({"steps": {}, "year": year or date.today().year})


# ---------------------------------------------------------------------- ingest

def ingest(rec):
    """Take one dict from the phone (inbox/*.json) and file it in the right place."""
    kind = (rec.get("kind") or rec.get("type") or "").lower()

    if kind in ("reading", "test", "water"):
        return "reading", add_reading(
            at=rec.get("at") or rec.get("date"), source=rec.get("source", "manual"),
            confirmed=rec.get("confirmed", True), image=rec.get("image", ""),
            note=rec.get("note", ""), rid=rec.get("id"),
            **{f: rec.get(f) for f in MEASURES})

    if kind in ("confirm", "confirm_reading"):
        return "confirm", confirm_reading(
            rec.get("id") or "", **{f: rec[f] for f in MEASURES if f in rec})

    if kind in ("opening", "opening_step"):
        if _truthy(rec.get("undo")):
            undo_opening_step(rec.get("step") or rec.get("step_id") or "")
            return "opening-undo", {"date": rec.get("date", "")}
        complete_opening_step(rec.get("step") or rec.get("step_id") or "",
                              rec.get("on") or rec.get("date"), rec.get("note", ""))
        return "opening", {"date": rec.get("on") or rec.get("date", "")}

    if kind in ("opening_reset", "reset_opening"):
        reset_opening(rec.get("year"))
        return "opening-reset", {"date": rec.get("date", "")}

    if kind == "ping":
        return "ping", {"date": rec.get("date", "")}      # just a nudge to rebuild

    return "action", add_action(rec.get("action") or rec.get("what") or "other",
                                rec.get("amount"), rec.get("unit", ""),
                                rec.get("at") or rec.get("date"), rec.get("note", ""),
                                rec.get("id"))


# ------------------------------------------------------------------------- cli

def _usage():
    print(__doc__.strip())
    print("\nCLI:")
    print('  py engine/store.py reading --ph 8.1 --orp 564 --salt 2791 --temp 78')
    print('  py engine/store.py did added_salt --amount 40 --unit lb')
    print('  py engine/store.py did backwashed_filter')
    print('  py engine/store.py opening physical_prep --on today')
    print('  py engine/store.py opening salt_addition --undo')
    print('  py engine/store.py show')


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        return _usage()
    cmd, rest = argv[0], argv[1:]

    def opt(flag, default=None):
        return rest[rest.index(flag) + 1] if flag in rest and rest.index(flag) + 1 < len(rest) \
            else default

    positional = [a for a in rest if not a.startswith("--")]
    if cmd == "reading":
        r = add_reading(at=opt("--at"), source=opt("--source", "manual"),
                        note=opt("--note", ""), ph=opt("--ph"), orp_mv=opt("--orp"),
                        salt_ppm=opt("--salt"), water_temp_f=opt("--temp"),
                        cya_ppm=opt("--cya"), borates_ppm=opt("--borates"),
                        ta_ppm=opt("--ta"), ch_ppm=opt("--ch"), fc_ppm=opt("--fc"))
    elif cmd in ("did", "action"):
        r = add_action(positional[0] if positional else "other", opt("--amount"),
                       opt("--unit", ""), opt("--at"), opt("--note", ""))
    elif cmd == "opening":
        sid = positional[0] if positional else ""
        r = undo_opening_step(sid) if "--undo" in rest else \
            complete_opening_step(sid, opt("--on"), opt("--note", ""))
    elif cmd == "show":
        r = {"latest_reading": latest_reading(), "opening": opening_state(),
             "counts": {"readings": len(readings()), "actions": len(actions())}}
    else:
        return _usage()
    print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
