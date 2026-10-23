# In-season (rest-of-season) projection updates

**Answer:** projections now update from games actually played. Availability (`ros-avail-0.4`) and per-game stats
(`stat-shrink-0.1`) are both **fitted** on 2011-2026 box scores and beat the static preseason projection out of sample
(test 2024-25 + 2025-26). `fantasy data update-projections --fetch` writes `data/processed/projections_2026_27.inseason.csv`,
which every tool loads by default (`FANTASY_PROJECTIONS=preseason` reverts). Code: `src/fantasy/model/inseason.py`
(`ros-proj-0.4.0`); fits: `scripts/inseason_model/` (reports `report_avail.json`, `report_stats.json`).

Example (fitted, 70-game fantasy season): prior 38.5 ± 24 games, plays the first 20 of 20 team games →
**55.0 expected fantasy-season games** (35.0 ± 10.9 of the remaining 50). Static prior: 43.5;
iid Beta-binomial: 67.7. Misses all 20 → 5.5 (Beta-binomial: 2.0).
Before his team's first game a healthy player's projection is exactly the preseason one (a `games_missed` player's
`gp` rate is re-expressed over the fantasy season, see deployment rules; his `season_gp_exp` equals the preseason gp).

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

## 1. Availability (fitted, `ros-avail-0.4`)
Target: share of remaining timeline games played after k team games **through the fantasy-season end** = the NBA's
last regular-season day − 21 days (league 44495: playoffs end Sun Mar 21, 2027; NBA ends Apr 11; same convention as
the playoff multiplier in `availability.py`). The NBA's last ~3 weeks (tanking, rest, shut-downs) are outside the
fantasy season; through `ros-avail-0.3` the target wrongly ran to the NBA season end (`--to-nba-end` reproduces it).
Population (production-like, 86% of
checkpoints): on the opening-night roster and not injured coming in (lost season, or a 10+ game absence at the end of
T-1) — production handles known preseason injuries with windows instead (below). Per checkpoint k ∈ {1,2,3,5,10,15,20,30,40,50,60}
a WLS (weights = remaining games): `R = b0 + b1·p0 + b2·o + b3·log1p(streak) + b4·[missed all] + b5·dnp/k` minus the
k = 0 level `(c0 + c1·p0) − p0` (c0 = 0.0365, c1 = 1.0165). p0 = prior gp/82, o = observed share, streak = current run of
missed games, dnp = missed games he was listed in the box score for (mostly coach's decisions, but ~21% of such ESPN
rows in 2024-25 and 2025-26 carry an injury / illness / rest / suspension reason; the historical nba.com data has no
reason field, so the feature is "listed, did not play", not split by reason). k = 0 deploys the prior itself
(gp, gp_sd): the fitted level shift is not significant out of sample (k = 0 RMSE 16.52 calibrated vs 16.61 prior,
gain 0.09 ± 0.12), so it is removed at every k and the change from the prior is information from games only.
Note the prior under-predicts fantasy-season games by ~2.3 games OOS (mean residual, k = 0; the gp model targets 82
games including the shut-down weeks) — a prior-calibration question for the gp model, not deployed here.
Interpolated linearly in k. Variance
`a·R̂(1−R̂) + c/remaining + d·log1p(streak)` (NNLS on squared residuals). Fit 2011-2024 (excl. 2020-21, n = 50,539);
test 2025-26 (n = 8,847, ≈ 740 per k).

OOS RMSE, remaining fantasy-season games (gain ± cluster-bootstrap SE by player-season; mean residual = actual −
predicted). "Calibrated static" = per-k fit of R on p0 alone (the prior recalibrated without game information);
"damped" = the deployed early-season damping (prior, below):

| k | update | damped | static | calibrated static | iid Beta-binomial | gain vs static | gain vs B-B | mean resid update / static |
|---|---|---|---|---|---|---|---|---|
| 1 | 15.22 | 15.86 | 16.44 | 16.37 | 15.37 | 1.22 ± 0.33 | 0.15 ± 0.17 | 2.01 / 2.21 |
| 2 | 15.10 | 15.27 | 16.29 | 16.22 | 15.70 | 1.19 ± 0.33 | 0.59 ± 0.19 | 2.09 / 2.12 |
| 3 | 15.11 | 14.95 | 16.15 | 16.10 | 16.27 | 1.05 ± 0.35 | 1.16 ± 0.22 | 2.11 / 2.02 |
| 5 | 14.71 | 14.71 | 15.84 | 15.79 | 16.58 | 1.12 ± 0.34 | 1.87 ± 0.23 | 2.19 / 1.85 |
| 10 | 13.34 | 13.34 | 14.90 | 14.89 | 15.38 | 1.56 ± 0.35 | 2.04 ± 0.23 | 2.18 / 1.5 |
| 20 | 11.41 | 11.41 | 13.03 | 13.02 | 12.95 | 1.62 ± 0.34 | 1.54 ± 0.21 | 1.94 / 1.09 |
| 30 | 9.28 | 9.28 | 11.07 | 11.08 | 10.46 | 1.79 ± 0.25 | 1.18 ± 0.2 | 1.46 / 0.7 |
| 40 | 7.16 | 7.16 | 8.84 | 8.89 | 8.05 | 1.68 ± 0.22 | 0.89 ± 0.14 | 0.75 / 0.28 |
| 60 | 2.81 | 2.81 | 3.83 | 3.84 | 3.45 | 1.02 ± 0.1 | 0.64 ± 0.08 | 0.19 / 0.01 |

