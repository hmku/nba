# In-season minutes models (`minutes-0.2.0`)

**Answer:** projected minutes now come from regressions on two EWMAs of a player's minutes in games played plus his
preseason mpg. Both models are fitted on 2010-11..2024-25 and beat the old equal-weight shrink out of sample on
2025-26 (all numbers below are **fitted**):
- **Model A (next 5 games, no intercept)** is for lineups and streaming. It is the `mpg_next5` column of
  `projections_2026_27.inseason.csv`. RMSE **4.26 vs 4.83** (−0.57 ± 0.06).
- **Model B (rest of season, with intercept)** is the `mpg` used by valuations and the season sim. RMSE **3.85 vs
  4.32** (−0.47 ± 0.09).

Code: `src/fantasy/model/minutes.py`, used by `inseason.update_projections` (`ros-proj-0.5.0+…+minutes-0.2.0`).
`--minutes-model shrink` reproduces `ros-proj-0.4.0`, with its own version string. Fit:
`scripts/inseason_model/fit_minutes.py` → `report_minutes.json`.

## Model
`e_h(0) = prior`, `e_h(t) = a_h·e_h(t−1) + (1−a_h)·m_t`, with `a_h = 0.5^(1/h)`. Each EWMA runs over games played and
starts at the prior, so with no games every EWMA equals the prior.
`mpg = const + b_short·e_1.5 + b_long·e_10 + b_prior·prior` for t ≥ 1. With no games (t = 0) the prediction is the
prior exactly. Per-minute rates are unchanged (`stat-shrink-0.1`). Predictions are conditional on playing.

| coefficient (± cluster-bootstrap SE by player, B = 200) | A: next 5 | B: rest of season |
|---|---|---|
| half-lives (5-fold player-clustered CV on the fit seasons) | 1.5 / 10 games | 1.5 / 10 games |
| const | 0 (none) | **3.60 ± 0.17** |
| b_short (EWMA 1.5) | 0.510 ± 0.007 | 0.271 ± 0.006 |
| b_long (EWMA 10) | 0.435 ± 0.010 | 0.573 ± 0.013 |
| b_prior | 0.053 ± 0.004 | 0.028 ± 0.012 |
| sum of slopes | **0.998 ± 0.0005** | 0.872 ± 0.007 |

- **A's slopes sum to 1.** It is a weighted average that preserves the player's level: a player who plays at his
  prior is projected at his prior.
- **B reverts toward a level.** const/(1 − Σ) = 3.60/0.128 ≈ 28 mpg: over a long horizon, minutes regress toward a
  common level, given that he keeps playing.
- Example: prior 15, 37 games at 15, then 3 at 30 → A 21.9, B 21.3, old formula 16.0.
- **CV grid** (RMSE; full grid in `report_minutes.json` `cv`): it is flat for short half-lives 1-2 and long 10-20
  (within 0.005). Short half-lives ≥ 3 or 0.5 are worse. For the next game alone the best pair is 1 / 6.

## Data
- Box scores: NocturneBear/NBA-Data-2010-2024 (nba.com, 2010-11..2023-24, **not committed**).
  - URL: `https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024/main/regular_season_box_scores_2010_2024_part_{1,2,3}.csv`.
  - sha256 for parts 1/2/3: `ca9636d0…7c557` / `5643af8a…7182d` / `65a01fee…6ef375` (full values in
    `scripts/inseason_model/README.md`).
  - 2024-25 and 2025-26 come from the committed ESPN ingester files.
- Starter flag: nba.com `position` is non-empty for starters; ESPN `starter`. Starters are used only for subgroups.
- Seasons: 15 fit seasons with 309,766 prediction points and 1,216 players. Test 2025-26: 23,033 points, 450
  players, strictly out of sample.
  - 2019-20 (cut at 2020-03-11) and 2020-21 (72 games) are kept. Dropping them changes the coefficients by < 0.02 and
    OOS RMSE by < 0.002.
- Prior for every fit season: `project_core(T)`, the production projection core on T−3..T−1. It needs the full
  sumitrodatta Player Totals, because the repo copy starts in 2022. Production uses the 50/50 core + ESPN blend.
- **Prior check (2024-25 alone, same half-lives), core vs blend prior:**

  | model | prior | b_short | b_long | b_prior |
  |---|---|---|---|---|
  | A | core | 0.515 ± 0.022 | 0.393 ± 0.034 | 0.094 ± 0.019 |
  | A | blend | 0.526 ± 0.022 | 0.389 ± 0.032 | 0.085 ± 0.016 |
  | B (no intercept) | core | 0.240 ± 0.020 | 0.633 ± 0.051 | 0.138 ± 0.044 |
  | B (no intercept) | blend | 0.255 ± 0.021 | 0.611 ± 0.055 | 0.144 ± 0.046 |

  The two sets agree within 1 SE, so production keeps the blended prior. OOS with the core prior at test time:
  A 4.25, B 3.85, about the same as with the blend.
