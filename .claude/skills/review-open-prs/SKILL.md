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
   `gh pr view <n> --json reviews,reviewRequests,commits,statusCheckRollup,mergeStateStatus,files,closingIssuesReferences`
   and its review threads with replies and resolution state:
   ```bash
   gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){
     reviewThreads(first:100){nodes{id isResolved path line comments(first:50){nodes{id databaseId author{login} body createdAt}}}}}}}' \
     -f o=<owner> -f r=<repo> -F n=<n>
   ```
   **The user's threads** are those with at least one comment by `$me`. Only these are yours to answer and
   resolve; threads of other reviewers are theirs (at most a «+1», see step 5).
2. Decide what each PR needs:
   - **new** — the user has not reviewed it yet → full review (step 3);
   - **updated** — the user reviewed it, and there are commits after the user's last review → re-review
     only the author's own commits since then, not what came from `main` via `/sync`:
     `git fetch origin <headRefName>`, then
     `git log --first-parent --no-merges -p <reviewed commit>..origin/<headRefName>` (without `--first-parent`
     the log also lists the commits a `/sync` brought in from `main`; `git range-diff` when the author rewrote
     history). This checks the fix, it is not a new review: as in a round of `/review-pr` (its «Following the
     fix», step 3), comment only on a must-fix in the lines the fix changed or a must-fix missed earlier, and
     raise no nits. Then go through the user's threads (step 4). At most two such rounds. Count them by the
     user's reviews in state `CHANGES_REQUESTED` (`reviews[].state` from step 1), not by all reviews: thread
     replies and COMMENT reviews also show up there. If the user already has two `CHANGES_REQUESTED` reviews on
     the PR and a must-fix is still open after this round, post a COMMENT that hands the PR to the humans
     instead of another REQUEST_CHANGES, and list it for the user;
   - **replied** — no new commits, but the author answered in the user's threads → step 4 only;
   - **done** — the user already approved the current head and nobody asked anything → skip, mention in the report.
3. Review each PR by the checklist of `/review-pr` (step 2 of `.claude/skills/review-pr/SKILL.md`):
   acceptance criteria of the linked issue, correctness, security, architecture, tests, i18n and design tokens,
   docs/ADR. Read the diff with `gh pr diff <n>` and open the surrounding code when the diff alone is not enough.
   When there are more than two PRs to review in full, review each in its own subagent in parallel
   (Agent tool, `general-purpose`): give it the PR number, the checklist file, and ask it to return findings
   as **must fix / nit** by the bar in «What blocks a PR» of that file, with `path:line` and a concrete
   suggestion, without posting anything. Check every finding you got back against the code before posting it:
   drop what is wrong or not about this diff, and move to nits what does not pass the bar.
4. Go through every unresolved thread of the user:
   - the author fixed it (with or without a reply) — reply briefly that it is fixed, with the commit, and resolve
     the thread. A silently fixed thread must be resolved too, otherwise the PR never qualifies for approval;
   - the author disagrees or asks — read their argument and the code honestly. If they are right, say so and
     resolve. If not, explain why with a concrete example. If it is a matter of taste, accept their choice;
   - a question you cannot answer from the code or the docs (product decision, requirements) — do not answer
     it; list it for the user;
   - not fixed and no reply — leave it open if it is a must-fix by the bar; it counts as an open finding in step 5.
     If it does not pass the bar (an old nit, or a «should fix» from before the bar), reply that it does not
     block and resolve it: an old minor thread must not hold the PR.
   Reply and resolve by thread id (the REST `/comments/<id>/replies` endpoint only accepts the first comment
   of a thread, so do not use it):
   ```bash
   gh api graphql -f query='mutation($id:ID!,$b:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$id,body:$b}){comment{id}}}' -f id=<thread id> -f b="..."
   gh api graphql -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}' -f id=<thread id>
   ```
5. Post one review per PR, with must-fix findings as inline comments on the lines and nits only as a short
   list in the body, marked «nit, не блокирует».
   Write the JSON to a file in the scratchpad directory, not in the repo:
   ```bash
   gh api repos/{owner}/{repo}/pulls/<n>/reviews --input <scratchpad>/review-<n>.json
   # {"commit_id": "<head sha>", "event": "APPROVE|COMMENT|REQUEST_CHANGES", "body": "...",
   #  "comments": [{"path": "...", "line": 42, "side": "RIGHT", "body": "..."}]}
   ```
   Inline comments can only point at lines inside the diff hunks; GitHub rejects the whole review (422) otherwise.
   A finding about a line outside the diff goes into the review body with `path:line`.
   Pick the event:
   - **APPROVE** — no must-fix left (new, or an unresolved thread of the user), the linked issue's acceptance
     criteria are met, and every required CI check has already passed. Do not approve while CI is pending: with
     auto-merge on, the PR would merge on green without anyone looking at the result. Nits go along with the
     approval in the body; do not hold the approval back to have them fixed.
   - **REQUEST_CHANGES** — at least one must-fix, whatever the CI state: rounds are counted by these reviews
     (step 2), and a must-fix posted as COMMENT would slip past the two-round limit.
   - **COMMENT** — no must-fix, and CI red or still running, or an open question for the user from step 4.
   Never approve when any of these is true; post a COMMENT and list the PR for the user instead:
   - the PR touches identity, kyc, payments, rentals or car commands and there is no evidence a security review
     was done: the template line `- [x] … /security-review` ticked, or a comment with the results of
     `/security-review`. A mere mention of `/security-review` does not count — the PR template contains it
     in every PR. Say what to check;
   - a Dependabot PR with a major version bump or a change of a pinned runtime (Python 3.12, ADR-0001);
   - changes to `.github/`, `infra/`, `.claude/` or `CLAUDE.md` that change how the team works — the user decides;
   - you could not read the full diff (too large, generated noise you cannot verify) — say which part is unchecked.
   Write the review body short: what the PR does in one line, what is good, what has to change.
   Do not repeat a comment another reviewer already left on the same line; add «+1» to their thread if you agree.
6. Report to the user, as a table: PR, author, what was done (approved / changes requested / commented /
   answered / skipped), the main points. Then list:
   - questions from authors that only the user can answer;
   - PRs behind `main` or with failing CI (the author's job, not yours — just say so);
   - PRs left without approval only because CI was still running — run the skill again when it is green;
   - approved PRs without auto-merge — someone has to press «Squash and merge».
