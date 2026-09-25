#!/usr/bin/env bash
# commit_push.sh -- commit whatever the pipeline produced and push, surviving a race.
#
# This repo gets written to from three places that don't coordinate with each
# other: the daily job, the interactive job, and me pushing code from my PC.
# If another push lands between this job's checkout and its own push, a plain
# `git push` is rejected (non-fast-forward). Rebase onto the latest main first.
#
# Rebase strategy is -X theirs: under `git rebase`, "ours"/"theirs" are the
# REVERSE of what they mean in `git merge` -- "ours" is the upstream (origin's
# older commit) and "theirs" is the commit being replayed (this run's fresh
# rebuild). -X theirs is what keeps the fresh rebuild on a conflict. Every file
# this commit touches (data/store/*, app/*) is a deterministic rebuild from the
# current code and data, so the fresher one is always correct. Anything
# genuinely irreplaceable -- inbox/*, data/images/* -- is written with a unique
# timestamped filename per action, so those are pure additions and never
# conflict in the first place.
#
# Usage: bash scripts/commit_push.sh "commit message"
set -euo pipefail
MSG="${1:?commit message required}"

git config user.name  "poolpilot-bot"
git config user.email "poolpilot-bot@users.noreply.github.com"
git add -A

if git diff --cached --quiet; then
  echo "nothing to commit"
  exit 0
fi
git commit -q -m "$MSG"

# 5 pushes, 4 rebases: rebasing after the final attempt would throw away the work
# of resolving the race without ever retrying the push it was resolving for.
for attempt in 1 2 3 4 5; do
  if git push -q; then
    echo "pushed"
    exit 0
  fi
  if [ "$attempt" -eq 5 ]; then break; fi
  echo "push rejected (attempt $attempt) -- rebasing onto the latest main"
  git fetch -q origin main
  git rebase -q -X theirs origin/main
done

echo "still couldn't push after 5 attempts" >&2
exit 1
