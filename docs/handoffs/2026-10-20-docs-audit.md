# Handoff 2026-10-20 - docs/skills audit during the live draft (audit subagent, cloud session)

## What was accomplished
- Checked every skill, subagent, CLAUDE.md, README, ARCHITECTURE, STATUS, USER_TODO and runbook against the code
  (`fantasy ... --help`, grep). Fixed stale or contradictory guidance in docs and skills only; no `src/` or `web/` changes (draft-path freeze).
- Main fixes: browser wording is now "whichever browser tools are available" (the desktop app's built-in browser or Claude in Chrome);
  draft-prep has the same recommend guard as live-draft and current ADP coverage and settings state; model-eval
  describes sim availability, value search and policy_eval, and lists the current version constants and exit-2 validation;
  draft-shadow no longer frames a top-pick mismatch as a "near-tie"; league-loop has `fantasy intel` as BUILT and daily lineups verified;
  STATUS.md has one current-state table and the post-draft priorities.

## Tests performed
- `python -m pytest -q` -> 464 passed, 1 failed: `tests/test_scoring.py::test_committed_league_settings_file`. This failure
  was already there: commit 436c3ba changed `data/manual/league_settings.json` (source `browser`, all fields verified), and the
  test still expects `yahoo_settings_screenshot` and `unverified == ["draft_order_type", "my_slot"]`. Not fixed (tests are out of scope tonight).
- `ruff check src tests` -> clean.

## Outstanding work
- Update `tests/test_scoring.py::test_committed_league_settings_file` to the verified settings file (after the draft).
- DB task backlog: T-007 and T-009 are built but still `todo`.

## Notes for next agent
- No runs, locks or txns were touched. The task list was read via `fantasy task list --rest`.
