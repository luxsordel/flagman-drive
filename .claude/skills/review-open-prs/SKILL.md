---
name: review-open-prs
description: Go through every open pull request of teammates, review each one, answer the authors' replies to earlier review comments, and approve the PRs that are fine. Use when the user says "/review-open-prs", "проверь все PR", "отревьюй открытые пулы", "что там у коллег на ревью".
---

# Review all open pull requests of teammates

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.
Review comments on GitHub are written in Russian too: the team reads them, the code stays in English.

GitHub calls can fail on a flaky network: retry a failed `gh` call a few times before reporting an error.

Running this skill is the user's permission to post reviews and approvals on their behalf, under the
rules below. It is not a permission to merge, close, push to, or edit anyone else's branch.

1. Collect the queue:
   ```bash
   me=$(gh api user --jq .login)
   gh pr list --state open --limit 50 --json number,title,author,isDraft,headRefName,updatedAt,reviewDecision
   ```
   Skip the user's own PRs and drafts. For every remaining PR get
   `gh pr view <n> --json reviews,reviewRequests,commits,statusCheckRollup,mergeStateStatus,files,closingIssuesReferences`.
2. Decide what each PR needs:
   - **new** — the user has not reviewed it yet → full review (step 3);
   - **updated** — the user reviewed it, and there are commits after the user's last review → re-review
     only what changed since then (`git diff <reviewed commit>..<head>` after `git fetch origin <headRefName>`),
     plus check whether each earlier comment of the user is fixed;
   - **replied** — no new commits, but the author answered the user's review threads → step 4 only;
   - **done** — the user already approved the current head and nobody asked anything → skip, mention in the report.
   Review threads with their replies and resolution state:
   ```bash
   gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){
     reviewThreads(first:100){nodes{id isResolved path line comments(first:50){nodes{id databaseId author{login} body createdAt}}}}}}}' \
     -f o=<owner> -f r=<repo> -F n=<n>
   ```
3. Review each PR by the checklist of `/review-pr` (step 2 of `.claude/skills/review-pr/SKILL.md`):
   acceptance criteria of the linked issue, correctness, security, architecture, tests, i18n and design tokens,
   docs/ADR. Read the diff with `gh pr diff <n>` and open the surrounding code when the diff alone is not enough.
   When there are more than two PRs to review in full, review each in its own subagent in parallel
   (Agent tool, `general-purpose`): give it the PR number, the checklist file, and ask it to return findings
   as **must fix / should fix / nit** with `path:line` and a concrete suggestion, without posting anything.
   Check every finding you got back against the code before posting it: drop what is wrong or not about this diff.
4. Answer the authors. For every unresolved thread where the last comment is from someone other than the user:
   - the author fixed it — reply briefly that it is fixed and resolve the thread
     (`gh api graphql -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}' -f id=<thread id>`);
   - the author disagrees or asks — read their argument and the code honestly. If they are right, say so and
     resolve. If not, explain why with a concrete example. If it is a matter of taste, accept their choice;
   - a question you cannot answer from the code or the docs (product decision, requirements) — do not answer
     it; list it for the user.
   Reply in a thread: `gh api repos/{owner}/{repo}/pulls/<n>/comments/<comment databaseId>/replies -f body="..."`.
5. Post one review per PR, with must-fix and should-fix findings as inline comments on the lines:
   ```bash
   gh api repos/{owner}/{repo}/pulls/<n>/reviews --input <tmp json>
   # {"commit_id": "<head sha>", "event": "APPROVE|COMMENT|REQUEST_CHANGES", "body": "...",
   #  "comments": [{"path": "...", "line": 42, "side": "RIGHT", "body": "..."}]}
   ```
   Pick the event:
   - **APPROVE** — no must-fix and no should-fix left (new or earlier, unresolved), the linked issue's
     acceptance criteria are met, and no CI check has failed (pending is fine: auto-merge waits for green).
     Nits may go along with the approval as non-blocking comments, marked «nit:».
   - **REQUEST_CHANGES** — at least one must-fix.
   - **COMMENT** — should-fix only, or a red CI check, or an open question for the user from step 4.
   Never approve when any of these is true; post a COMMENT and list the PR for the user instead:
   - the PR touches identity, kyc, payments, rentals or car commands and nobody has run a security review
     (no mention of `/security-review` in the PR body or comments) — say what to check;
   - a Dependabot PR with a major version bump or a change of a pinned runtime (Python 3.12, ADR-0001);
   - changes to `.github/`, `infra/`, `.claude/` or `CLAUDE.md` that change how the team works — the user decides;
   - you could not read the full diff (too large, generated noise you cannot verify) — say which part is unchecked.
   Write the review body short: what the PR does in one line, what is good, what has to change.
   Do not repeat a comment another reviewer already left on the same line; add «+1» to their thread if you agree.
6. Report to the user, as a table: PR, author, what was done (approved / changes requested / commented /
   answered / skipped), the main points. Then list:
   - questions from authors that only the user can answer;
   - PRs behind `main` or with failing CI (the author's job, not yours — just say so);
   - approved PRs without auto-merge — someone has to press «Squash and merge».
