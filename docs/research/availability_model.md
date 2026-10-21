# Availability (games-played) model — research note (2026-10-18)

Code: `src/fantasy/model/availability.py` (fitted constants), `projections.py` (`Params.gp_model`,
`manual_gp_scale`, `playoff_mult` column), `lines.py` (`availability()`), `valuation.py`
(`ValuationParams.gp_risk_lambda`, `playoff_weight`). Versions: projections `baseline-0.2.2`
(meta `gp_model.version = gp-ols-0.2`, `playoff_mult.version = playoff-mult-0.2`).
Fit script (committed since gp-ols-0.2): `scripts/gp_model/` (`panel.py`, `fit_gp.py`, README, and
`report_gp-ols-0.2.json` = the exact run behind the constants). The gp-ols-0.1 scripts were never committed;
`fit_gp.py` reproduces the 0.1 coefficients to ±0.04 and its sample sizes exactly.

## 0. gp-ols-0.2 (2026-10-21) — what changed and why
Fixes the two issues of the 2026-10-21 subgroup check (§4). **Fitted** (bbref history, targets 2014-2026; strict
out of sample = every part fit on targets ≤ 2024, tested on 2025 and 2026). Residuals are actual − predicted, ± SE.

**Model** (`predict_gp`; 82-game scale, cap 80):
1. *Main* (≥ 500 min last season): the 0.1 spec plus a **role hinge** `-0.572·max(0, 30 − mpg1)` (± 0.057) and the
   young bonus `+0.674·max(0, 24 − age)` only when g1 < 70. Coefficients (HC1 SE): const 35.95 (2.94), g1 0.273 (0.035),
   g2 0.083 (0.023), m2 4.83, g3 0.039 (0.022), m3 3.86, age>28 −0.92 (0.25), age>32 +0.85 (0.53), g1>70 +0.10 (0.11),
   mean>70 +0.39 (0.13).
2. *Recent-era calibration*: `gp = raw − 1.44 − 0.16·max(0, raw − 65)`, fitted on **rolling-origin one-year-ahead forecast
   errors** 2019-2024 (season T forecast by a main fit on targets < T only): a = −1.44 ± 0.48, h = −0.16 ± 0.17.
   Forecasts ran high in most seasons since 2018 (per-year error −0.7 to −2.7, except 2024 +0.35; games per rotation
   player fell from ~64 in 2014-17 to ~58 in 2025-26). 2025 and 2026 are left out on purpose: a 0-game season only shows
   up once the player plays again, so those targets have almost no zeros (main 0.6% / 0.0% vs ~2% before; low-minute
   6.7% / 0.0% vs ~11%). Including them gives h = −0.30, and per-year h has been strongly negative since 2022
   (−0.64, −0.89, −0.15, −1.02, −1.45), so the top-end shrink may be too weak. **Recalibrate after 2026-27.**
3. *Low-minute / lost season* (< 500 min last season, or no season at all → g1 = mp1 = 0, lost = 1; replaces the extrapolation):
   `gp = 11.40 + 4.53·min(max(mp2, mp3), 3000)/1000 + 0.478·g1 + 0.634·mpg1 + 18.3·lost − 1.40·max(0, age − 28)
   + 7.21·max(0, 22 − age)` (SEs 1.62, 1.25, 0.048, 0.125, 4.8, 0.27, 1.71; n = 1,315 incl. 46 lost seasons; residual
   sd 23.2 = its gp_sd, slightly conservative vs OOS RMSE 20.9-21.2). mpg1 separates injured starters (few games, big
   minutes) from end-of-bench players, and `lost` covers starters returning from a whole lost season (the first version
   without these two terms under-predicted injured starters by +11 ± 3.7 and lost seasons by +5.6 ± 3.9; quant review).
   The age < 22 hinge is for prospects growing into a role (without it, players ≤ 21 were under-predicted by +9.0 ± 2.2,
   n = 133). Between 400 and 600 minutes the two models are blended linearly (no jump at 500).
4. `gp_sd` knots refit on the calibrated rolling-origin residuals: 22.3 / 22.3 / 18.8 / 17.7 / 17.5 / 17.3 at predicted
   45.9 / 51.4 / 55.4 / 58.9 / 62.3 / 67.0. A manual gp now takes the sd of its own level (before: max with the model sd).
