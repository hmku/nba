# Season-simulator calibration: projection error, game noise, opponents, injuries (2026-10-18)

Question (user): are the season simulator's priors evidence-based? Before this note they were not. They were
`vol_sd = stat_sd = 0.08`, `fg_pct_sd = 0.015`, `ft_pct_sd = 0.03`, `GAME_CV` per stat, and the injury-spell
structure (`rest_share = 0.25`, `two_spell_prob = 0.5`). The simulator also had no within-game stat
correlation and no pace/opponent effects. This note fits each of them on historical data, out of sample where
that matters, with standard errors.

**Outcome:** the simulator (`season-sim-0.4.0`) now uses fitted values (`src/fantasy/sim/calibration.py`). The
draft model is **unchanged**. A refitted `GAME_CV` exists only as an opt-in (`FANTASY_GAME_CV=fitted`) and does not
change the picks at 1, 24 or 36 (section 6).

## Data (provenance)
- **Box scores:** NocturneBear/NBA-Data-2010-2024,
  `https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024/main/regular_season_box_scores_2010_2024_part_{1,2,3}.csv`.
  These are the regular seasons 2010-11 to 2023-24: 424,478 player-game rows (including DNP/DND/NWT rows) and
  16,658 games. They were cached by the availability study (scratchpad `gp/raw_box/`) and are not committed.
- **Season totals:** the sumitrodatta Basketball-Reference mirror, `Player Totals.csv` (1947-2026), from
  `https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/`. This is the same source
  as `data/raw/bbref_sumitrodatta/`; the full history is in the scratchpad.
- **Scripts** (scratchpad `calib/`, not committed, in the same way as the availability study):
  - `prep.py` builds the panels.
  - `proj_backtest.py`: part 1.
  - `game_noise.py`, `corr_team.py`: part 2.
  - `team_effects.py`: part 3.
  - `spells.py`: part 4.
  - `sim_effect.py`, `corr_check.py`, `pre_cmp.py`: effects in the simulator.
  - `mock_cv.py`, `val_cmp.py`: the draft experiment.
- **Populations:**
  - "Rostered" means ≥ 24 minutes per game, which is roughly the 156 players a 12 × 13 league rosters.
  - Game-level work uses player-season-team units with ≥ 30 games played; traded players are split by team.

## 1. Season-level projection error (out of sample)
**Method.** For each target season T = 2014…2024, I re-ran the baseline projection formulas of
`fantasy.model.projections` on seasons T-1…T-3 only: 5/3/2 weights, 400-minute prior toward position means × 0.85,
the age curve, recency-weighted minutes per game with shrinkage and growth. Overlays were left out. The result
was compared with the realised per-game stats of season T (players with ≥ 20 games and ≥ 15 projected mpg). The
residual for each stat is r = log(realised / projected).

The sampling noise of a g-game mean was subtracted, using the game-level variance function and correlations
from part 2. A one-factor model with equal loadings was then fitted to the 8 factor stats. It is exactly the
simulator's form:
- the common factor variance is the mean off-diagonal covariance;
- the stat-specific variance is the diagonal minus that.

For FG% and FT%, the realised − projected shift was taken, minus binomial noise. SEs come from a player-cluster
bootstrap (200 resamples). Seasons 2025-2026 were held out.

| parameter | prior (0.3.0) | rostered ≥ 24 mpg (n = 1828) | 30+ mpg (918) | 24-30 (910) | 15-24 (1319) | hold-out 2025-26, all tiers (583) vs same tiers 2014-24 (3147) |
|---|---|---|---|---|---|---|
| `vol_sd` | 0.08 | **0.207 (0.006)** | 0.153 (0.008) | 0.247 (0.008) | 0.330 (0.009) | 0.266 (0.012) vs 0.265 (0.006) |
| `stat_sd`, mean of 8 | 0.08 | 0.212 (0.004) | 0.207 (0.007) | 0.217 (0.004) | 0.247 (0.005) | 0.213 (0.007) vs 0.227 (0.003) |
| `fg_pct_sd` | 0.015 | **0.025 (0.001)** | 0.022 (0.001) | 0.027 (0.001) | 0.033 (0.002) | 0.030 (0.003) vs 0.028 (0.001) |
| `ft_pct_sd` (≥ 50 FTA) | 0.03 | **0.039 (0.002)** | 0.038 (0.003) | 0.041 (0.002) | 0.049 (0.003) | 0.038 (0.003) vs 0.043 (0.002) |

