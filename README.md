# Pool Pilot

A pool-maintenance PWA for one pool and one person. Python engine, JSONL in a git repo, a
self-contained web app on the iPhone home screen, AI reasoning through the Claude CLI on a Max
plan, and fully autonomous runs on GitHub Actions — so the PC can be off.

Same architecture as CycleSync: git is the database, GitHub Actions is the server, and the
phone is a static page that reads the repo over HTTPS and writes back through the GitHub API.

## The one thing to understand

**Doses are arithmetic. The AI only writes sentences.**

`engine/chem.py` and `engine/checklist.py` decide what needs doing and compute every pound and
fluid ounce, deterministically, with no language model anywhere near them. Claude is handed the
finished list and asked for a `why` and a `how` per item. `_apply()` copies only those two
strings onto items that already exist — it cannot add an item, remove one, or change a number.

If Claude is unreachable, a built-in writer produces the same list with plainer wording. The
doses are byte-identical either way.

Two more rules that live in code, not in a prompt:

- **A reading is unconfirmed until you say so.** The vision pass writes what it thinks it read
  and the app shows it as *"we read pH 8.1, ORP 564, salt 2,791 — look right?"* with every field
  editable. Nothing is dosed off an unconfirmed reading. A misread decimal point on pH is the
  difference between a cup of acid and a quart.
- **One reading, one dose.** If you logged "added salt" *after* the reading a salt dose was
  computed from, that item stops saying "add salt" and starts saying "retest first". Dosing
  twice against one measurement is how a pool gets overshot.

## Layout

One **public** repo. GitHub Pages serves it, which means the app and the data sit
on the same origin — so the phone reads with no credential at all, and a token is
only needed to write.

```
pool-pilot/                 PUBLIC — Pages serves this whole repo
  index.html                redirect, so the home-screen URL is just /pool-pilot/
  config.json               volume, targets, cadences, chemicals, safety rules, opening steps
  engine/
    poolcfg.py              loads config.json; renders safety_rules into every prompt
    chem.py                 pure dose arithmetic — the only place a number is produced
    store.py                append-only JSONL: readings, actions, opening progress
    vision.py               `claude -p --allowedTools Read` on a screenshot -> strict JSON
    vision_prompt.md
    checklist.py            the rules; builds the list, then asks Claude for the wording
    checklist_prompt.md
    state.py                one JSON bundle the PWA renders
    pwa.py                  builds app/ (shell) and out/ (local, data inlined)
    check_inbox.py          files whatever the phone dropped
    run_cloud.py            the orchestrator Actions runs
    push.py                 ntfy.sh push, quiet on a day with nothing to do
    serve.py                local server for when you are at the PC
    seed_demo.py            a plausible season, for looking around
  data/store/*.jsonl        the data — read by the phone over plain HTTPS, no token
  data/images/              archived screenshots, next to the readings they produced
  inbox/                    what the phone drops; archived after ingest
  app/                      the built app
  scripts/commit_push.sh    race-safe commit + push
  .github/workflows/        daily.yml, interactive.yml
```

Everything in this repo is public, including your readings. They are pH numbers
and "backwashed the filter" — but it is a public record, so decide that
deliberately. To go private instead: blank `app.public_data_url` in config.json,
make the repo private, and publish `app/` to a separate public repo by hand. The
app falls back to reading over the API with the token, which is the only thing
that changes.

## Setup

1. **Create the repo** — `pool-pilot`, public, and push this directory to it.

2. **Settings → Pages** — Source: *Deploy from a branch*, Branch: `main`, folder:
   `/ (root)`. Give it a minute; your URL is
   `https://<you>.github.io/pool-pilot/`.

3. **Settings → Secrets and variables → Actions → New repository secret**
   - `CLAUDE_CODE_OAUTH_TOKEN` — run `claude setup-token` locally and paste the
     result. This is what rides your Max plan instead of a pay-per-token API key.
     Without it everything still works except screenshot reading and the AI
     wording.
   - `NTFY_TOPIC` (optional) — an unguessable topic for phone pushes.

4. **Check `config.json`.** `pool.gallons` drives every dose; if it is wrong,
   every dose is wrong by the same ratio. `targets.why_these` explains each band.

5. **On the phone** — open the Pages URL, Share → Add to Home Screen. It will
   already be showing your data. To *log* anything, go to Settings and paste a
   fine-grained PAT with **Contents: read & write** on this one repo. It is
   stored in that browser only and never leaves the phone except in requests to
   github.com.

## Daily loop

- Drop the ICO in, screenshot the app, hit **Upload screenshot**.
- ~1 minute later the interactive workflow has read it and the app shows the confirm card.
- Tap **Looks right** (or fix a number first). The checklist rebuilds with real doses.
- Do the items. Tap **I did this** on each — that's what keeps the supersede rule honest.

Anything off-script goes through **Log something else** on the Today tab.

## Running it locally

```bash
py engine/serve.py          # http://127.0.0.1:8778, writes and rebuilds immediately
py engine/checklist.py      # rebuild the checklist (add --offline to skip Claude)
py engine/checklist.py --offline
py engine/vision.py path/to/screenshot.jpg
py engine/chem.py 18000     # what one unit of each chemical does to this pool
py engine/store.py show
```

`engine/store.py` also takes direct entries:

```bash
py engine/store.py reading --ph 8.1 --orp 564 --salt 2791 --temp 78
py engine/store.py did added_salt --amount 40 --unit lb
py engine/store.py opening physical_prep --on today
```

## Known limits

- **No true push notifications.** ntfy.sh only, same as CycleSync. iOS Safari PWAs don't get
  real background push here.
- **Photo capture is a file picker**, not a native camera integration — `<input type="file"
  accept="image/*" capture="environment">`. Good enough for screenshotting another app.
- **The dose formulas are estimates for typical water.** They're the standard per-10,000-gallon
  constants, they're capped, and every dose is paired with "circulate, then retest". Trust the
  water over the app.
- **Acid demand depends on TA**, which the ICO doesn't measure. With TA unknown the engine
  assumes a mid-band buffer and says so on the item. Test TA occasionally and the acid doses
  get sharper.
- **The JS mirror never computes a dose.** The phone recomputes panel status, the days-since
  chips and supersede marking so taps land instantly. It does not duplicate the rules, and it
  does not duplicate the arithmetic — when a local change invalidates a dose it blanks it and
  waits for the cloud rather than guessing. Two copies of a safety rule is one too many, and an
  unused copy of a formula is the one nobody notices has rotted.

## Safety

`config.json → safety_rules` is the single source. `poolcfg.safety_block()` renders it into
every AI prompt, `checklist._apply_gap()` enforces the shock/acid separation in code before any
model sees the list, and the app shows the rules at the bottom of the Today tab.

This is a vinyl-liner pool: nothing dry goes in without being predissolved or socked.

Chemicals are handled at your own risk. This app is a notebook that does arithmetic, not a
pool professional.
