# Handoff 2026-11-02 - weekly context review (scheduled, unattended, repo only)

## Fixed (small factual drift)
- Autonomy (user decision 2026-10-31) not yet reflected in three places: `daily-routine` description ("ASK before any
  add/drop"), `league-loop` schedule table ("asks before add/drops"), `docs/STATUS.md` header line.
- `docs/STATUS.md`: latest sim run -> `sim-20261101122822-e0a6be` 87.3 / 27.5 %; priority 2 roster state (Leonard O in
  IL; VanVleet/Sheppard Q, #845 blocks adds, week-3 drop due; Shead decision_miss); last-updated line.
- `CLAUDE.md`: "in-season-ops (Phase 2 placeholder)" -> "(capability map + approval policy)"; "Moves tab" -> "Inbox tab"
  (last week's proposal 2; factual, the dashboard tab is `/inbox`).
- `docs/USER_TODO.md`: pre-draft and draft-night checklists marked done (draft 2026-10-19).

## Proposed (needs the user)
1. **IL eligibility (update of last week's #3):** now settled by evidence — Yahoo lets P/Q players sit in IL, but then
   refuses every add/drop with #845 (2026-11-01). The lesson is in daily-routine §2; the code still exits
   `infeasible_roster[mine]` on any Q in IL, forcing stand-in analysis every run (10-26..11-01). Proposal: `lineup plan`
   treats my Q/P IL players as a warning ("blocks transactions") instead of exit 2, with a test.
2. **Approval latency (11-01 decision_miss):** an approved add waited ~5 h for the next scheduled run and was lost.
   Under autonomy this mostly disappears for adds; for trades consider a short-interval check after an approval.
3. `in-season-ops` §approvals "A scheduled run … never executes these" now only concerns trades (consistent with the
   task prompts "ask, never do"); say "trades" explicitly. Policy wording, so left alone.
4. Two approvals per move (`txn plan` files its own) — still open from last week (#4).
5. Daily log length: still 27-45 lines vs the ≤ 25 rule (last week #7).
6. Housekeeping: 6 stale worktrees in `.claude/worktrees/`; USER_TODO "CI mypy scope" still open (ci.yml unchanged).
7. 2026-10-31 handoff's "What was accomplished" quotes `AUTONOMOUS = {…, trade}`; code has trades gated. Historical
   note, not edited (the next line and policy.py are right).

## Checked and fine
- `tests/test_doc_commands.py` green; model versions in capability tables match the code (lineup-0.4.1,
  season-sim-0.8.1, inseason-0.3.1, opportunity-0.1.1, week-review-0.1.0, advice-0.3.0; "lineup-0.3.0"-style tags in
  daily-routine mark when a feature arrived).
- Scheduled tasks: crons/descriptions match context times (07:00, 18:30, 20:45, Sat/Sun 14:30, Mon 05:30 ET); prompts
  name a skill section/window and match the autonomy policy (adds/drops act, trades ask). No mismatches.
- `policy.py`: `AUTONOMOUS = {add, drop, add_drop}`, trades gated — matches CLAUDE.md and skills.

## Tests
`pytest -q` pass, `ruff check src tests` clean, CI mypy clean. Docs-only change.
