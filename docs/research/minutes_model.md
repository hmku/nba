# In-season minutes model (`minutes-0.1.0`)

**Answer:** projected mpg now comes from a no-intercept regression on two EWMAs of a player's minutes in games played
plus his preseason mpg. It beats the old equal-weight shrink out of sample on every horizon. Next-5 RMSE:
**4.27 vs 4.83 mpg** (−0.56 ± 0.06). After a role jump (+8 mpg over the last 3 games) the gap is much larger: 5.42 vs
8.36. Code: `src/fantasy/model/minutes.py`, used by `inseason.update_projections` (`ros-proj-0.5.0`; the old formula
stays available as `--minutes-model shrink`). Fit and report: `scripts/inseason_model/fit_minutes.py` →
`report_minutes.json`.

Example (**fitted**): prior 15 mpg, 37 games at 15, then 3 at 30. New model: **22.1** mpg. Old formula: 16.0.

## Model
`e_h(0) = prior`, `e_h(t) = a_h·e_h(t−1) + (1−a_h)·m_t`, with `a_h = 0.5^(1/h)`. Each EWMA runs over games played and
starts at the player's own prior, so early in the season it is mostly the prior.
`mpg = b_s·e_short + b_l·e_long + b_p·prior` for t ≥ 1. With no games (t = 0) the prediction is the prior exactly.
There is no intercept, so the shrinkage target is the player's own prior, not a population constant.
Per-minute rates are unchanged (`stat-shrink-0.1`). The prediction is conditional on playing; availability is a
separate model.

| fitted on 2024-25 (target = next 5 games played) | value ± SE (cluster bootstrap by player, B = 200) |
|---|---|
| half-life short / long (5-fold player-clustered CV on 2024-25) | 1.5 / 15 games |
| b_short | 0.561 ± 0.022 |
| b_long | 0.403 ± 0.036 |
| b_prior | 0.036 ± 0.019 |
| sum | **1.000 ± 0.002** |

**The coefficients sum to 1.** Without an intercept the model is a weighted average, so it preserves the player's
level: a player whose minutes match his prior is projected at his prior. The prior's real weight is larger than
b_prior because both EWMAs start at it. At t games that weight is ≈ 0.56·0.63^t + 0.40·0.955^t + 0.04: 0.62 at t = 2,
0.32 at t = 10 and 0.17 at t = 30. The old formula's prior weight is 5.2/(5.2+t): 0.72, 0.34 and 0.15.

**CV grid** (next 5, RMSE; rows = short half-life, columns = long half-life 6/8/10/15/20/30/40/∞):
the minimum 4.327 is at 1.5/15. The grid is flat for short 1-2 and long 8-40 (all within 0.01), and
worse for short ≥ 3 (≥ 4.36) or 0.5 with a long ≥ 15. The full grid is in `report_minutes.json` under `cv`.
Other targets: next game 1/8, next 10 and rest of season 1.5/15.

## Data and design
- ESPN box scores (`data/raw/espn_boxscores`, regular season). Train 2024-25: 22,919 prediction points, 442 players.
  Test **2025-26** (strictly out of sample): 23,033 points, 450 players. The 2011-24 NocturneBear box scores used by
  `fit_stats.py` are not in the repo (they would need a download), so they were not used. That is an open item.
- Prior: production-like preseason mpg = mean of the projection core on T−3..T−1 (`proj_backtest.project_core`, no
  leakage) and ESPN's preseason projection, using whichever exist. This is not last season's mpg.
- Samples: every player-season with a prior, at every t ≥ 1 games played that has at least one later game.
  Features use games before t only (`feature_panel`; a test checks this). There is no other selection on the
  future. This fixes the look-ahead selection in the earlier exploratory pass, which used star-absence events
  chosen by their later length.
- Targets: mean minutes over the next game / next 5 (primary, deployed) / next 10 games played, and over the rest of
  the season. Each point is weighted by its number of target games.

