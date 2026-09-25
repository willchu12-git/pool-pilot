"""
ask.py -- ask a question, get an answer from this pool's actual data.

  py engine/ask.py "why is my ORP so low when chlorine looks fine?"
  py engine/ask.py --last 5          # recent Q&A

The phone drops a question into ask/<timestamp>.txt, the workflow picks it up,
Claude answers it with the whole state bundle in context, and the answer lands in
data/store/qa.jsonl where the app reads it back.

The prompt (engine/ask_prompt.md) carries one rule that matters more than the
rest: the answer may QUOTE a dose the engine already computed, and may never
compute one. Everything else in this app routes pounds and fluid ounces through
chem.py, where they are capped and paired with a retest -- an assistant doing
mental arithmetic in a sentence would be a way around all of that, and it would
be the least visible way.

If Claude is unreachable the question is kept, not lost, and answered on the next
run. A missing answer is a delay; a wrong one about acid is not.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import poolcfg   # noqa: E402
import season    # noqa: E402
import state as state_mod  # noqa: E402
import trends    # noqa: E402

QA = "qa.jsonl"
MAX_Q = 600


def _now():
    return datetime.now().isoformat(timespec="seconds")


def context(st=None):
    """Trim the state to what actually helps answer a question, and keeps it cheap.

    Deliberately includes `reference` (what one unit of each chemical does to this
    pool) so the model has real per-volume numbers to quote instead of a reason to
    invent any.
    """
    st = st or state_mod.build()
    cl = st.get("checklist") or {}
    try:
        tr = trends.analyze()
    except Exception as e:                       # never let analysis break an answer
        tr = {"error": str(e)}
    return {
        "today": st.get("today"),
        "pool": st.get("pool"),
        "equipment": st.get("equipment"),
        "targets": st.get("targets"),
        "season": {k: st.get("season", {}).get(k)
                   for k in ("state", "closed", "closed_at", "days_closed",
                             "closing_water_words")},
        "panel": st.get("panel"),
        "latest_reading": st.get("latest_reading"),
        "reading_age_days": st.get("reading_age_days"),
        "checklist": {"headline": cl.get("headline"), "summary": cl.get("summary"),
                      "items": [{k: i.get(k) for k in
                                 ("title", "because", "dose", "measured", "target",
                                  "wait_hours", "superseded")}
                                for i in (cl.get("items") or [])]},
        "chips": st.get("chips"),
        "recent_actions": [{k: a.get(k) for k in ("date", "label", "amount", "unit", "note")}
                           for a in (st.get("timeline") or []) if a.get("type") == "action"][:15],
        "trends": {"drift": {k: {kk: v.get(kk) for kk in
                                 ("n", "span_days", "per_week", "per_month", "enough", "note")}
                             for k, v in (tr.get("drift") or {}).items()},
                   "acid_demand": tr.get("acid_demand"),
                   "recommendations": tr.get("recommendations")},
        "reference": st.get("reference"),
        "opening_brief": st.get("opening_brief"),
    }


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


def _clean(text):
    """Strip the wrappers a model sometimes adds around prose it was asked for plain."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def answer(question, st=None):
    """Answer one question. Returns '' if Claude was unreachable, never a guess."""
    q = (question or "").strip()[:MAX_Q]
    if not q:
        return ""
    tpl = open(os.path.join(HERE, "ask_prompt.md"), encoding="utf-8").read()
    prompt = (tpl.replace("{{SAFETY}}", poolcfg.safety_block())
                 .replace("{{STATE}}", json.dumps(context(st), indent=2, ensure_ascii=False,
                                                  default=str))
                 .replace("{{QUESTION}}", q))
    open(poolcfg.store_path("_ask_prompt.txt"), "w", encoding="utf-8").write(prompt)
    return _clean(_claude(prompt))


def qa_log(n=20):
    p = poolcfg.store_path(QA)
    rows = []
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-n:]


def record(question, ans, asked_at=None):
    rec = {"question": (question or "").strip()[:MAX_Q], "answer": ans,
           "asked_at": asked_at or _now(), "answered_at": _now(),
           "closed": season.is_closed()}
    with open(poolcfg.store_path(QA), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--last" in argv:
        i = argv.index("--last")
        n = int(argv[i + 1]) if i + 1 < len(argv) and argv[i + 1].isdigit() else 5
        for r in qa_log(n):
            print("\nQ: %s\nA: %s" % (r["question"], r["answer"]))
        return
    q = " ".join(a for a in argv if not a.startswith("-")).strip()
    if not q:
        print(__doc__.strip())
        return
    a = answer(q)
    if not a:
        print("! no answer came back -- the question is kept and will be retried")
        return
    record(q, a)
    print("\n" + a + "\n")


if __name__ == "__main__":
    main()