Stat-specific sds (rostered): FGA 0.153 (0.006), FTA 0.284 (0.008), 3PM 0.366 (0.019), REB 0.053 (0.019),
AST 0.236 (0.007), STL 0.061 (0.024), BLK 0.181 (0.013), TO 0.163 (0.008).

- **All three priors were too small, by a factor of 1.3 to 2.6.** The fitted errors are stable out of sample:
  the 2025-26 hold-out matches the 2014-24 fit.
- **Error falls with minutes.** Stars (30+ mpg) have half the volume error of 15-24 mpg players.
- **The volume factor is mostly minutes.** The volume residual correlates 0.87 (0.01) with the log minutes
  residual (sd 0.186).
- **One factor carries 59% (1.5%) of the residual variance.** REB and STL ride almost entirely on volume; 3PM,
  FTA and AST have large stat-specific errors.
- **Bias, not used by the simulator.** Realised per-game volume is 5-10% below projection on average
  (minutes −5.7% (0.5%) for rostered players). That is the known optimism of the baseline method
  (`optimism_decomposition.md`). The simulator stays mean-neutral, so a bias correction is a projections
  question, not a simulator one.
- **Caveat.** This backtests the *baseline* method. The adopted 50/50 ours/ESPN blend has about 5-7% lower
  per-game RMSE (`projection_quality.md`), so the fitted sds overstate the blend's error by roughly that much
  (that adjustment is an estimate, not a fit).

**Adopted:**
- `vol_sd`, `fg_pct_sd` and `ft_pct_sd` by each player's projected mpg tier (`calibration.ERROR_BY_MPG`; the
  pooled rostered values are used when projections lack mpg).
- Per-stat `stat_sds` (rostered fit).
- Scalars still override (`SimParams(vol_sd=0.08, stat_sd=0.08, ...)` reproduces 0.3.0).
- The 15-24 tier is applied to players under 15 mpg, an extrapolation; they are rarely rostered.

## 2. Game-to-game noise and within-game correlation
**CV conditional on playing** (pooled CV = sqrt(Σ var / Σ mean²); player-cluster bootstrap SE):

| stat | GAME_CV prior | 30+ mpg | 24-30 | 15-24 | rostered ≥ 24 (SE) | var = a·m + b·m² (rostered): a, b |
|---|---|---|---|---|---|---|
| FGM | 0.38 | 0.400 | 0.536 | 0.714 | 0.442 (0.004) | 1.38, −0.032 |
| FGA | 0.30 | 0.285 | 0.382 | 0.532 | 0.316 (0.003) | 1.43, −0.010 |
| FTM | 0.60 | 0.682 | 0.964 | 1.214 | 0.746 (0.014) | 1.97, −0.001 |
| FTA | 0.55 | 0.640 | 0.895 | 1.116 | 0.702 (0.014) | 2.20, −0.009 |
| 3PM | 0.75 | 0.720 | 0.834 | 1.040 | 0.761 (0.011) | 1.15, −0.004 |
| PTS | 0.35 | 0.374 | 0.515 | 0.688 | 0.416 (0.005) | 3.34, −0.028 |
| REB | 0.42 | 0.420 | 0.493 | 0.628 | 0.449 (0.007) | 1.26, +0.014 |
| AST | 0.48 | 0.467 | 0.627 | 0.809 | 0.512 (0.010) | 1.24, −0.002 |
| STL | 0.85 | 0.917 | 1.065 | 1.273 | 0.972 (0.010) | 1.04, +0.022 |
| BLK | 0.95 | 1.028 | 1.049 | 1.261 | 1.038 (0.027) | 1.05, +0.065 |
| TO | 0.58 | 0.632 | 0.790 | 0.987 | 0.680 (0.014) | 1.05, −0.020 |