## Out of sample (2025-26). RMSE ± SE; in brackets, how much worse than the new model (paired cluster-bootstrap SE)
| target | new | old shrink (n0 = 5.2) | last 3 | season mean | prior | MAE new / old | calib. slope new / old |
|---|---|---|---|---|---|---|---|
| next 1 | 6.16 ± 0.06 | 6.76 (+0.60 ± 0.05) | 6.52 (+0.36 ± 0.02) | 6.70 (+0.55 ± 0.04) | 8.44 | 4.73 / 5.21 | 0.96 / 0.93 |
| **next 5** | **4.27 ± 0.06** | 4.83 (+0.56 ± 0.06) | 4.95 (+0.68 ± 0.03) | 4.85 (+0.57 ± 0.07) | 6.86 | 3.26 / 3.65 | 0.94 / 0.91 |
| next 10 | 4.02 ± 0.08 | 4.45 (+0.43 ± 0.06) | 4.87 (+0.85 ± 0.04) | 4.55 (+0.53 ± 0.07) | 6.49 | 3.04 / 3.33 | 0.92 / 0.88 |
| rest of season | 4.14 ± 0.15 | 4.32 (+0.17 ± 0.05) | 5.26 (+1.12 ± 0.09) | 4.74 (+0.59 ± 0.10) | 5.93 | 3.11 / 3.18 | 0.80 / 0.78 |

Each row uses the model fitted to that target; the deployed next-5 model is within 0.02 of it on every row.
Mean residual for next 5: −0.04 mpg (old model: +0.15).

**Subgroups (next 5, RMSE new vs old):** games played 1-2: 5.38 vs 5.63. 41+: 4.14 vs 4.97. Last 3 games ≥ 8 mpg
above the earlier mean: 5.42 vs 8.36 (actual 23.3, new 22.6, old 17.6). ≥ 8 mpg drop: 4.81 vs 5.90. Promoted to
starter: 4.81 vs 7.10. Back from a ≥ 3-game absence: 4.65 vs 5.09. The new model beats old, last-3 and season mean
in every subgroup. **Where it is worse:** on the rest-of-season target in the first 1-5 games (t 1-2: 5.07 vs 5.00,
−0.07 ± 0.02; t 3-5: −0.04 ± 0.03). Very early, the season-long role is better forecast by trusting the prior a bit
more.

## Variants (same CV; OOS change in next-5 RMSE vs the deployed model, negative = better)
| variant | next 5 | rest of season | adopted? |
|---|---|---|---|
| with intercept (diagnostic) | −0.037 ± 0.010 | −0.28 ± 0.06 | no (spec: no intercept) |
| coefficients by games-played bucket (6 buckets, 18 coefficients) | −0.031 ± 0.008 | −0.013 ± 0.008 | no: 0.7% gain for 6× the parameters |
| + share of starts in the last 3 games | 0.000 | −0.02 ± 0.02 | no |
| + teammate absence (out now / in his last 3 games) | −0.002 ± 0.005 | −0.005 ± 0.014 | no (does not help OOS) |

**Intercept diagnostic** (next 5): const 1.66 ± 0.21, slopes summing to 0.937. That means mild reversion toward
1.66/0.063 ≈ 26 mpg. A likely cause is selection on playing: low-minute players who keep appearing tend to get more
minutes. For the rest of the season the intercept is 4.10 ± 0.53 with slopes summing to 0.856, and the gain is real
(−0.28 ± 0.06). This matches the calibration slope of 0.80: over long horizons, minutes regress more than a
weighted average allows.

## Fitted vs prior
Fitted: half-lives (by CV), coefficients and SEs. Priors / choices: the ≥ 1 future game requirement; using the 50/50
ours+ESPN prior; deploying the next-5 target rather than rest of season; dropping the intercept (user spec);
truncation to [0, 48]; one training season.

## Limitations / open questions
- Only one training season. The 2011-24 data has no starter flag, but this model does not need one, so it could be
  used for training.
- In-game injuries, foul trouble and blowouts read as role changes. The 1.5-game short half-life reacts within 1-2
  games. 2026-27 example: Duren 28/10 → 25.0 (old 26.7).
- The rest-of-season projection uses a next-5 model with no intercept. On that horizon an intercept or a
  rest-of-season fit is better (rest-of-season RMSE 3.86 with intercept vs 4.14). Is the next-5 target the right
  choice for ROS valuation?
- Teammate absence does not help as a pooled linear feature. A version targeted at the main beneficiary (same
  position, next in the rotation) was not tried.
