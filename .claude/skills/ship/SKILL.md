---
name: ship
description: Finish the current task - run all checks, self-review, commit with Conventional Commits, push the branch, open a PR by the repo template, request review from the teammates and move the issue to Review on the board. Use when the user says "ship it", "open a PR", "/ship".
---

# Ship the current branch

GitHub calls can fail on a flaky network: retry a failed `gh` call a few times before reporting an error.

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
6. Find the reviewers: every repository collaborator with write access except the current user.
   ```bash
   me=$(gh api user --jq .login)
   gh api repos/{owner}/{repo}/collaborators --jq "[.[] | select(.permissions.push) | .login | select(. != \"$me\")] | join(\",\")"
   ```
   If the list is empty, tell the user the PR will have no reviewer and ask whom to request.
7. Create the PR with the reviewers in the same call:
   `gh pr create --base main --title "<conventional title>" --body-file <tmp file> --reviewer <logins>`.
   The body follows `.github/pull_request_template.md`: what and why, `Closes #<issue>` (issue number is in the
   branch name), how to verify, checklist with honest ticks. If the diff is large because of generated files
   (lock files, generated API client), say in the body which files are worth reviewing.
8. Verify the review request actually landed: `gh pr view --json reviewRequests --jq '[.reviewRequests[].login]'`.
   If someone is missing, add them with `gh pr edit --add-reviewer <login>` and check again.
   Never finish with an empty reviewer list without telling the user.
9. Move the issue to **Review** on the team board (user project #1 of the repository owner):
   ```bash
   owner=$(gh repo view --json owner --jq .owner.login)
   item=$(gh project item-list 1 --owner "$owner" --format json --jq '.items[] | select(.content.number == <issue>) | .id')
   pid=$(gh project view 1 --owner "$owner" --format json --jq .id)
   fid=$(gh project field-list 1 --owner "$owner" --format json --jq '.fields[] | select(.name == "Status") | .id')
   opt=$(gh project field-list 1 --owner "$owner" --format json --jq '.fields[] | select(.name == "Status") | .options[] | select(.name == "Review") | .id')
   gh project item-edit --project-id "$pid" --id "$item" --field-id "$fid" --single-select-option-id "$opt"
   ```
   If the issue is not on the board or the call is not permitted, say so instead of failing the whole ship.
10. Print the PR URL and the requested reviewers.
