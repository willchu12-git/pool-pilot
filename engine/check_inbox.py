"""
check_inbox.py -- pick up whatever my phone dropped and file it.

The PWA never edits the JSONL files directly (two devices editing one file is how
you lose data). It CREATES one small file per action in the private repo:

  inbox/2026-04-18T091500-reading.jpg     an ICO screenshot to read
  inbox/2026-04-18T091500-confirm.json    "yes, that's what it said" (+ corrections)
  inbox/2026-04-18T091500-action.json     "I added 40 lb of salt"
  inbox/2026-04-18T091500-opening.json    "step 3 done, on this date"

This scans the folder, reads any images with the vision pass, appends everything
to the real store, archives the request and leaves the original behind. Creating
new files never conflicts, so logging from the phone while the cron job runs is
safe.

Images are processed BEFORE json, on purpose: a screenshot and a "that's right"
confirmation can land in the same push, and the confirmation has nothing to
confirm until the image has been read.

Usage:  py engine/check_inbox.py
"""
from __future__ import annotations
import json
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import poolcfg  # noqa: E402
import store    # noqa: E402
import vision   # noqa: E402


def _archive_dir(base):
    d = os.path.join(base, "_archive")
    os.makedirs(d, exist_ok=True)
    return d


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _move(fp, arch, fn, tag=""):
    shutil.move(fp, os.path.join(arch, "%s_%s%s" % (_stamp(), tag, fn)))


def images():
    """inbox/*.jpg -> an unconfirmed reading, waiting for me to say yes."""
    base = poolcfg.path_of("inbox")
    arch = _archive_dir(base)
    files = vision.inbox_images()
    n = 0
    for fp in files:
        fn = os.path.basename(fp)
        try:
            vision.process(fp)
            n += 1
        except Exception as e:                       # a bad image never stops the run
            print("  ! %s failed to process (%s)" % (fn, e))
        _move(fp, arch, fn)
    print("images: %d read" % n)
    return n


def json_drops():
    """inbox/*.json -> readings.jsonl / actions.jsonl / opening_progress.json"""
    base = poolcfg.path_of("inbox")
    arch = _archive_dir(base)
    files = sorted(f for f in os.listdir(base)
                   if f.lower().endswith(".json") and os.path.isfile(os.path.join(base, f)))
    n = 0
    for fn in files:
        fp = os.path.join(base, fn)
        try:
            payload = json.load(open(fp, encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError) as e:
            print("  ! %s unreadable (%s) -- archiving as-is" % (fn, e))
            _move(fp, arch, fn, "BAD_")
            continue
        records = payload if isinstance(payload, list) else [payload]
        for rec in records:
            if not isinstance(rec, dict):
                continue
            try:
                kind, saved = store.ingest(rec)
            except ValueError as e:                  # e.g. a locked opening step
                print("  ! refused: %s" % e)
                continue
            print("  + %s %s" % (kind, saved.get("date") or saved.get("id") or ""))
            n += 1
        _move(fp, arch, fn)
    print("json: %d record(s)" % n)
    return n


def main():
    got = images()
    got += json_drops()
    return got


if __name__ == "__main__":
    main()
