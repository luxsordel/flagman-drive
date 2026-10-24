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
3. Sort the findings by the bar below into **must fix** and **nit**, each with file:line and a concrete
   suggestion. Say explicitly if nothing blocks the PR.
4. Only if the user asks: post the review with `gh pr review <n> --comment|--request-changes|--approve -b "..."`.
   Never approve on your own initiative here: this skill is the manual review, where the human decides on one PR.
   Automatic approval exists only in `/review-open-prs`, under its own rules (green CI, nothing blocking left,
   no security-sensitive or process changes). Post must-fix items as inline comments on the relevant lines when
   the user agrees, so the author can resolve them one by one. A review with a must-fix always goes as
   `--request-changes`, never `--comment`: rounds are counted by those reviews (step 2 of `/review-open-prs`).
   Nits go only as a short list in the review body.
   With no must-fix and every required CI check passed, suggest approving right away, nits included, instead of
   a comment that waits for a fix.
5. After an approval, tell the user what happens next: if the author turned on auto-merge, the PR merges
   itself once CI is green; otherwise someone presses "Squash and merge". If the PR is behind main, say so.
6. If the posted review left must-fix items, follow the fix — see below. Nits alone are never followed.

## What blocks a PR

Every extra round costs both sides time and limits, so only a real defect blocks. A finding is **must fix**
only if merging the PR as it is would:
- break behaviour: a bug, or an edge case that can actually happen and is not handled;
- open a security hole (`docs/03-security.md`);
- break a written rule of `CLAUDE.md`, for example layers and module boundaries, ports, money and time, the
  rentals state machine, `extra="forbid"`, secrets and PII, i18n and design tokens, no disabled checks, no dead
  code, an ADR for a new architectural decision;
- leave new logic without tests, or with a test that does not check what it claims;
- leave an acceptance criterion of the linked issue unmet.

Everything else is a **nit**: naming, comment wording, "could be simpler", small refactors, style the linters
accept, problems that only might appear later. Test: "what goes wrong if this is merged as is?" — if you cannot
name a concrete failure or a broken rule, it is a nit. Drop pure matters of taste entirely and keep at most
five nits. A nit is never posted inline, never blocks an approval and nobody waits for it to be fixed.

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
   lists the commits a `/sync` brought in from `main`), then go through your unresolved threads — fixed ones get
   a reply with the commit and are resolved, judged by the code and not by the author's reply. A round checks the
   fix, it is not a new review: read the lines the fix changed and comment only on a must-fix, either in those
   lines (a fix can break something else) or one missed earlier, so a real defect still does not reach `main`.
   Nits are not raised in a round at all.
   Comments are in Russian, as in `/review-open-prs`.
4. Decide what is next. Count rounds as step 2 of `/review-open-prs` does, by your `CHANGES_REQUESTED` reviews
   on the PR, not by this session's memory: a restarted watcher or a switch to `/review-open-prs` keeps the count.
   - A must-fix is still open and you have fewer than two `CHANGES_REQUESTED` reviews → post the round summary
     the way step 5 of `/review-open-prs` does, note the new reviewed head and start the watcher again.
   - No must-fix is open → do not approve on your own. Tell the user (and send a push notification if the tool
     is available) in two or three lines what was fixed and the CI state, and ask whether to approve. Approve
     only on their yes, then continue with step 5 above. Unfixed nits do not hold this up.
   - A must-fix is still open and you already have two `CHANGES_REQUESTED` reviews, or a disagreement neither side gives up → stop watching,
     post a summary that hands the PR to the humans and tell the user. Two agents must not argue in a loop.
5. Rounds run without the user in the conversation: ask them nothing mid-round except the approval. Never push
   to the author's branch: the fix is theirs.
