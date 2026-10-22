---
name: sync
description: Bring the current branch up to date with fresh main - fetch, merge origin/main, resolve conflicts, re-run checks, push. On main itself, just fast-forward. Use when the user says "подтяни свежий main", "sync with main", "/sync", or a PR shows "This branch is out-of-date".
---

# Sync with main

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

1. `git status` — if there are uncommitted changes, ask whether to commit them first or stop.
2. `git fetch --prune origin` (retry on network errors).
3. On `main`: `git pull --ff-only origin main` and report what came in (`git log --oneline <old>..HEAD`). Done.
4. On a task branch:
   - `git merge origin/main` (never rebase or force-push a pushed branch).
   - Conflicts: resolve them keeping the intent of both sides. Generated files (`package-lock.json`,
     `uv.lock`, `packages/api-client/src/generated`, `openapi.json`) are regenerated, not hand-merged:
     take main's version, then rerun `npm install` / `uv lock` / the API client generator.
     If the intent of a conflict is unclear, show both sides to the user and ask.
   - Run the checks for the changed areas (same list as in `/ship`) and fix what the merge broke.
   - If the branch was already pushed: `git push`.
5. Report what came from main and whether anything needed fixing.