- **GAME_CV is about right for 30+ mpg stars and too small for everyone else.** The reason is that variance is
  close to *proportional to the mean* (b ≈ 0), not to the mean squared:
  - STL and BLK are Poisson (a ≈ 1.04);
  - FGA, REB and AST are mildly over-dispersed (a ≈ 1.25-1.4);
  - FTA is a ≈ 2.2, because free throws come in pairs.
  - So the CV is larger for lower-volume players. A constant CV is the wrong functional form.
- **Adopted for the simulator:** `game_var="fitted"` uses var = a·m + b·m² per player (`calibration.GAME_VAR`).
  `game_var="cv"` restores `GAME_CV`.

**Within-player game-level correlation** (player-season demeaned, rostered; player-bootstrap SE ≤ 0.010).
- The only large correlations are the mechanical ones: FGM-FGA 0.76, FTM-FTA 0.94, PTS-FGM 0.93, 3PM-FGM 0.49.
- Across categories they are small: REB-FGA 0.32, REB-AST 0.17, AST-STL 0.10, REB-TO 0.12, BLK-anything ≤ 0.09.
- **Minutes shock.** A single per-game minutes shock explains R² = 0.33 of FGA, 0.16 of REB, 0.09 of AST, 0.07 of
  FTA, 0.06 of 3PM, 0.03 of STL and TO, and 0.02 of BLK. Adding a usage shock (FGA + 0.44 FTA + TO) raises FGA to
  0.84 and PTS to 0.57.
- After partialling out minutes, the cross-category correlations are ≤ 0.07. So the cross-category
  correlation that exists is a minutes/role shock.
- The first PC of the 8 independent stats explains only 23% (equal shares would be 12.5%).

**For the simulator** I used the *covariance-pooled* correlation of the 10 noise variables (`calibration.NOISE_CORR`):
- the variables are FGA, makes | FGA, FTA, makes | FTA, 3PM, REB, AST, STL, BLK, TO;
- 150,100 player-games, season-bootstrap SE ≤ 0.009;
- "covariance-pooled" is what a team's weekly sum of independent players inherits.

Examples: FGA-REB 0.32, FGA-3PM 0.36, (FG makes | FGA)-3PM 0.39, REB-AST 0.16, REB-TO 0.12, REB-BLK 0.12. The
empirical team-week correlation *given each player's games* (13-player random teams, 38,033 team-weeks; SE ≤ 0.017)
has a mean of 0.14 over the 21 counting-category pairs. The simulator gives 0.06 with independent noise and 0.12
with `noise_corr=True`. Adopted: `noise_corr=True`.

## 3. Opponent, pace, home, rest and blowout effects
**Method:**
- Per-game deviations from the player-season mean, rostered players, 150,100 player-games.
- The opponent effect is an **out-of-fold** (2-fold by game id), empirical-Bayes-shrunk opponent-team-season
  mean. This prevents leakage: in-sample opponent fixed effects would overfit with about 400 groups.
- Positional matchup is tested as an out-of-fold opponent × position-group (G/F/C) increment *over* the
  opponent-team effect.
- Pace is either the opponent's out-of-fold season possessions (ex-ante) or the realised game possessions
  (ex-post).
- Home, back-to-back and 3+ days of rest are regressors, as is a blowout indicator (|margin| ≥ 20).
- R² is out of sample for the opponent terms. SEs come from a season-cluster bootstrap (14 seasons, 100 resamples).

**Per game, % of variance explained (SE):**

