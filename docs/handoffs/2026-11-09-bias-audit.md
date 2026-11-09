# 2026-11-09 — week-3 bias & calibration audit

- User asked for a league summary + model bias check after week 3. Two read-only subagent audits; findings in
  `docs/research/bias_audit_2026-11-09.md` (scripts in `docs/research/bias_audit_2026-11-09/`). No code/model change.
- Result: projections run high per game (PTS -12%, MIN -7.6%, FG% -1.9 pp) but mostly normal early-season + league-wide dip;
  small on rostered players. Sim not overconfident (matchup Brier 0.175 vs 0.219 calibrated, n=18). Me 2-1, on expectation.
- New tasks: T-022 team-minutes constraint, T-023 prior for unprojected players (Hezonja), T-024 sim streaming volume,
  T-025 league-level offsets, T-026 re-audit ~week 8. T-018 (banked results in season sim) is now the main gap for odds levels.
- Context updated: this note + research note (the audit method is reusable via the scripts).
- Caveat: other teams' standings are reconstructed, not Yahoo-final. A parallel `week-backfill` run (other session) was reading final scores.
