# In-season (rest-of-season) projection updates

**Answer:** projections now update from games actually played. Availability (`ros-avail-0.3`) and per-game stats
(`stat-shrink-0.1`) are both **fitted** on 2011-2026 box scores and beat the static preseason projection out of sample
(test 2024-25 + 2025-26). `fantasy data update-projections --fetch` writes `data/processed/projections_2026_27.inseason.csv`,
which every tool loads by default (`FANTASY_PROJECTIONS=preseason` reverts). Code: `src/fantasy/model/inseason.py`
(`ros-proj-0.3.0`); fits: `scripts/inseason_model/` (reports `report_avail.json`, `report_stats.json`).

Example (fitted): prior 38.5 ± 24 games, plays the first 20 of 20 team games →
**62.5 expected season games** (42.5 ± 13.6 of the remaining 62). Static prior: 49.1;
iid Beta-binomial: 79.1. Misses all 20 → 7.7 (Beta-binomial: 2.5).
Before his team's first game a player's projection is exactly the preseason one.

## Data
- Box scores: NocturneBear/NBA-Data-2010-2024 (nba.com box scores, 2010-11..2023-24, not committed; fetched
  2026-10-21) and ESPN's summary API for 2024-25 onward (`fantasy data fetch-boxscores`, committed as
  `data/raw/espn_boxscores/*.csv.gz` with provenance; 1230 regular-season games per season, NBA Cup final excluded).
- Box scores list dressed players only, so availability is read off the TEAM schedule: a player's *timeline* is his
  team's games while he was on the roster (`inseason.player_timeline`; same rules in the fit and in production:
  opening-night roster if on the team the season before / listed in its first 5 games, trades switch at the first
  listing for the new team, a waived player keeps missing games, a lost season = all games missed). Check: timeline
  games played vs bbref games, corr 0.90-0.96 per season.
- Priors for history: production gp-ols-0.2 (`availability.predict_gp`) on the bbref panel (coefficients fitted
  through 2026, so the static baseline is slightly *flattered* on the test seasons — conservative for the gain) and the
  projection core `proj_backtest.project_core` on T-3..T-1 for per-game stats (no leakage).