5. Guard: `baseline()` refuses an incomplete latest season (median minutes < 300), which would send everyone to the
   low-minute model.

**Out of sample vs gp-ols-0.1** (`report_gp-ols-0.2.json`):

| | 2025 v0.1 | 2025 v0.2 | 2026 v0.1 | 2026 v0.2 |
|---|---|---|---|---|
| RMSE (≥ 500 min) | 19.06 | 18.58 | 20.05 | 19.93 |
| RMSE gain (paired bootstrap) | | **0.48 ± 0.21** | | 0.13 ± 0.19 |
| bias, all | −1.8 ± 1.0 | −0.6 ± 1.0 | −1.4 ± 1.1 | −0.3 ± 1.1 |
| top: 0.1 pred ≥ 65, ≥ 1000 min (n 41/38) | −3.4 ± 2.7 | −1.4 ± 2.9 | −6.5 ± 3.0 | −4.6 ± 2.9 |
| young durable: age < 24, g1 ≥ 70, ≥ 1500 min (n 21/18) | −9.0 ± 3.4 | −8.5 ± 3.5 | +0.4 ± 3.1 | +1.4 ± 3.1 |
| < 1000 min (n 79/90) | −6.5 ± 2.5 | −2.2 ± 2.4 | −0.8 ± 2.3 | +2.8 ± 2.3 |
| injured starter: mpg ≥ 28, g1 < 50 (n 11/21) | +7.4 ± 5.9 | +4.7 ± 5.9 | +6.5 ± 3.7 | +3.7 ± 3.6 |
| low-minute population RMSE (n 120/118) | 23.5 | 20.9 | 24.3 | 21.2 |
| low-minute RMSE gain | | 2.5 ± 1.2 | | 3.0 ± 1.2 |
| low-minute bias | −9.4 ± 2.0 | +3.0 ± 1.9 | −9.5 ± 2.1 | +3.2 ± 1.9 |
| low-minute, ≥ 1000 min in T-2/T-3 (n 22/26) | −1.8 ± 5.3 | +4.9 ± 5.0 | −6.7 ± 4.1 | +2.3 ± 3.8 |

Pooled over both test seasons the top bucket goes from −4.9 ± 2.0 to −2.9 ± 2.0. Over the 2019-2026 rolling forecasts
(more data, but these years also guided the specification search, so read them as in-sample-ish): injured starters
+5.2 ± 2.1 → +1.0, healthy bench players (mpg < 20, g1 ≥ 70) −4.2 ± 1.3 → −0.2, young durable −3.4 ± 1.3 → −2.4 after
calibration. About ten main-model variants were compared on those rolling residuals; RMSE differences among the
reasonable ones were ≤ 0.05, the role term was the only large effect (a total-minutes hinge did as well but drove the
g1 coefficient to 0.04, i.e. ignored injury-shortened seasons of starters, so the mpg form was chosen). Tested and
rejected: era dummies / linear trend in the main fit (fix the level, not the top), recency-weighted least squares,
a training window from 2017 or 2019, a top shrink fitted on in-sample residuals (≈ 0 by construction).

**How much to believe (quant review 2026-10-21).** The coefficients are out of sample, the specification is not: the
hypotheses, knots (30 mpg, 70 games, 65, 500 min) and ~10 variants were chosen on 2019-26 rolling residuals, which
include the test years. The rolling gain of 0.2 over 0.1 is positive in 8 of 8 seasons (0.08-0.52), 2025 is its best
year and 2026 is not significant. **Honest claim: a small gain of about 0.1-0.3 games RMSE** for ≥ 500-minute players,
and a large one (~2.5) for low-minute players. The biases in the table are flattered by censoring: at historical zero
rates the main model's 2025-26 bias is about −1.5, not −0.4, and the low model's about 0, not +3. The 0.1 baseline in the
low-minute rows applies 0.1 with g1 = 0 to the 4 lost-season test rows (0.1 shifted the lags instead). 2026-27 actuals
are the first clean prospective test (spec frozen).
**Still open:** the top bucket keeps a −3 game miss in pooled OOS (≈ 1.5 SE; see h above); young durable players are
noisy (2025: −8.5, 2026: +1.4). No ESPN blending (ESPN games are ~12 games optimistic).