- Samples: every player-season with a prior, at every t ≥ 1 games played that has a later game. Features use games
  before t only (a test checks this); there is no other selection on the future. Targets are weighted by their number
  of target games.

## Out of sample, 2025-26 (blend prior). RMSE ± SE (in brackets: worse than the chosen model by, with paired cluster SE)
| model | A: next 5 | B: rest of season | calibration slope (A / B) |
|---|---|---|---|
| A (deployed for next 5) | **4.26 ± 0.06** | — | 0.94 |
| B with intercept (deployed) | — | **3.85 ± 0.13** | 0.92 (mean residual −0.29) |
| B without intercept | — | 4.12 (+0.27 ± 0.05) | 0.80 |
| B without intercept + third term (best by CV: equal-weight shrink, n0 = 2) | — | 4.12 (+0.27 ± 0.06) | 0.79 |
| B without intercept + third EWMA (40 / 60 / 100) | — | CV no better than without (3.883 vs 3.884) | |
| old shrink (n0 = 5.2) | 4.83 (+0.57 ± 0.06) | 4.32 (+0.47 ± 0.09) | 0.91 / 0.78 |
| last 3 | 4.95 (+0.69 ± 0.03) | 5.26 (+1.41 ± 0.08) | |
| season mean | 4.85 (+0.59 ± 0.06) | 4.74 (+0.89 ± 0.12) | |
| prior only | 6.86 | 5.93 | |

Next game: 6.16 vs old 6.76. Next 10: 4.00 vs 4.45.

**ROS decision (rule: an intercept only if it beats every no-intercept variant by more than 2 SE).** The intercept
version wins by 0.27 ± 0.05 overall and by more than 2 SE in every games-played bucket. The principled alternatives
do not close the gap: a third, longer EWMA adds nothing, and a third, slowly-shrinking term helps only 0.07 at
t ≤ 5. The reason is that the reversion is toward a population level (≈ 28 mpg conditional on playing), not toward
the player's own prior. Cost: low-minute players' ROS mpg is pulled up. On 2026-10-24, deep-bench players with
1-3 minutes in their 1-2 games got about +2 mpg over the old formula; this is the conditional-on-playing average
the data supports.

**Games-played buckets (RMSE, chosen model vs old; old minus new ± paired SE):**

| t | A: next 5 | B: rest of season |
|---|---|---|
| 1-2 | 5.39 vs 5.63 (+0.24 ± 0.03) | 4.72 vs 5.00 (+0.28 ± 0.07) |
| 3-5 | 4.42 vs 4.73 (+0.31 ± 0.04) | 4.26 vs 4.61 (+0.34 ± 0.08) |
| **1-5 (fallback rule)** | **+0.28 ± 0.02** | **+0.32 ± 0.08** |
| 6-10 | 4.29 vs 4.54 (+0.25) | 3.99 vs 4.38 (+0.39) |
| 11-20 | 4.27 vs 4.61 (+0.34) | 3.80 vs 4.22 (+0.43) |
| 21-40 | 4.19 vs 4.80 (+0.61) | 3.67 vs 4.17 (+0.50) |
| 41+ | 4.13 vs 4.97 (+0.84) | 3.47 vs 4.19 (+0.72) |

**Fallback rule for t ≤ 5 (use the old shrink unless the new model beats it by more than 2 SE): not triggered for
either model**, so `fallback_t = 0`. The mechanism stays in `MinutesParams` and is tested. The earlier single-season
fit (minutes-0.1.0) was worse than the old formula on the ROS target at t ≤ 2; the intercept model fixes that.

**Subgroups (A next 5 / B ROS, RMSE vs old):**
- +8 mpg jump: 5.39 vs 8.36 / 4.70 vs 6.83.
- −8 mpg drop: 4.81 vs 5.90 / 4.15 vs 4.44.
- Promoted to starter: 4.77 vs 7.10 / 3.79 vs 5.14.
- Back from a ≥ 3-game absence: 4.64 vs 5.09 / 4.43 vs 5.05.

**Earlier extensions (minutes-0.1.0, 2024-25 fit, not adopted):** coefficients by games-played bucket (−0.03 for 6×
the parameters), starter share (0.00) and teammate absence (−0.002 ± 0.005).

## Wiring
- `inseason.csv` `mpg` = B. The counting stats are per-minute rates × B.
- `mpg_next5` = A. Lineup and streaming tools should scale per-game counting stats by `mpg_next5 / mpg`; FG% and FT%
  are unchanged. Not wired into the lineup or stream tools yet.
- The meta `minutes` block holds both parameter sets.

## Fitted vs prior
- Fitted: half-lives (CV), all coefficients and SEs, and the intercept decision and fallback decision (pre-stated
  rules applied to OOS numbers).
- Choices: the "≥ 1 future game" sample, the 50/50 prior in production, and clipping to [0, 48].

## Limitations
- In-game injuries and blowouts look like role changes to the model (the short half-life of 1.5 games reacts fast).
- B's intercept is a population constant, and the level may drift by era. The test-season mean residual is −0.29
  mpg, i.e. B over-predicts slightly.
- Single test season.