| | PTS | REB | AST | STL | BLK | TO | 3PM | FG impact | FT impact |
|---|---|---|---|---|---|---|---|---|---|
| opponent team-season | 0.28 (0.02) | 0.27 (0.04) | 0.53 (0.10) | 0.27 (0.05) | 0.59 (0.10) | 0.60 (0.12) | 0.33 (0.04) | 0.45 (0.07) | 0.00 |
| + positional increment over team | 0.05 (0.04) | −0.07 (0.07) | 0.15 (0.13) | 0.09 (0.07) | −0.25 (0.17) | 0.05 (0.14) | 0.16 (0.05) | 0.09 (0.09) | −0.00 |
| opponent pace (ex-ante) | 0.25 | 0.13 | 0.13 | 0.12 | 0.06 | 0.03 | 0.04 | 0.01 | 0.00 |
| game pace (realised, not knowable in advance) | 0.80 | 0.66 | 0.19 | 0.68 | 0.23 | 0.83 | 0.04 | 0.00 | 0.00 |
| home + rest | 0.07 | 0.07 | 0.15 | 0.01 | 0.10 | 0.02 | 0.03 | 0.15 | 0.01 |
| blowout (realised) | 0.90 | 0.86 | 0.19 | 0.08 | 0.14 | 0.31 | 0.08 | 0.05 | 0.01 |

**Effect sizes** (per game; regression with all context terms together):
- Home: +0.32 (0.04) PTS, +0.13 REB, +0.15 AST.
- Back-to-back: −0.05 REB, −0.05 FG impact.
- One extra possession of opponent pace: +0.07 PTS.
- Blowout: −3.9 (0.05) minutes and −1.7 PTS.

**Aggregated to fantasy horizons.** These are shares of the variance of the weekly deviation from expectation
(Monday-Sunday weeks; zero-game weeks included; "games" is the games-count component relative to the player's
season availability, given the known schedule):

| % of weekly variance | PTS | REB | AST | STL | BLK | TO | 3PM | FG% impact | FT% impact |
|---|---|---|---|---|---|---|---|---|---|
| player-week: opponent | 0.08 (0.03) | 0.07 (0.02) | 0.20 (0.04) | 0.08 (0.05) | 0.21 (0.07) | 0.29 (0.06) | 0.12 (0.03) | 0.44 (0.07) | 0.01 |
| player-week: games played | 57 (2) | 52 (2) | 47 (2) | 21 (1) | 17 (1) | 34 (1) | 30 (2) | – | – |
| 13-player team-week: opponent | −0.04 (0.06) | −0.06 (0.05) | 0.05 (0.09) | −0.11 (0.08) | −0.06 (0.11) | 0.04 (0.11) | 0.09 (0.06) | 0.18 (0.12) | 0.01 |
| team-week: + positional over team | −0.4 (0.2) | −0.4 (0.2) | −0.5 (0.3) | −0.4 (0.1) | −1.4 (0.3) | −0.4 (0.3) | 0.0 (0.1) | −0.3 (0.2) | −0.0 |
| team-week: games played | 52 (2) | 53 (2) | 40 (2) | 22 (2) | 18 (1) | 36 (2) | 27 (2) | – | – |

**Conclusions:**
- **The opponent explains under 0.5% of a player's weekly variance and about 0% (|share| ≤ 0.2%, within 2 SE of 0)
  of a fantasy team's weekly category totals.** The team totals sum 13 players with unrelated opponents.
- **Ex-ante pace is just as negligible.**
- **Games played explains 18-53% of team-week variance; the rest is game-to-game noise.**
- **Positional matchups ("bad against centres") add nothing beyond team defence.** Out of sample they *lower*
  R², which is the signature of overfitting noise.
- The simulator keeps no opponent or pace terms. This is now a fitted conclusion, not an omission.