## 1. Availability (fitted, `ros-avail-0.3`)
Target: share of remaining timeline games played after k team games. Population (production-like, 86% of
checkpoints): on the opening-night roster and not injured coming in (lost season, or a 10+ game absence at the end of
T-1) — production handles known preseason injuries with windows instead (below). Per checkpoint k ∈ {1,2,3,5,10,15,20,30,40,50,60}
a WLS (weights = remaining games): `R = b0 + b1·p0 + b2·o + b3·log1p(streak) + b4·[missed all] + b5·dnp/k` minus the
k = 0 level `(c0 + c1·p0) − p0` (c0 = 0.0556, c1 = 0.9762). p0 = prior gp/82, o = observed share, streak = current run of
missed games, dnp = missed games he was dressed for (coach's decision rather than injury). k = 0 deploys the prior
itself (gp, gp_sd): the fitted level shift has no out-of-sample support (k = 0 RMSE 18.71 shifted vs 18.76 prior; the
2025-26 mean residual is +2.0 games for the prior and −1.5 for the shifted fit), so it is removed at every k and the
change from the prior is information from games only. Interpolated linearly in k. Variance
`a·R̂(1−R̂) + c/remaining + d·log1p(streak)` (NNLS on squared residuals). Fit 2011-2024 (excl. 2020-21, n = 50,943);
test 2025-26 (n = 8,868, ≈ 740 per k).

OOS RMSE, remaining games (gain ± cluster-bootstrap SE by player-season; mean residual = actual − predicted).
"Calibrated static" = per-k fit of R on p0 alone, i.e. the prior recalibrated without game information:

| k | update | static | calibrated static | iid Beta-binomial | gain vs static | gain vs B-B | mean resid update / static |
|---|---|---|---|---|---|---|---|
| 1 | 17.42 | 18.61 | 18.57 | 17.81 | 1.2 ± 0.36 | 0.39 ± 0.2 | 1.68 / 1.91 |
| 3 | 17.32 | 18.35 | 18.32 | 19.2 | 1.03 ± 0.38 | 1.87 ± 0.26 | 1.81 / 1.72 |
| 5 | 16.91 | 18.05 | 18.03 | 19.65 | 1.14 ± 0.37 | 2.73 ± 0.27 | 1.92 / 1.55 |
| 10 | 15.59 | 17.18 | 17.18 | 18.55 | 1.59 ± 0.38 | 2.96 ± 0.27 | 1.95 / 1.2 |
| 20 | 13.74 | 15.44 | 15.43 | 16.09 | 1.7 ± 0.38 | 2.35 ± 0.25 | 1.78 / 0.78 |
| 30 | 11.79 | 13.66 | 13.65 | 13.58 | 1.86 ± 0.29 | 1.78 ± 0.24 | 1.34 / 0.39 |
| 40 | 9.74 | 11.63 | 11.63 | 11.17 | 1.89 ± 0.28 | 1.43 ± 0.17 | 0.65 / -0.02 |
| 60 | 5.4 | 7.14 | 7.08 | 6.66 | 1.74 ± 0.18 | 1.27 ± 0.14 | 0.17 / -0.32 |

- The gain is information from games, not recalibration (calibrated static ≈ static at every k).
- Why not iid Beta-binomial: absences are spells. The current streak has a large negative coefficient at every k ≥ 5
  (−0.03 to −0.16 per log-game) and the observed share gets weight only 0.20-0.40: a hot start is partly luck that does
  not persist; an ongoing absence does. dnp: an early dressed DNP is milder than not being listed (+0.17 at k = 1), a
  late one means out of the rotation (−0.06 to −0.16 at k ≥ 20).
- Calibration (OOS): mean z² 0.94-1.10 at every k ≥ 1, normal 80% intervals cover 82-87%; players in a current absence
  of 10+ games remain under-dispersed at k ≥ 20 (z² 1.2-1.7, cover80 0.69-0.80; bimodal: back or not).
- Selection: history only has player-seasons of players who play again later (gp panel), so the newest seasons lack
  lost seasons (all-missed share 2026 lower) — long absences in the test are biased toward returning. Priors for
  history are the production gp-ols-0.2 fit (through 2026): static baseline flattered, i.e. gains are conservative;
  TODO rolling-origin priors.
- k = 1: o, streak and all-missed are collinear; the min-norm fit's predictions are exact, its SEs undefined.
- **Known limitation (early big drops):** a player missing from his team's opening box score drops a lot (e.g. 59 → 36
  gp-equivalent after one game). Excluding players injured coming in only softened this slightly (0.44 → 0.465 share at
  p0 = 0.72), so most of it is real (injured, waived or out of the rotation), but history may still contain undeclared
  preseason injuries. The meta lists |Δgp| > 10 after ≤ 3 team games as `low_confidence`: check the reason before acting.