(`ros-avail-0.3`, to the NBA season end: k = 1 17.42 vs static 18.61, k = 10 15.59 vs 17.18, k = 30 11.79 vs 13.66;
not comparable in level — fewer remaining games now.)

- The gain is information from games, not recalibration (calibrated static ≈ static at every k).
- Why not iid Beta-binomial: absences are spells. The current streak has a large negative coefficient at every k ≥ 5
  (−0.03 to −0.19 per log-game) and the observed share gets weight only 0.20-0.42: a hot start is partly luck that does
  not persist; an ongoing absence does. dnp: an early listed DNP is milder than not being listed (+0.17 at k = 1), a
  late one means out of the rotation (−0.06 to −0.25 at k ≥ 15).
- Calibration (OOS, undamped): mean z² 0.93-1.11 at every k ≥ 1, normal 80% intervals cover 82-87%; players in a
  current absence of 10+ games remain under-dispersed at k ≥ 20 (z² 1.2-1.7, cover80 0.74-0.80; bimodal: back or not).
- Selection: history only has player-seasons of players who play again later (gp panel), so the newest seasons lack
  lost seasons (all-missed share 2026 lower) — long absences in the test are biased toward returning. Priors for
  history are the production gp-ols-0.2 fit (through 2026): static baseline flattered, i.e. gains are conservative;
  TODO rolling-origin priors.
- k = 1: o, streak and all-missed are collinear; the min-norm fit's predictions are exact, its SEs undefined.
- **Early damping (PRIOR, `ros-proj-0.4.0`):** the raw fit moves a lot after one game (p0 = 0.72: play game 1 → 62.4,
  miss it → 35.7 on the 82 scale; Embiid 44.3 → 52.0 after one game) because history's "missed the opener" group is
  dominated by undeclared preseason injuries, which production handles with injury windows and the feed. For k < 5 the
  deployed mean and variance are `prior + (k/5)·(fit − prior)` (k = 1: Embiid 44.3 → 45.9, gp_sd 22.3 → 21.7 instead
  of collapsing to 13.8). OOS on history (which still contains those injuries) this costs 0.64 ± 0.27 games at
  k = 1 and 0.17 ± 0.21 at k = 2 and gains 0.16 ± 0.15 at k = 3 (table) — the price of not trusting one game.
  Not done: conditioning on the feed (healthy-listed + missed treated like a listed DNP) — the feed's silence is not
  a reliable "healthy" for fringe players (G League, not with team), and history has no feed to fit it on.
  The meta lists |Δgp| > 4 (either direction) after 1-5 team games as `low_confidence`: check the reason before acting
  (2026-10-21, k = 1: 4 of 79 players with a game, all missed the opener).
- Deployment rules (not fitted): a preseason `games_missed = m` override means the prior already expects the first m
  games out, so the update skips that window (healthy prior gp·82/(82−m), sd (gp_sd − 0.15m)/(82−m); the update starts at
  the expected or actual return; remaining expected games exclude the rest of the window; return-timing sd 0.15 per
  window game still to come, as in the preseason build — so before any game `season_gp_exp` equals the preseason gp;
  the `gp` rate is over the 68-71 fantasy-season games, so a 20-game window is 20/70 of it, not 20/82).
  Remaining games = the team's scheduled games through the league's `playoff_end_date` (bbref schedule) minus its
  completed games. **Prior:** players listed day-to-day (`gtd`) before the opener get a 1-game window without rescaling
  (gp × 69/70 before any game). `status`/`injury_note` come from the ESPN injury feed (age in ET days, −1..3: a
  morning-after fetch for yesterday's games is fine); a player absent from the feed is healthy unless his preseason
  injury window still has games to come and he has not played (preseason status kept); without a feed a preseason
  out/gtd is cleared once he has played. `playoff_mult` is renormalised to the remaining calendar (same
  playoff/other-week ratio, decoded with the consumers' 3/20 share). `gp_sd` floored at 0.5. ESPN rows whose names map
  to one `player_id` from several ESPN ids are warned about and deduplicated to one row per game.

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
- In the in-season file `gp` is a **rate**, not a count: 82 × expected share of the REMAINING fantasy-season team
  games (what every consumer reads as the per-game play probability), `gp_sd` its sd on that scale. Counts:
  `ros_team_games`/`ros_games` (through Mar 21), `fantasy_gp_exp` (played + ros_games), `season_gp_exp` (82-game
  season total, same share applied to every remaining NBA game); also `games_played`, `team_games`,
  `missed_streak`, `gp_preseason`.
- Box-score ingestion: a scoreboard with a regular-season game not yet final is never cached (fixed in 0.4.0: an
  in-progress board used to be cached and served forever, silently dropping that game from team timelines).
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
