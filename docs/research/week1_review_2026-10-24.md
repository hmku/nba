# Week 1 (Oct 20-23) actual vs projected — why Shalom Jerome leads PTS

**Answer:** the model isn't badly wrong about Shalom; one week is mostly noise + games played. Shalom's actives scored
405 PTS vs 380 projected for the games they played (z +0.7). We scored 252 vs 354 (z −3.2), on 36.7% FG.

Method (reproducible: `python docs/research/week1_review_2026-10-24.py pre|ins`): daily active lineups from the morning
Yahoo roster captures, stats from ESPN box scores, projection = preseason per-game line (`projections_2026_27.csv`) for each
game actually played, z = residual / sqrt(Σ per-game var) from `noise-var-1.0`. Reconstruction matches Yahoo exactly for
my team (88/240 FG, 252 PTS); Joshua is ~10% short (in-day swaps after the 07:14 ET capture are missed).

- **"11th in PTS" is a season-average weekly rank** (sim-20261024165226-49ce62): Shalom 696 / week, me 712, #4 740. Ranks
  4–11 span ~6%, about one team-week sd (~40 PTS) — it is not a forecast that they score poorly in any given week.
- **Shalom this week:** most player-games played (23, tied). Over-performance = Ty Jerome 73 PTS in 2 games vs 35 proj
  (z +3.8; playing 32 mpg vs 24 projected — role change the preseason line didn't know; in-season line already 26.5 mpg /
  21.1 PTS), Paul George +17. Wembanyama −15 (10/34 FG). Ex-Jerome they're at projection.
- **Us:** 11 of 12 actives below projection; FG% 36.7% vs 47.6% proj ≈ −26 FGM ≈ −50 PTS. Minutes flags: Fears 21–24 vs 27.6
  proj, Kel'el Ware 14–15 vs 25.4 (REB 7 vs 20 proj). Jokić 13 PTS in his one game.
- **League-wide (model check):** all 12 teams' actives −306 PTS vs 4,318 projected (−7%); all players with a projection
  −2.0 PTS/game (SE 0.26), −2.7 min/game, PTS/min 0.476 vs 0.504; rostered FG% 46.0 vs 48.5. Team PTS z: mean −0.81,
  mean z² 2.38 (1.72 after removing the mean; chi² SE of mean z² with 12 teams ≈ 0.41). Suggests (a) early-season level
  bias and/or selection bias on rostered players, (b) weekly team variance understated (known open concern: noise model
  under-states weekly PTS var ~21%, schedule variance not modelled). One week, in-sample — monitor, don't refit yet.
