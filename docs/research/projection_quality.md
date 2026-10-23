# Projection quality: backtest, external sources, blend (2026-10-19)

_Owner: projections researcher. Code: `src/fantasy/model/proj_backtest.py` (backtest helpers),
`src/fantasy/data/external.py` (`fantasy data fetch-external`), `src/fantasy/model/blend.py`
(`fantasy data build-projections --blend ...`); tests `tests/test_external_blend.py`. Analysis
scripts (not committed) were in the session scratchpad `proj/` (`bt.py` builds the per-season
panels, `an1.py`–`an6.py` produce the tables, `cmp.py` the 2026-27 comparison, `mock.py` the mock
drafts). Projections `baseline-0.2.1`, valuation `valuation-0.2.1`, optimizer `optimizer-0.2.0`._

> **Adopted 2026-10-19.** The 50/50 ours/ESPN per-game blend with our GP is now the default draft projections
> (`data/processed/projections_2026_27.csv`, `projections-2026-27-0.3.0`, `blend-0.2.0`; valuation `valuation-0.2.2`).
> Configured in `data/manual/projection_blend.json` and rebuilt by plain `fantasy data build-projections`. The unblended model
> is kept as `data/processed/projections_2026_27.base.csv` (`--blend none` makes it the default again). The default file is
> byte-identical to the blend evaluated below (sha256 `6acf9cce…`). Handoff: `docs/handoffs/2026-10-19-blend-adoption.md`.

## TL;DR

1. **The statistical core is sound (fitted).** Backtested out of sample on 2017-18 to 2025-26 (9 target
   seasons, ~200 rotation players each), it beats all three naive baselines on every per-game stat and on
   fantasy-value rank. Top-150 Spearman (projected vs realised per-game G-score) is **0.724 (SE 0.016)**
   for ours, compared with 0.703 for last season only, 0.697 for a 3-yr minutes-weighted average and 0.666 for a 3-yr average.
   Paired gains are +0.021 (0.007), +0.027 (0.005) and +0.058 (0.008). Ours is best or tied best in 8 of 9 seasons.
2. **It has systematic biases (fitted).** Players aged 30-32 are over-projected by 0.54 G-score (SE 0.07), mostly through
   minutes (-1.9 mpg). Ages 22-25 are under-projected (+0.16 to +0.35). Players who change teams are over-projected
   (-0.29, SE 0.06). Projected minutes are not regressed enough (low-minute players gain about 2.6 mpg; 25-30 mpg players
   lose about 1.4). These are real but small next to the noise: the value RMSE is about 1.5 G-score.
3. **ESPN's season projections help (fitted, with a caveat).** ESPN's public fantasy API also serves its *past* season
   projections, so a 50/50 blend of our core with ESPN could be backtested on 6 seasons. The blend raises top-150
   Spearman by **+0.020 (0.007)** and cuts points RMSE by 0.20 (0.03) per game, about 7%. The weight optimum is flat
   between 0.4 and 0.6. ESPN alone is no better than ours (+0.009, SE 0.012). Caveat: ESPN's stored 2022-23 projection is
   clearly an in-season update, so that season is excluded. The other seasons look preseason, but this cannot be proven.
4. **ESPN games played is badly optimistic (fitted).** ESPN's projected GP for its top 150 has been +4 to +17 games above
   realised in every season (mean about +11). Keep our fitted availability model for GP.
5. **Recommendation:** use `ours=0.5,espn=0.5` per-game stats with our GP as the default for the draft, if the lead accepts
   a small change on draft eve. Effect: the top 5 are unchanged and the mean absolute rank move in our top 60 is 5.5.
   Pick 1 stays Jokić. Pick 24 changes from Chet Holmgren to Kawhi Leonard (the optimizer scores are within 0.03 expected
   category wins, a near tie). Pick 36 stays Derrick White. The blend moves our ranks toward the market (Spearman with
   Yahoo ADP, top 150: 0.77 → 0.83), which also shrinks the "edge from disagreeing with the market" in
   `optimism_decomposition.md`. That shrinkage is the point: the backtest says about half of that disagreement is our
   error, not ESPN's.

## 1. Backtest of the projection core

### Design
- **Data (literature/source):** Basketball-Reference player totals, full history, from the sumitrodatta GitHub mirror. As in
  `availability_model.md`, the full upstream file was downloaded to the scratchpad and is not committed. Seasons
  2014-15 to 2025-26 are used.
- **No leakage:** for target season T the *production* `projections.baseline()` runs on T-3..T-1 only. The seasons are
  relabelled so that T-1 becomes 2026, which lets the unchanged production code see exactly the inputs it would have had.
  `test_project_core_uses_only_prior_seasons` changes season T and checks that the projection does not move. Manual
  offseason overlays (trades, injuries, roles, rookies) do not exist historically, so this tests the statistical core.
  Like production, it only projects players with a T-1 season.
  One leak remains: the fitted games model (`availability.py`) was fitted on data up to 2026. It does not matter here,
  because every metric is per game.
- **Baselines** (same inputs, same players): `last` = T-1 per-game line; `avg3` = 3-season per-game average
  (stat sum / games sum); `mw3` = per-minute rates with 5/3/2 season weights × recency-weighted MPG (0.6/0.3/0.1). `mw3` is
  our method without regression to the position mean, without the age curve and without minutes growth or shrinkage.
- **Fantasy value** = per-game 9-cat G-score from the production `valuation.compute_values` (kappa = 1), with everyone
  available every game (gp = 82). Projected and realised values are computed separately over the same universe:
  players projected by all methods with at least 25 realised games in T (n ≈ 320-360 per season).