## 4. Injury and absence spell structure
Rotation players: ≥ 20 mpg and ≥ 20 games, one team, excluding the 2012, 2020 and 2021 seasons. That gives 2,306
player-seasons. "Missed" means any team game without minutes (injury, rest, coach's decision); it is what matters
for fantasy. Season-cluster bootstrap SEs. The simulator's absence model was applied to the *same* player-seasons
(same missed counts) with a grid of priors.

| statistic | real (SE) | sim, rest 0.25 / two-spell 0.5 (prior, kept) | sim 0.1 / 0.5 | sim 0.4 / 0.5 |
|---|---|---|---|---|
| share of missed games in 1-game runs | 0.136 (0.005) | 0.200 | 0.099 | 0.289 |
| share in runs ≤ 4 | 0.342 (0.010) | 0.335 | 0.184 | 0.488 |
| share in runs ≥ 10 | 0.475 (0.015) | 0.493 | 0.630 | 0.338 |
| absence runs per season | 3.46 (0.20) | 3.98 | 2.47 | 5.42 |
| P(≥ 2 long (≥ 5) spells \| ≥ 1) | 0.30 (0.014) | 0.24 | 0.28 | 0.21 |
| weekly missed-games variance | 0.846 (0.039) | 0.788 | 0.932 | 0.654 |
| … ratio to iid misses | 2.37 (0.02) | 2.22 | 2.62 | 1.84 |

The priors `rest_share = 0.25` and `two_spell_prob = 0.5` reproduce the run-length distribution and the weekly
missed-games variance within about 1.5 SE. The fantasy-relevant number is the weekly variance: 0.79 vs 0.85. A
slightly lower `rest_share` (about 0.2) would match the weekly variance better, but the improvement is inside the
noise, so the **priors are kept and are now validated**. By missed-games band:
- players who miss 1-4 games: 53% of missed games are single games;
- players who miss 20-39 games: 79% of missed games are in runs of 5+, and 49% of these players have 2+ long
  spells.

## 5. Effect on simulator outputs
**Fixed leagues.** Three ADP-snake leagues, 2,000 sims each. The favourite's edge is the mean |P(win weekly
matchup) − 0.5|; seasons are measured by the sd of season category wins.

| variant | edge, league 0 / 1 / 2 | sd(season cat wins), league 0 |
|---|---|---|
| 0.3.0 priors | 0.104 / 0.101 / 0.091 | 12.05 |
| + fitted season error (pooled 0.21) | 0.095 / 0.095 / 0.084 | 12.93 |
| + fitted season error (tiered by mpg) | 0.096 / 0.094 / 0.085 | 12.77 |
| + fitted game variance | 0.095 / 0.093 / 0.085 | 12.72 |
| + noise correlation (= 0.4.0 default) | 0.093 / 0.091 / 0.083 | 12.75 |

The calibration pulls weekly matchup probabilities about 10% closer to 50/50:
- about 8 points of that come from the larger season-level projection error;
- about 1 point comes from the variance function;
- about 2 points come from the within-game correlation. Correlated categories move together, so the weekly
  category count is more volatile and favourites win slightly less often. The sign is the same in all 3 leagues.

P(playoffs) changes by less than 3 pp in these leagues. That is within the run-to-run noise (SE about 0.011).

**Pre-draft projection** (slot 1, 10 simulated drafts × 200 seasons, same drafts and seeds):
| variant | my P(playoffs) (SE) | P(bye) | P(final) | P(title) (SE) | expected rank |
|---|---|---|---|---|---|
| 0.3.0 priors | 0.954 (0.011) | 0.782 | 0.651 | 0.465 (0.018) | 1.99 |
| fitted, pooled season error | 0.938 (0.013) | 0.720 | 0.586 | 0.391 (0.017) | 2.33 |
| **0.4.0 default** (tiered error, fitted variance, correlated noise) | 0.939 (0.013) | 0.717 | 0.583 | 0.390 (0.020) | 2.31 |
| 0.4.0 with independent noise | 0.937 (0.014) | 0.723 | 0.594 | 0.397 (0.018) | 2.28 |

