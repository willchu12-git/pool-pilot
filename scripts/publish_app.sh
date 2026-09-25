#!/usr/bin/env bash
# publish_app.sh -- copy the PUBLIC app shell into the public Pages repo.
#
# Only app/ ever leaves the private repo: HTML, CSS, JS, icons. No readings, no
# actions, no screenshots. Pool data reaches the phone straight from the private
# repo over the GitHub API, using the token stored in the browser.
#
# Needs (set in the private repo's settings):
#   vars.APP_REPO           e.g. yourname/pool-pilot-app
#   secrets.APP_REPO_TOKEN  a PAT with Contents: read & write on that repo
set -euo pipefail

: "${APP_REPO:?set the APP_REPO repository variable, e.g. yourname/pool-pilot-app}"
: "${APP_REPO_TOKEN:?set the APP_REPO_TOKEN secret}"

SRC="$(cd "$(dirname "$0")/.." && pwd)/app"
TMP="$(mktemp -d)"

echo "publishing $SRC -> $APP_REPO"
git clone --depth 1 "https://x-access-token:${APP_REPO_TOKEN}@github.com/${APP_REPO}.git" "$TMP/repo" 2>/dev/null \
  || { mkdir -p "$TMP/repo" && cd "$TMP/repo" && git init -q && git remote add origin \
       "https://x-access-token:${APP_REPO_TOKEN}@github.com/${APP_REPO}.git"; }

cd "$TMP/repo"
# wipe tracked files but keep git metadata, then copy the fresh shell in
find . -mindepth 1 -maxdepth 1 -not -name '.git' -exec rm -rf {} +
cp -r "$SRC"/. .

git config user.name  "poolpilot-bot"
git config user.email "poolpilot-bot@users.noreply.github.com"
git add -A
if git diff --cached --quiet; then
  echo "app shell unchanged -- nothing to publish"
  exit 0
fi
git commit -q -m "chore: publish app shell"
git branch -M main
git push -q origin main
echo "published"
