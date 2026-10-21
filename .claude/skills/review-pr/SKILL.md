---
name: review-pr
description: Review a teammate's pull request against the project rules and help the human reviewer decide. Use when the user says "review PR 45", "/review-pr 45".
---

# Review a pull request

Argument: PR number. The human makes the final decision; you prepare it.

1. `gh pr view <n> --comments`, `gh pr diff <n>`, `gh pr checks <n>`. Read the linked issue.
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
   Never approve on your own initiative.
