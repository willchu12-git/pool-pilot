"""
chem.py -- the dose arithmetic. Pure functions, no I/O, no AI.

Everything here is deterministic and testable, and it is the ONLY place a number
of pounds or fluid ounces is ever produced. The AI never computes a dose; it only
gets handed one and asked to explain it in plain English. That split is the whole
safety model: if the language model has a bad day, the worst that happens is a
clumsy sentence, not a gallon of acid.

The constants are the standard pool-chemistry ones, all normalized to
"per 10,000 gallons", and every one of them is an ESTIMATE for typical water.
Real pools have a mind of their own, which is why every dose this module returns
is deliberately capped and paired with "add this much, circulate, then retest".

  water weight            8.345 lb per gallon
  1 lb of salt / CYA      +12 ppm in 10,000 gal
  1 gal of 12.5% chlorine +10.6 ppm FC in 10,000 gal   (PoolMath's figure)
  muriatic acid demand    ~0.86 fl oz of 31.45% per point of TA per pH unit
                          per 10,000 gal -- linear only across a narrow band,
                          which is the only band we ever use it in

Usage:  py engine/chem.py 18000
"""
from __future__ import annotations
import sys

LB_PER_GAL_WATER = 8.345
PPM_PER_LB_PER_10K = 12.0            # salt, CYA: same mass/volume arithmetic
FC_PPM_PER_GAL_12_5_PER_10K = 10.6   # 12.5% sodium hypochlorite
ACID_FLOZ_PER_TA_PER_PH_PER_10K = 0.86   # 31.45% muriatic
BORON_FRACTION_OF_BORATE_PPM = 1.0   # test kits read borate as ppm boron

DEFAULT_TA = 80                      # when TA is unknown, assume mid-band and say so

# Never suggest more than this in one go, regardless of what the arithmetic says.
# A big correction is always split across days with a retest in between, because
# the second half of a large dose is being added to water nobody has measured yet.
# ~0.45 of a pH unit at TA 80. Past that you are pouring against water you
# measured an hour ago and cannot see any more, so a big correction is split
# across days with a retest in between.
MAX_ACID_FLOZ_PER_10K = 31.0
MAX_SALT_LB_PER_10K = 50.0
MAX_CYA_LB_PER_10K = 2.0             # CYA only comes out by draining -- undershoot
MAX_CHLORINE_GAL_PER_10K = 2.5


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def scale(gallons):
    """How many '10,000 gallon units' this pool is."""
    g = _f(gallons, 0) or 0
    return (g / 10000.0) if g > 0 else 1.8


def _round_to(x, step, down=False):
    """Round to a pourable increment. `down` when the value is already at a cap --
    rounding a limit UP would quietly hand back more than the cap allows."""
    n = (x / step)
    n = int(n) if down else round(n)
    return round(n * step, 2)


def _capped(want, cap, gallons):
    """Return (dose, capped) -- the dose to actually give, and whether we clipped it."""
    limit = cap * scale(gallons)
    return (round(limit, 1), True) if want > limit else (round(want, 1), False)


# ------------------------------------------------------------------- raisers

def salt_lb(current_ppm, target_ppm, gallons):
    """Pounds of pool salt to raise salt from current to target. None if not needed."""
    cur, tgt = _f(current_ppm), _f(target_ppm)
    if cur is None or tgt is None or tgt <= cur:
        return None
    lb = (tgt - cur) * scale(gallons) / PPM_PER_LB_PER_10K
    dose, capped = _capped(lb, MAX_SALT_LB_PER_10K, gallons)
    return {"lb": _round_to(dose, 0.5, capped), "want_lb": round(lb, 1), "capped": capped,
            "raises_ppm": round(tgt - cur, 0)}


def salt_bags(lb, bag_lb=40):
    """Whole and part bags, because salt is bought in bags, not pounds."""
    b = _f(bag_lb, 40) or 40
    n = (_f(lb, 0) or 0) / b
    return {"bags": round(n, 2), "whole": int(n), "bag_lb": b}


def cya_lb(current_ppm, target_ppm, gallons):
    """Pounds of stabilizer. Deliberately conservative: CYA does not come back down
    without draining water, so undershooting costs a week and overshooting costs a
    partial drain."""
    cur, tgt = _f(current_ppm), _f(target_ppm)
    if cur is None or tgt is None or tgt <= cur:
        return None
    lb = (tgt - cur) * scale(gallons) / PPM_PER_LB_PER_10K
    dose, capped = _capped(lb, MAX_CYA_LB_PER_10K, gallons)
    return {"lb": _round_to(dose, 0.25, capped), "want_lb": round(lb, 1), "capped": capped,
            "raises_ppm": round(tgt - cur, 0)}


def borate_lb(current_ppm, target_ppm, gallons, boron_pct=17.5):
    """Pounds of boric acid to reach the borate target.

    Borate is measured as ppm boron, and boric acid is only ~17.5% boron by mass,
    so the pound figure is always much larger than people expect -- roughly
    2.4 lb per 1,000 gallons to reach 50 ppm from zero. Not capped, because
    borates go in as a one-time buffer build in batches rather than a correction.
    """
    cur, tgt = _f(current_ppm, 0) or 0, _f(target_ppm)
    pct = _f(boron_pct, 17.5) or 17.5
    if tgt is None or tgt <= cur:
        return None
    boron_lb = (tgt - cur) * (_f(gallons, 18000) or 18000) * LB_PER_GAL_WATER / 1e6
    lb = boron_lb / (pct / 100.0)
    return {"lb": round(lb, 1), "want_lb": round(lb, 1), "capped": False,
            "raises_ppm": round(tgt - cur, 0),
            "batches": max(1, int(round(lb / 10.0)))}   # ~10 lb per bucket, predissolved


