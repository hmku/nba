# Season-simulator calibration: projection error, game noise, opponents, injuries (2026-10-18)

Question (user): are the season simulator's priors evidence-based? Before this note they were not. They were
`vol_sd = stat_sd = 0.08`, `fg_pct_sd = 0.015`, `ft_pct_sd = 0.03`, `GAME_CV` per stat, and the injury-spell
structure (`rest_share = 0.25`, `two_spell_prob = 0.5`). The simulator also had no within-game stat
correlation and no pace/opponent effects. This note fits each of them on historical data, out of sample where
that matters, with standard errors.

**Outcome:** the simulator (`season-sim-0.4.0`) now uses fitted values (`src/fantasy/sim/calibration.py`).
**Since 2026-10-19 there is one game-to-game noise model** for the draft and the simulator: the fitted variance
function var(stat, m) = a·m + b·m² in `src/fantasy/model/noise.py` (valuation-0.3.0, optimizer-0.3.0,
season-sim-0.5.0). It beats a constant CV out of sample (section 8). The constant-CV models (`GAME_CV_PRIOR`,
`GAME_CV_FITTED`, `GAME_CV_POOLED`) and the `FANTASY_GAME_CV` switch were deleted; git history keeps their values
(and sections 2, 5 and 6 below quote them).

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
- **Adopted for the simulator** (0.4.0) and, since 2026-10-19, for the draft too: var = a·m + b·m² per player,
  now in `fantasy.model.noise.GAME_VAR` (section 8). The `game_var="cv"` option was deleted.

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

**What this did not validate: spell timing.** The comparison above is on spell *lengths* and weekly variance with the
same missed counts. *Where* the spells fall in the season was not checked, and the 0.2.0-0.6.0 placement (min/max of
two uniforms on the NBA-season axis) made availability U-shaped and 1-12% below gp/82 in the fantasy window. Fixed in
season-sim-0.7.0 (circular placement on the fantasy window: flat at gp/82 in every week, realised missed share = q;
before/after table in `season_simulator.md`, "Availability timing fix"; regression test
`test_availability_flat_at_gp_share_every_week`). Run lengths are unchanged by the fix (the same length draws).

## 5. Effect on simulator outputs
_Note: every number in this section was computed with the prior constant CV in the draft model (the default at
the time). The constant-CV code was deleted on 2026-10-19; reproduce these tables from git history (commit a6079da
or earlier, `FANTASY_GAME_CV=prior`). Current numbers: section 8._
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

## 6. Draft model: fitted GAME_CV (adopted 2026-10-18, valuation-0.2.3; superseded 2026-10-19 by section 8)
_Historical: the constant-CV draft model described here was replaced by the variance function on 2026-10-19._

**Update:** adopted as the draft default by user decision on 2026-10-18 (before the draft-path freeze):
valuation-0.2.3 uses `GAME_CV_FITTED`, and `FANTASY_GAME_CV=prior` reverts (read at import; only `""`, `fitted`
or `prior` are accepted). Picks 1/24/36 are unchanged; an independent review reproduced this (same top-60 set,
Spearman 0.997). Valuation, season-sim and draft-sim runs record the source as `game_cv` in their params.

`GAME_CV` also sets the draft valuation's G-score week-to-week variance and the optimizer's category model. The
analysis below was run while the fitted CV was still opt-in, comparing it with `GAME_CV_PRIOR` on the adopted
blend projections:
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
- **Recommendation at the time:** not material to the draft, so leave the default until after the draft. The user
  chose to adopt it before the draft instead (see the update above). A later step is to replace the CV with the
  fitted variance function in-season, when the category model is next versioned.

## 7. What changed (`season-sim-0.4.0`)
- `src/fantasy/sim/calibration.py`: the fitted constants with SEs.
- `SimParams` defaults: `vol_sd`, `fg_pct_sd`, `ft_pct_sd` = `None` (by mpg tier), `stat_sd = None` with per-stat
  `stat_sds`, `game_var = "fitted"`, `noise_corr = True`. To reproduce 0.3.0, check out a6079da or earlier and
  set `vol_sd=0.08, stat_sd=0.08, fg_pct_sd=0.015, ft_pct_sd=0.03, game_var="cv", noise_corr=False` with
  `FANTASY_GAME_CV=prior` (the `game_var` field and the CV constants no longer exist since season-sim-0.5.0).
