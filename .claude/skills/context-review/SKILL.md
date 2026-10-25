---
name: context-review
description: Weekly consistency sweep of the agentic context for this repo - CLAUDE.md, .claude/skills, .claude/agents, docs/runbooks, docs/STATUS.md, docs/USER_TODO.md and the scheduled-task prompts - against the code, the CLI and the week's handoffs/daily logs; fixes small factual drift directly (stale times, commands, versions, file paths, duplicated or contradictory rules) and proposes larger rewrites instead of making them. Run by the scheduled task "fantasy-context-review" (Mondays) or on demand ("clean up the context", "is the agentic context up to date?"). No Yahoo access.
---

# Context review (weekly; repo only, no Yahoo, no ledger writes)

Why: the context drifts between sessions (2026-10-25 audit: three files still said 10:00 ET after the daily task moved
to 07:00; a task prompt contradicted the dashboard-approval rule; another re-captured the matchup in a way the code warns
against). Each fix-on-the-spot (CLAUDE.md "Learn from mistakes") only touches the file at hand; this sweep checks the
whole set.

## 0. Orient
```bash
cd ~/dev/nba && source .venv/bin/activate && git pull --rebase --autostash origin main
git log --since="8 days ago" --oneline            # what changed this week (code, model versions, skills)
ls -t docs/handoffs/ | head; ls -t docs/daily/ | head -8
```
Read this week's handoffs and daily logs: note every "context updated", "learned", "bug", "workaround" and anything an
agent did differently from what a skill says (a deviation is either a doc bug or an agent mistake — decide which).

## 1. Mechanical checks
- `python -m pytest -q tests/test_doc_commands.py` — every `fantasy ...` command/flag in the context exists. Fix the doc.
- Model versions: compare versions quoted in skills/STATUS (`lineup-0.x`, `season-sim-0.x`, `opportunity-0.x`, ...)
  with the `MODEL_VERSION` constants in `src/fantasy/` (`grep -rn "MODEL_VERSION\s*=" src/fantasy`).
- File paths mentioned in the context exist (scripts, data files, research notes).
- Scheduled tasks (`~/.claude/scheduled-tasks/*/SKILL.md`, cron in the Mac's local time = ET): each prompt only names a
  skill section/window (rule since 2026-10-25); times in the context match the crons. Do NOT edit or run tasks —
  report mismatches to the user.

## 2. Semantic checks (read, compare, decide)
- Contradictions between CLAUDE.md, skills, runbooks and STATUS (authorisation rules, approval policy, times, paths).
- The same rule stated in several places with different wording: keep one home, point to it from the others.
- STATUS.md "Next priorities" vs what actually happened this week; USER_TODO items that are done.
- Skills describing behaviour the code no longer has (read the function/CLI `--help` when in doubt).
- Lessons in daily logs/handoffs that never reached a skill (e.g. "learned 2026-10-2x: ..." only in a log).

## 3. Act
- **Fix directly** (small, factual, verifiable): stale times/versions/paths/commands, a missing pointer, a lesson that
  belongs in a skill, a done USER_TODO item, a contradiction where the code or a user decision settles which side is
  right (cite it).
- **Propose, don't apply** (anything that changes policy, authorisation, approval rules, a model default, or rewrites
  more than a section): list it in the handoff under "Proposed" and tell the user in the final message.
- Never change safety rules (CLAUDE.md "Safety rules") on your own; never delete a lesson, move it.
- Keep edits concise (CLAUDE.md "Be concise"); don't add boilerplate.

## 4. Finish
`python -m pytest -q` and `ruff check src tests` green; handoff `docs/handoffs/YYYY-MM-DD-context-review.md` (what was
fixed with file list, what is proposed, what was checked and found fine); commit only the files you changed;
`git pull --rebase --autostash origin main && git push origin main`. Final message ≤ 6 lines: fixes made, proposals
awaiting the user, scheduled-task mismatches.