**Effect (2026-10-21).** Top-150 ADP players: mean Δgp +1.1 (sd 1.6); largest: Dejounte Murray 38.5→45.5, Embiid
39.6→44.3, Morant 39.7→43.9; Day'Ron Sharpe 55.4→51.5, Edey 42.6→39.4, Ware 64.1→61.1. Unrostered Georges Niang (no
2025-26 season, override without gp) 65.9→31.0 by the lost-season rule. Season sim (simulated, 12k sims, seed 0, same
rosters file, before → after): my team P(playoffs) 91.2% → 89.0%, P(title) 32.3% → 30.6%, exp. rank 2.7 → 2.9;
player-games/week 46.7 → 46.5 while most rivals gain ~0.5 (the young-roster games edge narrows). Category ranks: ast
2→4, ft% 5→4, tov 7→6, others unchanged (`sim-20261021233550-a97895`).


## 1. Expected games played (gp-ols-0.1, superseded by §0; kept for the data definitions and history)

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

### gp_sd (calibrated; gp-ols-0.1 knots — see §0 for the 0.2 knots)
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
  3 IL slots (verified 2026-10-18; injured free agents can go straight into an IL slot) plus 6 adds
  per week let you replace an injured player's games.
- The non-diversifiable risk that matters for winning the title is a star missing the 2-3 playoff
  weeks. `playoff_weight` prices that directly, using a fitted multiplier. Adding λ on top would
  double count.
- There is no data to fit λ (it would need season-level title simulations with injury draws).
  Sensitivity: λ = 0.25 lowers the ADP rank correlation from 0.683 to 0.657 and mainly pushes
  injury-prone players down (Embiid 63→71, AD 51→58, Butler 263→322, Paul George 116→127).
  Use it as a manual risk-aversion knob: `fantasy values build --gp-risk-lambda 0.25`.

## 3. Playoff weeks (`playoff_weight`, default 0.30) and the fitted `playoff_mult`
`playoff_mult` (in the projections file) = expected availability in the league's fantasy playoff weeks
divided by season-average availability. **League 44495's playoffs are weeks 18-20 ending Sun
2027-03-21** (Yahoo settings page, verified 2026-10-18), i.e. Mar 1-21, ending **21 days before** the
NBA regular season ends (Apr 11, 2027). Fit (`playoff-mult-0.2`): 2010-11..2023-24 box scores
(NocturneBear/NBA-Data-2010-2024 on GitHub, includes DNP rows), 82-game seasons, single-team players
with ≥1000 minutes the season before (n = 2,339 player-seasons, 11 seasons). Window per season = the
21 days ending 21 days before that season's last regular-season day (≈ Mar 1-21 for an Apr 11
finish). Multiplicative OLS `p_window = p_season·(c + b_tank·(0.5 - team_prev_wpct) + b_inj·max(0, 60 - g1)/10)`,
season-cluster bootstrap SEs (2,000 resamples). Script: scratchpad `settings/playoff_refit.py`
(reproduces the old fit exactly when the window offset is set back to 7 days).

| window (offset before season end) | ratio window/season | const | tank | injury |
|---|---|---|---|---|
| 7 days (old, ≈ Mar 15 - Apr 4; `playoff-mult-0.1`) | 0.946 ± 0.007 | 0.958 ± 0.007 | -0.077 ± 0.045 | -0.035 ± 0.015 |
| 14 days | 0.962 ± 0.006 | 0.971 ± 0.007 | -0.030 ± 0.034 | +0.005 ± 0.016 |
| **21 days (league window, ≈ Mar 1-21)** | **0.974 ± 0.006** | 0.982 ± 0.006 | -0.023 ± 0.034 | +0.018 ± 0.014 |
| 28 days | 0.989 ± 0.006 | 0.996 ± 0.004 | -0.013 ± 0.029 | +0.016 ± 0.016 |

On the league window: prior-season win% ≤ .35 → ratio 0.962, > .65 → 0.972; prior season ≤ 50 games →
0.994 (n = 104). The injury-history term has an unstable sign across eras (2011-17: -0.005, 2018-24:
+0.035) and is dropped; the tank term is not significant but keeps its plausible sign (2011-17 -0.027,
2018-24 -0.012). **Chosen:** `mult = 0.984 (± 0.005) - 0.019 (± 0.034)·(0.5 - team_prev_wpct)` → 0.978-0.989
for every player (was 0.75-0.98). The late-season effect the old fit found (tanking teams and
injury-prone players sitting) lives mostly in the last 3 weeks before the season ends, which are
*after* this league's fantasy season (NBA games Mar 22 - Apr 11 do not count at all).

