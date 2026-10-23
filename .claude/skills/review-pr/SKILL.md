---
name: review-pr
description: Review a teammate's pull request against the project rules and help the human reviewer decide. Use when the user says "review PR 45", "/review-pr 45".
---

# Review a pull request

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

Argument: PR number. If none is given, list open PRs where the current user is a requested reviewer
(`gh pr list --search "review-requested:@me"`) and ask which one. The human makes the final decision; you prepare it.

1. `gh pr view <n> --comments`, `gh pr diff <n>`, `gh pr checks <n>`. Read the linked issue.
   If the diff is dominated by generated files (lock files, generated API client), review the hand-written
   files and only sanity-check the generated ones (they must match their generator).
   For a Dependabot PR: check the changelog for breaking changes and whether the bump crosses a version the
   project pins on purpose (Python 3.12, ADR-0001).
2. Check, in this order:
   - Acceptance criteria of the issue are actually met.
   - Correctness: edge cases, error handling, state transitions of rentals, money in integer kopecks, timezones.
   - Security (`docs/03-security.md`): object-level authorization, server-side pricing, input validation,
     secrets, PII in logs, rate limits.
   - Architecture (`CLAUDE.md`): layer rules, no cross-module model/repository imports, external systems via ports.
   - Tests cover the new logic; no disabled checks.
   - i18n for UI strings, design tokens instead of hard-coded styles.
   - Docs/ADR updated when needed.
3. Report findings to the user grouped as **must fix / should fix / nit**, each with file:line and a concrete
   suggestion. Say explicitly if you found nothing serious.
4. Only if the user asks: post the review with `gh pr review <n> --comment|--request-changes|--approve -b "..."`.
   Never approve on your own initiative. Post must-fix and should-fix items as inline comments on the
   relevant lines when the user agrees, so the author can resolve them one by one.
5. After an approval, tell the user what happens next: if the author turned on auto-merge, the PR merges
   itself once CI is green; otherwise someone presses "Squash and merge". If the PR is behind main, say so.
6. If the posted review left must-fix or should-fix items, follow the fix — see below. Tell the user you are
   watching the PR and that they can say "stop watching" at any time.

## Following the fix

The author (usually their Claude session, see `ship`) fixes the comments, pushes, replies in the threads and
re-requests the review. You notice it and recheck without the user relaying anything. The user's consent to
post the review covers the follow-up: replying in threads, resolving them and posting the round summary need no
new confirmation. Approving still does.

1. Remember the head commit the review was based on (`gh pr view <n> --json headRefOid`) and start the watcher
   with the Bash tool in the background (`run_in_background`, not Monitor — it may wait for hours):
   `bash scripts/wait-pr-activity.sh <n>`. It exits when someone else pushes, comments, reviews or re-requests
   the review, when the PR closes, or after 24 h of silence; its output says which.
2. When it exits: if the PR is merged or closed, or the wait timed out, tell the user and stop. Otherwise run a
   recheck round:
   - Your unresolved threads with their replies:
     `gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{id isResolved isOutdated path line comments(first:50){nodes{author{login} body}}}}}}}' -F o=<owner> -F r=<repo> -F n=<n>`
   - What changed since the reviewed commit: `gh api repos/{owner}/{repo}/compare/<reviewed-sha>...<head>`
     (the list of files and patches), plus `gh pr checks <n>`.
   - For each of your unresolved threads decide: **fixed**, **not fixed / fixed partly**, or **the author
     disagrees**. Judge by the code, not by the author's reply.
     Fixed → reply briefly what you checked, then resolve. Not fixed → reply what is still missing, leave it
     open. Disagreement → if the argument holds, say so and resolve; if not, reply once with your reason and
     leave the thread to the humans.
     Reply: `gh api graphql -f query='mutation($t:ID!,$b:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t,body:$b}){comment{id}}}' -f t=<thread id> -f b='...'`.
     Resolve: `gh api graphql -f query='mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}' -f t=<thread id>`.
   - Review the changed lines by the checklist of step 2: a fix can break something else. New problems go as
     new inline comments.
   - Write in the language of the original review.
3. Decide what is next:
   - Something is still open → post a short round summary (`gh pr review <n> --comment`, or
     `--request-changes` if a must-fix is still open), update the reviewed commit and start the watcher again.
   - Everything is fixed and CI is green → do not approve on your own. Tell the user (and send a push
     notification if the tool is available) in two or three lines what was fixed, and ask whether to approve.
     Approve only on their yes, then continue with step 5 above.
   - The third round still leaves open items, or a thread is a disagreement neither side gives up on → stop
     watching, post a summary and hand the PR to the humans. Two agents must not argue in a loop.
4. Rounds run without the user in the conversation, so do not ask them anything mid-round except the approval.
   Never push to the author's branch: the fix is theirs.
