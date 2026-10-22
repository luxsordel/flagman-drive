---
name: weekly-update
description: Draft the weekly async team update from GitHub activity. Use when the user says "weekly update", "/weekly-update", "what did we do this week".
---

# Weekly update

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

1. Collect activity for the last 7 days:
   - merged PRs: `gh pr list --state merged --search "merged:>=<date>" --json number,title,author,url`
   - open PRs waiting for review: `gh pr list --state open --json number,title,author,reviewDecision,url`
   - issues closed / opened, issues in progress (assigned, open).
2. Draft the update in Russian:
   - **Сделано** — merged PRs, grouped by area
   - **В работе** — who does what
   - **Ждёт ревью** — PRs older than 1 day without review (highlight them)
   - **Блокеры и вопросы** — ask the user to add theirs
   - **План на неделю**
3. Show the draft. Only after the user confirms, post it as a GitHub Discussion in the "Weekly" category
   (or as an issue comment if Discussions are disabled) — ask which.
