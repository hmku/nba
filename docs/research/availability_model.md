# Availability (games-played) model — research note (2026-10-18)

Code: `src/fantasy/model/availability.py` (fitted constants), `projections.py` (`Params.gp_model`,
`manual_gp_scale`, `playoff_mult` column), `lines.py` (`availability()`), `valuation.py`
(`ValuationParams.gp_risk_lambda`, `playoff_weight`). Versions: projections `baseline-0.2.0`
(meta `gp_model.version = gp-ols-0.1`), valuation `valuation-0.2.0`.
Experiment scripts (not committed; session scratchpad `gp/`): `build_panel.py`, `fit.py`,
`final_fit.py`, `late_season.py`, `compare.py`, `sim.py`.

## 1. Expected games played

### Data
- Basketball-Reference player totals and team summaries, full history, from the sumitrodatta GitHub
  mirror (the committed raw file only keeps seasons from 2022 on, so the full upstream file was
  downloaded to the scratchpad for fitting; nothing new is committed under `data/raw`).
- One row per player-season. Traded players use the combined `2TM/3TM/TOT` row. Games are scaled
  to an 82-game schedule using the team's games that season (1999, 2012, 2020 and 2021 were short).
  Traded players use the longest schedule among their stints, and games are capped at 82.
- Unit: player p, target season T, with an NBA season in T-1 of at least 500 minutes.
- Outcome: games in T (82 scale). **Non-injury absence:** if p has no T row but plays again later,
  T counts as 0 games (a whole season lost while still in the league, which is mostly injury). If p
  never plays again (retired, out of the league or overseas), the row is dropped. This matches the
  pipeline, where `inactive_2026_27.csv` and the stale-season rule remove those players before any
  gp is used. Limitation: for T = 2026 we cannot see a later season, so 2026 has no 0-game rows. In
  earlier seasons these were 1-3% of rows.
- Train T ∈ [2014, 2024] (n = 3,679). Test 2025 (n = 340) and 2026 = 2025-26 (n = 347).
  Production refit on T ∈ [2014, 2026] (n = 4,366).

### Models compared (RMSE, out of sample)
| Model | 2025 all | 2025 mpg≥24 | 2025 age≥30 | 2026 all | 2026 mpg≥24 | 2026 age≥30 |
|---|---|---|---|---|---|---|
| current (5/3/2 recency mean, prior 68, weight 1) | 21.00 | 18.71 | 19.46 | 21.87 | 19.78 | 22.95 |
| current, prior/weights re-tuned (prior 50, w 5/2/1) | 19.09 | 17.32 | 17.12 | 20.33 | 18.18 | 20.26 |
| OLS lags only | 19.01 | 17.30 | 17.22 | 20.22 | 18.01 | 20.16 |
| OLS lags + age | 19.08 | 17.50 | 16.91 | 20.11 | 18.02 | 19.67 |
| OLS lags + age + mpg (+ starts) | 18.74–18.76 | 17.56–17.58 | 16.76–16.89 | 20.07–20.08 | 18.02–18.06 | 19.56–19.57 |
| **chosen: lags + age + ironman hinges** | **19.06** | **17.53** | **16.90** | **20.05** | **17.90** | **19.67** |

Bias (mean of predicted minus actual): the current rule is **+8.6 / +8.1 games** (all players, 2025 / 2026),
**+6.7 / +7.8** for mpg≥24 and **+9.5 / +11.9** for age≥30. For the chosen model the figures are
+1.8 / +1.4, -0.6 / +0.4 and -0.1 / +2.3. The league trend explains part of the gap: rotation
players averaged about 61 games over 2005-17 and about 56 over 2022-26. The fixed prior of 68 is
far above what is realised now.

Paired bootstrap of the RMSE gain over the current rule (2,000 resamples):
2025 all +1.94 ± 0.31, mpg≥24 +1.18 ± 0.44, age≥30 +2.56 ± 0.86;
2026 all +1.82 ± 0.32, mpg≥24 +1.88 ± 0.47, age≥30 +3.28 ± 0.78.
The MAE gain is small (+0.4 to +0.6 overall; -0.3 to +0.3 in the mpg≥24 tier), and that is expected.
The outcome is left-skewed (a few seasons are lost to long injuries), so a mean-unbiased model gives up
a little MAE against a median-like predictor. Valuation is linear in games, so the mean is the right
target.

