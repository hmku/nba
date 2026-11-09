# Handoff 2026-11-09 - weekly context review (scheduled, unattended, repo only)

## Fixed
- `in-season-ops` capability map: in-season projections `ros-proj-0.5.0` -> `ros-proj-0.6.0` (+ `pop-prior-0.1`,
  preseason `team-min-0.1`), matching `INSEASON_MODEL_VERSION`. Added the `weekly-audit` and `context-review` skills
  (and their Monday tasks) to the "which skill" table.
- `docs/STATUS.md`: latest sim -> `sim-20261109061512-9c6867` (86.0 / 24.9 %); priority 2 roster state as of 11-08
  (was 11-01); priority 4 version -> `ros-proj-0.6.0+pop-prior-0.1`.
- **Test time bomb:** `tests/test_intel_advisor.py::test_cli_source_advisor_and_show` failed from ~10:00Z today: fixed
  seed timestamps (T0 = 11-02 12:00Z) fell out of `intel latest`'s default 7-day window. The test now passes
  `--since 2026-11-01` (as `test_intel_threads.py` does). CI on main was still green (last push 06:16Z); the next
  push would have gone red. Lesson: tests with fixed dates must pin `--since`/`at=` or monkeypatch `intel.now`.

## Proposed (needs the user)
1. Still open from 11-02: `in-season-ops` approvals wording ("a scheduled run never executes these") should say
   "trades" explicitly; two approvals per move (`txn plan` files its own); daily logs 20-30 lines vs the 25-line rule
   (better this week: only 11-02 and 11-08 are over).
2. Housekeeping: 6 stale worktrees in `.claude/worktrees/`. USER_TODO "CI mypy scope" still open (ci.yml unchanged).
3. Consider a sweep for other fixed-date tests that depend on wall-clock windows (one found; there may be more).

## Checked and fine
- `tests/test_doc_commands.py` green. Other quoted versions match the code (lineup-0.4.1, season-sim-0.8.1,
  inseason-0.3.1, opportunity-0.1.1, week-review-0.1.0, advice-0.3.0, recommend-0.2.0; older tags mark when a feature
  arrived).
- Scheduled tasks: crons match context (daily 07:00, tip-off 18:30 / 20:45 / Sat-Sun 14:30, context review Mon 05:30,
  weekly audit Mon 09:30 ET); prompts name a skill/section and match policy (adds/drops act, trades ask). No mismatches.
- Paths quoted in skills/runbooks/STATUS exist (`src/content/...` in dashboard.md is relative to `web/`).
- This week's lessons reached their homes (matchup Totals fallback -> roster_sync.md; review backlog -> daily-routine
  1.7; infeasible_roster -> daily-routine §2; Mac sleep -> USER_TODO; `swapactive` check already in daily-routine).

## Tests
`pytest -q` pass, `ruff check src tests` clean, CI mypy clean.
