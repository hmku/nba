# Handoff 2026-11-23 - weekly context review (scheduled, unattended, repo only)

## Fixed
- `.claude/skills/daily-routine/SKILL.md`:
  - `fantasy run decision`: `agrees_with_model` is computed by identity key (`ops/ledger.py`), so the skill's example
    (full plan JSON vs `{"set": ...}`) always logged `False` for the model's own plan (daily logs 11-16, 11-20, 11-21,
    11-22). Example now gives both sides the same `id` (checked: returns `True`).
  - `fantasy inseason stream` needs `--adds-used <N>` (default 0); the skill never mentioned it, hence "0/6 vs 1/6
    not reconciled" on 11-22.
- `docs/STATUS.md`: latest sim -> `sim-20261122123128-918a6e` (90.8 / 23.3 %, exp. rank 3.0, season-sim-0.9.0);
  priority 2 roster state as of 11-22 (VanVleet back, Leonard IL, week 4 6-3, week 5 4-4 into Sunday).

## Proposed (needs the user or the weekly audit)
1. **Stream execute bar** (daily log 11-22): a `recommend` at 2.03 SE on 2,000 sims vanished at 3 seeds × 8,000.
   Proposal: daily-routine §4 requires that confirmation before executing a stream `recommend`. Changes the autonomy
   execute rule -> not applied; for today's weekly audit / the user.
2. **Decision log history**: lineup decisions since at least 11-16 carry `agrees_with_model = False` wrongly; any
   audit using that column should ignore it before 11-23 (no ledger rewrite done; ledger is off-limits here).
3. **Capture scripts run as "condensed/inline copies"** (11-21, 11-22; one silently dropped a player, caught by the
   id check). Skill says run `scripts/yahoo/capture_*.js`. Either the scripts are too long for the browser JS tool
   (then ship a short variant in `scripts/yahoo/`) or agents should stop improvising — needs a decision.
4. Still open from earlier reviews: `week-review-0.1.0` not bumped after 9ab8e19 changed its output; in-season-ops
   approvals wording ("trades" explicit); daily logs 30-42 lines vs the 25-line rule (every day over — raise the limit
   or move tip-off checks to their own file); 6 stale worktrees in `.claude/worktrees/`; USER_TODO "CI mypy scope".
5. No `docs/research/weekly_audit_2026-11-16.md` exists although the 11-16 audit run "succeeded" (48 min); the skill's
   fallback to `bias_audit_2026-11-09.md` still applies today. Worth checking that today's run writes the note.

## Checked and fine
- `tests/test_doc_commands.py` green; quoted versions match the code (season-sim-0.9.0, lineup-0.4.1, inseason-0.3.2,
  opportunity-0.1.1, ros-proj-0.6.0+pop-prior-0.1, minutes-0.2.0, advice-0.3.0, recommend-0.2.0, valuation-0.3.0;
  older tags mark feature arrival).
- Scheduled tasks: 6 tasks, crons match the context (07:00, 18:30, 20:45, Sat/Sun 14:30, Mon 05:30, Mon 09:30 ET);
  prompts name a skill/section only. No mismatches.
- This week's lessons that did reach a skill: slot labels after reload (11-17), `--choice` JSON (11-19), `--locked`
  takes our ids (11-21), season-sim drop sanity check + draft-prep adverse-selection check (11-16).
- USER_TODO: no item newly done.

## Tests
`pytest -q` pass, `ruff check src tests` clean, CI mypy clean.
