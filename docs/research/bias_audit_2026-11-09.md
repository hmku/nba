# Bias audit after week 3 (2026-11-09)

Two read-only audits, 3-week sample (games 10-20..11-08). Scripts in `bias_audit_2026-11-09/`. Standings for teams other than mine are RECONSTRUCTED from ESPN box scores x daily active lineup captures (validated: 5/6 team-weeks within ±1 per stat vs Yahoo-final).

## Summary
- Projections run high per game (PTS -12%, MIN -7.6%, FG% -1.9 pp; clustered t 6-8), but ~half is the normal early-season pattern (ESPN preseason projections missed the same way in 2024-25/2025-26) and the rest is a league-wide scoring/shooting dip; uniform shifts mostly cancel in H2H. On currently rostered players the bias is small (PTS -0.39 ± 0.30) except FG% and TOV.
- Fixable model errors (>= 3 SE): team projected minutes unconstrained; players with minutes but no projection row dropped (incl. rostered Hezonja); sim streaming volume +6 games/team-week vs +2.3 observed.
- Simulator NOT overconfident: matchup Brier 0.175 vs 0.219 calibrated (n=18); category favourites 66.4% vs 66.3% predicted (n=162). Detectable only if slope < ~0.7; re-check ~week 8.
- Me 2-1 (15-12 cats) vs 2.10 wins / 16.3 cats expected at week starts.

# Projection bias audit, weeks 1-3 (games 2026-10-20..11-08; 146 games, 2,859 player-games, 371 projected players)

