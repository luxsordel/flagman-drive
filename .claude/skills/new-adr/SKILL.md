---
name: new-adr
description: Record an architecture decision as a new ADR in docs/adr. Use when a significant technical decision is made or the user says "/new-adr", "write an ADR".
---

# New ADR

Talk to the user in Russian (see «Язык» in CLAUDE.md), even though this skill is written in English.

1. Find the next number: highest `docs/adr/NNNN-*.md` + 1.
2. Copy the structure of `docs/adr/0000-template.md`. Write in Russian. Status "предложено" unless the user
   says the decision is accepted; date — today.
3. Fill context, decision, alternatives (with honest reasons for rejection), consequences.
4. If the decision changes rules in `CLAUDE.md` or `docs/02-architecture.md` — update them in the same change
   and link the ADR.
5. Show the result to the user for confirmation.