My pre-draft P(title) falls by about 7.5 pp (0.465 → 0.390) and P(bye) by 6 pp, while P(playoffs) barely moves.
More honest season-level uncertainty shrinks the in-sample edge the optimizer's roster has on paper. This is
a fitted correction to part of the optimism that `optimism_decomposition.md` describes. SEs are clustered by
simulated league; the variants share drafts and seeds, so the differences are paired.

**What cannot be validated yet:** the absolute calibration of P(win matchup). It needs real fantasy matchup outcomes
(in-season, T-016). The components that feed it are each fitted above.

## 6. Draft model: fitted GAME_CV as an opt-in (not adopted)
`GAME_CV` also sets the draft valuation's G-score week-to-week variance and the optimizer's category model. Under
the draft-path freeze, the default is unchanged (`GAME_CV_PRIOR`). `FANTASY_GAME_CV=fitted` switches to the pooled
rostered CVs (`GAME_CV_FITTED`; read at import, recorded in valuation params). Effect on the adopted blend
projections:
- **Valuation:**
  - The top 60 is the same set of players. Spearman 0.997; mean |rank move| 0.8; max 4.
  - The moves: Lillard 57 → 53, Booker 34 → 31, Ware 29 → 32, Sarr 54 → 57.
  - The top 10 is identical.
- **Mock draft:** fresh SQLite, `mock-cv-*`, slot 1, 3RR; opponents by the same prior-valuation market order.
  - Pick 1: Jokić in both runs. Fitted 5.981 vs prior 6.074 expected category wins; the scale is lower because
    every week is noisier.
  - Pick 24: Kawhi Leonard in both (5.983 vs 6.067).
  - Pick 36: Derrick White in both (5.968 vs 6.040).
  - Only the order of near-ties below the top choice changes: Holmgren and Duren swap with D. White at 24, and Bane
    with Kyrie at 36.
- **Recommendation:** it is not material to the draft. Leave the default until after the draft. Adopt the fitted CV
  (or better, the variance function) in-season, when the category model is next versioned.

## 7. What changed (`season-sim-0.4.0`)
- `src/fantasy/sim/calibration.py`: the fitted constants with SEs.
- `SimParams` defaults: `vol_sd`, `fg_pct_sd`, `ft_pct_sd` = `None` (by mpg tier), `stat_sd = None` with per-stat
  `stat_sds`, `game_var = "fitted"`, `noise_corr = True`. To reproduce 0.3.0, set
  `vol_sd=0.08, stat_sd=0.08, fg_pct_sd=0.015, ft_pct_sd=0.03, game_var="cv", noise_corr=False`.
- `rest_share` and `two_spell_prob` are unchanged (validated in section 4).
- `fantasy.model.lines`: `GAME_CV_PRIOR` (default), `GAME_CV_FITTED`, and the `FANTASY_GAME_CV` switch. The draft
  default is unchanged.
- `sim/symmetric.py` `PERTURB_DEFAULT` (opponents' private projection views) is unchanged. It models how far
  *opponents' beliefs* diverge from ours, not the truth's divergence. Opponents' errors are correlated with ours
  (same public data), so the fitted truth error would overstate it.
- Tests: `tests/test_sim_calibration.py`.

## Limitations
- The season error is fitted to the baseline method, not the blend (section 1 caveat). Rookies and players with
  less than one prior season are excluded from the fit; the simulator gives them their mpg tier's error, which
  is probably too small for rookies.
- The equal-loading one-factor form is what the simulator implements. The free-loading first PC shows FGA and AST
  load more than REB, STL and BLK.
- Correlations and variances were pooled over 2010-24. The league's 3-point volume rose over the period, and
  3PM noise was not modelled by era.
- Absence structure: DNP-coach's-decision games count as missed, which is right for fantasy. Mid-season trades
  and 10-day contracts are excluded.