Among the fitted variants the differences (≤0.4 RMSE) are within noise. The minutes term adds bias in
the mpg≥24 tier (+2.4 to +3.6), so it was dropped. The ironman hinges fix the top-end calibration:
without them, players predicted 68-72 games were under-predicted by 1.5 games. With them, the
residual means are within ±0.7 in every predicted-gp bucket.

### Chosen model (production fit, 82-game scale, cap 80)
```
gp = 27.50 + 0.289*g1 + 0.117*g2 + 0.068*g3          (g2 missing: +5.93, g3 missing: +4.20)
     - 0.946*max(0, age-28) + 0.746*max(0, age-32) + 0.547*max(0, 24-age)
     + 0.192*max(0, g1-70) + 0.381*max(0, mean(g1..g3)-70)
```
- Lag weights are about 0.29 / 0.12 / 0.07 per game, so roughly 50% of the deviation from the mean
  carries over. The old rule's weights were 5/3/2 with a prior worth one season, so about 67% carried over.
- Age: from 28 to 32 a player loses about 0.95 games per year. After 32 the net slope is only -0.2
  per year (survivor effect: players who are still in the league at 34+ are the durable ones). Young
  players (<24) gain about 0.55 games per year below 24.
- Deployment lags are calendar seasons 2025-26, 2024-25 and 2023-24. A player who missed 2025-26
  entirely is shifted so that his last played season is lag 1 (only such players with a manual
  override are projected at all).

### gp_sd (calibrated)
The residual sd depends on the predicted level and on nothing else that matters:

| predicted gp (bucket mean) | 42.7 | 49.3 | 55.2 | 60.3 | 65.2 | 70.1 |
|---|---|---|---|---|---|---|
| residual sd | 23.9 | 22.3 | 21.2 | 18.8 | 17.5 | 15.3 |

Within a predicted level, the ratio of realised to modelled sd is 0.96-1.02 across age buckets (≤24,
25-28, 29-31, 32-34, 35+) and across history-volatility buckets. So age and history are already
captured by the level. `gp_sd = interp(gp, knots)`. Test seasons: z-score sd 0.95 / 0.99 with the
new sd. The old `gp_sd` (mean about 9) was far too tight: z sd 2.2, and only 42-44% of outcomes
fell within ±1 sd. The distribution is skewed: the median residual is +4 to +7 and the 10th
percentile is -20 to -37.

### Manual gp values (`manual_gp_scale = 0.90`)
Explicit `gp` values in `player_overrides_2026_27.csv` (Haliburton 66, Tatum 66, Kessler 64,
VanVleet 64, Lillard 58, Kyrie 58) and the rookie priors (66-72) were set on the old scale. With
the fitted model, a literal reading of those values would lift those players relative to everyone
else. In an intermediate build Haliburton went to #4, Kyrie from 47 to 32, Lillard from 69 to 52
and VanVleet from 65 to 46. The old rule's realised/predicted ratio for rotation players is 0.87-0.92
(0.89 for 2025-26 pooled with 2024-25 at mpg≥24, 0.92 pooled over 2014-26), so manual gp values are
multiplied by 0.90. Set `Params(manual_gp_scale=1.0)` to take them literally. `games_missed` is
relative and is not rescaled. For reference, players who lost a whole season after a 1000-minute
season and later returned averaged 34-37 games in their return season (n = 117). That figure
includes players who were not ready on opening night, so the healthy-for-opener manual values are
plausible but optimistic.

## 2. Pricing gp uncertainty (`gp_risk_lambda`, default 0)
Implementation: availability uses `gp - λ·gp_sd` (clipped at 0) instead of `gp`.

Why the default is 0:
- H2H weekly value is additive across weeks. A player's contribution to expected category wins is
  linear in the games he plays, and missed games are already priced through
  `avail*own + (1-avail)*stream_recovery*replacement`. The expected-value objective needs E[gp],
  and that is what the fitted model provides (the old rule was 8 games optimistic, a much larger
  error than any risk premium).
