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
    ondilo.py               pulls readings straight off the ICO over Ondilo's API
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

**New to this? [SETUP.md](SETUP.md) is the same thing spelled out click by click.**
What follows is the short version.

1. **Create the repo** — `pool-pilot`, public, and push this directory to it.

2. **Settings → Pages** — Source: *Deploy from a branch*, Branch: `main`, folder:
   `/ (root)`. Give it a minute; your URL is
   `https://<you>.github.io/pool-pilot/`.

3. **Settings → Secrets and variables → Actions → New repository secret**
   - `CLAUDE_CODE_OAUTH_TOKEN` — run `claude setup-token` locally and paste the
     result. This is what rides your Max plan instead of a pay-per-token API key.
     Without it everything still works except screenshot reading and the AI
     wording.
   - `ONDILO_REFRESH_TOKEN` (optional, and the one worth having) — see
     *Connecting the ICO directly* below. With it, readings arrive on their
     own and there is nothing to screenshot.
   - `NTFY_TOPIC` (optional) — an unguessable topic for phone pushes.

4. **Check `config.json`.** `pool.gallons` drives every dose; if it is wrong,
   every dose is wrong by the same ratio. `targets.why_these` explains each band.

5. **On the phone** — open the Pages URL, Share → Add to Home Screen. It will
   already be showing your data. To *log* anything, go to Settings and paste a
   fine-grained PAT with **Contents: read & write** on this one repo. It is
   stored in that browser only and never leaves the phone except in requests to
   github.com.

## Connecting the ICO directly (no screenshots)

Ondilo publishes a [Customer API](https://interop.ondilo.com/docs/api/customer/v1). With it
switched on, `.github/workflows/poll.yml` asks the ICO for its own numbers every three hours and
files them — nothing to photograph.

The reason this works unattended: **Ondilo refresh tokens are non-expiring and are not
rotated.** Refreshing returns a new access token and no new refresh token, so you authorize once
and the secret never needs touching again.

```bash
py engine/ondilo.py login
```

It prints a URL, you sign in, and you paste the redirected URL back (it won't load — the part
that matters is the `?code=` in the address bar). Out comes a refresh token. Put it in the
repository **secret** `ONDILO_REFRESH_TOKEN` — never in `config.json`, this repo is public —
and set `ondilo.enabled` to `true`. If the account has more than one pool, `login` lists the ids
for `ondilo.pool_id`.

```bash
py engine/ondilo.py pools          # what the account can see
py engine/ondilo.py raw            # exactly what the API returns
py engine/ondilo.py pull --dry-run # map it to a reading without writing
```

How a pulled reading differs from a screenshotted one:

- **It is written confirmed.** The confirm card exists because OCR misreads decimal points; an
  API integer has no such failure mode. The sanity bounds still apply, and a measure the ICO
  itself flags `is_valid: false` is dropped with its `exclusion_reason` kept as a note.
- **Its id is the ICO's own `value_time`**, not the time we fetched. Polling eight times a day
  over one hourly measurement collapses onto a single record instead of piling up eight.
- **Temperature comes back in the account's preferred unit**, so `/user/units` is consulted
  rather than guessed at — 40 is a plausible pool in either scale.

The ICO measures temperature, pH, ORP, salt and TDS. It does **not** measure CYA, total
alkalinity, free chlorine or borates — those stay strip tests you type in, and with the pull
switched on the "New reading" card says so.

Rate limit is 30 requests/hour per user; a poll makes three, every three hours.

## Daily loop

With the ICO connected there mostly isn't one — readings arrive on their own and the checklist
is current when you look at it. What's left:

- Do the items. Tap **I did this** on each — that's what keeps the supersede rule honest.
- Type in a strip test when the checklist asks for CYA, alkalinity or borates.
- Anything off-script goes through **Log something else** on the Today tab.

Without the ICO connected: drop it in, screenshot the app, **Upload screenshot**, and about a
minute later the confirm card appears. Tap **Looks right**, or fix a number first.

## Running it locally

```bash
py engine/serve.py          # http://127.0.0.1:8778, writes and rebuilds immediately
py engine/checklist.py      # rebuild the checklist (add --offline to skip Claude)
py engine/checklist.py --offline
py engine/vision.py path/to/screenshot.jpg
py engine/ondilo.py pull    # fetch from the ICO right now
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
  accept="image/*" capture="environment">`. Mostly moot once the ICO pull is on.
- **The ICO API can be flaky.** `/lastmeasures` has a history of returning nothing; when it
  does, the poll logs it and leaves the previous reading alone rather than filing a blank
  one. The screenshot path stays as a fallback.
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
