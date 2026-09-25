"""
seed_demo.py -- fill the store with a plausible season so I can look around.

  py engine/seed_demo.py          # write demo data (refuses if there's real data)
  py engine/seed_demo.py --force  # overwrite whatever is there
  py engine/seed_demo.py --clear  # wipe the store back to empty

Everything it writes is invented. It exists so the app can be judged on a full
screen rather than an empty one, and so the checklist rules can be exercised
without waiting eight weeks for a real pool to drift.

It refuses to run over real data unless told twice, because "I just wanted to
see what it looked like" is a bad reason to lose a season of readings.
"""
from __future__ import annotations
import os
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import poolcfg  # noqa: E402
import store    # noqa: E402

FILES = (store.READINGS, store.ACTIONS, store.OPENING, "season.json",
         "checklist.json", "state.json")

# A season that drifts the way a saltwater pool actually drifts: pH climbing week
# on week, salt sagging as rain dilutes it, CYA burning off in the sun.
WEEKS = [
    # days_ago, ph,  orp, salt, temp, cya,  ta
    (56, 7.8,  665, 3180, 68, 46, 92),
    (49, 7.9,  650, 3120, 72, 43, 90),
    (42, 8.0,  641, 3060, 76, 40, 90),
    (35, 8.0,  628, 3010, 80, 37, 88),
    (28, 8.1,  615, 2960, 84, 34, 88),
    (21, 8.1,  602, 2900, 85, 31, 86),
    (14, 8.2,  588, 2860, 83, 28, 86),
    (7,  8.2,  571, 2810, 80, 26, 95),
    (2,  8.3,  558, 2760, 79, 24, 95),
]
DOINGS = [
    (54, "emptied_skimmer", None, ""), (51, "emptied_skimmer", None, ""),
    (48, "backwashed_filter", None, ""), (47, "emptied_skimmer", None, ""),
    (44, "brushed_pool", None, ""), (43, "emptied_skimmer", None, ""),
    (40, "added_acid", 24, "fl oz"), (39, "emptied_skimmer", None, ""),
    (34, "backwashed_filter", None, ""), (33, "emptied_skimmer", None, ""),
    (30, "added_salt", 40, "lb"), (29, "vacuumed_pool", None, ""),
    (26, "emptied_skimmer", None, ""), (22, "added_acid", 28, "fl oz"),
    (20, "backwashed_filter", None, ""), (19, "emptied_skimmer", None, ""),
    (15, "cell_output", 60, "%"), (12, "emptied_skimmer", None, ""),
    (9, "added_cya", 2, "lb"), (5, "emptied_skimmer", None, ""),
]
OPENING_DONE = [("physical_prep", 62), ("mechanical_start", 61), ("base_sanitization", 60)]


def clear():
    for f in FILES:
        p = poolcfg.store_path(f)
        if os.path.exists(p):
            os.remove(p)
    print("store cleared")


def has_real_data():
    return bool(store.readings() or store.actions())


def main():
    if "--clear" in sys.argv:
        return clear()
    if has_real_data() and "--force" not in sys.argv:
        print("there's already data in the store -- re-run with --force to overwrite it")
        return
    clear()

    today = date.today()
    for ago, ph, orp, salt, temp, cya, ta in WEEKS:
        at = datetime.combine(today - timedelta(days=ago), datetime.min.time()) \
            .replace(hour=9, minute=15)
        store.add_reading(at=at, source="ico", confirmed=True, ph=ph, orp_mv=orp,
                          salt_ppm=salt, water_temp_f=temp, cya_ppm=cya, ta_ppm=ta)
    for ago, what, amt, unit in DOINGS:
        at = datetime.combine(today - timedelta(days=ago), datetime.min.time()) \
            .replace(hour=17)
        store.add_action(what, amt, unit, at)
    for sid, ago in OPENING_DONE:
        store.complete_opening_step(sid, today - timedelta(days=ago))

    print("seeded %d readings, %d actions, %d opening steps"
          % (len(WEEKS), len(DOINGS), len(OPENING_DONE)))
    print("now run:  py engine/checklist.py --offline  &&  py engine/state.py  &&  py engine/pwa.py")


if __name__ == "__main__":
    main()
