---
name: start-task
description: Start work on a GitHub issue of this repo - read it, assign it, move it to "In progress" on the board, prepare the branch, gather context and propose a plan. Use when the user says "start task #123", "take issue 123", "/start-task 123".
---

# Start a task

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

GitHub calls can fail on a flaky network: retry a failed `gh` call a few times before reporting an error.

Argument: GitHub issue number (e.g. `123`). If none is given, show the user the open issues assigned to them
(`gh issue list --assignee @me --state open`) and the free ones (`gh issue list --label ready --state open
--search "no:assignee"`), recommend one that does not touch the same files as issues other people have
in progress, and ask which one to take.

1. `gh issue view <n> --comments` — read the goal and acceptance criteria. If the issue is closed, stop.
   If there are no acceptance criteria, stop and propose them; add them to the issue only after the user confirms.
2. Check the assignees. Someone else assigned — stop and tell the user. Otherwise: `gh issue edit <n> --add-assignee @me`.
3. Make sure the working tree is clean (`git status`). If it is not — stop and ask.
4. Prepare the branch from fresh `origin/main`. Branch name: `<type>/<n>-<short-slug>`, where `<type>` is
   `feat|fix|chore|docs|refactor` based on the issue labels and title.
   - `git fetch origin`.
   - If this session runs in a worktree the app created (the path contains `.claude/worktrees`) and its current
     branch has no commits of its own (`git log origin/main..HEAD` is empty) and no changes: move it to fresh
     main with `git switch -C <name> origin/main`, then delete the app's original branch if it is now unused
     (`git branch -d <old>`).
   - Otherwise: `git switch -c <name> origin/main`.
5. Move the issue to **In progress** on the board: `bash scripts/board-status.sh <n> "In progress"`.
   If it fails, tell the user and continue — the board is not a reason to stop the task.
6. Rename this session after the task if the session-title tool is available: `#<n> <short title>`.
7. Read the docs relevant to the issue area (`docs/01..07`, ADRs) and the code it touches.
8. Present a plan: files to change, tests to add, risks (security-sensitive modules: identity, kyc, payments,
   rentals, car commands), and any overlap with other open PRs (`gh pr list --state open` + their files).
   For anything bigger than a trivial change, wait for the user's approval of the plan before writing code.
9. After the plan is approved, post it as a short comment on the issue (`gh issue comment <n>`) so the
   teammates see what is going on, unless the user says not to.
