# 2026-11-09 — week-3 bias & calibration audit

- User asked for a league summary + model bias check after week 3. Two read-only subagent audits; findings in
  `docs/research/bias_audit_2026-11-09.md` (scripts in `docs/research/bias_audit_2026-11-09/`). No code/model change.
- Result: projections run high per game (PTS -12%, MIN -7.6%, FG% -1.9 pp) but mostly normal early-season + league-wide dip;
  small on rostered players. Sim not overconfident (matchup Brier 0.175 vs 0.219 calibrated, n=18). Me 2-1, on expectation.
- New tasks: T-022 team-minutes constraint, T-023 prior for unprojected players (Hezonja), T-024 sim streaming volume,
  T-025 league-level offsets, T-026 re-audit ~week 8. T-018 (banked results in season sim) is now the main gap for odds levels.
- Context updated: this note + research note (the audit method is reusable via the scripts).
- Caveat: other teams' standings are reconstructed, not Yahoo-final. A parallel `week-backfill` run (other session) was reading final scores.

## Later same session
- Shipped T-022 `team-min-0.1` (preseason per-team minutes budget; minutes gain only, 9-cat effect negligible, in-sample selection) and T-023 `ros-proj-0.6.0+pop-prior-0.1` (population-prior rows for 87 unprojected players incl. Hezonja; mismatch warning kept). Quant-reviewed, all items addressed.
- Re-sim (paired, seed 0, 12k): me 87.0→86.0% playoffs, 25.8→24.9% title (`sim-20261109061512-9c6867`, saved). Week 4 vs Christian P(win) 80.5→79.5% (stale capture, not saved — 07:00 daily saves the official plan).
- Rival-moves review `docs/research/rival_moves_2026-11-09.md`: no systematic edge; recent minutes predict next week (slope 0.43 ± 0.07) → T-027; T-028 stream candidates; T-029 P(title) in trade eval.
- New: `scripts/audit/` (weekly_audit.py, moves_review.py, sim_moves.py), skill `weekly-audit`, scheduled task `fantasy-weekly-audit` (Mon 09:30 ET; first run today).
- Note: `fantasy inseason stream` with default args fails `infeasible_roster` on the 11-08 capture with OLD and NEW projections alike (not a regression); daily run passes explicit args — check at 07:00.
- Learned: a subagent's cleanup deleted a file another agent's output was copied to (shared path scripts/audit/moves_review.py) — when two agents share a directory, copy integrations in only after both finish.
