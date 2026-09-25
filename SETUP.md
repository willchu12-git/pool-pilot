# Setting up Pool Pilot, start to finish

No prior knowledge assumed. About 30 minutes, once.

There are three parts. Each one ends with a way to check it worked before you move on.

| Part | What you get | Time |
|---|---|---|
| 1 | The app is online at a web address | ~15 min |
| 2 | It reads your ICO by itself and writes in plain English | ~10 min |
| 3 | It's an icon on your phone's home screen | ~5 min |

**A few words you'll see:**

- **Repo** (repository) — a folder that lives on GitHub. All your pool stuff goes in one.
- **Secret** — a password-ish string you give GitHub to hold. GitHub can use it but nobody can
  read it back out, not even you. This is where the sensitive things go.
- **Token** — a long random password that a program uses instead of you typing a password.
- **Workflow** — a little program GitHub runs for you on a schedule, on GitHub's computers.
  This is what lets your PC be off.

---

# Part 1 — Get it online

## Step 1. Find your GitHub username

Go to [github.com](https://github.com) and sign in. Click your picture, top right. The name at
the top of that menu is your username — something like `willchu12-git`.

Write it down. You'll type it a few times. Everywhere below that says **YOURNAME**, use it.

## Step 2. Check the app knows your username

Open `config.json` in the pool-pilot folder. Near the bottom, find:

```json
"app": {
  "data_repo": "willchu12-git/pool-pilot",
```

If that first bit isn't your username, change it and save. **This is the only file you need to
edit by hand in Part 1.**

## Step 3. Make the repo on GitHub

Go to [github.com/new](https://github.com/new).

- **Repository name:** `pool-pilot`
- **Public** ← must be public, this is what makes the app free to host
- Leave every checkbox **unticked** (no README, no .gitignore, no license)
- Click **Create repository**

You'll land on a page of setup instructions. Ignore all of it — the next step covers it.

> **"Public? Really?"** Your pool readings will be visible to anyone with the link. That's pH
> numbers and "backwashed the filter." The trade is that public repos get free web hosting and
> the app can read your data with no password at all. If you'd rather it were private, say so —
> it's a small change, and you'd trade it for having to paste a token before the app shows
> anything.

## Step 4. Send the code to GitHub

Back in Claude Code, run this — **with your username swapped in**:

```bash
cd "C:/Users/willc/OneDrive/Apps/Claude/pool-pilot" && git remote add origin https://github.com/YOURNAME/pool-pilot.git && git push -u origin main
```

A browser window will pop up asking you to sign in to GitHub. Do that. It only asks once.

**Worked if:** refresh your repo page on GitHub and you see folders — `engine`, `app`, `data`.

## Step 5. Turn on the website

On your repo page: **Settings** (top row, far right) → **Pages** (left sidebar).

Under *Build and deployment*:

- **Source:** Deploy from a branch
- **Branch:** `main`, folder: `/ (root)`
- Click **Save**

Wait 1–2 minutes. Refresh. A green box appears at the top with your address:

```
https://YOURNAME.github.io/pool-pilot/
```

**Worked if:** you open that address and see a dark screen saying **Pool Pilot** with
*Not connected yet*. That's correct — there's no data in it yet.

---

# Part 2 — Turn on the smart bits

Two tokens go in here. Both are free.

## Step 6. Get your Claude token

This is what lets the app write its checklist in plain English on your Claude subscription,
without any extra billing. In Claude Code:

```bash
claude setup-token
```

Follow the prompts. At the end it prints a long string. **Copy the whole thing.**

## Step 7. Give it to GitHub

On your repo page: **Settings** → left sidebar, **Secrets and variables** → **Actions**.

Click the green **New repository secret**.

- **Name:** `CLAUDE_CODE_OAUTH_TOKEN` ← exactly this, all capitals, underscores not spaces
- **Secret:** paste the long string
- **Add secret**

## Step 8. Get your Ondilo token

This is what lets the app read your ICO directly, so you never screenshot anything. In
Claude Code:

```bash
py engine/ondilo.py login
```

It prints a web address. Open it — you'll get an Ondilo sign-in box with **Your email
address**, **Your password**, and an **Authorize** button.

> ⚠️ **Sign in with the account your ICO phone app uses.** Lots of people have two Ondilo
> accounts without realising: the **ondilo.com shop** account from buying the device, and the
> **ICO mobile app** account the device is actually registered to. Only the second one works
> here. If you're not sure which is which, open the ICO app on your phone and look at what email
> it's signed in as.
>
> Using the shop account gets you `?error=access_denied` in the address bar instead of a code.
> If that happens, paste the address in anyway — the tool will explain it.

Your browser will then go to a page that **fails to load**. That is expected and fine. What
matters is the address bar at the top — it now has a long URL with `?code=` in it. **Copy that
whole address**, click back into Claude Code, paste it, press Enter.

It prints a token between two lines of `=====`. Copy it.

> If it also lists more than one pool, note the id of the right one — you'll need it in Step 10.

## Step 9. Give that to GitHub too

Same place as Step 7: **Settings** → **Secrets and variables** → **Actions** → **New repository
secret**.

- **Name:** `ONDILO_REFRESH_TOKEN`
- **Secret:** paste the token
- **Add secret**

You should now have two secrets listed. You'll never touch either again — the Ondilo one doesn't
expire.

## Step 10. Switch the ICO reading on

Open `config.json` again. Find the `"ondilo"` section and change `false` to `true`:

```json
"ondilo": {
  "enabled": true,
```

If Step 8 listed more than one pool, also put the right id in `"pool_id": ""`.

Save, then send the change up:

```bash
cd "C:/Users/willc/OneDrive/Apps/Claude/pool-pilot" && git add -A && git commit -m "turn on the ICO" && git push
```

## Step 11. Run it for the first time

On your repo page: **Actions** (top row).

If it asks *"Workflows aren't being run on this forked repository"* or shows a green **I
understand my workflows, go ahead and enable them** button, click it.

In the left sidebar click **Pool Pilot — daily**, then the **Run workflow** dropdown on the
right, then the green **Run workflow** button.

Wait about 90 seconds and refresh. A green tick means it worked.

**Worked if:** open `https://YOURNAME.github.io/pool-pilot/` again and there are now real
numbers on it from your pool.

> A red X isn't a disaster. Click into the run and find the step that's red — the message
> usually says plainly what's missing. Paste it to Claude Code and it'll sort it.

---

# Part 3 — Put it on your phone

## Step 12. Add it to your home screen

On your iPhone, open **Safari** (this bit doesn't work in Chrome) and go to:

```
https://YOURNAME.github.io/pool-pilot/
```

Tap the **Share** button (the square with the arrow pointing up, at the bottom) → scroll down →
**Add to Home Screen** → **Add**.

It's now an app icon. It'll already be showing your pool.

## Step 13. Let the phone log things

Right now the phone can *read* but not *write* — so you can see the checklist but can't tick
anything off. One more token fixes that.

On your phone or your PC, go to:

**[github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new)**

Fill in:

- **Token name:** `pool pilot phone`
- **Expiration:** 1 year (or *No expiration* if you'd rather never redo this)
- **Repository access:** choose **Only select repositories** → pick **pool-pilot**
- **Repository permissions:** find **Contents** in the list → change *No access* to
  **Read and write**

Scroll down, click **Generate token**. Copy the string it shows you — **this is the only time
it's shown.**

Now in the Pool Pilot app on your phone: bottom bar → **Settings**. Paste the token into the
**GitHub token** box. The repo box above it should already say `YOURNAME/pool-pilot` — fix it if
not. Tap **Save & sync**.

**Worked if:** the little label at the top right of the app changes from *read-only* to
*synced*.

---

# Done. How you actually use it

- Open the app. The top says what today needs. Doses are already worked out for your pool.
- Do a thing → tap **I did this** on it. This matters more than it looks: it's how the app knows
  not to tell you to add salt twice off one reading.
- Your ICO reports itself every few hours. Nothing to upload.
- When the list asks for **CYA**, **alkalinity** or **borates** — the ICO can't measure those.
  Use a test strip and type the number into **New reading → Type a reading in by hand**.
- Anything else you do to the pool goes in **Log something else**.

## If something looks wrong

Trust the water, not the app. Every dose is an estimate from your pool's volume and one
measurement. Add it, let the pump circulate, test again.

And the rule the whole thing is built around: **never dose twice against the same reading.**

## The one number to double-check

`config.json` says your pool is **18,000 gallons**. Every single dose is scaled from that. If
it's wrong, every dose is wrong by the same proportion. Worth being sure.
