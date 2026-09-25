"""
trends.py -- what the water is DOING, not just where it is.

  py engine/trends.py              # the analysis
  py engine/trends.py --verbose    # with the underlying series

A single reading tells you pH is 7.97. Four weeks of readings tell you pH climbs
0.08 a week and you have poured acid five times to hold it there -- which is the
fact that actually decides whether borates are worth 43 pounds of boric acid.

So this fits a least-squares line through each measurement, measures how much
acid has genuinely been going in, and turns that into a recommendation about the
STABILISERS -- the chemicals you buy once to stop fighting the same fight every
week. Borates, CYA, alkalinity.

Two rules it is built around:

  1. IT IS ALLOWED TO SAY NO. A recommender that only ever says "buy more
     chemicals" is an advert. If pH is holding steady and the acid is barely
     moving, the answer is that borates are not worth it yet, and it says so.

  2. IT NEVER EXTRAPOLATES FROM NOTHING. Three readings is a line through noise.
     Every verdict carries how many readings and how many days it rests on, and
     under the minimum it returns "not enough yet" rather than a confident slope.

The doses themselves still come from chem.py. This decides WHETHER, never HOW MUCH.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chem      # noqa: E402
import poolcfg   # noqa: E402
import store     # noqa: E402

# Below this a "trend" is a line drawn through noise, and saying so is the
# honest output. Four readings over two weeks is the floor for an opinion.
MIN_POINTS = 4
MIN_SPAN_DAYS = 12
WINDOW_DAYS = 90

# How hard pH has to be climbing before buffering it is worth discussing, in
# pH units per week. Below this the acid is a rounding error on a Saturday.
PH_CLIMB_PER_WEEK = 0.04

# Acid actually poured, per month, before borates start paying for themselves.
# 32 fl oz is one quart -- roughly a quart a month is the point where people
# start noticing they are always buying acid.
ACID_FLOZ_PER_MONTH_WORTH_IT = 28.0

# Borates do not stop pH rise, they slow it. Published and anecdotal accounts
# vary a lot, so this is deliberately the cautious end of what people report.
BORATE_ACID_REDUCTION = 0.4

# fl oz per unit, for normalising however an acid dose got logged
FLOZ = {"fl oz": 1.0, "floz": 1.0, "oz": 1.0, "cup": 8.0, "cups": 8.0,
        "qt": 32.0, "quart": 32.0, "quarts": 32.0, "gal": 128.0, "gallon": 128.0,
        "l": 33.814, "ml": 0.033814, "": 1.0}


def _dnum(iso):
    return date.fromisoformat(str(iso)[:10]).toordinal()


# ------------------------------------------------------------------- fitting

def fit(points):
    """Least-squares slope through [(day_ordinal, value)]. Units: value per day.

    Also returns r2, because a slope without a fit quality is a number pretending
    to be a finding -- a pool that bounced between 7.6 and 8.2 at random has a
    slope too, and it means nothing.
    """
    n = len(points)
    if n < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in points)
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    r2 = 1.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return {"per_day": slope, "intercept": intercept, "r2": round(r2, 3), "n": n}


def series(readings, key, today=None, days=WINDOW_DAYS):
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    return [(r["date"], r[key]) for r in readings
            if r.get("confirmed") and r.get(key) is not None
            and (r.get("date") or "") >= cutoff]


def drift(readings, key, today=None, days=WINDOW_DAYS):
    """How `key` is moving. Honest about not knowing yet."""
    pts = series(readings, key, today, days)
    span = (_dnum(pts[-1][0]) - _dnum(pts[0][0])) if len(pts) >= 2 else 0
    base = {"key": key, "n": len(pts), "span_days": span,
            "first": pts[0] if pts else None, "last": pts[-1] if pts else None}
    if len(pts) < MIN_POINTS or span < MIN_SPAN_DAYS:
        return dict(base, enough=False, per_week=None, per_month=None, r2=None,
                    note="Only %d reading(s) over %d day(s) -- not enough to call a trend yet."
                         % (len(pts), span))
    f = fit([(_dnum(d), v) for d, v in pts])
    if not f:
        return dict(base, enough=False, per_week=None, per_month=None, r2=None,
                    note="Readings are all from the same day.")
    pw, pm = f["per_day"] * 7.0, f["per_day"] * 30.0
    return dict(base, enough=True, per_week=round(pw, 4), per_month=round(pm, 3),
                r2=f["r2"], steady=f["r2"] >= 0.5,
                direction="up" if pw > 0 else ("down" if pw < 0 else "flat"),
                note="%s %s per week across %d readings over %d days (fit %.0f%%)."
                     % (key, _fmt_rate(key, pw), len(pts), span, f["r2"] * 100))


def _fmt_rate(key, v):
    if v is None:
        return "?"
    sign = "+" if v > 0 else ""
    return "%s%.2f" % (sign, v) if key == "ph" else "%s%.0f" % (sign, v)


# --------------------------------------------------------------- acid demand

def acid_demand(actions, today=None, days=30):
    """How much acid has actually gone in, normalised to fluid ounces.

    This is the number the borate case rests on, and it comes from what was
    logged rather than from what the checklist suggested -- a dose that was
    recommended and never poured is not demand.
    """
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    rows = [a for a in actions if a.get("action") == "added_acid"
            and (a.get("date") or "") >= cutoff]
    total = 0.0
    for a in rows:
        amt = a.get("amount")
        if amt is None:
            continue
        total += float(amt) * FLOZ.get((a.get("unit") or "").strip().lower(), 1.0)
    return {"days": days, "occasions": len(rows), "floz": round(total, 1),
            "floz_per_month": round(total * 30.0 / days, 1),
            "per_month_occasions": round(len(rows) * 30.0 / days, 1),
            "logged_amounts": sum(1 for a in rows if a.get("amount") is not None)}


# ----------------------------------------------------------- recommendations

def _rec(key, title, verdict, because, confidence, **kw):
    """verdict: 'yes' | 'not yet' | 'no' | 'watch' | 'unknown'"""
    return dict({"key": key, "title": title, "verdict": verdict, "because": because,
                 "confidence": confidence}, **kw)


def borate_case(readings, actions, latest, ph, acid, today=None):
    """Would borates actually earn their keep here?

    The case for borates is not "borates are good", it is "you are buying acid
    every week and this would stop that". So it is argued from measured acid
    demand and measured pH climb, and when neither is happening it argues the
    other way.
    """
    tgt = poolcfg.CONFIG["targets"].get("borates_ppm")
    have = (latest or {}).get("borates_ppm")
    gallons = poolcfg.gallons()
    dose = chem.borate_lb(have or 0, tgt, gallons,
                          poolcfg.CONFIG["chemicals"]["borate"].get("boron_pct", 17.5))
    cost = {"lb": dose["lb"], "batches": dose["batches"]} if dose else None

    if tgt is None:
        return _rec("borates", "Borates", "unknown",
                    "No borate target is set in config.json, so there is nothing to compare to.",
                    "none")
    if have is not None and have >= tgt - 5:
        return _rec("borates", "Borates", "no",
                    "Already at %g ppm against a %g ppm target. Nothing to do -- borates are "
                    "not consumed, they only leave by dilution, so this holds until you "
                    "replace a lot of water." % (have, tgt), "high", at_target=True)

    climbing = ph.get("enough") and ph.get("per_week") is not None \
        and ph["per_week"] >= PH_CLIMB_PER_WEEK
    pouring = acid["floz_per_month"] >= ACID_FLOZ_PER_MONTH_WORTH_IT

    if not ph.get("enough") and acid["occasions"] < 2:
        return _rec("borates", "Borates", "unknown",
                    "Not enough history yet. %s Log your acid doses and give it a month of "
                    "readings, and this can be answered from your own pool rather than "
                    "from generalities." % ph.get("note", ""), "none", dose=cost)

    if climbing and pouring:
        saved = acid["floz_per_month"] * BORATE_ACID_REDUCTION
        return _rec("borates", "Borates", "yes",
                    "pH is climbing %s a week and you have put in about %g fl oz of acid a "
                    "month across %g occasions. Borates at %g ppm are a second buffer where "
                    "carbonate alkalinity is weakest, and people typically see acid demand "
                    "fall by something like a third to a half -- call it %g fl oz a month "
                    "back. That is the case for the %g lb build: it is a one-off that stops "
                    "a recurring chore."
                    % (_fmt_rate("ph", ph["per_week"]), acid["floz_per_month"],
                       acid["per_month_occasions"], tgt, round(saved), cost["lb"] if cost else 0),
                    "high" if ph.get("steady") else "medium", dose=cost,
                    acid_per_month=acid["floz_per_month"],
                    expected_saving_floz_per_month=round(saved))

    if climbing and not pouring:
        return _rec("borates", "Borates", "watch",
                    "pH is climbing %s a week, but only about %g fl oz of acid a month has "
                    "actually been logged -- either the climb is being handled cheaply or the "
                    "doses are not all being logged. Tap 'I did this' on the acid items for a "
                    "few weeks and this gets a real answer."
                    % (_fmt_rate("ph", ph["per_week"]), acid["floz_per_month"]),
                    "low", dose=cost, acid_per_month=acid["floz_per_month"])

    if pouring and not climbing:
        return _rec("borates", "Borates", "yes",
                    "pH is not drifting much, but that is because you are holding it there "
                    "with about %g fl oz of acid a month across %g occasions. Borates would "
                    "do that holding for you. The %g lb build is a one-off."
                    % (acid["floz_per_month"], acid["per_month_occasions"],
                       cost["lb"] if cost else 0),
                    "medium", dose=cost, acid_per_month=acid["floz_per_month"])

    return _rec("borates", "Borates", "not yet",
                "pH is steady (%s a week) and you have only used about %g fl oz of acid a "
                "month. Borates would work, but they are %g lb of boric acid to solve a "
                "problem you do not currently have. Worth revisiting if the acid starts "
                "adding up."
                % (_fmt_rate("ph", ph.get("per_week") or 0), acid["floz_per_month"],
                   cost["lb"] if cost else 0),
                "medium" if ph.get("enough") else "low", dose=cost)


def cya_case(readings, latest, cya, today=None):
    """CYA: is it going anywhere, and will it leave the band before you notice?"""
    lo, hi = poolcfg.band("cya_ppm")
    have = (latest or {}).get("cya_ppm")
    gallons = poolcfg.gallons()

    if have is None:
        return _rec("cya", "Stabiliser (CYA)", "unknown",
                    "CYA has not been measured. The ICO does not read it, so it needs a strip "
                    "test -- and on a saltwater pool it is the number that decides whether the "
                    "cell can keep up at all.", "none")

    dose = chem.cya_lb(have, (lo + hi) / 2.0 if lo and hi else (lo or 0) + 10, gallons)
    cost = {"lb": dose["lb"]} if dose else None

    if not cya.get("enough"):
        base = ("CYA is %g ppm against a %g-%g band. " % (have, lo, hi)) if lo and hi else ""
        if lo is not None and have < lo:
            return _rec("cya", "Stabiliser (CYA)", "yes",
                        base + "It is under the band now, which is enough to act on without a "
                        "trend -- without stabiliser the sun strips chlorine faster than the "
                        "cell can make it. " + cya.get("note", ""), "high", dose=cost)
        return _rec("cya", "Stabiliser (CYA)", "watch",
                    base + cya.get("note", ""), "none", dose=cost)

    pm = cya["per_month"]
    if pm is not None and pm < -1.0 and lo is not None:
        below = have < lo
        months = 0 if below else (have - lo) / abs(pm)
        # "drops under the floor within weeks" is the wrong thing to say about a
        # number that is already under it
        when = ("already under the %g floor" % lo) if below else (
            ("under the %g floor in about %.1f months" % (lo, months)) if months >= 0.5
            else ("under the %g floor within weeks" % lo))
        return _rec("cya", "Stabiliser (CYA)", "yes" if below or months < 2 else "watch",
                    "CYA is falling about %.0f ppm a month and sits at %g -- %s. Losing it "
                    "that fast usually means dilution: heavy backwashing, splash-out, rain "
                    "overflow or a leak. Worth knowing which, because topping it up without "
                    "finding the cause just means doing it again."
                    % (abs(pm), have, when),
                    "high" if cya.get("steady") else "medium", dose=cost,
                    months_to_floor=round(months, 1), already_below=below)

    if hi is not None and have > hi:
        return _rec("cya", "Stabiliser (CYA)", "no",
                    "CYA is %g ppm, over the %g ceiling. It only comes down by replacing "
                    "water, so the move is to stop adding any and let rain and backwashing "
                    "dilute it. On an ORP-controlled pool this matters more than usual -- the "
                    "probe only sees unbound chlorine." % (have, hi), "high")

    return _rec("cya", "Stabiliser (CYA)", "no",
                "CYA is %g ppm and holding (%s a month across %d readings). Nothing to add."
                % (have, _fmt_rate("cya_ppm", pm or 0), cya["n"]), "high")


def salt_case(readings, latest, salt):
    """Salt only leaves by dilution, so its slope is a dilution gauge."""
    have = (latest or {}).get("salt_ppm")
    if have is None or not salt.get("enough"):
        return None
    pm = salt["per_month"]
    if pm is None or pm > -40:
        return None
    return _rec("salt", "Water loss", "watch",
                "Salt is falling about %.0f ppm a month. Salt does not evaporate or burn off "
                "-- it only leaves with water -- so that is a direct measure of how much fresh "
                "water is going in, from backwashing, splash-out, rain overflow or a leak. It "
                "is also diluting your CYA and borates at the same rate."
                % abs(pm), "high" if salt.get("steady") else "medium")


def ta_case(latest, ta):
    """TA is what the acid is actually chewing through."""
    lo, hi = poolcfg.band("ta_ppm")
    have = (latest or {}).get("ta_ppm")
    if have is None:
        return _rec("ta", "Total alkalinity", "unknown",
                    "TA has not been measured. It is what acid demand actually scales with, so "
                    "without it every acid dose is computed against an assumed buffer.", "none")
    if hi is not None and have > hi:
        return _rec("ta", "Total alkalinity", "watch",
                    "TA is %g ppm, over the %g ceiling, and high TA is the engine driving the "
                    "pH climb. Bringing it down is acid plus aeration rather than an additive "
                    "-- and if you are considering borates, lowering TA first makes them work "
                    "better." % (have, hi), "high")
    return None


def analyze(today=None, days=WINDOW_DAYS):
    today = today or date.today()
    readings = store.readings(False)
    actions = store.actions()
    latest = store.latest_reading(confirmed_only=True)

    d = {k: drift(readings, k, today, days)
         for k in ("ph", "orp_mv", "salt_ppm", "cya_ppm", "ta_ppm", "water_temp_f")}
    acid = acid_demand(actions, today, 30)

    recs = [borate_case(readings, actions, latest, d["ph"], acid, today),
            cya_case(readings, latest, d["cya_ppm"], today),
            salt_case(readings, latest, d["salt_ppm"]),
            ta_case(latest, d["ta_ppm"])]
    recs = [r for r in recs if r]
    order = {"yes": 0, "watch": 1, "not yet": 2, "unknown": 3, "no": 4}
    recs.sort(key=lambda r: order.get(r["verdict"], 5))

    return {
        "today": today.isoformat(),
        "window_days": days,
        "drift": d,
        "acid_demand": acid,
        "recommendations": recs,
        "readings_used": len([r for r in readings if r.get("confirmed")]),
        "headline": recs[0]["because"][:160] if recs and recs[0]["verdict"] == "yes"
                    else "Nothing needs buying.",
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    a = analyze()
    if "--verbose" not in argv:
        for k in a["drift"]:
            a["drift"][k].pop("first", None)
            a["drift"][k].pop("last", None)
    print(json.dumps(a, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