- **Error sets:** per-stat RMSE/MAE use rotation players (≥ 20 realised mpg and ≥ 40 games; n ≈ 190-217 per season).
  Value-rank Spearman uses the top 150 by *realised* value. That selects on the outcome, which lowers every method's ρ
  equally, so the comparison stays fair.
- **SEs:** players are bootstrapped within season (500 resamples for ranks, 300 for RMSE). Each statistic is a mean over seasons.
  Paired differences use the same resample. Season-level SEs are not included, but the per-season table shows the consistency.

### Per-game error (rotation players, mean over 9 seasons; RMSE, SE in brackets)

| stat | ours | last | avg3 | mw3 | last − ours | avg3 − ours | mw3 − ours |
|:--|:--|:--|:--|:--|:--|:--|:--|
| pts | 3.212 (0.056) | 3.310 (0.058) | 3.731 (0.061) | 3.466 (0.058) | +0.098 (0.034) | +0.519 (0.029) | +0.254 (0.017) |
| reb | 1.088 (0.024) | 1.156 (0.026) | 1.233 (0.027) | 1.168 (0.027) | +0.069 (0.013) | +0.145 (0.013) | +0.080 (0.008) |
| ast | 0.939 (0.022) | 0.986 (0.023) | 1.043 (0.023) | 0.985 (0.022) | +0.047 (0.014) | +0.104 (0.009) | +0.046 (0.007) |
| stl | 0.243 (0.005) | 0.268 (0.006) | 0.264 (0.006) | 0.255 (0.005) | +0.025 (0.003) | +0.021 (0.002) | +0.011 (0.001) |
| blk | 0.208 (0.006) | 0.224 (0.006) | 0.214 (0.007) | 0.211 (0.006) | +0.016 (0.004) | +0.007 (0.003) | +0.004 (0.002) |
| 3pm | 0.502 (0.010) | 0.512 (0.010) | 0.552 (0.011) | 0.519 (0.011) | +0.010 (0.007) | +0.050 (0.004) | +0.017 (0.002) |
| tov | 0.433 (0.008) | 0.454 (0.009) | 0.472 (0.008) | 0.449 (0.008) | +0.021 (0.005) | +0.039 (0.003) | +0.016 (0.002) |
| fga | 2.381 (0.045) | 2.452 (0.046) | 2.768 (0.049) | 2.570 (0.047) | +0.070 (0.025) | +0.386 (0.021) | +0.189 (0.013) |
| fta | 0.931 (0.019) | 0.964 (0.019) | 1.042 (0.021) | 0.979 (0.019) | +0.033 (0.011) | +0.110 (0.009) | +0.048 (0.004) |
| FG% (fga-weighted) | 0.031 (0.001) | 0.036 (0.001) | 0.033 (0.001) | 0.032 (0.001) | +0.005 (0.001) | +0.001 (0.000) | +0.001 (0.000) |
| FT% (fta-weighted) | 0.050 (0.001) | 0.057 (0.001) | 0.051 (0.001) | 0.051 (0.001) | +0.008 (0.001) | +0.002 (0.000) | +0.001 (0.000) |
| mpg | 4.50 (0.09) | 4.74 (0.10) | 5.13 (0.10) | 4.85 (0.10) | +0.24 (0.05) | +0.63 (0.04) | +0.34 (0.03) |

MAE gives the same ordering (pts: 2.52 ours, 2.59 last, 2.93 avg3, 2.73 mw3).
Reading: positive differences mean the baseline is worse. Ours wins on every stat, and most differences are more than 3 SE.
"Last season only" is the strongest naive baseline because minutes and roles drift. The 3-year average is the worst.

### Fantasy value rank (Spearman, projected vs realised per-game G-score)

| set | ours | last | avg3 | mw3 | ours − last | ours − avg3 | ours − mw3 |
|:--|:--|:--|:--|:--|:--|:--|:--|
| top 150 by realised value | **0.724 (0.016)** | 0.703 (0.017) | 0.666 (0.018) | 0.697 (0.017) | +0.021 (0.007) | +0.058 (0.008) | +0.027 (0.005) |
| top 150 by our projected value¹ | 0.744 (0.015) | 0.711 (0.016) | 0.639 (0.019) | 0.694 (0.017) | +0.033 (0.010) | +0.105 (0.011) | +0.050 (0.007) |
| all (≥ 25 g) | 0.790 (0.009) | 0.774 (0.009) | 0.734 (0.010) | 0.765 (0.009) | +0.016 (0.004) | +0.056 (0.004) | +0.025 (0.003) |
| value RMSE, top 150 (G units, lower = better) | 1.546 (0.036) | 1.654 (0.037) | 1.780 (0.038) | 1.659 (0.037) | −0.108 (0.021) | −0.234 (0.018) | −0.112 (0.011) |

¹ Selecting on our own projection favours ours slightly. It is shown because it matches the decision set (whom we would draft).

Per season (top 150 by realised value; ours / last / avg3 / mw3):
2018 .710/.701/.683/.695 · 2019 .741/.718/.689/.722 · 2020 .664/.627/.591/.611 · 2021 .671/.660/.591/.650 ·
2022 .727/.721/.638/.688 · 2023 .747/.751/.716/.737 · 2024 .782/.769/.725/.764 · 2025 .761/.727/.701/.726 ·
2026 .715/.657/.663/.685.