- `rest_share` and `two_spell_prob` are unchanged (validated in section 4).
- `fantasy.model.lines`: `GAME_CV_PRIOR`, `GAME_CV_FITTED` (draft default since valuation-0.2.3), and the
  `FANTASY_GAME_CV=prior` switch (all deleted in section 8's change).
- `sim/symmetric.py` `PERTURB_DEFAULT` (opponents' private projection views) is unchanged. It models how far
  *opponents' beliefs* diverge from ours, not the truth's divergence. Opponents' errors are correlated with ours
  (same public data), so the fitted truth error would overstate it.
- Tests: `tests/test_sim_calibration.py`.

## 8. Unified noise model (2026-10-19)
**Decision (user request):** one game-to-game noise model for the draft and the simulator, the best-supported one,
with the alternatives deleted. It is the fitted variance function

    var(stat, m) = a_stat·m + b_stat·m²   (per game, given the player plays; clipped at 0)

with the section 2 coefficients, now in `src/fantasy/model/noise.py` (`NOISE_MODEL_VERSION = "noise-var-1.0"`).
The API is `game_var(stat, mean)` (vectorised), `game_var_matrix(means)` (last axis = `lines.STATS`) and
`ratio_var(makes, attempts, p_ref, den)` (FG%/FT% impact variance: binomial + volume term). A team's weekly
variance is Σ_players games·var(m).

**Out-of-sample evidence** (fitted, script `docs/research/noise_oos_2026-10-19.py`; data: the section 2 box
scores). Three models are fitted on the TRAIN seasons' rostered player-seasons (≥ 24 mpg, ≥ 30 games):
- `cv`: a constant CV, pooled, which is the old `GAME_CV_FITTED` method;
- `lin`: var = a·m, one parameter;
- `vf`: var = a·m + b·m².

They are scored on the TEST seasons, on every player-season with ≥ 30 games and ≥ 10 mpg (streamers included).
Split A trains on 2011-18 and tests on 2019-24 (1,374 train units; 2,145 test units, 124,884 games). Split B is
the reverse: train 2018-24, test 2011-17 (2,407 test units, 151,407 games).

*(1) Game-level Gaussian log-likelihood per game, vf − cv* (player-cluster bootstrap SE), plus *(2) weekly
calibration*. For (2), z = (weekly sum − g·m)/√(g·var(m)) per player-week, and the table shows the mean z² by
minutes tier (1 = calibrated; > 1 = variance under-stated). Split A:

| stat | ΔLL vf−cv, all (SE) | ΔLL 15-24 mpg | ΔLL 30+ | ΔLL vf−lin | weekly z², cv: 15-24 / 24-30 / 30+ | weekly z², vf: 15-24 / 24-30 / 30+ |
|---|---|---|---|---|---|---|
| FGM | +0.296 (0.014) | +0.380 | +0.031 | +0.007 | 3.39 / 1.70 / 0.91 | 1.42 / 1.14 / 1.07 |
| FGA | +0.368 (0.020) | +0.468 | +0.025 | +0.007 | 4.23 / 1.91 / 0.99 | 1.87 / 1.33 / 1.11 |
| FTM | +0.615 (0.026) | +0.854 | +0.111 | 0.000 | 4.13 / 2.75 / 1.40 | 1.01 / 1.01 / 1.03 |
| FTA | +0.619 (0.029) | +0.841 | +0.111 | 0.000 | 4.13 / 2.68 / 1.39 | 1.06 / 1.02 / 1.02 |
| 3PM | +0.186 (0.010) | +0.233 | +0.082 | 0.000 | 2.18 / 1.39 / 1.13 | 1.06 / 1.00 / 0.99 |
| PTS | +0.322 (0.014) | +0.416 | +0.033 | +0.009 | 3.59 / 1.81 / 0.94 | 1.49 / 1.21 / 1.11 |
| REB | +0.226 (0.012) | +0.280 | +0.030 | −0.001 | 2.81 / 1.63 / 1.16 | 1.38 / 1.05 / 0.93 |
| AST | +0.538 (0.025) | +0.728 | +0.081 | 0.000 | 4.09 / 2.42 / 1.31 | 1.20 / 1.11 / 0.98 |
| STL | +0.183 (0.008) | +0.251 | +0.032 | 0.000 | 2.23 / 1.49 / 1.17 | 1.03 / 0.97 / 0.95 |
| BLK | +0.307 (0.010) | +0.338 | +0.257 | +0.003 | 2.41 / 2.23 / 2.14 | 0.96 / 0.97 / 0.94 |
| TO | +0.307 (0.014) | +0.427 | +0.045 | 0.000 | 2.90 / 1.88 / 1.10 | 1.05 / 1.03 / 0.99 |

What the table shows:
- **The variance function wins on every stat, in both splits.** Split B: ΔLL vf−cv runs from +0.17 (STL) to
  +0.75 (AST), every SE ≤ 0.031 (max 0.0304, split B).
- **The gain is largest for 15-24 mpg players**, the streamers and bench players the old CV mishandled. The vf
  gain is still positive for 30+ mpg stars.
- **vf vs `lin`:** the quadratic term adds little likelihood (|Δ| ≤ 0.009 per game, every stat, both splits). It
  is kept because it is the model the simulator has used since 0.4.0, and its coefficients are unchanged. b is
  not negligible at star volume, though: PTS at m = 32 has a·m = 107 and b·m² = −29, so var = 78 (−27%).
- **The `cv` weekly z² is inflated by tiny-mean players** (the mean of per-week ratios).
- **Ratio of summed variances, a less extreme view** (game level, split A; script `docs/research/noise_tier_2026-10-19.py`):
  - the constant CV under-states variance by ×1.5-2.8 at 15-24 mpg and by ×0.9-1.6 at 24-30 mpg;
  - it over-states it by ~25% for 30+ mpg stars (ratio 0.69-0.98);
  - vf is within ±7% at 24-30 and 30+ (REB 30+ 0.89) **at game level**.
- **Weekly variance is not calibrated at 24-30 mpg.** The weekly mean z² of vf (the table above, and split B) is:

  | stat | split A | split B |
  |---|---|---|
  | FGA | 1.33 | 1.46 |
  | PTS | 1.21 | 1.23 |
  | FGM | 1.14 | 1.23 |
  | REB | 1.05 | 1.23 |

  So the model under-states those stats' weekly variance by 14-46% in that tier (REB split A only 5%); the other stats are at 0.87-1.16
  (split B 3PM 0.87, AST 1.16). The game-level ratio is near 1 while weekly z² is not. A likely cause (a guess, not
  tested) is within-week dependence: minutes/role swings that persist across a week's games, which a per-game
  model summed over independent games does not capture. Also, the mean of per-week z² weights player-weeks
  equally, whereas the ratio pools variance. At 30+ mpg weekly z² is 0.93-1.11 (A) and 0.89-1.06 (B).
- **Remaining miss:** at 15-24 mpg vf under-states FGA by ×1.40, PTS ×1.23, REB ×1.23 and FGM ×1.18. That is
  minutes volatility for bench players.
  - Refitting on ≥ 15 mpg would fix it (×1.1-1.2) but worsens 30+ REB (0.84).
  - The draft population is mostly ≥ 24 mpg, so the ≥ 24 fit is kept. This is a known limitation.
- **The review's example:** steals at 0.7 per game now get a CV of 1.22 (√var/m). The actual figure at 15-24 mpg
  is 1.27; the old constant was 0.97.

**What changed.**
- *Valuation* (`valuation-0.3.0`): the G-score within-week variance is `game_var(stat, eff)` for count categories,
  and `ratio_var` for FG%/FT%.
- *Optimizer's category model* (`optimizer-0.3.0`): V = `game_var_matrix(S)`; R = `ratio_var`.
- *Market-implied projections* (`sim/market_truth.py`): same formula as the valuation.
- *Simulator* (`season-sim-0.5.0`): `Simulator.game_variance` = `game_var_matrix`. The numbers are unchanged
  because the coefficients are the same. `SimParams.game_var` is removed.
- *Run params:*
  - valuation, `draft_sim` and `season_sim` runs record `"noise_model": "noise-var-1.0"` instead of `"game_cv"`;
  - `valuation_params()` is still the single cache key.
- *Deleted:*
  - `GAME_CV_PRIOR`, `GAME_CV_FITTED`, `GAME_CV` and `GAME_CV_SOURCE` (with the `FANTASY_GAME_CV` env var);
  - `lines.game_variance`;
  - `calibration.GAME_VAR` (moved to `model.noise`) and `calibration.GAME_CV_POOLED`;
  - the `game_var="cv"` option;
  - their tests.
  - Git history (a6079da and earlier) keeps the old values.
- *Unchanged:*
  - Effective lines (availability-blended) feed the variance function directly. Its linear term makes avail·m
    have avail × the per-played-game variance, which is consistent.
  - Within-game correlation (`NOISE_CORR`, `SimParams.noise_corr`) stays simulator-only. The draft objective,
    expected category wins, is a sum of per-category marginals, so correlation does not change it.
- *Not retrained:* the value-search model (`value_search.md`, "Known mismatch").

**Draft effect vs main at a6079da.** Same blend projections and settings; old = constant fitted CV, new = vf.

*Valuation ranks:*

| | top 60 | top 156 |
|---|---|---|
| set overlap | 60/60 | 155/156 (Jaime Jaquez Jr. 157 → 152 in, Mitchell Robinson 156 → 160 out) |
| Spearman | 0.997 | 0.999 |
| mean \|rank move\| | 0.87 | 1.37 |
| biggest movers | Anunoby 52 → 57, Duren 27 → 24, Avdija 59 → 56, Amen Thompson 28 → 31, Knueppel 19 → 16 | Camara 97 → 104, Ausar Thompson 107 → 113, Sheppard 72 → 77, Dosunmu 102 → 97, LaVine 129 → 124 |

The top 10 is identical. The direction is the expected one: high-volume players lose a little noise penalty,
and low-volume, steal- and block-dependent players gain some.

*Mock draft:* slot 1 with third-round reversal; the other teams pick the best ADP available. The table shows the
top-5 `draft recommend` scores (expected category wins); I take #1.

| pick | before (old CV) | after (vf) |
|---|---|---|
| 1 | **Jokić 5.981**, SGA 5.943, Wembanyama 5.910, Dončić 5.772, Haliburton 5.713 | **Jokić 5.904**, Wembanyama 5.853, SGA 5.810, Dončić 5.718, Haliburton 5.589 |
| 24 | **Kawhi Leonard 5.983**, Adebayo 5.974, Holmgren 5.956, Duren 5.954, Mobley 5.950 | **Kawhi Leonard 5.902**, Adebayo 5.888, Holmgren 5.887, Duren 5.881, D. White 5.874 |
| 36 | **D. White 5.968**, Clingan 5.965, Bane 5.952, Kyrie 5.951, Knueppel 5.934 | **D. White 5.889**, Bane 5.889, Clingan 5.888, Kyrie 5.872, A. Davis 5.862 |
| 37 | **Clingan 5.966**, Bane 5.958, Knueppel 5.957, Kyrie 5.951, A. Davis 5.941 | **Bane 5.890**, Clingan 5.888, Knueppel 5.882, Kyrie 5.873, A. Davis 5.863 |
| 60 | **M. Porter Jr. 5.904**, Edgecombe 5.904, Ware 5.902, Bridges 5.902, Naz Reid 5.901 | **Naz Reid 5.863**, Ware 5.863, M. Porter Jr. 5.860, Edgecombe 5.844, D. Daniels 5.840 |
| 61 | **Ware 5.924**, Naz Reid 5.920, Edgecombe 5.908, Bridges 5.905, D. Daniels 5.897 | **Ware 5.865**, M. Porter Jr. 5.863, Edgecombe 5.849, D. Daniels 5.846, Bridges 5.845 |

- Picks 1, 24, 36 and 61 are unchanged.
- At 37 Clingan and Bane swap, with a margin of 0.002. At 60 Porter Jr. and Naz Reid swap, with a margin of
  0.003.
- Both swaps are well under the ~0.03 noise threshold for recommendation margins.
- Scores are ~0.06-0.08 lower everywhere because weekly variance is larger for most rostered players.

*`fantasy draft simulate --sims 200 --seed 0`* (optimizer vs BPA, paired; this is the category model's own view):

| | optimizer ECW | BPA ECW | edge ECW (SE) | edge P(win matchup) (SE) | P(optimizer better) |
|---|---|---|---|---|---|
| before | 5.960 | 5.865 | +0.095 (0.007) | +0.0086 (0.0010) | 0.88 |
| after | 5.893 | 5.796 | +0.098 (0.006) | +0.0104 (0.0009) | 0.93 |

Pick 1 is Jokić in 200/200 sims in both runs. At pick 24 Kawhi Leonard is taken in 99 and 98 of 200.

*`fantasy sim preseason --leagues 20 --sims 2000 --draft-seed 100 --seed 0`* (slot 1; SE clustered by league):

| | P(playoffs) (SE) | P(bye) | P(final) | P(title) (SE) | expected rank (SE) |
|---|---|---|---|---|---|
| before (season-sim-0.4.0, valuation-0.2.3) | 0.930 (0.007) | 0.710 | 0.594 | 0.400 (0.015) | 2.36 (0.07) |
| after (season-sim-0.5.0, valuation-0.3.0) | 0.947 (0.007) | 0.727 | 0.626 | 0.413 (0.011) | 2.26 (0.09) |

- The simulator's noise is numerically identical in the two runs; only the drafted rosters differ, through the
  draft model.
- The differences (+1.7 pp playoffs, +1.3 pp title) are within about 1.5 combined SE. The two runs are not paired
  pick by pick, because different rosters change the random streams. So the honest reading is "no detectable
  change".
- **Conclusion:** the switch is a modelling-correctness change, with no material effect on the draft at the top.
  It matters more for in-season streaming and waiver decisions, where low-volume players are the candidates.

## Limitations
- The season error is fitted to the baseline method, not the blend (section 1 caveat). Rookies and players with
  less than one prior season are excluded from the fit; the simulator gives them their mpg tier's error, which
  is probably too small for rookies.
- The equal-loading one-factor form is what the simulator implements. The free-loading first PC shows FGA and AST
  load more than REB, STL and BLK.
- Game variance at 15-24 mpg is still under-stated for FGA/PTS/REB/FGM (×1.2-1.4, section 8).
- Correlations and variances were pooled over 2010-24. The league's 3-point volume rose over the period, and
  3PM noise was not modelled by era.
- Absence structure: DNP-coach's-decision games count as missed, which is right for fantasy. Mid-season trades
  and 10-day contracts are excluded.
