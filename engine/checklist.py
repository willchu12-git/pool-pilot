"""
checklist.py -- what the pool actually needs today, and how to do it safely.

  py engine/checklist.py            # build it, ask Claude for the wording
  py engine/checklist.py --offline  # skip Claude, use the built-in writer

Two halves, and the split is the point:

  DETERMINISTIC (this file, plus chem.py)
      Compares the latest CONFIRMED reading against config.targets, cross-checks
      the last 7 days of actions.jsonl, and computes every dose. Nothing here
      involves a language model. The list of tasks and the number of fluid ounces
      are arithmetic, and they are the same every time.

  LANGUAGE (engine/checklist_prompt.md)
      Claude is handed the finished list and asked for two sentences per item --
      WHY it matters and HOW to do it without hurting yourself or the liner. It
      cannot add an item, remove one, or change a dose; _apply() only copies the
      `why` and `how` strings onto items that already exist.

If Claude is unreachable -- no CLI, no network, cron blip -- a deterministic
fallback writer produces the same structure. The checklist is never blank, and
the doses are identical either way.

The single most important rule in here isn't a chemistry rule, it's this: if I
already did something after the last reading was taken, the checklist stops
asking for it and asks for a RETEST instead. Dosing twice against one measurement
is how a pool gets overshot.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chem      # noqa: E402
import poolcfg   # noqa: E402
import store     # noqa: E402

# priority 0 = safety / do first, 1 = today, 2 = this week, 3 = just watching
P_SAFETY, P_TODAY, P_SOON, P_WATCH = 0, 1, 2, 3

# A dose item and the action that would make it stale. If I logged that action
# AFTER the reading the dose was computed from, the dose is based on water that
# no longer exists -- so the item becomes "retest", not "add more".
SUPERSEDED_BY = {
    "ph_down": "added_acid",
    "salt_up": "added_salt",
    "cya_up": "added_cya",
    "borate_up": "added_borate",
    "shock": "added_shock",
    "orp_cell": "cell_output",
}

# what to call each one in "Retest before touching X again"
SUPERSEDED_NOUN = {
    "ph_down": "pH", "salt_up": "salt", "cya_up": "stabilizer",
    "borate_up": "borates", "shock": "chlorine", "orp_cell": "the cell",
}

MEASURE_LABELS = {
    "ph": ("pH", ""), "orp_mv": ("ORP", " mV"), "salt_ppm": ("Salt", " ppm"),
    "cya_ppm": ("CYA", " ppm"), "borates_ppm": ("Borates", " ppm"),
    "ta_ppm": ("TA", " ppm"), "ch_ppm": ("Calcium", " ppm"),
    "fc_ppm": ("Free chlorine", " ppm"), "tds_ppm": ("TDS", " ppm"),
    "water_temp_f": ("Water temp", "°F"),
}


def _band_text(key):
    lo, hi = poolcfg.band(key)
    label, unit = MEASURE_LABELS.get(key, (key, ""))
    if lo is not None and hi is not None:
        return ("%g%s" % (lo, unit)) if lo == hi else ("%g-%g%s" % (lo, hi, unit))
    if lo is not None:
        return "at least %g%s" % (lo, unit)
    if hi is not None:
        return "no more than %g%s" % (hi, unit)
    return "no target set"


def _fmt(key, v):
    label, unit = MEASURE_LABELS.get(key, (key, ""))
    if v is None:
        return "not measured"
    return ("%g%s" % (round(v, 2), unit)) if key != "salt_ppm" else "%s ppm" % format(int(v), ",")


# How far under the borate target still counts as fine. Borate is a buffer you
# build once and top up rarely, not a band you hold -- so 47 ppm against a 50 ppm
# target is a success, not a deficiency.
BORATE_SLACK_PPM = 5.0


def _bounds(key):
    """The (low, high) actually used to judge a value.

    A scalar target like borates_ppm comes back from poolcfg.band() as (50, 50),
    a zero-width band that calls 49 "low" and 51 "high". Nothing about borates
    works that way, and it made the panel disagree with the checklist, which
    treats anything within BORATE_SLACK_PPM as fine.
    """
    lo, hi = poolcfg.band(key)
    if key == "borates_ppm" and lo is not None and lo == hi:
        return lo - BORATE_SLACK_PPM, None
    return lo, hi


def _status(key, v):
    """low / ok / high / unknown against the configured band."""
    if v is None:
        return "unknown"
    lo, hi = _bounds(key)
    if lo is not None and v < lo:
        return "low"
    if hi is not None and v > hi:
        return "high"
    return "ok"


def panel(reading):
    """Every measured value with its target and its verdict -- the dials on Today."""
    r = reading or {}
    out = []
    for key in ("ph", "orp_mv", "fc_ppm", "salt_ppm", "cya_ppm", "borates_ppm", "ta_ppm",
                "water_temp_f"):
        lo, hi = _bounds(key)
        if lo is None and hi is None and key != "water_temp_f":
            continue
        v = r.get(key)
        label, unit = MEASURE_LABELS[key]
        out.append({"key": key, "label": label, "unit": unit.strip(),
                    "fmt_unit": unit, "value": v,
                    "display": _fmt(key, v),
                    "target": "" if key == "water_temp_f" else _band_text(key),
                    "status": _status(key, v) if key != "water_temp_f" else
                              ("ok" if v is not None else "unknown"),
                    "low": lo, "high": hi})
    return out


def _item(key, title, kind, priority, because, **kw):
    it = {"id": key, "key": key, "title": title, "kind": kind, "priority": priority,
          "because": because, "why": "", "how": "", "dose": None, "measured": None,
          "target": None, "wait_hours": None, "after": None, "superseded": False}
    it.update(kw)
    return it


def _age_days(iso_at, today):
    if not iso_at:
        return None
    try:
        return (today - date.fromisoformat(str(iso_at)[:10])).days
    except ValueError:
        return None


# --------------------------------------------------------------- the rules

def _ph_items(r, gallons, chems):
    ph = r.get("ph")
    if ph is None:
        return []
    lo, hi = poolcfg.band("ph")
    if hi is not None and ph > hi:
        target = hi - 0.05 if (lo is None or hi - 0.05 > lo) else hi
        d = chem.acid_floz(ph, target, gallons, r.get("ta_ppm"),
                           chems["acid"].get("strength_pct", 31.45))
        if not d:
            return []
        dose = {"amount": d["floz"], "unit": "fl oz", "chemical": chems["acid"]["name"],
                "label": "%g fl oz (%g cups) of %s" % (d["floz"], d["cups"],
                                                       chems["acid"]["name"]),
                "detail": d}
        because = ("pH is %s, above the %s target. %s" % (
            _fmt("ph", ph), _band_text("ph"),
            "TA wasn't measured, so this dose assumes a mid-band buffer -- treat it as a "
            "starting point and retest." if d["ta_assumed"] else
            "Computed against the measured TA of %g." % d["ta_used"]))
        if d["capped"]:
            lands = chem.ph_after_acid(ph, d["floz"], gallons, r.get("ta_ppm"),
                                       chems["acid"].get("strength_pct", 31.45))
            because += (" This is a partial dose on purpose -- it should land near pH %s, not "
                        "all the way to target. Past about half a pH unit you're pouring "
                        "against water you can't see any more, so the rest goes in tomorrow "
                        "after a retest." % lands)
        return [_item("ph_down", "Bring pH down", "dose", P_TODAY, because,
                      dose=dose, measured=_fmt("ph", ph), target=_band_text("ph"))]
    if lo is not None and ph < lo:
        return [_item("ph_up", "pH is below target", "task", P_SOON,
                      "pH is %s, under the %s target. On a saltwater pool pH normally climbs, "
                      "so a low reading is worth re-testing before you treat it."
                      % (_fmt("ph", ph), _band_text("ph")),
                      measured=_fmt("ph", ph), target=_band_text("ph"))]
    return []


# The floor comes from config.targets.fc_ppm so the panel and the shock rule can
# never disagree about what "low" means. 2.0 is only the fallback.
FC_SHOCK_TARGET_PPM = 6.0   # where a shock dose aims


def _fc_floor():
    lo, _ = poolcfg.band("fc_ppm")
    return lo if lo is not None else 2.0


def _shock_items(r, gallons, chems):
    """Shock only on a MEASURED free-chlorine shortfall.

    Deliberately not triggered by low ORP alone. ORP is a proxy that moves with pH,
    salt and temperature, and "the proxy looks low so add a gallon of chlorine" is
    exactly the reflex that overshoots a pool. No FC number, no shock item.
    """
    floor = _fc_floor()
    fc = r.get("fc_ppm")
    if fc is None or fc >= floor:
        return []
    d = chem.chlorine_gal(FC_SHOCK_TARGET_PPM - fc, gallons,
                          chems["chlorine"].get("strength_pct", 12.5))
    if not d:
        return []
    dose = {"amount": d["gal"], "unit": "gal", "chemical": chems["chlorine"]["name"],
            "label": "%g gal of %g%% %s" % (d["gal"], d["strength_pct"],
                                            chems["chlorine"]["name"]),
            "detail": d}
    return [_item("shock", "Raise free chlorine", "dose", P_SAFETY,
                  "Free chlorine is %s, under the %g ppm floor. That's the one number where "
                  "low means the water genuinely isn't protected, so it goes first."
                  % (_fmt("fc_ppm", fc), floor),
                  dose=dose, measured=_fmt("fc_ppm", fc),
                  target=_band_text("fc_ppm"))]


def _ta_items(r, items_so_far):
    """Total alkalinity. There is no such thing as an 'add this to lower TA' dose.

    TA only comes down by acid plus aeration: acid drops pH and TA together, then
    aerating drives pH back up while leaving TA where it fell. So when acid is
    already on today's list this is just a note that it is doing double duty, and
    when it isn't, this is a project to plan rather than a chore to tick off.
    """
    ta = r.get("ta_ppm")
    if ta is None:
        return []
    lo, hi = poolcfg.band("ta_ppm")
    if hi is not None and ta > hi:
        acid_today = any(i["key"] == "ph_down" and i["dose"] for i in items_so_far)
        because = ("TA is %s, over the %s band, and high TA is what makes pH climb every week "
                   "on a saltwater pool." % (_fmt("ta_ppm", ta), _band_text("ta_ppm")))
        because += (" The acid already on today's list pulls TA down with the pH, so this needs "
                    "no separate dose -- just keep an eye on it as the pH corrections land."
                    if acid_today else
                    " Bringing it down is acid plus aeration, not an additive: acid to drive pH "
                    "to about 7.2, then run the waterfall to aerate pH back up while TA stays "
                    "down. Worth planning for a weekend, not doing on a Tuesday.")
        return [_item("ta_high", "Total alkalinity is running high", "watch", P_WATCH, because,
                      measured=_fmt("ta_ppm", ta), target=_band_text("ta_ppm"))]
    if lo is not None and ta < lo:
        return [_item("ta_low", "Total alkalinity is low", "watch", P_SOON,
                      "TA is %s, under the %s band. Low TA means pH swings on very little "
                      "provocation. Borates buffer some of this, but if it keeps drifting down "
                      "it wants baking soda -- worth a confirming test first."
                      % (_fmt("ta_ppm", ta), _band_text("ta_ppm")),
                      measured=_fmt("ta_ppm", ta), target=_band_text("ta_ppm"))]
    return []


def _salt_items(r, gallons, chems):
    salt = r.get("salt_ppm")
    if salt is None:
        return []
    lo, hi = poolcfg.band("salt_ppm")
    if lo is not None and salt < lo:
        mid = (lo + hi) / 2.0 if hi is not None else lo + 100
        d = chem.salt_lb(salt, mid, gallons)
        if not d:
            return []
        bags = chem.salt_bags(d["lb"], chems["salt"].get("bag_lb", 40))
        dose = {"amount": d["lb"], "unit": "lb", "chemical": chems["salt"]["name"],
                "label": "%g lb of salt (about %g of a %g lb bag)"
                         % (d["lb"], bags["bags"], bags["bag_lb"]),
                "detail": dict(d, **bags)}
        return [_item("salt_up", "Add salt", "dose", P_TODAY,
                      "Salt is %s, below the %s the cell needs. A cell run under its salt band "
                      "makes less chlorine and wears out faster."
                      % (_fmt("salt_ppm", salt), _band_text("salt_ppm")),
                      dose=dose, measured=_fmt("salt_ppm", salt),
                      target=_band_text("salt_ppm"))]
    if hi is not None and salt > hi:
        return [_item("salt_high", "Salt is above the band", "watch", P_WATCH,
                      "Salt is %s, over the %s band. There is no additive that removes salt -- "
                      "it only comes down by replacing water, and rain and backwashing will do "
                      "some of that for you." % (_fmt("salt_ppm", salt),
                                                 _band_text("salt_ppm")),
                      measured=_fmt("salt_ppm", salt), target=_band_text("salt_ppm"))]
    return []


def _cya_items(r, gallons, chems):
    cya = r.get("cya_ppm")
    if cya is None:
        return []
    lo, hi = poolcfg.band("cya_ppm")
    if lo is not None and cya < lo:
        mid = (lo + hi) / 2.0 if hi is not None else lo + 10
        d = chem.cya_lb(cya, mid, gallons)
        if not d:
            return []
        dose = {"amount": d["lb"], "unit": "lb", "chemical": chems["cya"]["name"],
                "label": "%g lb of %s" % (d["lb"], chems["cya"]["name"]), "detail": d}
        because = ("CYA is %s, under the %s target. Without stabilizer the sun strips chlorine "
                   "faster than the cell can replace it, so the cell runs flat out and the ORP "
                   "still sags." % (_fmt("cya_ppm", cya), _band_text("cya_ppm")))
        if d["capped"]:
            because += (" This is a partial dose on purpose -- CYA only comes back down by "
                        "draining water, so it goes up in steps with a retest between them.")
        return [_item("cya_up", "Add stabilizer", "dose", P_TODAY, because,
                      dose=dose, measured=_fmt("cya_ppm", cya), target=_band_text("cya_ppm"))]
    if hi is not None and cya > hi:
        return [_item("cya_high", "CYA is above the band", "watch", P_WATCH,
                      "CYA is %s, over the %s band. High stabilizer slows chlorine down, and "
                      "the only way back is replacing water -- so just stop adding any and let "
                      "rain and backwashing dilute it." % (_fmt("cya_ppm", cya),
                                                           _band_text("cya_ppm")),
                      measured=_fmt("cya_ppm", cya), target=_band_text("cya_ppm"))]
    return []


def _borate_items(r, gallons, chems):
    tgt = poolcfg.CONFIG["targets"].get("borates_ppm")
    cur = r.get("borates_ppm")
    if tgt is None:
        return []
    if cur is None:
        return [_item("borate_test", "Test borates", "test", P_WATCH,
                      "Borates aren't on the ICO panel and haven't been tested. The target is "
                      "%g ppm; a strip test once a season is enough to know where you are." % tgt,
                      target="%g ppm" % tgt)]
    if cur >= tgt - BORATE_SLACK_PPM:
        return []
    d = chem.borate_lb(cur, tgt, gallons, chems["borate"].get("boron_pct", 17.5))
    if not d:
        return []
    dose = {"amount": d["lb"], "unit": "lb", "chemical": chems["borate"]["name"],
            "label": "%g lb of %s, in about %d bucket batches"
                     % (d["lb"], chems["borate"]["name"], d["batches"]),
            "detail": d}
    return [_item("borate_up", "Build the borate buffer", "dose", P_SOON,
                  "Borates are %s against a %g ppm target. At 50 ppm borates hold pH steady, "
                  "which on a saltwater pool is most of the reason pH stops climbing every "
                  "week. This is a one-time build, not a weekly chore -- and it is a lot of "
                  "powder, so it goes in over batches."
                  % (_fmt("borates_ppm", cur), tgt),
                  dose=dose, measured=_fmt("borates_ppm", cur), target="%g ppm" % tgt)]


def _orp_items(r, out_of_band, acts, gallons):
    """ORP is downstream of everything else, so this rule reads the other rules first.

    Turning the cell up while salt or CYA is low is chasing a symptom: the cell is
    already the constraint, and more output on a cell that can't keep up just wears
    it out. So when anything upstream is out of band, this becomes an explanation
    instead of an instruction.
    """
    orp = r.get("orp_mv")
    if orp is None:
        return []
    lo, hi = poolcfg.band("orp_mv")
    if lo is None or orp >= lo:
        return []
    upstream = [k for k in ("ph", "salt_ppm", "cya_ppm") if k in out_of_band]
    if upstream:
        labels = [MEASURE_LABELS[k][0] for k in upstream]
        names = (labels[0] if len(labels) == 1 else
                 ", ".join(labels[:-1]) + " and " + labels[-1])
        return [_item("orp_blocked", "Leave the cell alone for now", "watch", P_SOON,
                      "ORP is %s, under the %s floor -- but %s %s out of band too, and ORP sits "
                      "downstream of all of it. Fix %s first, give the water a full day to "
                      "circulate, then re-read. Turning the cell up now just means running it "
                      "harder against the same problem."
                      % (_fmt("orp_mv", orp), _band_text("orp_mv"), names,
                         "are" if len(upstream) > 1 else "is",
                         "those" if len(upstream) > 1 else "that"),
                      measured=_fmt("orp_mv", orp), target=_band_text("orp_mv"))]
    last = store.last_action("cell_output", acts)
    last_age = _age_days(last.get("at") if last else None, date.today())
    if last_age is not None and last_age < 2:
        return [_item("orp_wait", "Give the cell change time to show up", "watch", P_SOON,
                      "ORP is %s, under the %s floor, but you changed the cell output %s. It "
                      "takes a day or two of running for that to move ORP -- changing it again "
                      "now means you'll never know which change did what."
                      % (_fmt("orp_mv", orp), _band_text("orp_mv"),
                         "today" if last_age == 0 else "yesterday"),
                      measured=_fmt("orp_mv", orp), target=_band_text("orp_mv"))]
    step = poolcfg.CONFIG["equipment"].get("salt_cell_increment_pct", 10)
    cur_pct = (last or {}).get("amount")
    nudge = chem.cell_nudge(cur_pct, "up", step)
    label = ("Turn the %s cell up from %g%% to %g%%" % (
        poolcfg.CONFIG["equipment"].get("salt_cell", "salt"), nudge["from_pct"], nudge["to_pct"])
        if nudge["known"] else
        "Turn the %s cell output up one step (about %g%%)"
        % (poolcfg.CONFIG["equipment"].get("salt_cell", "salt"), step))
    return [_item("orp_cell", "Turn the salt cell up one step", "setting", P_TODAY,
                  "ORP is %s, under the %s floor, and pH, salt and CYA are all in band -- so "
                  "the cell genuinely isn't making enough. One step only: the water takes a day "
                  "or two to answer, and stacking changes before it does is how you end up "
                  "chasing it." % (_fmt("orp_mv", orp), _band_text("orp_mv")),
                  dose={"amount": step, "unit": "%", "chemical": "salt cell output",
                        "label": label, "detail": nudge},
                  measured=_fmt("orp_mv", orp), target=_band_text("orp_mv"))]


def _cadence_items(acts, today, reading_age):
    """The chores nothing measures: backwash, skimmer, and testing itself."""
    cad = poolcfg.CONFIG["cadence_days"]
    out = []

    every = cad.get("reading")
    if reading_age is None:
        out.append(_item("reading_none", "Take a water reading", "test", P_TODAY,
                         "There's no confirmed reading on record yet. Everything else on this "
                         "list is waiting on one -- upload an ICO screenshot or type a test in "
                         "by hand and the checklist fills itself out."))
    elif every and reading_age >= every:
        out.append(_item("reading_stale", "Take a fresh reading", "test",
                         P_TODAY if reading_age >= every * 2 else P_SOON,
                         "The last confirmed reading is %d days old and the target is every %d. "
                         "Doses below are computed from water that's %d days stale -- worth a "
                         "fresh test before you act on them."
                         % (reading_age, every, reading_age)))

    for key, title, verb in (("backwash", "Backwash the filter", "backwashed"),
                             ("skimmer_basket", "Empty the skimmer basket", "emptied")):
        every = cad.get(key)
        akey = store.CADENCE_FOR.get(key)
        if not every or not akey:
            continue
        n = store.days_since(akey, today, acts)
        if n is None:
            out.append(_item(akey, title, "task", P_SOON,
                             "Never logged. The target cadence is every %d days -- log it once "
                             "and the clock starts." % every))
        elif n >= every:
            out.append(_item(akey, title, "task",
                             P_TODAY if n >= every * 1.5 else P_SOON,
                             "Last %s %d days ago; the cadence is every %d."
                             % (verb, n, every)))
    return out


def _apply_supersedes(items, reading_at, acts):
    """Anything I already did AFTER the reading stops being an instruction.

    This is the check that keeps one measurement from being dosed against twice.
    The item stays on the list -- it just turns into "you already did this, go
    retest" -- because silently dropping it would look like the problem fixed
    itself.
    """
    if not reading_at:
        return items
    for it in items:
        akey = SUPERSEDED_BY.get(it["key"])
        if not akey:
            continue
        later = [a for a in acts if a.get("action") == akey and (a.get("at") or "") > reading_at]
        if not later:
            continue
        a = later[-1]
        amt = ("%g %s " % (a["amount"], a.get("unit") or "")) if a.get("amount") else ""
        it["superseded"] = True
        it["priority"] = P_SOON
        it["kind"] = "test"
        it["title"] = "Retest before touching %s again" % SUPERSEDED_NOUN.get(
            it["key"], "that")
        it["because"] = ("You logged \"%s\" %son %s, which is AFTER the reading this dose was "
                         "computed from. The water has already moved -- take a fresh reading "
                         "instead of dosing the same measurement twice."
                         % (a.get("label") or akey, amt, a.get("date")))
        it["dose"] = None
    return items


def _apply_gap(items):
    """Keep acid and shock apart by the configured gap, and say the gap out loud.

    This is safety_rules' `never_shock_and_acid_same_session_min_gap_hours` made
    real, in code, before any language model sees the list -- so it holds even
    when the AI never runs.
    """
    gap = poolcfg.min_gap_hours()
    acid = next((i for i in items if i["key"] == "ph_down" and i["dose"]), None)
    shock = next((i for i in items if i["key"] == "shock" and i["dose"]), None)
    if acid and shock:
        shock["wait_hours"] = gap
        shock["after"] = acid["id"]
        shock["because"] += (" Acid is on today's list too: wait at least %g hours after the "
                             "acid before this goes in, and never put them in together."
                             % gap)
    return items


def _order(items):
    """Sort by urgency, then force any item that must FOLLOW another to sit below it.

    Sorting on priority alone got this dangerously wrong: a shock item is
    P_SAFETY (low free chlorine is the one genuine emergency) and acid is
    P_TODAY, so the list rendered "Raise free chlorine -- wait at least 4 hours
    after the acid" ABOVE the acid it was waiting for. A safety gap you have to
    read the list backwards to honour is not a safety gap.

    So `after` is enforced here rather than merely recorded: the dependent item
    is lifted out and reinserted directly below whatever it depends on.
    """
    items.sort(key=lambda i: (i["priority"], 0 if i["dose"] else 1, i["title"]))
    for _ in range(len(items)):                  # bounded; chains here are length 1
        moved = False
        for it in list(items):
            dep = it.get("after")
            if not dep:
                continue
            di = next((n for n, x in enumerate(items) if x["id"] == dep), None)
            if di is None:
                it["after"] = None               # dependency vanished (superseded)
                continue
            ii = items.index(it)
            if ii < di:
                items.insert(di + 1, items.pop(ii))
                moved = True
        if not moved:
            break
    for n, it in enumerate(items, 1):
        it["order"] = n
    return items


def build(today=None, reading=None):
    """The whole deterministic checklist. No AI anywhere in this function."""
    today = today or date.today()
    gallons = poolcfg.gallons()
    chems = poolcfg.CONFIG["chemicals"]
    acts = store.actions()
    r = reading if reading is not None else store.latest_reading(confirmed_only=True)
    pending = [x for x in store.readings(False) if not x.get("confirmed")]

    reading_age = _age_days((r or {}).get("at"), today)
    pnl = panel(r)
    out_of_band = {p["key"] for p in pnl if p["status"] in ("low", "high")}

    items = []
    items += _cadence_items(acts, today, reading_age)
    if r:
        items += _ph_items(r, gallons, chems)
        items += _shock_items(r, gallons, chems)
        items += _salt_items(r, gallons, chems)
        items += _cya_items(r, gallons, chems)
        items += _borate_items(r, gallons, chems)
        items += _ta_items(r, items)
        items += _orp_items(r, out_of_band, acts, gallons)

    items = _apply_supersedes(items, (r or {}).get("at"), acts)
    items = _apply_gap(items)
    items = _order(items)

    todo = [i for i in items if i["priority"] <= P_SOON and not i["superseded"]]
    return {
        "date": today.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reading": r,
        "reading_age_days": reading_age,
        "pending_confirmation": pending[-3:],
        "panel": pnl,
        "items": items,
        "counts": {"total": len(items), "todo": len(todo),
                   "doses": len([i for i in items if i["dose"]]),
                   "out_of_band": len(out_of_band)},
        "safety_rules": poolcfg.safety_lines(),
        "pool": {"gallons": gallons, "type": poolcfg.CONFIG["pool"]["type"],
                 "sanitization": poolcfg.CONFIG["pool"]["sanitization"]},
        "reference": chem.summary(gallons),
        "basis": basis_of(r, items, today),
    }


def basis_of(r, items, today):
    """The few facts the wording is only valid for. If these move, rewrite it."""
    return {"date": today.isoformat(), "reading_id": (r or {}).get("id"),
            "items": sorted(i["key"] for i in items),
            "doses": {i["key"]: (i["dose"] or {}).get("amount") for i in items if i["dose"]}}


# ----------------------------------------------------------------- the words

def _claude(prompt):
    coach = poolcfg.CONFIG["coach"]
    cmd = "claude -p"
    if coach.get("claude_model"):
        cmd += " --model " + coach["claude_model"]
    try:
        r = subprocess.run(cmd, input=prompt, shell=True, capture_output=True, text=True,
                           timeout=coach.get("timeout_sec", 300), encoding="utf-8",
                           errors="replace")
        if r.returncode != 0:
            print("  ! claude exited %s: %s" % (r.returncode, (r.stderr or "").strip()[:200]))
        return (r.stdout or "").strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print("  ! claude call failed: %s" % e)
        return ""


def _parse(text):
    """Pull the JSON object out of whatever Claude returned; None if unusable."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), dict):
        return None
    if not obj.get("headline"):
        return None
    return obj