**Verdict:** the core is not obviously flawed. It is a well-behaved Marcel-type system (literature: Tango's Marcel;
Basketball-Reference's "simple projection system" is the same idea). Regression to the mean and the age curve each add
measurable rank accuracy over naive averages. The ceiling is low for *any* method: a ρ of about 0.72 means much of the
value ordering is unpredictable from box-score history alone (role changes, injuries, development).

### Systematic biases (residual = realised − projected; all target seasons; n = 3,113 player-seasons with ≥ 25 g)

Overall value bias is −0.04 (0.03) and minutes bias is −0.34 (0.09) mpg, so the core is close to unbiased on average. By group:

| group | n | value bias (SE) | mpg bias (SE) | pts bias (SE) |
|:--|--:|:--|:--|:--|
| age ≤ 21 | 238 | +0.05 (0.09) | −0.29 (0.35) | +0.23 (0.21) |
| age 22-23 | 498 | **+0.35 (0.07)** | +0.70 (0.26) | +1.00 (0.16) |
| age 24-25 | 619 | +0.16 (0.06) | +0.44 (0.21) | +0.40 (0.12) |
| age 26-29 | 975 | −0.07 (0.05) | −0.09 (0.16) | +0.04 (0.10) |
| age 30-32 | 456 | **−0.54 (0.07)** | −1.91 (0.20) | −0.95 (0.13) |
| age 33+ | 327 | −0.29 (0.07) | −2.02 (0.23) | −0.85 (0.14) |
| proj mpg < 15 | 421 | +0.74 (0.07) | +2.59 (0.27) | +1.03 (0.12) |
| proj mpg 15-20 | 662 | +0.19 (0.06) | +0.26 (0.21) | +0.23 (0.11) |
| proj mpg 20-25 | 636 | −0.20 (0.06) | −1.21 (0.22) | −0.34 (0.13) |
| proj mpg 25-30 | 638 | −0.35 (0.06) | −1.39 (0.20) | −0.38 (0.13) |
| proj mpg 30+ | 756 | −0.28 (0.05) | −0.89 (0.12) | +0.01 (0.12) |
| changed team (T-1 last team ≠ T first team) | 802 | **−0.29 (0.06)** | −1.36 (0.20) | −0.68 (0.11) |
| same team | 2,311 | +0.05 (0.03) | +0.01 (0.10) | +0.29 (0.06) |

Within the top 150 by our projection, the age 30-32 bias is −0.81 (0.09) and the team-change bias is −0.76 (0.11). Ages 26-29
come out at −0.39 (0.07). That is partly selection: ranking by projection picks up positive projection errors (winner's curse).

Interpretation and caveats:
- **Age curve:** the decline from 30 to 32 is too gentle. The +1 to +2.5 mpg growth for ages 19-24 plus the age multipliers
  slightly under-credit ages 22-25. The production rate is fine (points per minute bias ≈ 0). The miss is in **minutes**. A
  refit of `AGE_CURVE`/`MPG_GROWTH` would be the natural next model change. It was not done before the draft freeze.
- **Minutes regression:** residuals conditional on projected minutes slope the wrong way. Some of that is selection:
  low-minute players only enter the ≥ 25 g set if they earned minutes. The rest says projected MPG should be pulled
  further toward about 22 mpg.
- **Team changers:** the flag uses T's first team, so it includes mid-season trades, which are not knowable preseason.
  Offseason trades are partly handled in production by manual `mpg`/`usage_mult` overrides that the backtest cannot see.
- **Rookies** are not covered at all: production uses hand-set priors in `rookies_2026_27.csv`, and history has no
  equivalent.

## 2. External 2026-27 projections

Reachability from this cloud container on 2026-10-18/19 (curl; the earlier "blocked" table in
`data_sources.md` is out of date):

| source | reachable | what it offers | ingested | licensing / ToS notes |
|:--|:--|:--|:--|:--|
| **ESPN fantasy API** (`lm-api-reads.fantasy.espn.com`, view `kona_player_info`) | yes | per-game season projection incl. FGM/FGA/FTM/FTA, MIN, GP for ~350 players. **Also past seasons' projections** (2018-19 onward; 2019-20 has only 14 rows; 2022-23 is an in-season update) | **yes** (`espn`, 2026-27 + 2018-19..2025-26) | Unofficial, undocumented public API. ESPN's Terms of Use forbid commercial use and redistribution of content. Personal fantasy use only. |
| **FantasyPros consensus** (`/nba/projections/overall.php`) | yes | season totals PTS REB AST BLK STL FG% FT% 3PM GP MIN TO, 264 players; **no attempts** | **yes** (`fantasypros`, 2026-27) | FantasyPros ToS: personal, non-commercial use; no reproduction or redistribution. Current season only. |
| Basketball-Reference player pages, "2026-27 Projection" table | yes | **per-36 rates only** (no MPG/GP) from bbref's simple projection system (Marcel-like, same family as ours) | no: needs about 300 page fetches at ≤ 20 req/min, and it is methodologically close to ours (little diversification) | Sports Reference terms: personal use, rate limit, no bulk redistribution |
| Hashtag Basketball projections | no (connection fails) | – | no | – |
| FantasyData | no (connection fails) | – | no | subscription product |
| DARKO (shinyapps) | no (connection fails) | – | no | – |

Ingestion: `fantasy data fetch-external --source espn|fantasypros|all [--season 2027] [--refresh]`. It makes one request
per payload with a browser UA and a 2 s pause, retries with backoff, and is cached: an existing raw payload is re-parsed
unless `--refresh`. Parsing is deterministic. Output: `data/raw/<source>/projections_<yyyy_yy>.csv` (normalised per-game
columns `EXTERNAL_COLUMNS`, `player_id` = `canonical_id(name)`) plus `.provenance.json`. The raw payloads
(`*.json`/`*.html`) are git-ignored because of the third-party ToS and this repository is **public**. The normalised CSVs
(numbers only, about 50 KB each) are committed so the blend and backtest can be reproduced. **The user should decide
whether that is acceptable or whether the repo should be private** (added to `docs/USER_TODO.md`).

Name matching: 331 of 348 ESPN rows and 260 of 264 FantasyPros rows match our player ids (after adding the alias
`walter-clayton-jr`). The unmatched rows are mostly fringe players. One notable gap: **Morez Johnson Jr.** (DAL rookie,
Yahoo ADP 168, ESPN 24.7 mpg) is missing from our projections: he is in no file except ADP/positions, so he is not
draftable in our tools.

## 3. Ours vs external (2026-27)

Per-game agreement on our top 250 (Pearson r; mean of source − ours):

| stat | ESPN r | ESPN − ours | FantasyPros r | FP − ours |
|:--|:--|:--|:--|:--|
| pts | 0.963 | +0.64 | 0.960 | −0.24 |
| reb | 0.974 | +0.11 | 0.967 | −0.22 |
| ast | 0.977 | +0.12 | 0.976 | −0.09 |
| stl | 0.943 | +0.03 | 0.916 | −0.02 |
| blk | 0.980 | +0.02 | 0.978 | −0.03 |
| 3pm | 0.958 | +0.04 | 0.968 | −0.05 |
| tov | 0.967 | +0.06 | 0.962 | −0.03 |
| FG% | 0.984 | +0.002 | 0.989 | +0.001 |
| FT% | 0.981 | +0.002 | 0.990 | +0.001 |
| mpg | 0.862 | +0.41 | 0.885 | −0.40 |
| **gp** | 0.775 | **+12.0** | 0.701 | **+16.5** |

Value-rank agreement (Spearman of season value ranks; all sources valued with **our** GP so only per-game lines differ):
our top 50: ESPN 0.75, FP 0.77; top 150: ESPN 0.91, FP 0.89. With ESPN's own GP, ESPN's top-150 agreement drops to 0.84.
Against Yahoo ADP (top 150 by ADP), ours has ρ = 0.77, ESPN-lines 0.85, FP-lines 0.85, the 50/50 blend 0.83 and ESPN with its own GP 0.87.

### 25 biggest disagreements (ours vs ESPN per-game value; either rank ≤ 100)
Ranks are season-value ranks with our GP. Columns: ours rank → ESPN-lines rank (FP-lines rank; Yahoo ADP), mpg ours/ESPN.

| player | team, age | ours → ESPN (FP; ADP) | mpg ours/ESPN | likely reason |
|:--|:--|:--|:--|:--|
| Caleb Wilson | CHI, 20 | 170 → 55 (30; 78.6) | 26.0 / 32.1 | **rookie**: our hand-set prior (11 pts) is far below ESPN/FP (≈ 18) |
| Brook Lopez | LAC, 38 | 178 → 91 (109; 167) | 22.7 / 26.2 | **age**: our 33+ minutes cut (−0.5 mpg/yr) and age multiplier |
| LeBron James | PHI, 42 | 130 → 70 (51; 38.5) | 28.9 / 30.7 | **age** (×0.89 production, −5 mpg) and team change |
| Ty Jerome | MEM, 29 | 169 → 90 (116; 112) | 21.7 / 26.7 | small 2025-26 sample (regression), role |
| Stephen Curry | GSW, 38 | 74 → 39 (33; 23) | 28.5 / 32.0 | **age** |
| AJ Dybantsa | WAS, 20 | 157 → 89 (55; 79) | 31.0 / 32.2 | **rookie** prior; our FG%/TO line is weaker |
| Naz Reid | CHO, 27 | 51 → 19 (37; 66) | 26.3 / 31.7 | **role change** (trade to CHO): ESPN gives a bigger role |
| Tyrese Maxey | PHI, 26 | 5 → 8 (6; 9.8) | 37.5 / 34.2 | we cap at 37.5 mpg; ESPN expects fewer minutes next to LeBron |
| Deandre Ayton | WAS, 28 | 95 → 171 (186; 167) | 28.6 / 23.8 | **role** (new team, minutes) |
| Norman Powell | CHI, 33 | 118 → 75 (85; 94) | 29.7 / 33.8 | age / role on new team |
| Paul George | BOS, 36 | 115 → 74 (80; 80) | 29.5 / 33.2 | age, team change |
| Nickeil Alexander-Walker | ATL, 28 | 52 → 25 (56; 62) | 29.9 / 32.3 | role (ESPN more minutes) |
| Collin Gillespie | PHO, 27 | 159 → 95 (121; 152) | 22.2 / 26.8 | role |
| Kyrie Irving | DAL, 34 | 45 → 21 (24; 48) | 32.0 / 34.6 | age; return from ACL (games are our GP in both) |
| Kevin Durant | HOU, 38 | 42 → 20 (16; 15.9) | 33.5 / 34.6 | **age** |
| Jeremiah Fears | NOP, 20 | 81 → 127 (158; 152) | 28.8 / 26.4 | young-player minutes growth in ours |
| James Harden | CLE, 37 | 48 → 26 (18; 35.6) | 32.4 / 33.6 | **age** |
| Saddiq Bey | NOP, 27 | 83 → 126 (138; 160) | 31.4 / 27.1 | role / minutes |
| LaMelo Ball | MIN, 25 | 31 → 16 (15; 25.3) | 29.6 / 32.9 | our manual `usage_mult` 0.95 after the trade; ESPN keeps his usage |
| Toumani Camara | POR, 26 | 82 → 125 (92; 116) | 32.3 / 29.4 | minutes |
| Devin Vassell | SAS, 26 | 80 → 124 (114; 158) | 30.9 / 27.5 | minutes |
| Jabari Smith Jr. | HOU, 23 | 55 → 92 (84; 93) | 33.3 / 32.1 | age-curve boost in ours (23) |
| Dyson Daniels | ATL, 23 | 26 → 52 (45; 66) | 32.3 / 32.4 | ours higher on steals (2.3 vs 2.0) and age boost |
| Austin Reaves | LAL, 28 | 38 → 24 (38; 23.4) | 34.4 / 36.6 | ESPN more usage (25.4 vs 20.4 pts) |
| Cameron Boozer | MEM, 19 | 101 → 81 (52; 55.3) | 29.0 / 31.7 | **rookie** prior |

Pattern: **ESPN (and FantasyPros) like veterans (33+) and rookies more than we do. We like 20-25-year-olds and
high-minute role players more.** The historical record does not clearly support either side on veterans. Our 33+ bias was
−0.13 G and ESPN's −0.45 on the ESPN seasons, so ESPN has been *more* over-optimistic on old players. On the 22-25 cohort we
have been too low, not too high (+0.19 to +0.35), and so has ESPN to a lesser degree. Rookies are the clearest case where
the external sources should get weight: our priors are deliberately conservative hand-set numbers and have no backtest.

## 4. Blend

### Method (`fantasy.model.blend`, `blend-0.1.0`)
- Linear per-stat blend of makes, attempts, counting stats and mpg, with weights renormalised over the sources that cover
  a player. Points are recomputed from makes, and percentages follow from blended makes/attempts (attempt-weighted).
- FantasyPros has no attempts. Its FGA/FTA are imputed from ours, scaled so that 2·FGM + 3PM + FTM equals FP's points at FP's
  FG%/FT%.
- GP and gp_sd stay ours by default (`--blend-gp` can override, which we do not recommend: ESPN GP is about +11 games
  optimistic, see below). Player set, team, status, positions, ADP and playoff_mult stay ours, so our manual decisions
  (inactive, injuries, trades) win.
- CLI (since the 2026-10-19 adoption, `blend-0.2.0`): `fantasy data build-projections` writes the unblended model to
  `data/processed/projections_2026_27.base.csv` and the blend from `data/manual/projection_blend.json` to the default
  `projections_2026_27.csv` (meta: weights, input hashes, coverage, ignored external players). `--blend none` gives the
  unblended default, `--blend "ours=0.5,espn=0.25,fantasypros=0.25"` other weights, and `--blend-output <path>` writes a
  blend elsewhere while keeping the default unblended. (Under `blend-0.1.0` the blend went to a separate `.blend.csv` instead.)

### Evidence for the weights (fitted, ESPN historical projections)
Seasons 2018-19, 2020-21, 2021-22, 2023-24, 2024-25, 2025-26. 2022-23 is excluded because ESPN's stored projection there is an
in-season update: Jokić GP 43, and ESPN's mpg correlation with the outcome is 0.96 against 0.75-0.82 in the other years.
2019-20 is excluded with only 14 rows. Players covered by both sources: n ≈ 270-300 per season. "Ours" is the
core *without* manual overlays, while ESPN includes offseason news. **This overstates ESPN's value relative to production
ours**, so the fitted weight is an upper bound on the ESPN weight.

Top-150 Spearman (realised value) by ESPN weight w (SE in brackets; paired difference vs ours):

| w (ESPN) | 0 (ours) | 0.25 | 0.4 | **0.5** | 0.6 | 0.75 | 1 (ESPN) |
|:--|:--|:--|:--|:--|:--|:--|:--|
| ρ | 0.739 (0.019) | 0.754 | 0.758 | **0.760 (0.017)** | 0.760 | 0.758 | 0.748 |
| Δ vs ours | – | +0.014 (0.004) | +0.019 (0.005) | **+0.020 (0.007)** | +0.020 (0.008) | +0.018 (0.009) | +0.009 (0.012) |

Per season the 50/50 blend beats ours in all 6 seasons (by +0.004 to +0.033).
Per-game RMSE (rotation players), ours and the change for ESPN alone and for the 50/50 blend (SE):

| stat | ours | ESPN − ours | blend − ours |
|:--|:--|:--|:--|
| pts | 3.101 | −0.086 (0.061) | **−0.203 (0.031)** |
| reb | 1.020 | +0.003 (0.031) | −0.056 (0.016) |
| ast | 0.940 | −0.032 (0.016) | −0.046 (0.009) |
| stl | 0.238 | +0.014 (0.004) | −0.002 (0.002) |
| blk | 0.204 | +0.025 (0.006) | +0.003 (0.003) |
| 3pm | 0.478 | +0.017 (0.010) | −0.012 (0.005) |
| tov | 0.424 | +0.007 (0.008) | −0.010 (0.004) |
| fga | 2.291 | −0.087 (0.046) | −0.155 (0.023) |
| fta | 0.925 | +0.012 (0.018) | −0.034 (0.009) |

ESPN alone is *worse* than ours on steals and blocks and *better* on points and assists. The blend is at least as good as the
better source on every stat. This is the textbook forecast-combination result: two similar-accuracy forecasts with
imperfectly correlated errors. Per-stat optimal weights differ a little (ESPN about 0.6 for pts/ast, about 0.3 for
stl/blk), but a single 0.5 is within noise of the optimum, so 0.5 is used.

**ESPN games played (fitted):** for ESPN's top 150 who played the season before, projected GP minus realised games
(82-game scale; 0 for players who did not play) was +11.2 (2019), +16.8 (2021), +12.6 (2022), +4.0 (2024), +9.9 (2025) and
+11.1 (2026). Our fitted GP model is about unbiased out of sample (`availability_model.md`). Hence GP stays ours.

FantasyPros could not be backtested (no historical projections). Its 2026-27 lines track ESPN closely (both higher on
veterans and rookies), so giving it weight adds little independent information. `ours=0.5,espn=0.25,fantasypros=0.25` is
offered as an alternative **prior**, not fitted.

### Effect on 2026-27 (blend `ours=0.5,espn=0.5`, our GP)
- Coverage: 331 of 456 players blended. The rest (fringe or out players ESPN does not project) keep ours.
- Rank agreement with ours: Spearman 0.914 (top 50), 0.964 (top 150). The top 5 are unchanged. Mean absolute move in our top 60 is
  5.5 ranks, maximum 23 (Curry 74 → 51).
- Biggest risers into the top 60: Curry +23, Naz Reid +18, Kyrie +13, Harden +13, Alexander-Walker +13, LaMelo +12, Durant
  +11, Kawhi +9. Biggest fallers: Jabari Smith Jr. 55 → 76, Derik Queen 59 → 74, Dyson Daniels 26 → 41, VJ Edgecombe 28 → 43,
  Alex Sarr 41 → 54, De'Aaron Fox 36 → 48, Myles Turner 54 → 66, Trey Murphy 16 → 24, Clingan 18 → 25, Holmgren 15 → 22.

Top 60 by blended value (ours rank, ESPN-lines rank, FP-lines rank, Yahoo ADP, move):

| blend | player | ours | ESPN | FP | ADP | move |
|--:|:--|--:|--:|--:|--:|--:|
| 1 | Nikola Jokić | 1 | 1 | 1 | 1.4 | 0 |
| 2 | Victor Wembanyama | 2 | 2 | 3 | 2.0 | 0 |
| 3 | Shai Gilgeous-Alexander | 3 | 3 | 2 | 3.9 | 0 |
| 4 | Luka Dončić | 4 | 4 | 4 | 3.4 | 0 |
| 5 | Tyrese Haliburton | 6 | 5 | 5 | 12.9 | +1 |
| 6 | Tyrese Maxey | 5 | 8 | 6 | 9.8 | −1 |
| 7 | Jayson Tatum | 10 | 6 | 7 | 8.2 | +3 |
| 8 | Anthony Edwards | 8 | 7 | 8 | 7.8 | 0 |
| 9 | Jamal Murray | 7 | 11 | 13 | 20.8 | −2 |
| 10 | Donovan Mitchell | 11 | 9 | 11 | 13.9 | +1 |
| 11 | Scottie Barnes | 9 | 13 | 10 | 15.1 | −2 |
| 12 | Karl-Anthony Towns | 14 | 10 | 9 | 15.6 | +2 |
| 13 | Jalen Johnson | 13 | 12 | 17 | 12.4 | 0 |
| 14 | Cade Cunningham | 12 | 14 | 12 | 6.0 | −2 |
| 15 | Cooper Flagg | 21 | 15 | 14 | 10.0 | +6 |
| 16 | Bam Adebayo | 19 | 18 | 19 | 26.4 | +3 |
| 17 | Derrick White | 17 | 23 | 22 | 45.2 | 0 |
| 18 | Kawhi Leonard | 27 | 17 | 20 | 34.1 | +9 |
| 19 | LaMelo Ball | 31 | 16 | 15 | 25.3 | +12 |
| 20 | Kon Knueppel | 20 | 30 | 31 | 53.1 | 0 |
| 21 | Desmond Bane | 22 | 28 | 26 | 53.2 | +1 |
| 22 | Chet Holmgren | 15 | 33 | 32 | 31.6 | −7 |
| 23 | Jalen Brunson | 23 | 31 | 25 | 21.6 | 0 |
| 24 | Trey Murphy III | 16 | 40 | 27 | 35.7 | −8 |
| 25 | Donovan Clingan | 18 | 37 | 44 | 44.6 | −7 |
| 26 | Evan Mobley | 25 | 27 | 21 | 29.2 | −1 |
| 27 | Jalen Duren | 30 | 22 | 23 | 34.9 | +3 |
| 28 | Amen Thompson | 24 | 35 | 28 | 22.9 | −4 |
| 29 | Kel'el Ware | 34 | 29 | 42 | 76.6 | +5 |
| 30 | Austin Reaves | 38 | 24 | 38 | 23.4 | +8 |
| 31 | Kevin Durant | 42 | 20 | 16 | 15.9 | +11 |
| 32 | Kyrie Irving | 45 | 21 | 24 | 48.1 | +13 |
| 33 | Naz Reid | 51 | 19 | 37 | 65.8 | +18 |
| 34 | Devin Booker | 33 | 34 | 34 | 25.5 | −1 |
| 35 | James Harden | 48 | 26 | 18 | 35.6 | +13 |
| 36 | Mikal Bridges | 32 | 42 | 29 | 81.6 | −4 |
| 37 | Alperen Şengün | 29 | 44 | 41 | 18.4 | −8 |
| 38 | Josh Giddey | 44 | 32 | 36 | 22.9 | +6 |
| 39 | Nickeil Alexander-Walker | 52 | 25 | 56 | 61.9 | +13 |
| 40 | Walker Kessler | 37 | 36 | 35 | 41.6 | −3 |
| 41 | Dyson Daniels | 26 | 52 | 45 | 66.3 | −15 |
| 42 | Michael Porter Jr. | 39 | 38 | 63 | 62.5 | −3 |
| 43 | VJ Edgecombe | 28 | 50 | 48 | 76.9 | −15 |
| 44 | Onyeka Okongwu | 35 | 43 | 43 | 55.2 | −9 |
| 45 | Anthony Davis | 46 | 41 | 47 | 42.9 | +1 |
| 46 | Lauri Markkanen | 40 | 46 | 64 | 35.9 | −6 |
| 47 | Matas Buzelis | 47 | 45 | 60 | 63.5 | 0 |
| 48 | De'Aaron Fox | 36 | 60 | 68 | 79.3 | −12 |
| 49 | Payton Pritchard | 43 | 57 | 50 | 82.0 | −6 |
| 50 | Jaren Jackson Jr. | 49 | 54 | 69 | 47.0 | −1 |
| 51 | Stephen Curry | 74 | 39 | 33 | 23.0 | +23 |
| 52 | OG Anunoby | 50 | 56 | 40 | 67.5 | −2 |
| 53 | Jarrett Allen | 58 | 48 | 49 | 72.3 | +5 |
| 54 | Alex Sarr | 41 | 66 | 82 | 66.4 | −13 |
| 55 | Giannis Antetokounmpo | 56 | 53 | 46 | 6.2 | +1 |
| 56 | Brandon Miller | 60 | 51 | 57 | 42.6 | +4 |
| 57 | Damian Lillard | 65 | 47 | 39 | 69.1 | +8 |
| 58 | Miles Bridges | 53 | 61 | 62 | 101.2 | −5 |
| 59 | Pascal Siakam | 67 | 49 | 54 | 50.4 | +8 |
| 60 | Deni Avdija | 62 | 58 | 53 | 39.7 | +2 |

Dropping out of the top 60: Tyler Herro (57 → 61), Myles Turner (54 → 66), Derik Queen (59 → 74), Jabari Smith Jr. (55 → 76).
(Giannis at 55 vs ADP 6.2 is a GP effect, 46.9 projected games, and is the same in both files.)

**Mock-draft recommendations** (`fantasy draft recommend`, optimizer-0.2.0, objective cat_wins, fresh SQLite under the
scratchpad; slot 1, 3RR; my picks = the recommendation; the other 22 picks before #24/#36 follow Yahoo ADP via
`market_order`; score = expected category wins of the best final roster):

| pick | ours (baseline-0.2.1) | blend ours/ESPN 50/50 | blend ours/ESPN/FP 50/25/25 |
|:--|:--|:--|:--|
| 1 | **Jokić** (6.276), SGA 6.247, Wembanyama 6.183 | **Jokić** (6.074), SGA 6.011, Wembanyama 5.990 | **Jokić** (6.057), Wembanyama 5.943, SGA 5.940 |
| 24 | **Chet Holmgren** (6.267), Trey Murphy 6.253, Bam Adebayo 6.249 | **Kawhi Leonard** (6.067), Bam Adebayo 6.065, Derrick White 6.040, Holmgren 6.040 | **Bam Adebayo** (6.045), Kawhi 6.040, Mobley 6.026 |
| 36 | **Derrick White** (6.265), Desmond Bane 6.264, Knueppel 6.257 | **Derrick White** (6.040), Clingan 6.040, Kyrie 6.033 | **Derrick White** (6.025), Bane 6.024, Clingan 6.020 |

Scores are not comparable across columns, because each projection set also re-grades the opponents. Within a column the
top 3-4 candidates at #24/#36 are within 0.03 expected category wins, i.e. near ties under any of these projections.

## 5. Recommendation and limitations

**Recommendation:** switch the draft default to the 50/50 ours/ESPN per-game blend, keeping our GP (`fantasy data
build-projections --blend default`, then copy or point `--projections` to the blend file). Justification: a fitted +0.020 (0.007)
gain in top-150 rank accuracy and about 5-7% lower per-stat RMSE over 6 backtest seasons. The weight optimum is flat around 0.5,
and the blend is robust: it is never worse than the better single source on any stat. The cost is a modest reshuffle in
ranks 15-60 toward veterans and away from young high-minute players, plus a smaller apparent "edge" against the
market. If the lead prefers not to touch draft-path data on draft eve, the lower-risk option is to keep ours and use the
blend file as a tiebreaker when the optimizer's top candidates are within about 0.05 expected category wins.

Limitations:
- The ESPN backtest relies on ESPN's *stored* projections being preseason. 2022-23 visibly is not, and the others pass
  plausibility checks (accuracy comparable to ours, ESPN mpg correlation 0.76-0.82), but revisions cannot be ruled out.
- The backtest "ours" has no manual overlays, while production does (trades, injuries, roles, rookies), so the
  production-vs-ESPN gain from blending is probably smaller than measured.
- Every metric is per game. Games played is a separate model (`availability_model.md`), and blending it with ESPN is
  contra-indicated.
- Bootstrap SEs treat players within season as exchangeable. The between-season variance of the gain (+0.004 to +0.033,
  all positive) is the more honest uncertainty statement.
- FantasyPros weights are a prior (no history). The bbref per-36 projections, Hashtag Basketball and DARKO were not ingested.

## 6. Review fixes: `baseline-0.3.0` / `projections-2026-27-0.4.0` (2026-10-22)

**Two behaviour changes (TOV, minutes growth) and three clarifications with no effect on output.** Sizes are measured on the
unblended base build against `baseline-0.2.2`. The default file is the 50/50 blend, so for players ESPN covers about half of
each change reaches it.

1. **TOV and the age curve: kept exempt, on evidence (decided 2026-10-22).** `_per_minute_rates` scales makes, attempts,
   REB, AST, STL and BLK by `age_mult` but not TOV; the old docstring claimed "turnovers scale with usage too". A review
   flagged the mismatch and a fix (scale TOV too) was built and tested. *Out-of-sample check (fitted, thin):* bbref 2022-26,
   target seasons 2025 and 2026, rotation players ≥ 40 g and ≥ 20 mpg (n = 413). Pooled TOV MSE unchanged (paired Δ
   −0.00005, SE 0.0034). Bias (realised − projected) improved for ages ≤ 24 (+0.25 → +0.19, SE 0.04) but worsened for 33+
   (n = 38; +0.05 → +0.18, SE 0.07): old players' turnovers fall less than their production. With no pooled gain and the
   larger move against the change, the code stays as it was (TOV exempt) and the docstring was corrected to match. Had it
   shipped, LeBron 2.64 → 2.33 TO/g (rank 132 → 120), Durant 46 → 39, Knueppel 40 → 50. This settles the open question
   "projections docstring vs code on the age curve for TOV". Test: `test_tov_is_exempt_from_the_age_curve`.
   **TODO:** 9-season backtest (§1, full upstream totals) for TOV by age bucket; if young players' TOV is
   under-projected there too, give TOV its own age curve (rising for the young, flat for the old).
2. **`prior_min` units (no output change).** The rate prior was documented as "400 minutes" but was added to Σ w_s·min_s with
   weights 5/3/2 that sum to 10. That is about 80 real minutes for a player with only the latest season, and about 40 per
   season for three equal seasons. The new `effective_minutes()` = Σ (w_s / mean w)·min_s puts weighted minutes on a
   real-minute scale (three equal seasons of M count as 3M), and `prior_min = 120` effective minutes reproduces the old
   strength exactly. The undocumented `× 2` in the inclusion rule is now `min_total_minutes = 225` effective minutes, which is
   the same rule. Rebuilds are bit-identical outside TOV and minutes, and a test pins the legacy formula. Applying the literal
   documented value instead (400 effective minutes, 3.3× stronger) would move up to 1.5 pts/g and 20 top-150 ranks (Ty Jerome
   +20, Robert Williams +16, Thybulle +15), so the default keeps the old strength. **Prior, unfitted. TODO:** refit
   `prior_min` per stat in the §1 backtest. Counting stats and percentages probably need different strengths.
3. **`MPG_GROWTH` cliff (behaviour change, prior).** Young players got the full +0.5 to +3 mpg below 30 mpg and nothing at
   or above it, so 29.9 and 30.1 mpg projected 32.4 and 30.1. Growth now tapers linearly from full at 27 mpg to 0 at 30
   (`mpg_growth()`). The 3-mpg band equals the largest growth, so projected mpg never decreases as historical mpg rises
   (tested). Eleven players are affected, all losing minutes: Carrington −1.3 mpg, Castle −1.3 (rank 153 → 180 with the TOV
   change), Coulibaly −0.9, Mathurin −0.5, K. George −0.4, Podziemski / Duren / J. Green −0.3, Holmgren −0.2, Bailey and Sarr
   −0.1. §1 found ages 22-25 slightly *under*-projected in minutes, so this is a consistency fix and not an accuracy claim.
   Refitting `AGE_CURVE` / `MPG_GROWTH` is still the open item from §1.
4. **Manual gp scale per row (no output change).** Every hand-entered `gp` was multiplied by `manual_gp_scale = 0.90`,
   which converts values set on the old baseline-0.1 scale (`availability_model.md`). A value entered as a realistic
   expectation would have been cut twice. The new column `gp_scale` in `player_overrides_2026_27.csv` (and optionally in
   `rookies_2026_27.csv`) takes `baseline01` (also the meaning of a blank) or `fitted` (taken literally). The six existing gp
   rows are marked `baseline01`, so output is unchanged. **New rows with a gp should use `fitted`.** Unknown values raise.
5. **Documented, unchanged.**
   - `games_missed` multiplies the gp the player already has (fitted or manual) by (82−n)/82 and widens `gp_sd` by 0.15·n.
     Both are priors. A 10-game absence on a 60-gp projection removes 7.3 games, not 10.
   - The blend dilutes manual `mpg` / `usage_mult` / rookie priors to w_ours (50%) for every player ESPN covers (see the
     `blend.py` docstring and handoff 2026-10-19-blend-adoption).
   - `external._finish` now asserts that a directly given FG% / FT% (FantasyPros) is in (0, 1]. A percent-scale value
     would otherwise be clipped silently by `impute_attempts` (k ∈ [0.5, 2]). Percentages derived from makes and attempts are
     not checked: ESPN's stored 2020-21 and 2022-23 files contain 14 rows with FTM > FTA, and the blend clamps those.