- Deployment rules (not fitted): a preseason `games_missed = m` override means the prior already expects the first m
  games out, so the update skips that window (healthy prior gp·82/(82−m), sd (gp_sd − 0.15m)/(82−m); the update starts at
  the expected or actual return; remaining expected games exclude the rest of the window; return-timing sd 0.15 per
  window game still to come, as in the preseason build — so before any game the output equals the preseason
  projection). **Prior:** players listed day-to-day (`gtd`) before the opener get a 1-game window without rescaling
  (gp × 81/82 before any game). `status`/`injury_note` come from the ESPN injury feed (0-3 days old), else a
  preseason out/gtd is cleared once he has played. `playoff_mult` is renormalised to the remaining calendar (same
  playoff/other-week ratio, decoded with the consumers' 3/20 share). `gp_sd` floored at 0.5.

## 2. Per-game stats (fitted)
`mpg_post = (n0·mpg_prior + g·mpg_obs)/(n0 + g)`; per-minute rates `(n0·r_prior + M·r_obs)/(n0 + M)`; FG%/FT% by
attempts; makes = shrunk % × shrunk attempts; PTS from makes (prior's scoring ratio). One n0 per stat, pooled over
k = 5..50 (per-k optima add 0.002 ± 0.001 z: not worth it). Train 2012-19 + 2022, validation 2023-24, test 2025-26.

| stat | n0 ± SE | units | OOS RMSE prior → shrunk |
|---|---|---|---|
| MPG | 5.2 ± 0.4 | games | 5.48 → 4.16 |
| FGA | 206 ± 10 | minutes | 2.49 → 1.86 |
| AST | 289 ± 18 | minutes | 0.96 → 0.74 |
| 3PM | 380 ± 26 | minutes | 0.52 → 0.43 |
| REB | 433 ± 30 | minutes | 1.18 → 0.96 |
| FTA | 494 ± 28 | minutes | 0.93 → 0.76 |
| BLK | 636 ± 68 | minutes | 0.21 → 0.19 |
| TOV | 688 ± 42 | minutes | 0.47 → 0.38 |
| STL | 1399 ± 125 | minutes | 0.29 → 0.26 |
| FG% | 398 ± 30 | attempts | .0396 → .0371 |
| FT% | 116 ± 11 | attempts | .0601 → .0580 |

9-cat RMS error in z units: prior 1.73, observed 2.19, shrunk **1.48** (−14.4% ± 1.4%, test); validation −12.9%.
Same with the production 50/50 ours+ESPN prior (−13.8%); n0 refit on that prior changes OOS by 0.0007 ± 0.0023.
n0 is "observed sample worth as much as our prior", not the literature's stabilisation point (which is against the
population mean): it is large where the prior is already good (STL, BLK). FT% updating faster than FG% suggests the
projection core over-regresses FT% (TODO, T-012).

## 3. Injury type (literature / prior, not fitted)
Our box data cannot identify Achilles ruptures (long-injured players are not in box scores) and the case count is
~10 per decade, so no fitted term. Literature: ~70-80% of NBA players return after Achilles repair; games in the first
season back ≈ 75% of the pre-injury season (Trofa et al. 2017, AJSM, multi-sport, 25 NBA players); in the NBA-only
cohort of Khalil et al. 2020 (OJSM, n = 47) 72% returned, with lower minutes/PER at 1 year. The gp model's
"lost season" term covers returning-from-a-lost-season players generally; Achilles-specific adjustments stay manual
overrides (labelled prior), and once the season starts the in-season update learns from games actually played.

## Consumers / caveats
- In the in-season file `gp` = 82 × expected share of REMAINING team games (what every consumer reads as the per-game
  play probability), `gp_sd` its sd on that scale; `season_gp_exp`, `games_played`, `team_games`, `missed_streak`,
  `gp_preseason` added.
- The market alt set gets the same update (`market.inseason.csv`); the stress set (ESPN games) stays preseason by
  design (independent of our availability model).
- Rookies / players without a prior NBA season were not in the fit (no prior); production applies the same formulas.
- The season simulator still simulates the whole calendar from week 1 (no banked results), so with rest-of-season gp it
  re-simulates past weeks at the rest-of-season rate: P(playoffs) is not conditional on current standings (paired
  deltas between moves remain a fair comparison). Follow-up: sim from a start date with actual standings.
- `draft`/`valuesearch` training stay on the preseason file (pinned).
- Box-score sources differ between train (nba.com) and test (ESPN); the test result includes that shift.

Sources: [Trofa et al. 2017](https://journals.sagepub.com/doi/10.1177/0363546517713001),
[Khalil et al. 2020](https://pmc.ncbi.nlm.nih.gov/articles/PMC7708715).