def _apply(cl, parsed):
    """Copy ONLY the wording across.

    Claude cannot add an item, drop one, or change a dose: this walks the items
    the engine already built and copies two strings onto each. Anything it
    invented for an id that doesn't exist is simply never read.
    """
    if not parsed:
        return False
    said = parsed.get("items") or {}
    n = 0
    for it in cl["items"]:
        part = said.get(it["id"]) or said.get(it["key"]) or {}
        if isinstance(part, dict):
            why, how = str(part.get("why", "")).strip(), str(part.get("how", "")).strip()
            if why or how:
                it["why"], it["how"] = why, how
                n += 1
    cl["headline"] = str(parsed.get("headline", "")).strip()[:120]
    cl["summary"] = str(parsed.get("summary", "")).strip()[:600]
    return n > 0


# ------------------------------------------------------------------ fallback

HOW = {
    "ph_down": "Pump running. Measure the acid into a bucket of pool water first -- acid into "
               "water, never water into acid -- then walk it slowly around the deep end, away "
               "from the skimmer and the returns. Never down the skimmer. Retest tomorrow.",
    "salt_up": "Pump running. Broadcast the salt across the deep end, then brush it off the "
               "floor until it dissolves so it isn't sitting on the liner. Give it a full "
               "circulation -- 24 hours -- before you trust a salt reading.",
    "cya_up": "Predissolve it in a 5-gallon bucket of pool water, or hang it in a sock in front "
              "of a return. Never broadcast granules onto a vinyl liner. It reads slowly -- "
              "wait a week before retesting.",
    "borate_up": "Predissolve each batch in a 5-gallon bucket of warm pool water with the pump "
                 "running, and pour it in slowly. Do a couple of batches a day rather than all "
                 "at once, and retest pH afterwards.",
    "shock": "Pump running. Pour liquid chlorine slowly around the deep end, never into the "
             "skimmer, and never into a bucket that has had acid in it. Keep the pump running "
             "overnight.",
    "orp_cell": "Change it on the cell controller only -- don't touch anything else the same "
                "day, so tomorrow's reading tells you what this one change did.",
    "backwashed_filter": "Pump off, valve to backwash, pump on until the sight glass runs clear, "
                         "then rinse for 20 seconds before going back to filter. Top the water "
                         "level back up afterwards.",
    "emptied_skimmer": "Pump off first. Lift the basket, empty it, check the weir flap still "
                       "swings freely, drop it back in, pump on.",
    "reading_stale": "Drop the ICO back in or run a strip, then upload the screenshot here and "
                     "confirm what it read.",
    "reading_none": "Drop the ICO in or run a test strip, then upload the screenshot here and "
                    "confirm what it read.",
    "borate_test": "A borate test strip takes ten seconds. Once a season is plenty.",
}
HOW["reading_stale"] = HOW["reading_none"]
DEFAULT_HOW = "Pump running before anything goes in, and keep it running afterwards."