Effect on values (`fantasy values build`, defaults): rank Spearman vs before 0.99993; 30 of the top 60
move, by at most 8 places: Walker Kessler 45→37, Anthony Davis 51→46, Tyler Herro 62→57 (enters the
top 60), Derik Queen 61→59; Brandon Miller 56→60, Durant 39→42, Pritchard 40→43, Avdija 59→62 and
VanVleet 60→63 (leave the top 60). The top 15 is unchanged.

The weight is a **prior, not fitted**:
`a_eff = a·((1-w)·r + w·m)`, with `r = (1 - s·m)/(1 - s)` and `s = 3/20` (the calendar share of
playoff weeks). `w = s = 0.15` reproduces the plain season average. The default `w = 0.30` counts a
playoff week twice as much as a regular-season week, because for a strong team the playoffs decide
the title. With `playoff-mult-0.1` this changed availability by -3.5% (multiplier 0.80, e.g. AD) to
-0.5% (contender, healthy); with the refit multipliers (0.978-0.989) the tilt is -0.3% to -0.2% for
everyone, i.e. `playoff_weight` now barely matters. `--playoff-weight 0.5` is the stronger,
title-focused setting. (`s = 3/20` counts matchups; by days the playoffs are 21 of the 153 fantasy-season
days, 0.137 — the difference is immaterial at these multipliers.)

## 4. Open issues
- `stream_recovery = 0.5` is still unfitted. It should reflect the verified roster rules: **3 IL slots**
  (not 4 as previously assumed; no IL+), injured free agents/waiver players can be added directly into
  an IL slot, 6 adds per week, no season cap, daily lineups (effective today). One fewer IL slot means a
  team with 4+ simultaneously injured players must use a bench spot (or drop), so recovery is slightly
  lower than the 4-IL reasoning suggested; 6 weekly adds with daily lineups still argue it may be above
  0.5. The default is left at 0.5 until it is fitted (in-season transaction logs / the season simulator's
  streaming model).
- Playoff weeks are verified (18-20, ending Sun 2027-03-21 → Mar 1-21); the dates of the other weeks
  are derived (see `docs/research/season_simulator.md`).
- Rookies have no history; with `playoff-mult-0.2` the injury term is 0 for everyone anyway. Rookie gp is a manual prior × 0.90.
- **Subgroup residual check (2026-10-21, quant-reviewer; near in-sample: targets 2025-26 are inside the production fit;
  actual − predicted games, ± SE).** All players with ≥500 min the season before: −1.4 ± 0.7 (n=687). "One bad injury
  year" (g1 < 40, g2 ≥ 65): −3.7 ± 5.6 (n=18), so these players are NOT under-predicted. Players with < 500 min last
  season and ≥ 1000 the year before (Murray/Jerome-like; outside the fit population, so the prediction extrapolates):
  −7.6 ± 3.9 (n=29); comparable players actually played ~37-44 games. Age shows no bias (28-31: −1.2 ± 1.5; 32+: −0.8 ± 2.1;
  < 24: −1.1 ± 1.6). **Mild optimism at the top:** predictions ≥ 65 with ≥ 1000 min: −3.5 ± 1.9 (n=75); age < 24 with
  g1 ≥ 70 and ≥ 1500 min: −4.5 ± 2.4 (n=39). ESPN's games are ~12 games optimistic for everyone and 5-10 more for
  injury-history players, so ESPN's smaller gap between healthy and injury-prone rosters is its bias, not information:
  **don't blend ESPN gp in.**
  **Done in gp-ols-0.2 (§0)**: recent-era calibration incl. a top-end shrink, role hinge, and a dedicated low-minute /
  lost-season model, validated out of sample on 2024-25 and 2025-26.
- **Reading team category ranks (League page):** they rank simulated weekly *volume* (started games × per-game stats), so
  projected games matter as much as per-game quality. Example 2026-10-21: Thomas's team is 11th of 12 in summed pts/g but
  12th in projected games (690 vs 822 for us) → 12th in points; our team is ~7th per game but 1st in games.