- Variance mostly diversifies: injuries to the 13 rostered players are close to independent, and
  4 IL slots plus 6 adds per week let you replace an injured player's games.
- The non-diversifiable risk that matters for winning the title is a star missing the 2-3 playoff
  weeks. `playoff_weight` prices that directly, using a fitted multiplier. Adding λ on top would
  double count.
- There is no data to fit λ (it would need season-level title simulations with injury draws).
  Sensitivity: λ = 0.25 lowers the ADP rank correlation from 0.683 to 0.657 and mainly pushes
  injury-prone players down (Embiid 63→71, AD 51→58, Butler 263→322, Paul George 116→127).
  Use it as a manual risk-aversion knob: `fantasy values build --gp-risk-lambda 0.25`.

## 3. Playoff weeks (`playoff_weight`, default 0.30) and the fitted `playoff_mult`
`playoff_mult` (in the projections file) = expected availability in Yahoo weeks 18-20 divided by
season-average availability. It was fitted on 2010-11..2023-24 box scores (NocturneBear/NBA-Data-2010-2024
on GitHub, which includes DNP rows), using 82-game seasons and single-team players with ≥1000 minutes
the season before (n = 2,391). The window is the 3 weeks that end 7 days before the last
regular-season game (about Mar 15 – Apr 4 for an Apr 11 finish).

| group | season avail | window avail | ratio |
|---|---|---|---|
| all | 0.779 | 0.737 | 0.946 ± 0.007 |
| team prior-season win% ≤ .35 | 0.757 | 0.697 | 0.921 ± 0.018 |
| team prior-season win% > .65 | 0.796 | 0.767 | 0.964 ± 0.014 |
| prior season ≤ 50 games | 0.693 | 0.597 | 0.862 ± 0.042 |
| prior season > 75 games | 0.824 | 0.782 | 0.949 ± 0.009 |
| age 31-33 / 34+ | 0.716 / 0.747 | 0.690 / 0.705 | 0.965 / 0.944 (± 0.025) |

(The final week of the NBA season is worse still, at 0.664 overall and 0.467 for teams that finish
≤ .35, but it falls outside the fantasy playoffs.)

Regression (multiplicative, with bootstrap SEs clustered by season):
`mult = 0.958 - 0.077·(0.5 - team_prev_wpct) - 0.035·max(0, 60 - g1)/10`
(tank -0.077 ± 0.042, injury -0.035 ± 0.015). The age term was +0.003 ± 0.013 and was dropped.
The age-≥32 prior suggested in the task is **not supported**: older players miss more games in
total, but no more in the playoff window than in the rest of the season. Minutes ≥ 34 gave
-0.011 ± 0.010 per 4 min, not significant, and was dropped. The tank effect is larger in 2018-24
(-0.165) than in 2011-17 (-0.024). Tank proxy: the player's 2026-27 team's 2025-26 record
(`TEAM_RECORD_2025_26`). This proxy is noisy. Indiana, for example, went 19-63 without Haliburton.

The weight is a **prior, not fitted**:
`a_eff = a·((1-w)·r + w·m)`, with `r = (1 - s·m)/(1 - s)` and `s = 3/20` (the calendar share of
playoff weeks). `w = s = 0.15` reproduces the plain season average. The default `w = 0.30` counts a
playoff week twice as much as a regular-season week, because for a strong team the playoffs decide
the title. This changes availability by -3.5% (multiplier 0.80, e.g. AD) to -0.5% (contender,
healthy). Players with the biggest moves: AD 46→51, Kevin Durant 42→39, Brandon Miller 60→56.
`--playoff-weight 0.5` is the stronger, title-focused setting.

## 4. Open issues
- `stream_recovery = 0.5` is still unfitted. With 4 IL slots and 6 adds per week it may be too low.
  Raising it would reduce the penalty for every missed game, including the gp-model change.
- Playoff week dates are approximate (Yahoo's schedule was not verified).
- Rookies have no history, so they get `playoff_mult` without the injury term and a manual gp × 0.90.