def fallback(cl):
    """A real checklist without the AI: the engine's own reasoning, plus the safe
    method for each item. Same items, same doses -- just plainer sentences."""
    for it in cl["items"]:
        it["why"] = it["because"]
        it["how"] = HOW.get(it["key"], DEFAULT_HOW if it["dose"] else "")
        if it["wait_hours"]:
            it["how"] = ("Wait at least %g hours after the acid. " % it["wait_hours"]) + it["how"]
    todo = cl["counts"]["todo"]
    age = cl["reading_age_days"]
    if not cl.get("reading"):
        cl["headline"] = "Upload a reading and this fills in."
        cl["summary"] = ("Nothing is dosed without a measurement. Upload an ICO screenshot or "
                         "type a test in by hand, confirm what it read, and the checklist "
                         "builds itself.")
    elif todo == 0:
        cl["headline"] = "Nothing needs doing today."
        cl["summary"] = ("Everything measured is inside its target band and no chore is due. "
                         "The last reading is %s old." % ("%d day(s)" % age if age is not None
                                                          else "of unknown age"))
    else:
        worst = cl["items"][0]
        cl["headline"] = worst["title"] + ("." if not worst["title"].endswith(".") else "")
        cl["summary"] = ("%d thing(s) on the list today, %d of them a dose. Reading is %s old. "
                         "Pump running before anything goes in."
                         % (todo, cl["counts"]["doses"],
                            "%d day(s)" % age if age is not None else "unknown age"))
    return cl


