---
name: ship
description: Finish the current task - run all checks, self-review, commit with Conventional Commits, push the branch and open a PR by the repo template. Use when the user says "ship it", "open a PR", "/ship".
---

# Ship the current branch

1. Refuse to ship from `main` — tell the user to create a task branch (`/start-task`).
2. Run every check that exists for the changed areas and fix failures (never by disabling a check):
   - `apps/api`, `services/*`: `uv run ruff format --check`, `uv run ruff check`, `uv run mypy`, `uv run pytest`
   - `apps/mobile`, `apps/admin`, `packages/*`: `npm run lint`, `npm run typecheck`, `npm test`
   - If the API schema changed — regenerate `packages/api-client`.
3. Self-review the diff (`git diff origin/main...`) against `CLAUDE.md` and `.github/pull_request_template.md`.
   If the diff touches identity, kyc, payments, rentals or car commands — tell the user to run
   `/security-review` before the PR and wait.
4. Show the user a summary of the changes and the proposed commit message(s) (Conventional Commits, English).
   Commit after confirmation.
5. `git push -u origin HEAD`.
6. Create the PR with `gh pr create --base main --title "<conventional title>" --body-file <tmp file>`, the body following
   `.github/pull_request_template.md`: what and why, `Closes #<issue>` (issue number is in the branch name),
   how to verify, checklist with honest ticks. Request review from the teammate
   (`gh pr edit --add-reviewer <login>` — ask for the login if it is not known).
7. Print the PR URL.
