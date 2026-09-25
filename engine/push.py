"""
push.py -- morning push to my iPhone via ntfy.sh (free, no account).

Sends today's headline plus whatever actually needs doing. Install the free
"ntfy" app, subscribe to the topic in config.json (or the NTFY_TOPIC secret), and
the daily cron lands on my phone whether or not the PC is on.

It stays quiet on a day with nothing to do. A maintenance app that pings you
every morning to say "no change" is an app you stop reading.

Usage:  py engine/push.py
        py engine/push.py --always    # push even when nothing's due
"""
from __future__ import annotations
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import poolcfg  # noqa: E402


def _read_json(name):
    p = poolcfg.store_path(name)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _trim(text, limit=420):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def main():
    push = poolcfg.CONFIG["push"]
    topic = (push.get("ntfy_topic") or "").strip()
    if not push.get("enabled") or not topic or topic.startswith("REPLACE-ME"):
        print("push disabled or topic not set -- skipping")
        return

    cl = _read_json("checklist.json")
    counts = cl.get("counts") or {}
    todo = counts.get("todo", 0)
    age = cl.get("reading_age_days")
    stale = age is not None and age >= (poolcfg.CONFIG["cadence_days"].get("reading") or 7)

    if not todo and not stale and "--always" not in sys.argv:
        print("nothing due -- staying quiet")
        return

    doses = [i for i in (cl.get("items") or []) if i.get("dose")]
    lines = ["• %s: %s" % (i["title"], i["dose"]["label"]) for i in doses[:4]]
    tasks = [i["title"] for i in (cl.get("items") or [])
             if not i.get("dose") and i.get("priority", 3) <= 2 and not i.get("superseded")]
    if tasks:
        lines.append("• " + ", ".join(tasks[:4]))

    title = "%s — %d to do" % (poolcfg.title(), todo) if todo else poolcfg.title()
    body = _trim(cl.get("headline") or "Today's checklist is ready.")
    if lines:
        body += "\n\n" + "\n".join(lines)
    if stale:
        body += "\n\nLast reading is %d days old — worth a fresh test." % age
    body += "\n\nPump running before anything goes in."

    url = "%s/%s" % (push["ntfy_server"].rstrip("/"), topic)
    cmd = ["curl", "-s", "-S", "-H", "Title: %s" % title,
           "-H", "Tags: %s" % ("droplet" if doses else "sunny"),
           "-H", "Markdown: yes", "-d", body, url]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
        print("pushed to %s" % url)
    except subprocess.CalledProcessError as e:
        print("push failed: %s" % (e.stderr or e))
    except FileNotFoundError:
        print("curl not found on PATH -- skipping push")
    except subprocess.TimeoutExpired:
        print("push timed out -- skipping")


if __name__ == "__main__":
    main()