# ---------------------------------------------------------------------- main

def context(cl):
    """Trim the checklist to what actually helps the writer (and keeps it cheap)."""
    return {
        "date": cl["date"],
        "pool": cl["pool"],
        "equipment": poolcfg.CONFIG["equipment"],
        "reading": {k: v for k, v in (cl.get("reading") or {}).items()
                    if k in store.MEASURES or k in ("at", "source")},
        "reading_age_days": cl["reading_age_days"],
        "panel": cl["panel"],
        "recent_actions": [{k: a.get(k) for k in ("date", "label", "amount", "unit", "note")}
                           for a in store.recent_actions(7)],
        "items": [{k: i[k] for k in ("id", "order", "title", "kind", "priority", "because",
                                     "dose", "measured", "target", "wait_hours", "after",
                                     "superseded")}
                  for i in cl["items"]],
    }


def generate(cl, offline=False):
    if offline:
        print("  offline mode -- using the built-in writer")
        return fallback(cl), "fallback"
    tpl = open(os.path.join(HERE, "checklist_prompt.md"), encoding="utf-8").read()
    prompt = (tpl.replace("{{SAFETY}}", poolcfg.safety_block())
                 .replace("{{CHECKLIST}}", json.dumps(context(cl), indent=2, ensure_ascii=False)))
    open(poolcfg.store_path("_checklist_prompt.txt"), "w", encoding="utf-8").write(prompt)
    parsed = _parse(_claude(prompt))
    if parsed and _apply(cl, parsed):
        return cl, "claude"
    print("  ! no usable AI output -- falling back to the built-in writer")
    return fallback(cl), "fallback"


def main(argv=None):
    """argv is explicit so callers other than the CLI can pick the offline path --
    engine/serve.py does, because it answers a browser fetch synchronously and
    must not sit on a 300-second CLI call to do it."""
    argv = sys.argv[1:] if argv is None else argv
    offline = "--offline" in argv
    cl = build()
    cl, source = generate(cl, offline=offline)
    cl["source"] = source
    json.dump(cl, open(poolcfg.store_path("checklist.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print("checklist written (%s): %s -- %d item(s), %d dose(s)"
          % (source, cl.get("headline", ""), cl["counts"]["todo"], cl["counts"]["doses"]))
    return cl


if __name__ == "__main__":
    main()
