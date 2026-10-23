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
