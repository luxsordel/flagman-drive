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
   Never approve on your own initiative here: this skill is the manual review, where the human decides on one PR.
   Automatic approval exists only in `/review-open-prs`, under its own rules (green CI, nothing blocking left,
   no security-sensitive or process changes). Post must-fix and should-fix items as inline comments on the
   relevant lines when the user agrees, so the author can resolve them one by one.
5. After an approval, tell the user what happens next: if the author turned on auto-merge, the PR merges
   itself once CI is green; otherwise someone presses "Squash and merge". If the PR is behind main, say so.
6. If the posted review left must-fix or should-fix items, follow the fix — see below.

## Following the fix

The author (usually their Claude session, see `ship`) fixes the comments, pushes, replies in the threads and
re-requests your review once the whole fix is in. You notice the re-request and recheck without the user
relaying anything. The user's consent to post the review covers the rounds below: replies in threads, resolving
them and the round summaries need no new confirmation. Approving still does (step 4).

1. Note the head commit you reviewed (`gh pr view <n> --json headRefOid`) and start the watcher with the Bash
   tool in the background (`run_in_background`, not Monitor — it may wait for hours):
   `bash scripts/wait-pr-activity.sh <n> --until-review-request`. It wakes only on the re-request, so a fix
   still in progress does not cost a round. Tell the user you are watching the PR and that "stop watching"
   ends it; on that, stop the background task (TaskStop) and confirm it is stopped.
2. When the watcher exits, read its output:
   - review requested → run a round (step 3);
   - PR merged or closed, nothing for 24 h (exit 3) or GitHub unreachable (exit 1) → tell the user and stop.
3. A round is steps 2 and 4 of `/review-open-prs` for this one PR: re-review only the author's own commits since
   the reviewed head (`git fetch origin <headRefName>`, then
   `git log --first-parent --no-merges -p <reviewed>..origin/<headRefName>`: without `--first-parent` the log also
   lists the commits a `/sync` brought in from `main`), then go through your unresolved threads — fixed ones get a reply with the commit and are
   resolved, judged by the code and not by the author's reply. Check the changed lines by the checklist of
   step 2: a fix can break something else; new problems go as new inline comments. Comments are in Russian,
   as in `/review-open-prs`.
4. Decide what is next:
   - Something is still open → post the round summary the way step 5 of `/review-open-prs` does (COMMENT, or
     REQUEST_CHANGES while a must-fix is open), note the new reviewed head and start the watcher again.
   - Everything is fixed → do not approve on your own. Tell the user (and send a push notification if the tool
     is available) in two or three lines what was fixed and the CI state, and ask whether to approve. Approve
     only on their yes, then continue with step 5 above.
   - The third round still leaves open items, or a disagreement neither side gives up → stop watching, post a
     summary that hands the PR to the humans and tell the user. Two agents must not argue in a loop.
5. Rounds run without the user in the conversation: ask them nothing mid-round except the approval. Never push
   to the author's branch: the fix is theirs.
