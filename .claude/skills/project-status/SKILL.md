---
name: project-status
description: Report the project status from GitHub (milestone progress, who works on what, PRs waiting for review or blocked, CI, Dependabot) and fix board columns that drifted. Use when the user asks "статус проекта", "what's the status", "/project-status".
---

# Project status

GitHub calls can fail on a flaky network: retry a failed `gh` call a few times before reporting an error.
Talk to the user in Russian (see «Язык» in CLAUDE.md), plain language, short.

1. Collect:
   - `git fetch --prune origin`; local branch, uncommitted changes, how far local `main` is behind `origin/main`.
   - recent `main` history: `git log --format='%h %ad %an: %s' --date=format:'%m-%d %H:%M' origin/main -10`.
   - issues: `gh issue list --state all --limit 100 --json number,title,state,assignees,milestone,labels`.
   - PRs: `gh pr list --state all --limit 30 --json number,title,state,author,reviewDecision,mergedBy,additions,isDraft`
     and, for every open PR: `gh pr view <n> --json reviewRequests,reviews,statusCheckRollup,mergeStateStatus,autoMergeRequest,files,updatedAt`.
   - board: `gh project item-list 1 --owner <repo owner> --format json`.
   - CI on main: `gh run list --branch main --limit 5`.
2. Fix board drift with `bash scripts/board-status.sh <n> <Status>`:
   - closed issue not in **Done** → Done;
   - open issue with an open PR that closes it → **Review**;
   - open issue with an assignee, no PR, sitting in Ready/Backlog → **In progress**.
   Say what you moved.
3. Report, in this order:
   - progress of the current milestone (done / in review / in progress / free), as a small table;
   - who is working on what;
   - PRs: waiting for review (and for how long), changes requested, failing CI, behind main, auto-merge state.
     For Dependabot PRs, say whether each is safe (patch/minor of a tool) or needs a decision (major version,
     language/runtime version — the project is pinned to Python 3.12, ADR-0001);
   - problems worth attention: PRs without reviewers, PRs merged without approval, open PRs older than a day,
     free issues nobody took, red CI on main;
   - what each person should do next. Prefer tasks that do not touch the same files as work in progress,
     and tasks that unblock others.
4. Do not merge, approve, close or comment on anything during a status report; offer it instead.
