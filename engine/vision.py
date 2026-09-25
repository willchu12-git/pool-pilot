"""
vision.py -- read the numbers off an ICO screenshot with Claude on my Max plan.

  py engine/vision.py inbox/2026-04-18T091500-reading.jpg
  py engine/vision.py                 # every unprocessed image in inbox/

The phone drops a photo into inbox/ via the GitHub API. This shells out to the
Claude CLI -- `claude -p --allowedTools Read`, pointed at the checked-out file --
and asks for strict JSON. Same _claude()/_parse() shape as checklist.py.

Two things make this safe enough to build a dose on:

  1. It writes the reading UNCONFIRMED. The app shows it as "we think we read
     pH 8.1, ORP 564, salt 2,791 -- look right?" with every field editable, and
     the checklist will not compute a dose from it until I've said yes. A misread
     decimal point on pH is the difference between a cup of acid and a quart.

  2. When it can't read the image it says so and files an unconfirmed reading with
     every value null and a note -- so the picture shows up in the app as "couldn't
     read this, enter it manually" instead of vanishing silently.

The original image is archived alongside the reading either way, so a bad OCR can
always be checked against the picture it came from.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import poolcfg  # noqa: E402
import store    # noqa: E402

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".webp")
KEYS = ("ph", "orp_mv", "salt_ppm", "water_temp_f", "cya_ppm", "ta_ppm", "ch_ppm",
        "fc_ppm", "borates_ppm")
IMAGE_DIR = "data/images"


def _claude(prompt):
    """Claude with file-reading turned on -- it has to actually open the screenshot."""
    coach = poolcfg.CONFIG["coach"]
    cmd = "claude -p"
    if coach.get("claude_model"):
        cmd += " --model " + coach["claude_model"]
    flags = (coach.get("vision_flags") or "").strip()
    if flags:
        cmd += " " + flags
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
    """Pull the JSON object out of whatever came back; None if unusable.

    A response with no readable measurement in it is treated as unusable even if
    it parsed, because "valid JSON full of nulls with ok:true" is the failure mode
    that would otherwise look like a successful read of a blank pool.
    """
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
    if not isinstance(obj, dict):
        return None
    if obj.get("ok") is False:
        return obj
    if not any(obj.get(k) is not None for k in KEYS):
        return None
    return obj


def _archive_image(path, stamp):
    """Keep the picture next to the reading it produced, so a bad OCR is checkable."""
    d = os.path.join(ROOT, IMAGE_DIR)
    os.makedirs(d, exist_ok=True)
    ext = os.path.splitext(path)[1].lower() or ".jpg"
    rel = "%s/%s%s" % (IMAGE_DIR, stamp.replace(":", "-"), ext)
    try:
        shutil.copy2(path, os.path.join(ROOT, rel))
        return rel
    except OSError as e:
        print("  ! couldn't archive the image (%s)" % e)
        return ""


def read_image(path):
    """Ask Claude what's on this screenshot. Returns the parsed dict, or None."""
    tpl = open(os.path.join(HERE, "vision_prompt.md"), encoding="utf-8").read()
    prompt = tpl.replace("{{IMAGE}}", os.path.abspath(path))
    open(poolcfg.store_path("_vision_prompt.txt"), "w", encoding="utf-8").write(prompt)
    return _parse(_claude(prompt))


def _stamp_from_name(fn):
    """inbox/2026-04-18T09-15-00-reading.jpg -> 2026-04-18T09:15:00, else now."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T_ ](\d{2})-(\d{2})-(\d{2})", os.path.basename(fn))
    if m:
        return "%sT%s:%s:%s" % m.groups()
    return datetime.now().isoformat(timespec="seconds")


def process(path):
    """One image -> one unconfirmed reading on disk. Never raises, never skips silently."""
    stamp = _stamp_from_name(path)
    kept = _archive_image(path, stamp)
    print("  * reading %s" % os.path.basename(path))
    got = read_image(path)

    if not got or got.get("ok") is False:
        note = (got or {}).get("notes") or "Couldn't read this image -- enter the numbers by hand."
        rec = store.add_reading(at=stamp, source="ico_failed", confirmed=False,
                                image=kept, note=note[:200], rid=stamp)
        print("  ! unreadable: %s" % note[:120])
        return rec

    at = got.get("taken_at") or stamp
    note = (got.get("notes") or "").strip()
    if got.get("unreadable"):
        note = (note + " Couldn't read: %s." % ", ".join(
            str(u) for u in got["unreadable"][:6])).strip()
    rec = store.add_reading(at=at, source="ico", confirmed=False, image=kept,
                            note=note[:200], rid=stamp,
                            **{k: got.get(k) for k in KEYS})
    shown = ", ".join("%s %s" % (k.replace("_ppm", "").replace("_mv", "").replace("_f", ""),
                                 rec[k]) for k in KEYS if rec.get(k) is not None)
    print("  + read: %s" % (shown or "nothing usable"))
    if rec.get("dropped"):
        print("  ! out-of-range, dropped: %s" % ", ".join(rec["dropped"]))
    return rec


def is_reading_image(fn):
    """Only `*-reading.*` goes to the vision pass.

    The app also uploads photos that are not water tests -- the equipment pad at
    closing, for one. Running OCR over a picture of a pump and filing whatever
    numbers it hallucinates as a reading would be a genuinely bad outcome, so the
    filename decides, and anything unrecognised is simply kept as a picture.
    """
    return "-reading." in os.path.basename(fn).lower()


def inbox_images(readings_only=True):
    """Images sitting in inbox/, oldest name first."""
    base = poolcfg.path_of("inbox")
    out = [os.path.join(base, f) for f in sorted(os.listdir(base))
           if f.lower().endswith(IMAGE_EXT) and os.path.isfile(os.path.join(base, f))]
    return [f for f in out if is_reading_image(f)] if readings_only else out


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    paths = [a for a in argv if not a.startswith("-")] or inbox_images()
    if not paths:
        print("no images to read")
        return []
    out = []
    for p in paths:
        if not os.path.exists(p):
            print("  ! %s not found" % p)
            continue
        out.append(process(p))
    return out


if __name__ == "__main__":
    main()
