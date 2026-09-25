"""
run_cloud.py -- the orchestrator GitHub Actions runs (and I can run by hand).

  python engine/run_cloud.py full          # daily: pull, ingest, rebuild the checklist, push
  python engine/run_cloud.py interactive   # on a new drop: ingest, re-checklist if it moved
  python engine/run_cloud.py poll          # every few hours: pull from the ICO, rebuild if new
  python engine/run_cloud.py build         # just rebuild state + app from what's on disk

Claude runs through the CLI on my Max plan using the CLAUDE_CODE_OAUTH_TOKEN
secret, so reading a screenshot and writing the checklist cost nothing extra and
my PC can stay off. Every step is wrapped: one failing piece never takes the
whole run down, because a missing bit of wording should still leave a working
app on my phone with correct doses on it.

The interactive run does one thing worth understanding: if a drop MOVED the
water picture -- a screenshot got read, a reading got confirmed, I logged a dose
-- then this morning's checklist is now about a pool that has changed, so it is
rewritten on the spot instead of waiting for tomorrow. If nothing moved (a
skimmer log, a note), it just rebuilds the app and stays cheap.
"""
from __future__ import annotations
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_inbox    # noqa: E402
import checklist      # noqa: E402
import ondilo         # noqa: E402
import poolcfg        # noqa: E402
import pwa            # noqa: E402
import push as pushmod  # noqa: E402
import state as state_mod  # noqa: E402


def step(label, fn, *a, **kw):
    print("-- %s" % label)
    try:
        return fn(*a, **kw)
    except Exception:                      # keep the pipeline alive
        print("  ! %s failed:" % label)
        traceback.print_exc(limit=3)
        return None


def pull_ondilo():
    """Ask the ICO for its latest numbers, if that's turned on.

    Returns the reading id that landed, or None. Wrapped like every other step:
    the ICO being unreachable is a reason to fall back to typing a reading in,
    never a reason for the morning checklist not to exist.
    """
    if not (poolcfg.CONFIG.get("ondilo") or {}).get("enabled"):
        return None
    rec = ondilo.pull()
    return (rec or {}).get("id")


def stale_checklist():
    """Did the new data invalidate the checklist we're currently showing?

    Compares the saved basis -- date, which reading it was computed from, which
    items existed and what each dose was -- against what the engine would produce
    right now. Any difference means the list on my phone is about water that has
    already moved.
    """
    p = poolcfg.store_path("checklist.json")
    if not os.path.exists(p):
        return True, "no checklist yet"
    try:
        old = (json.load(open(p, encoding="utf-8")) or {}).get("basis") or {}
    except (json.JSONDecodeError, OSError):
        return True, "checklist unreadable"
    new = checklist.build().get("basis") or {}
    if old.get("date") != new.get("date"):
        return True, "checklist is from %s" % old.get("date")
    if old.get("reading_id") != new.get("reading_id"):
        return True, "a newer reading was confirmed"
    if old.get("items") != new.get("items"):
        return True, "the list of items changed"
    if old.get("doses") != new.get("doses"):
        return True, "a dose changed"
    return False, ""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    print("== pool pilot (%s) == config: %s" % (mode, poolcfg.CONFIG.get("_config_path")))

    if mode in ("full", "poll"):
        step("pull from the ICO", pull_ondilo)

    if mode in ("full", "interactive", "poll"):
        step("ingest phone drops", check_inbox.main)

    if mode == "full":
        step("daily checklist", checklist.main)
    elif mode in ("interactive", "poll"):
        stale, why = step("check the checklist", stale_checklist) or (True, "check failed")
        if stale:
            print("   the water picture moved: %s -- rewriting the checklist" % why)
            # poll.yml deliberately installs no Claude CLI: a poll only files
            # numbers and recomputes doses, both deterministic. Asking for the AI
            # wording here would just fail and fall back every three hours. The
            # daily run rewrites the sentences.
            step("rewrite checklist", checklist.main,
                 ["--offline"] if mode == "poll" else [])
        else:
            print("   checklist still matches the data")
            if mode == "poll":
                # nothing moved, so there is nothing to rebuild or commit --
                # a poll that finds no new measurement should cost nothing
                print("== done (no change) ==")
                return

    step("state", state_mod.main)
    step("build app", pwa.main)

    if mode == "full":
        step("push", pushmod.main)

    print("== done ==")


if __name__ == "__main__":
    main()
