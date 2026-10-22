---
name: ship
description: Finish the current task - run all checks, self-review, commit with Conventional Commits, push, open a PR by the repo template with reviewers, labels and auto-merge, and move the issue to Review on the board. Use when the user says "ship it", "open a PR", "/ship".
---

# Ship the current branch

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

GitHub calls can fail on a flaky network: retry a failed `gh` call a few times before reporting an error.

1. Refuse to ship from `main` — tell the user to create a task branch (`/start-task`).
   The issue number is in the branch name (`<type>/<n>-<slug>`).
2. Bring the branch up to date: `git fetch origin` and, if `origin/main` has new commits, merge it
   (`git merge origin/main`, never rebase a pushed branch). Resolve conflicts, keeping both sides' intent;
   ask the user when the intent is unclear.
3. Run every check that exists for the changed areas and fix failures (never by disabling a check):
   - `apps/api`, `services/*`: `uv run ruff format --check`, `uv run ruff check`, `uv run mypy`, `uv run pytest`
   - `apps/mobile`, `apps/admin`, `packages/*`: `npm run lint`, `npm run typecheck`, `npm test`
   - If the API schema changed — regenerate `packages/api-client`.
4. Self-review the diff (`git diff origin/main...`) against `CLAUDE.md` and `.github/pull_request_template.md`.
   If the diff touches identity, kyc, payments, rentals or car commands — tell the user to run
   `/security-review` before the PR and wait.
5. Show the user a summary of the changes and the proposed commit message(s) (Conventional Commits, English).
   Commit after confirmation.
6. `git push -u origin HEAD`.
7. Find the reviewers: every repository collaborator with write access except the current user.
   ```bash
   me=$(gh api user --jq .login)
   gh api repos/{owner}/{repo}/collaborators --jq "[.[] | select(.permissions.push) | .login | select(. != \"$me\")] | join(\",\")"
   ```
   If the list is empty, tell the user the PR will have no reviewer and ask whom to request.
8. Pick the area labels from the changed top-level paths: `apps/api` → `area:api`, `apps/mobile` → `area:mobile`,
   `apps/admin` → `area:admin`, `services/car-emulator` → `area:emulator`, `infra`, `.github`, `scripts` →
   `area:infra`, `docs` → `docs`; add `security` if step 4 flagged a security-sensitive module.
9. Create the PR in one call:
   `gh pr create --base main --title "<conventional title>" --body-file <tmp file> --reviewer <logins> --assignee @me --label <labels>`.
   The body follows `.github/pull_request_template.md`: what and why, `Closes #<issue>`, how to verify,
   checklist with honest ticks. If the diff is large because of generated files (lock files, generated API
   client), say in the body which files are worth reviewing.
10. Verify the review request landed: `gh pr view --json reviewRequests --jq '[.reviewRequests[].login]'`.
    If someone is missing, add them with `gh pr edit --add-reviewer <login>` and check again.
    Never finish with an empty reviewer list without telling the user.
11. Turn on auto-merge so the PR squash-merges itself once it is approved and CI is green:
    `gh pr merge --auto --squash`. Tell the user they can turn it off with `gh pr merge --disable-auto`
    (or the button on the PR page) if they want to merge by hand. If auto-merge is not available, skip it and say so.
12. Move the issue to **Review** on the board: `bash scripts/board-status.sh <issue> Review`.
    If it fails, say so instead of failing the whole ship.
13. Print the PR URL, the requested reviewers and whether auto-merge is on.
