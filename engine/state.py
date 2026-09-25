"""
state.py -- assemble everything the app and the AI need into one JSON bundle.

  the latest confirmed reading  +  today's checklist (checklist.py)
  +  the cadence chips (days since backwash / salt / shock)
  +  the merged readings-and-actions timeline
  +  the opening wizard's position  +  the raw rows the phone recomputes from

Written to data/store/state.json. That single file is what the PWA renders and
what the checklist is generated from -- so the phone and the cron job can never
disagree about what the pool needs.

It also carries the RAW readings and actions. That looks redundant next to the
computed chips, but it lets the phone re-run the same arithmetic locally the
instant I tap "backwashed the filter", instead of waiting a minute for the cloud
to catch up. Same inputs, same answer, no lag. The cloud recomputes and
overwrites, so the server always wins in the end.

Usage:  py engine/state.py
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chem       # noqa: E402
import checklist  # noqa: E402
import poolcfg    # noqa: E402
import season     # noqa: E402
import store      # noqa: E402

DISCLAIMER = ("Every dose here is an estimate from your pool's volume and one measurement. "
              "Add, circulate, retest -- never dose twice against the same reading. "
              "Chemicals are handled at your own risk; when a number looks wrong, trust the "
              "water and retest before you trust this app.")

# what shows as a chip on the Today tab: how long since each of these
CHIPS = [
    {"key": "added_salt", "label": "Salt"},
    {"key": "added_shock", "label": "Shock"},
    {"key": "added_acid", "label": "Acid"},
    {"key": "backwashed_filter", "label": "Backwash"},
    {"key": "emptied_skimmer", "label": "Skimmer"},
]


def chips(acts, today):
    """Days-since for the things worth knowing at a glance.

    `due` is only set where config.cadence_days has an opinion -- a chip with no
    configured cadence is information, not a nag.
    """
    cad = poolcfg.CONFIG["cadence_days"]
    by_action = {v: k for k, v in store.CADENCE_FOR.items()}   # action key -> cadence key
    out = []
    for c in CHIPS:
        n = store.days_since(c["key"], today, acts)
        cad_key = by_action.get(c["key"])
        every = cad.get(cad_key) if cad_key else None
        last = store.last_action(c["key"], acts)
        out.append({"key": c["key"], "label": c["label"], "days": n,
                    "date": (last or {}).get("date"),
                    "amount": (last or {}).get("amount"), "unit": (last or {}).get("unit"),
                    "every": every,
                    "due": bool(every and n is not None and n >= every),
                    "never": n is None})
    return out


def timeline(readings, acts, limit=80):
    """Readings and actions merged into one card feed, newest first.

    One feed rather than two lists because the question I actually ask is "what
    has happened to this pool lately", and a dose is only meaningful next to the
    reading that caused it.
    """
    rows = []
    for r in readings:
        vals = [{"key": k, "label": checklist.MEASURE_LABELS[k][0],
                 "display": checklist._fmt(k, r[k]),
                 "status": checklist._status(k, r[k])}
                for k in store.MEASURES if r.get(k) is not None and k in checklist.MEASURE_LABELS]
        rows.append({"type": "reading", "at": r.get("at"), "date": r.get("date"),
                     "id": r.get("id"), "source": r.get("source"),
                     "confirmed": bool(r.get("confirmed")), "values": vals,
                     "note": r.get("note", ""), "image": r.get("image", "")})
    for a in acts:
        rows.append({"type": "action", "at": a.get("at"), "date": a.get("date"),
                     "id": a.get("id"), "action": a.get("action"),
                     "label": a.get("label") or a.get("action"),
                     "amount": a.get("amount"), "unit": a.get("unit", ""),
                     "note": a.get("note", "")})
    rows.sort(key=lambda r: (r.get("at") or "", r.get("type")), reverse=True)
    return rows[:limit]


TREND_KEYS = ("ph", "orp_mv", "salt_ppm", "water_temp_f")


def trends(readings, days=90, today=None):
    """The measured history for the chart. Gaps stay gaps.

    Nothing is interpolated between readings: a pool measured on the 1st and the
    20th has no data for the 10th, and drawing a straight line through it would
    invent a week of water that nobody tested.
    """
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    pts = [r for r in readings if (r.get("date") or "") >= cutoff and r.get("confirmed")]
    series = []
    for k in TREND_KEYS:
        lo, hi = poolcfg.band(k)
        vals = [{"date": r["date"], "at": r.get("at"), "v": r[k]}
                for r in pts if r.get(k) is not None]
        if not vals:
            continue
        series.append({"key": k, "label": checklist.MEASURE_LABELS[k][0],
                       "unit": checklist.MEASURE_LABELS[k][1].strip(),
                       "low": lo, "high": hi, "points": vals,
                       "min": min(v["v"] for v in vals), "max": max(v["v"] for v in vals),
                       "latest": vals[-1]["v"]})
    return {"from": cutoff, "to": today.isoformat(), "days": days, "series": series,
            "n_readings": len(pts)}


def basis(cl):
    """The few facts the checklist wording is only valid for."""
    return cl.get("basis") or {}


def build(today=None, with_checklist=True):
    today = today or date.today()
    readings = store.readings(False)
    acts = store.actions()
    latest = store.latest_reading(confirmed_only=True)
    pending = [r for r in readings if not r.get("confirmed")]

    cl = None
    p = poolcfg.store_path("checklist.json")
    if with_checklist and os.path.exists(p):
        try:
            cl = json.load(open(p, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cl = None
    # A checklist is stale if it is from another day OR if it was computed from a
    # different reading than the one now on top. Date alone was not enough: a
    # reading can land hours after the morning checklist was written, and the old
    # list would keep being served all day because it still carried today's date.
    stale = (cl is None
             or cl.get("date") != today.isoformat()
             or (cl.get("basis") or {}).get("reading_id") != (latest or {}).get("id"))
    if stale:
        # Never leave the app without a list. Written back to disk as well as used
        # here, so checklist.json always matches what the phone is actually showing
        # -- and so run_cloud's "did the checklist go stale?" check has something
        # to compare against on the very first run.
        cl = checklist.fallback(checklist.build(today))
        cl["source"] = cl.get("source") or "fallback"
        json.dump(cl, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    st = {
        "generated_at": date.today().isoformat(),
        "today": today.isoformat(),
        "title": poolcfg.title(),
        "pool": poolcfg.CONFIG["pool"],
        "equipment": poolcfg.CONFIG["equipment"],
        "targets": poolcfg.CONFIG["targets"],
        "cadence_days": poolcfg.CONFIG["cadence_days"],
        "chemicals": poolcfg.CONFIG["chemicals"],
        "safety_rules": poolcfg.safety_lines(),
        "checklist": cl,
        "panel": cl.get("panel") or checklist.panel(latest),
        "latest_reading": latest,
        "reading_age_days": cl.get("reading_age_days"),
        "pending_confirmation": pending[-3:],
        "chips": chips(acts, today),
        "timeline": timeline(readings, acts),
        "trends": trends(readings, 90, today),
        "opening": store.opening_state(),
        # what last autumn knows that this spring needs
        "opening_brief": season.opening_brief(today),
        "season": season.status(today),
        "closing_steps": season.closing_state()["steps"],
        "action_kinds": store.ACTION_KINDS,
        # only whether it is switched on -- no token, no pool id, nothing that
        # would be a leak in a public repo
        "ondilo": {"enabled": bool((poolcfg.CONFIG.get("ondilo") or {}).get("enabled"))},
        "reference": chem.summary(
            poolcfg.gallons(),
            poolcfg.CONFIG["chemicals"]["borate"].get("boron_pct", 17.5)),
        "counts": {"readings": len(readings), "confirmed": len(readings) - len(pending),
                   "actions": len(acts), "pending": len(pending)},
        "raw": {"readings": readings[-120:], "actions": acts[-200:]},
        "disclaimer": DISCLAIMER,
    }
    st["basis"] = basis(cl)
    return st


def main():
    st = build()
    p = poolcfg.store_path("state.json")
    json.dump(st, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    if st["season"]["closed"]:
        d = st["season"]["days_closed"]
        print("state written: CLOSED since %s%s -> %s"
              % (st["season"]["closed_at"],
                 (" (%d days)" % d) if d and d > 0 else "", p))
        return st
    r = st["latest_reading"]
    if r:
        print("state written: reading %s (%s days old), %d item(s) on the list -> %s"
              % (r.get("date"), st["reading_age_days"],
                 st["checklist"]["counts"]["todo"], p))
    else:
        print("state written (no confirmed reading yet) -> %s" % p)
    return st


if __name__ == "__main__":
    main()
