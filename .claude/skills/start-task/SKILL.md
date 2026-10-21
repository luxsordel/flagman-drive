---
name: start-task
description: Start work on a GitHub issue of this repo - read it, assign, create the branch, gather context and propose a plan. Use when the user says "start task #123", "take issue 123", "/start-task 123".
---

# Start a task

Argument: GitHub issue number (e.g. `123`). If none is given, run `gh issue list --assignee @me --state open`
plus `gh issue list --label ready --state open` and ask which one to take.

1. `gh issue view <n> --comments` — read the goal and acceptance criteria. If there are no acceptance
   criteria, stop and ask the user to add them (or propose them and ask for confirmation).
2. Check nobody else is assigned. If someone else is — stop and tell the user.
   Otherwise: `gh issue edit <n> --add-assignee @me`.
3. Make sure the working tree is clean (`git status`). If it is not — stop and ask.
4. `git fetch origin` and create the branch from fresh `origin/main`:
   `git switch -c <type>/<n>-<short-slug> origin/main`, where `<type>` is `feat|fix|chore|docs|refactor`
   based on the issue labels.
5. Read the docs relevant to the issue area (`docs/01..07`, ADRs) and the code it touches.
6. Present a plan: files to change, tests to add, risks (security-sensitive modules: identity, kyc, payments,
   rentals, car commands). For anything bigger than a trivial change, wait for the user's approval of the plan
   before writing code.
7. After the plan is approved, ask whether to post it as a comment on the issue
   (`gh issue comment <n>`), so the teammate sees what is going on.
