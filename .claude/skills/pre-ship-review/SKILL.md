---
name: pre-ship-review
description: Before /ship, have a fresh subagent that knows nothing about this session read the branch diff like a picky teammate and list everything a reviewer could flag. Use when the user says "/pre-ship-review", "проверь свежим взглядом", "что скажут на ревью", or before running /ship.
---

# Fresh-eyes review before shipping

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

The point is a reviewer who has not seen how the code was written: no knowledge of the reasons, the dead ends
or the "we agreed on this" from this session. So the review runs in a subagent, and you do not pass it your
own explanations — only where to look.

1. Refuse on `main`. Take the issue number from the branch name (`<type>/<n>-<slug>`).
   `git fetch origin`. If there are uncommitted changes, they are part of the review: the subagent reads
   `git diff origin/main` (working tree), not only commits.
2. Launch one subagent (Agent tool, `general-purpose`, no fork — it must start with an empty context).
   Prompt it with this, filling in the branch, the issue and the repo path:

   > You are a strict senior reviewer on the FLAGMAN Drive team. Review the changes of branch `<branch>` in
   > `<repo path>` against `origin/main` (`git diff origin/main`, including uncommitted changes) for issue
   > #<n> (`gh issue view <n> --comments`). Do not edit files, commit, push or post anything.
   > Read first: `CLAUDE.md`, `docs/03-security.md`, `.github/pull_request_template.md`, the docs and ADRs
   > for the area the diff touches, and the checklist in `.claude/skills/review-pr/SKILL.md` (step 2).
   > Learn what this team actually complains about: read the last ~100 review comments,
   > `gh api "repos/{owner}/{repo}/pulls/comments?sort=created&direction=desc&per_page=100"`,
   > and look for the same kinds of problems here.
   > Read every changed file in full, not only the hunks, and the code that calls the changed code.
   > Look for: acceptance criteria not met; bugs and unhandled edge cases (empty input, duplicates,
   > concurrency, time zones, kopecks); missing object-level authorization, trusting client values,
   > PII or tokens in logs, Pydantic without `extra="forbid"`; layer and module boundary violations,
   > external systems not behind `ports.py`; rental status changes outside the state machine;
   > new logic without tests, tests that do not assert what they claim; hard-coded UI strings, colors,
   > spacing instead of i18n and design tokens; `any`, `type: ignore`, disabled lint rules; dead code,
   > commented-out code, leftover debug output, TODOs without an issue; comments that say "what"
   > instead of "why"; naming that does not match the surrounding code; new settings missing from
   > `.env.example`; API schema changed but `packages/api-client` not regenerated; docs or ADR not updated;
   > changes unrelated to the issue.
   > Return a list grouped as **must fix / should fix / nit**. Every item: `path:line`, what is wrong,
   > why a reviewer would flag it, a concrete fix. Mark items you are not sure about as "unsure".
   > If a group is empty, say so. No praise, no summary of the diff.
3. Verify every item yourself against the code before showing it: drop what is wrong, already handled
   elsewhere, or outside the diff. Keep "unsure" items only if you cannot settle them — then say so.
4. Show the user the verified list (must fix / should fix / nit, with file links), and how many items you
   dropped and why in one line. Ask what to fix: everything, must + should, or chosen items.
5. Fix what the user chose, run the checks for the changed areas (same list as in `/ship`), and show the result.
   If anything was must-fix, offer to run this skill once more on the new diff; otherwise offer `/ship`.