**Answer: yes, biased high, but mostly in a way that is either structural or league-wide.** Per game played, the draft-time
blend (d295766) over-projects PTS by 1.49 ± 0.20 (−12%), MIN by 1.90 ± 0.27 (−7.6%) and FG% by 1.9 ± 0.3 pp. About half of
that recurs every season: ESPN's own preseason projections missed the same way in the first 20 days of 2024-25 and 2025-26.
The rest matches a league-wide drop in shooting this season. For fantasy-relevant players (top-150 ADP) minutes are on
target; their bias is per-minute scoring and FG%. Fixable model errors (≥ 3 SE): **team minute totals aren't constrained**,
and **players who appear in box scores with no projection row are dropped**.
Method: SEs are clustered by player (fitted noise var a·m+b·m² gives player-level mean z² of 1.2–5.0, so the projection
error is far larger than game noise; pure-noise z's like PTS −13 overstate the evidence, so clustered t is used). Fitted
on 3 weeks; small sample.
## 1. League-wide bias per game played (actual − projected; blend; t = mean/clustered SE)
| stat | proj/g | bias ± SE | % | t | of which minutes | of which per-min rate | ours-only t | ESPN t |
|---|---|---|---|---|---|---|---|---|
| PTS | 12.47 | −1.49 ± 0.20 | −12% | −7.6 | −0.83 ± 0.12 | −0.66 ± 0.14 | −5.8 | −8.3 |
| REB | 4.59 | −0.40 ± 0.07 | −9% | −5.6 | −0.34 ± 0.05 | −0.06 ± 0.05 | −4.6 | −5.5 |
| AST | 2.85 | −0.29 ± 0.06 | −10% | −5.3 | −0.18 ± 0.03 | −0.11 ± 0.04 | −4.0 | −5.6 |
| 3PM | 1.39 | −0.12 ± 0.04 | −9% | −3.3 | −0.11 ± 0.02 | −0.01 ± 0.03 | −3.0 | n/a |
| BLK | 0.53 | −0.06 ± 0.02 | −11% | −3.6 | −0.04 ± 0.01 | −0.02 ± 0.02 | −2.9 | n/a |
| STL | 0.86 | −0.05 ± 0.02 | −5% | −2.1 | −0.06 ± 0.01 | +0.01 ± 0.02 | −1.4 | −2.2 |
| TOV | 1.47 | −0.03 ± 0.03 | −2% | −0.8 | −0.10 ± 0.02 | **+0.08 ± 0.03** (rate too low, t 2.8) | +0.1 | −1.0 |
| FG% | .474 | −1.93 ± 0.33 pp at actual FGA (pooled .457 vs .474) | | −5.8 | | | −5.4 | −5.7 |
| FT% | .788 | −1.06 ± 0.54 pp | | −2.0 | | | −1.5 | −2.4 |
| MIN | 24.97 | −1.90 ± 0.27 | −7.6% | −7.0 | | | −5.8 | −7.1 |
- **The ESPN half carries more of it** (same players: PTS −1.76 ESPN vs −1.15 ours; MIN −2.02 vs −1.45; FTA −0.12 vs +0.06).
- **Early-season benchmark (ESPN's projections, first 20 days):** 2025-26 PTS −0.76, MIN −2.0, FG −0.45 pp; 2024-25 PTS −1.13, MIN −1.5,
  FG −1.47 pp. The same sign shows up over full seasons (MIN −1.4/−0.7). So the bias beyond what is normal this year is ≈ −0.4..−0.7 PTS/g and ≈ −1 pp FG%.
- **League environment** (team per game, same 20-day window): PTS 113.3 vs 116.7 (2025-26), FG% 45.7 vs 46.8, 3P% 34.7 vs 35.8,
  TOV 15.1 vs 14.7. Historically FG% rises 0.3–0.4 pp after the first 20 days, so expect part of this to revert.
- **Rostered players** (168 on current Yahoo rosters; managers add hot players and drop cold ones, so this sample leans positive):
  PTS −0.39 ± 0.30, MIN +0.02 ± 0.30, TOV +0.15 ± 0.05, FG .466 vs .480. For the players that matter the bias is small, apart from FG% and TOV.
## 2. Heterogeneity (player-level WLS, weights = games, HC1; ~55 tests, so Bonferroni needs |t| > 3.3)
| effect | estimate | t | verdict |
|---|---|---|---|
| team's projected minutes per game above 240 (per +10) | MIN −0.25, value z −0.13 | −3.5 / −3.2 | **real**: team totals run 180–324 (SD 37; MEM has 25 rows = 324) |
| ADP tier: minutes residual | top-50 −0.47 ± 0.29, 51-150 −0.06 ± 0.38, rest −3.17 ± 0.41 | | minutes bias is **bench/fringe** |
| projected rookies (n = 11) | value z +2.1, PTS +4.4 | +3.5 / +2.8 | borderline; driven by Acuff (+11.9 PTS), Lendeborg (+11.4) |
| manual overrides (n = 115) | PTS −0.9 | −2.1 | weak |
| changed team (n = 28) | MIN −3.3; FG% +5 pp | −2.0 / +2.8 | weak |
| age, position, tier (value) | all \|t\| < 1.6 | | no effect |
- **Regression to the mean:** value slope (actual on projected) 0.98 ± 0.05, so projections are not over-dispersed overall. Per stat:
  AST 0.92 ± 0.03, REB 0.92 ± 0.04, 3PM 0.88 ± 0.05, BLK 0.86 ± 0.07 (2–3 SE below 1: specialists slightly too extreme); AST per minute 0.90 ± 0.03.
  Minutes slope 1.07 ± 0.04 with intercept −3.6: players projected for low minutes lose more.
## 3. Availability (projected share = gp/82; actual = games played / team games)
- All 456 players: gp-ols-0.2 +0.023 ± 0.015 (draft file +0.007 ± 0.016), so **no aggregate bias**. Healthy and without an injury window:
  +0.060 ± 0.016, which is the expected early-season effect (injuries pile up later). Older players are **not** over-projected
  (age 33+: +0.14 ± 0.05; they played more). Injury-window players +0.035 ± 0.046.
- Spread: projected share < 0.55 → 0.34 actual vs 0.47; ≥ 0.70 → 0.91–0.98 vs 0.74–0.83. On the ESPN-common sample: slope 1.17
  (ESPN 1.57), correlation 0.40 vs 0.41. 3 weeks can't test a season total.
## 4. Minutes (≥ 4 games; preseason mpg → actual; in-season ros mpg)
Gainers: Reed DET 13.5→26.6 (23.1), Acuff SAC 26.8→37.0 (32.3), J. Richardson ORL 12.6→22.6 (21.4), D. Mitchell MIA 27.3→35.5 (32.1),
Lendeborg GSW 24.3→32.2 (29.3), Da Silva ORL 22.6→30.4 (28.5), J. Hayes UTA 17.5→25.3 (22.6), Larsson MIA 22.3→29.4 (27.4),
Ja'Kobe Walter TOR 22.2→29.1 (26.9), W. Carter ORL 26.6→33.4 (31.3).
Losers: C. Williams MIN 24.0→3.7, Clifford SAC 26.4→6.2, Vučević ORL 26.4→8.6, W. Riley WAS 24.9→7.1, J. Edwards PHI 19.9→2.2,
N. Richards MIA 18.0→2.0, Hendricks MEM 20.3→4.5, Filipowski UTA 22.8→7.9, Mathurin NOP 29.6→14.9, Fontecchio MIA 17.3→3.2.
Projected ≥ 25 mpg: also Hardaway −11.7, Ayton −11.1, Zubac −10.2, Collier −9.6, Monk −8.9, Carrington −8.7, Sexton −8.3.
**Teams** (TVD = share of minutes allocated differently from projection; mean 0.28): SAC 0.42 (12.5% of minutes to unprojected players), UTA 0.40,
MEM 0.39, IND 0.37, BRK 0.36, CHI 0.35 (16.5% unprojected), DAL 0.35, OKC 0.34 (21.6% unprojected; projected only 180 min/g).
Most stable: BOS 0.12, HOU 0.14, SAS 0.15. Value over/under (9-cat z per game): Curry +8.6, Dončić +6.7, Dunn +6.6, D. Robinson +6.6,
Zion +5.7 / Gobert −9.1, Vučević −8.4, Filipowski −8.0, Ware −7.9 (rostered by us), Zubac −7.7.
## 5. In-season model (ros-proj-0.5.0)
- Per-minute shrinkage matches its formula M/(n0+M) exactly (median weight on observed: REB 0.33, AST 0.43, FGA 0.51, STL 0.13).
  Minutes move 0.44–0.49 of the observed gap for ≥ 4 games, and 1–2-game flukes only 0.11 (Knueppel: one 6-min game, 32.8→28.5).
  Value moves 0.35 of the observed residual. **Reasonable.**
- But it shrinks toward a prior whose league level is off (FG%, PTS/min, TOV/min), and no league-level offset is ever learned.
- **Coverage gap:** 90 players with minutes have no preseason row (6.5% of all minutes; 87 still missing in-season), and `update_projections`
  drops them. That includes **rostered Mario Hezonja** (CLE, 204 min in 10 g; roster id `unknown:mario-hezonja`) plus T. Lyles, A. Mara, B. Stirtz.
  So FA breakouts are invisible to the tools. Name-normalised matching found **0 ESPN↔projection id mismatches**. (The `GG Jackson` override
  does apply to `gg-jackson-ii`: suffix-stripped match.) 80 players have 0 GP after ≥ 3 team games
  (stats kept at the prior, gp 39→19), including rostered Ingram, Butler, Nurkic (and Claxton, who returned 11-08).
## Suggested fixes (only where evidence ≥ ~3 SE; backtest first)
1. **Team minute budget**: scale each team's projected mpg × share to ≈ 241.5 × (1 − expected share for unprojected players), mostly by
   trimming bench minutes (t 3.5; team totals 180–324). Backtest on 2024-25/2025-26.
2. **League-level offset in the in-season update**: estimate per-stat league rate offsets (FG% −1.9 ± 0.3 pp, PTS/min −5.7%,
   TOV/min +5.6%) and their SEs, shrunk toward the historical early-vs-full drift (FG% recovers ~0.35 pp), then apply them to the
   priors before player shrinkage. This matters mainly for absolute weekly totals and for FA-vs-rostered comparisons;
   H2H category comparisons are mostly unaffected by a uniform shift.
3. **Population prior for unprojected players** (position-level replacement per-minute rates, shrunk with n0) so the in-season
   file covers everyone with minutes, including rostered Hezonja. Extend the rookie file beyond the 11 picks.
4. Watch, don't fix yet: rookie priors (t 3.5, but 2 players drive it), specialists' AST/REB/3PM/BLK slopes 0.86–0.92 (2–3 SE),
   and the GP spread. No evidence for age, position or GP-for-older-players fixes.


---

# League weeks 1-3 + simulator calibration (2026-11-09)

**Answer:** We're 2-1 (15-12 cats), right in line with week-start expectations (2.10 expected wins, 16.3 expected cats). The simulator is **not overconfident**. If anything it is slightly underconfident: favourites won 15/18 matchups against 64% predicted. Its one clear bias is volume: the streaming model adds a flat +6 games per team-week, and real teams added only about +2.3.

**Data basis:** All 3 of my matchups use **Yahoo-final** totals. The other 15 matchups are **reconstructed** (`*` below): ESPN box scores × active lineup slots from the daily roster captures, via `fantasy review week --team`. Validation against Yahoo-final on 6 team-weeks:
- 5 of 6 match within ±1 on every raw stat.
- W1 Joshua is short 2 games (−24 PTS, −22 FGA), which flips 1 of 9 categories (PTS). The cause is intra-day lineup changes the captures missed.
- So reconstructed W1 results carry about a 1-in-9 category-flip risk per team-week; W2 and W3 captures were denser.

## Standings (slot / team: W-L, cat W-L-T, results)
| # | Team | W-L | Cats | Weeks | Strong / weak cats (W-L over 3 wks) |
|---|---|---|---|---|---|
| 4 | Haagen Daz Center | 3-0 | 20-7 | W 8-1*, 6-3*, 6-3* | FG% FT% 3PM PTS 3-0 / AST 0-3 |
| 8 | Ed's Extraordinary | 3-0 | 19-7-1 | W 6-3*, 6-2-1*, 7-2* | PTS REB AST STL FG% 3-0 / 3PM 0-2 |
| 6 | Yeet | 2-1 | 16-11 | L*, W*, W* | FT% 3PM REB 3-0 / FG% 0-3 |
| 1 | **Harrison (me)** | 2-1 | 15-12 | W 6-3 v12, L 3-6 v7, W 6-3 v2 | REB BLK 3-0 / FG% AST 0-3 |
| 3 | Waiver Warrior | 2-1 | 15-12 | W*, W*, L 2-7* | BLK 3-0 / REB 0-3 |
| 7 | Andrew's Awesome | 2-1 | 14-13 | W*, W 6-3 v1, L* | AST 3-0 / BLK TOV 0-3 |
| 11 | Pledge Allegiance (FLAGG) | 2-1 | 13-14 | W*, L*, W* | / BLK 0-3 |
| 10 | Christian's Bad | 1-2 | 13-13-1 | L*, L*, W* | BLK 3-0 |
| 2 | Justin's Groovy | 1-2 | 12-15 | L*, W*, L 3-6 v1 | balanced |
| 5 | Yes it's a mess | 0-3 | 10-17 | L 4-5*, L*, L* | / FT% 3PM PTS REB 0-3 |
| 12 | Joshua's Excellent | 0-3 | 8-19 | L v1, L*, L* | / 3PM REB BLK 0-3 |
| 9 | Jeffrey's Great | 0-3 | 6-21 | L 1-8*, L*, L* | / FT% PTS REB STL 0-3 |

## Me: actual vs expected
| Wk | Opp | Result | P(win): week-start sim (20k) / analytic lineup plan / post-draft | Exp cats → actual |
|---|---|---|---|---|
| 1 | Joshua | W 6-3 | 0.71 / 0.62 / 0.73 | 5.37 → 6 |
| 2 | Andrew | L 3-6 | 0.77 / 0.81 / 0.77 | 5.80 → 3 (Andrew 3PM +1.8σ, TOV +2.5σ) |
| 3 | Justin | W 6-3 | 0.62 / 0.63 / 0.72 | 5.08 → 6 |

- **Sum:** 2.10 expected wins vs 2 actual. Post-draft sim: 2.21 wins and 16.8 cats expected, vs 15 cats (−1.8, about −0.7σ).
- **Category profile behaves as drafted.** The post-draft sim expected about 0.9 FG% wins in 3 weeks (we got 0) and 1.6 AST (got 0); REB 2.4 (got 3) and BLK 2.1 (got 3).
- **FG%** is our only persistent miss: total z vs the sim −1.9, −0.8, −0.9. About −0.75σ of that is league-wide (see bias below), so our FG% relative to the league is only about −0.4σ.
- **Volume:** games were on plan in W1 (31 vs 31.1 expected) and W3 (41 vs 40.9). W2 was 40 vs 32 (the week-start expectation missed adds).

## Calibration (week-start sim re-run at the Monday-morning commits: W1 4bb561a on the preseason file, W2 f36d3ef, W3 8e1d8ae; 20k sims; outcomes as above)
- **Matchup P(win), n=18**
  - Brier 0.175, vs 0.219 expected if calibrated and 0.25 for coin-flips. Gap −0.044 ± 0.030 (1.5 SE).
  - Favourites: 64% predicted, 83% won (+19 ± 9 pp).
  - Logistic recalibration slope 2.5 (90% CI 0.7–5.0; >1 means underconfident). No overconfidence.
- **Category P(win), n=162 in 18 clusters** (cluster-bootstrap SEs)
  - Brier 0.204 vs 0.213 if calibrated (gap −0.009 ± 0.009).
  - Favourites: 66.3% predicted vs 66.4% won (± 3.2 pp). Slope 1.15 (90% CI 0.90–1.52).
  - Reliability bins: 0.5–0.6 → 0.47 (n=47); 0.6–0.7 → 0.68 (n=58); 0.7–0.8 → 0.81 (n=37); 0.8–0.9 → 0.78 (n=18).
  - **What's detectable:** with a slope SE of 0.18 we would only detect overconfidence if the true slope were below about 0.7. The point estimate leans the other way.
- **Per category:** only TOV looks off. Favourites won 44% vs 67% predicted (n=18, about 2 SE), which is not significant across 9 tests. All other categories are within ±11 pp.
- **Weekly totals vs the sim's own mean/SD** (36 team-weeks; mean z ± SE; var z with 90% CI).
  - *Streaming on (default):*
    - Games −3.7/team-week (z −1.30 ± 0.20).
    - That volume gap drags every count stat low: PTS −0.60, REB −0.59, BLK −0.68, 3PM −0.52 (each ± ~0.14).
  - *Streaming off:*
    - Games +2.3 ± 0.5 (z +0.69 ± 0.16).
    - Count stats roughly unbiased: PTS +0.21, REB +0.33, AST +0.36 (± ~0.14).
    - TOV +0.85 ± 0.19 is high.
  - *FG% is low in both versions:* −2.0 pp (z −0.72 ± 0.16). It is league-wide: 45.7% actual vs 47.7% projected on the same attempts (2,859 player-games). A common shift like this cancels in H2H.
  - *var(z):* about 1 for FG%, STL, TOV and games. 0.57–0.71 for 3PM, AST, PTS, FT% (90% CI upper bounds 0.89–1.10). So the sim's weekly SD is not too narrow, and it may be 15–25% too wide for 3PM and AST.
- **Week-review z** (`fantasy review week`, per-game noise given games played): var z is 1.4–1.6 for PTS, 3PM and TOV. Its z understates weekly variance by about √1.5, as the 11-01 handoff already noted. Divide those z by about 1.2 before reading them as surprises.

## Over/underperformers vs the post-draft sim (`sim-20261020033348-d0613f`; 3-wk expected cats = exp_cat_wins/17×3)
- **Over:** Ed +5.9 cats (pre P(po) 53% → now 82%), Haagen +4.8 (76% → 77%), Yeet +2.7 (48% → 70%).
- **Under:** Jeffrey −7.0 (43% → 18%), Joshua −5.3 (47% → 15%), Yes it's a mess −3.3 (48% → 38%), me −1.8 (92% → 87%).
- The spread of (actual − expected) is SD 3.9 cats, vs about 2.5 from weekly noise alone (empirical, rough). Correlation of pre-season expected vs actual: 0.36. Real team spread looks wider than the sim's. Plausible cause: manager activity. Over 3 weeks, Justin and Joshua played 10 and 6 fewer games than even the no-stream roster prediction, while Waiver Warrior and FLAGG played 16–17 more. The sim treats every manager identically.

## My season-sim trajectory (prod `model_runs`, 12k sims each)
- Post-draft: 91.9% playoffs / 33.7% title.
- 10-22..10-24: dropped to 75–78% / 17–20%. This came from the in-season projections plus an uncapped-roster artefact; the 10-24 roster-cap fix took it back to 84.5%.
- 10-25..11-08: flat at 86–88% / 23–29%; latest is 86.8% / 25.9%. Day-to-day moves (±2–4 pp title) are unpaired noise plus rival roster moves.
- **It has not moved with results:** banked weeks are not used (T-018 is still open), so the 2-1 record and rivals' 3-0 / 0-3 starts are invisible to it. Its levels are projection-only.

## Model-fix suggestions (only where the evidence supports one)
1. **Streaming volume (strong, z −1.3 ± 0.2 on games):** replace the flat +6 games per team-week with about +2.3 league-average. Better still, use a per-manager rate estimated from observed adds and games. This matters for absolute totals and for inactive rivals; it barely moves P(win), because both sides get the same +6.
2. **T-018 (banked results in the season sim)** is the main gap for P(playoffs) levels now that 3 of 17 weeks are played.
3. **Week-review z:** use the sim's weekly variance, or inflate by about 1.2, so "±2σ" headlines aren't overstated.
4. **No change to variance or correlation for overconfidence:** the data don't support it. Re-check at about week 8 (48 matchups, slope SE about 0.11). FG% and TOV level shifts are common to all teams; let in-season shrinkage absorb them.

Scripts and outputs are in this folder:
- `sim_weekstart.py`: week-start sims → `preds.json`, `preds_nostream.json`
- `analyze.py`: standings and calibration → `analysis*.json`
- `extra.py`: preseason comparison, trajectory, league FG%
- `wr/`: 36 `fantasy review week --team` JSONs
- `season_sims.json`, `week_reviews_prod.json`: read-only prod dumps
