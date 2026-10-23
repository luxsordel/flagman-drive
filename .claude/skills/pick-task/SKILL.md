---
name: pick-task
description: Find the next free GitHub issue that will not conflict with open pull requests or with tasks teammates have in progress, and recommend it. Use when the user says "/pick-task", "какую задачу взять", "найди задачу", "что мне делать дальше".
---

# Pick a task without conflicts

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

GitHub calls can fail on a flaky network: retry a failed `gh` call a few times before reporting an error.

This skill only reads and recommends. It does not assign issues or move them on the board: `/start-task` does that.

1. Collect:
   - `git fetch --prune origin`.
   - candidates — open issues without an assignee:
     `gh issue list --state open --limit 100 --search "no:assignee" --json number,title,labels,milestone,body`.
   - the user's own unfinished work: `gh issue list --assignee @me --state open --json number,title,labels,milestone`.
     Those without an open PR go first in the report: finishing started work beats new work.
   - work in progress of others:
     - open PRs: `gh pr list --state open --json number,title,author,headRefName,files,closingIssuesReferences`;
     - assigned open issues without a PR: `gh issue list --state open --limit 100 --json number,title,assignees,body --jq '[.[] | select(.assignees | length > 0)]'`
       and their plan comments (`gh issue view <n> --comments` — `/start-task` posts the plan there);
     - their pushed branches: `git branch -r --list 'origin/*/<n>-*'` and
       `git diff --name-only origin/main...<branch>` for the files already touched.
   - the board (`gh project item-list 1 --owner <repo owner> --format json`) for the Ready/Backlog order.
2. Build the footprint of every piece of work in progress: the files it changes (from the PR or the branch)
   or, when there is no code yet, the files its plan names, else the modules its issue describes.
3. Estimate the footprint of each candidate: read the issue, find the code it will touch
   (`apps/api/src/flagman/<module>/`, screens in `apps/mobile/src/app/`, pages in `apps/admin/src/`, …).
   Be concrete — module and file level, not "backend".
4. Rate the conflict risk of each candidate against all work in progress:
   - **high** — the same file or the same module of the API; or the issue is blocked by an open one
     ("blocked by #", "depends on #", a task list that references it) or needs code that is not merged yet;
   - **medium** — shared hot spots that both sides almost always edit: Alembic migrations (two new heads),
     `openapi.json` and `packages/api-client` (regenerated), router registration in the API app,
     `apps/admin/src/routes.tsx` / `navigation.ts`, i18n resource files, `package.json` / lock files,
     `docs/02-architecture.md`. These merge, but someone will run `/sync`;
   - **low** — no overlap.
5. Rank the candidates: low risk first; then the current milestone before later ones; then issues that unblock
   others; then the board order (Ready before Backlog); `good first issue` higher for a newcomer.
   Skip issues without acceptance criteria unless nothing else is left — and then say they need criteria first.
6. Report, short:
   - the recommended issue: number, title, why it is safe (what it touches, that nobody else touches it),
     what it unblocks;
   - 2–3 alternatives with their risk and the reason;
   - risky ones worth waiting for, with what they wait for (PR #, teammate);
   - medium-risk overlaps the user should keep in mind (e.g. "both add a migration — whoever merges second runs `/sync`").
   Then offer `/start-task <n>` for the recommended one.