def chlorine_gal(fc_delta_ppm, gallons, strength_pct=12.5):
    """Gallons of liquid chlorine to raise free chlorine by fc_delta_ppm."""
    d = _f(fc_delta_ppm)
    s = _f(strength_pct, 12.5) or 12.5
    if d is None or d <= 0:
        return None
    ppm_per_gal = FC_PPM_PER_GAL_12_5_PER_10K * (s / 12.5) / scale(gallons)
    gal = d / ppm_per_gal
    dose, capped = _capped(gal, MAX_CHLORINE_GAL_PER_10K, gallons)
    dose = _round_to(dose, 0.25, capped)
    # What the ROUNDED dose actually delivers, which is not what was asked for.
    # Quoting the request rather than the result is how an instruction ends up
    # promising 2 ppm while handing over a jug that gives 1.5.
    return {"gal": dose, "want_gal": round(gal, 2), "capped": capped,
            "raises_ppm": round(d, 1),
            "actual_ppm": round(dose * ppm_per_gal, 1), "strength_pct": s}


# -------------------------------------------------------------------- loweres

def acid_floz(ph_now, ph_target, gallons, ta_ppm=None, strength_pct=31.45):
    """Fluid ounces of muriatic acid to bring pH down to target.

    Acid demand is driven by TOTAL ALKALINITY, not by pH -- TA is the buffer the
    acid actually has to chew through. With TA unknown we assume the mid-band
    DEFAULT_TA and say so in the result, because a dose computed against a
    guessed buffer is a starting point to retest from, not an answer.
    """
    now, tgt = _f(ph_now), _f(ph_target)
    s = _f(strength_pct, 31.45) or 31.45
    if now is None or tgt is None or now <= tgt:
        return None
    ta = _f(ta_ppm)
    assumed = ta is None
    ta = DEFAULT_TA if assumed else ta
    floz = ACID_FLOZ_PER_TA_PER_PH_PER_10K * ta * (now - tgt) * scale(gallons) * (31.45 / s)
    if floz < 1.0:
        return None          # nothing worth pouring; a "0 fl oz" instruction is noise
    dose, capped = _capped(floz, MAX_ACID_FLOZ_PER_10K, gallons)
    dose = _round_to(dose, 1, capped)
    return {"floz": dose, "want_floz": round(floz, 1), "capped": capped,
            "cups": round(dose / 8.0, 2), "quarts": round(dose / 32.0, 2),
            "ta_used": ta, "ta_assumed": assumed, "drops_ph": round(now - tgt, 2),
            "strength_pct": s}


def ph_after_acid(ph_now, floz, gallons, ta_ppm=None, strength_pct=31.45):
    """The pH this much acid is expected to land on -- used only to sanity-check
    that a capped dose isn't about to overshoot the bottom of the target band."""
    now = _f(ph_now)
    oz = _f(floz, 0) or 0
    if now is None:
        return None
    ta = _f(ta_ppm) or DEFAULT_TA
    s = _f(strength_pct, 31.45) or 31.45
    drop = oz / (ACID_FLOZ_PER_TA_PER_PH_PER_10K * ta * scale(gallons) * (31.45 / s))
    return round(now - drop, 2)


# ------------------------------------------------------------------ salt cell

def cell_nudge(current_pct, direction, step_pct=10):
    """One nudge of the salt cell, clamped to 0-100.

    Deliberately ONE step. The cell's effect on ORP shows up over a day or two, so
    stacking changes before the water has answered is how you end up chasing it.
    """
    cur = _f(current_pct)
    step = _f(step_pct, 10) or 10
    if cur is None:
        return {"to_pct": None, "step_pct": step, "direction": direction, "known": False}
    to = max(0.0, min(100.0, cur + (step if direction == "up" else -step)))
    return {"from_pct": round(cur), "to_pct": round(to), "step_pct": step,
            "direction": direction, "known": True, "at_limit": to in (0.0, 100.0)}


# ------------------------------------------------------------------- reporting

def summary(gallons, boron_pct=17.5):
    """What one unit of each chemical does to THIS pool -- the numbers worth knowing
    by heart, shown in the app so the doses never feel like magic."""
    sc = scale(gallons)
    return {
        "gallons": _f(gallons, 18000),
        "salt_lb_per_100ppm": round(100 * sc / PPM_PER_LB_PER_10K, 1),
        "cya_lb_per_10ppm": round(10 * sc / PPM_PER_LB_PER_10K, 2),
        "acid_floz_per_0_1ph_at_ta80": round(
            ACID_FLOZ_PER_TA_PER_PH_PER_10K * DEFAULT_TA * 0.1 * sc, 1),
        "chlorine_gal_per_1ppm": round(sc / FC_PPM_PER_GAL_12_5_PER_10K, 2),
        "boric_acid_lb_per_10ppm": round(10 * (_f(gallons, 18000) or 18000)
                                         * LB_PER_GAL_WATER / 1e6
                                         / ((_f(boron_pct, 17.5) or 17.5) / 100.0), 1),
    }


if __name__ == "__main__":
    import json
    g = float(sys.argv[1]) if len(sys.argv) > 1 else 18000
    print(json.dumps({
        "summary": summary(g),
        "example_salt_2791_to_3000": salt_lb(2791, 3000, g),
        "example_acid_8_1_to_8_0": acid_floz(8.1, 8.0, g),
        "example_cya_20_to_40": cya_lb(20, 40, g),
        "example_borate_0_to_50": borate_lb(0, 50, g),
        "example_shock_5ppm": chlorine_gal(5, g),
    }, indent=2))
