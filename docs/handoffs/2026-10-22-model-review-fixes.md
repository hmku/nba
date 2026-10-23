# Handoff 2026-10-22 - four-part model review (Fable reviewers) and fixes (main session)

## What happened
Four read-only reviewers (lineup optimizer, projections/valuation, availability + in-season, season sim + trade/stream
tools); fixes built by four worktree subagents, merged by the main session.

| Area | Fix | Version |
|---|---|---|
| Season sim | Injury spells placed on the fantasy window as a circle: weekly availability now flat at gp/82 (was U-shaped: ~0.83 wk1, ~0.64 mid-season, ~0.80 playoffs at gp 60; mean below gp/82). Preseason-built stress set no longer votes in trade/stream unanimity when blend/market are in-season. | season-sim-0.7.0 |
| Lineup | Non-converging fixed point keeps the best visited plan (the `--protect-ratios` re-plan could start the player ratio-bench said to sit, depending on iteration parity); opponent untagged-in-IL no longer aborts my plan (`infeasible_roster[side]`); PTS analytic variance built like the MC; IL-move cap; locked starters P(plays)=1. | lineup-0.4.1 |
| In-season | Unfinished scoreboards never cached (silent game-day loss); k<=5 availability updates damped (prior); rest-of-season target ends at the fantasy-season end (refit, OOS gain 1.0-1.8 games vs static); gp_sd interpolated early; feed-silent status kept inside games_missed windows; ET feed age; id-collision guard. | ros-proj-0.4.0 |
| Projections | TOV kept OFF the age curve on evidence (2025-26 OOS: scaling worsened 33+ bias +0.05 -> +0.18, SE 0.07, no pooled gain); docstring corrected. MPG growth tapered 27-30 mpg; prior_min stated in effective minutes (same strength); `gp_scale` override column. | baseline-0.3.0 |

## Effect on our odds (simulated, 2000 sims each, SE ~1 pp title)
- Old sim 0.6.0 + old projections: P(title) 30.6%, P(playoffs) ~0.89 (refit session's run).
- New sim 0.7.0 + old projections: 25.3% / 0.882 -> the timing fix is about -5 pp title (the old placement inflated
  playoff-week availability).
- + projection fixes (preseason): 25.6% / 0.880 (no change).
- + in-season update after 2 days: 22.1% / 0.846 (saved: sim-20261023001536-c49864). Driven by per-game shrinkage of our
  players' opening games (mpg n0 = 5.2 games, fitted: one game moves minutes ~16% of the way), e.g. Bridges 18.7 -> 17.7
  pts, Sheppard 12.8 -> 11.8; availability barely moved (-0.2 games summed). Noisy this early.

## Open / next
- Trade and add/drop numbers computed before these fixes (VanVleet cards, Champagnie) are stale; re-run before acting.
- TODOs: 9-season TOV-by-age backtest; per-stat prior_min refit; consider damping per-game stat updates for k<=5 like
  availability (stat-shrink is fitted, but opening-night minutes are noisy); `as_of` / banked results in the sim (T-018);
  market-set-alone rule for trade recommendations (documented, not enforced).
- Agentic context updated: daily-routine skill (lineup + update-projections steps), projections-update skill
  (`gp_scale`), research notes (season_simulator, sim_calibration, il_replacement, streaming_and_trade_scan,
  lineup_optimizer, inseason_updates, projection_quality, availability_model).
