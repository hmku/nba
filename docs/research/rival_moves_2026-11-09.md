# Rival moves retrospective, 10-22..11-08 (58 moves by 11 rival teams; box scores through 11-08)

**Answer: no systematic edge.** Their pickups produced a bit less than our projections said (−0.75 ± 0.55 z/g, n=46), and our
projection-ranked top FAs out-produced what they picked. Two things they did see that our tools miss: **minutes trends**
(this is a fitted, large-n result) and **fresh injury news** (anecdotal, 1–2 cases). Activity is not paying off.

Unit: 9-cat z per game (bias-audit definition), VORP = Σ over games played of (z − z of the 156th player).
Ex-ante = projection committed before the move day; ex-post = games while on that team's roster. SE of an ex-post
delta ≈ 5.0·√games. Sims: `sim_moves.py`, HEAD code, 4000 sims, paired, from THAT team's side.

## 1. Most consequential moves: Δ P(playoffs) for the moving team, pp ± SE (ex-ante proj | 11-08 proj)
| date | team | move | ex-ante | 11-08 | ex-post VORP in−out | class |
|---|---|---|---|---|---|---|
| 10-26 | Waiver Warrior | +Dunn −Porter Jr | +0.7±0.5 | **+7.4±0.5** | +19.8 (SE 12) | lucky / model gap: L5 min 31 vs proj 26 |
| 10-26 | Joshua | +McCain −Jalen Williams | **−11.6±0.6** | +0.7±0.3 | −6.7 (13) | Williams-out news came out *after our morning build*: the news edge was real |
| 10-26 | Jeffrey | +Achiuwa −Clowney | +2.1±0.4 | +2.1±0.3 | +34.0 (19) | good |
| 10-22 | Joshua | +Braun −B. Lopez | −3.1±0.5 | −0.9±0.3 | +33.3 (20) | lucky |
| 10-24 | Yes it's a mess | +D. Robinson −Hartenstein | −2.0±0.6 | +0.7±0.6 | +20.9 (20) | lucky (noise) |
| 10-30 | Yeet | +Ja'Kobe Walter −Porziņģis | +0.8±0.4 | +0.2±0.4 | −23.5 (11) | unlucky |
| 11-04 | Jeffrey | +Klay −Toppin | +1.2±0.4 | +0.1±0.3 | +23.6 (11) | good |
| 11-01 | Waiver Warrior | +Shead (open spot) | −1.2±0.3 | −0.5±0.4 | +6.5 (5) | weekly stream (week-2 P(win) was its point) |
| 11-04 | WW ↔ Joshua | Haliburton for Reaves | WW −4.6±0.7, **P(title) +2.1±0.5**; Joshua +2.6±0.6 | WW −6.2 / J +3.2 | WW −28 (Hali hasn't played) | WW is betting on the title, which our P(playoffs) metric misses |
| 11-06 | WW ↔ Andrew | Acuff for Eason+Quickley | WW **−9.4±0.6**; Andrew +9.7±0.6 | WW −6.8 / A +6.7 | WW +23 (9) | bad ex-ante for WW; Acuff (rookie) is beating projection by 7 z/g |
All 58 moves (in/out, ex-ante/ex-post, signals, our verdict at the time): `out/moves.csv`. Counts: good 11, lucky 12, unlucky 7,
bad 24, no games yet 4. Only 10 of 58 ex-post deltas exceed 2 SE (6 of them "bad"), so classifications are mostly noise.

## 2. Do their adds beat our ex-ante view? (adds only; windows overlap → SEs are optimistic)
| comparison | mean ± SE | n |
|---|---|---|
| their adds: actual − our projected z/g | −0.75 ± 0.55 | 46 |
| top-40 FAs (by our projection) over the same days | −2.08 ± 0.15 | 53 |
| selection edge (their adds − FA pool) | **+1.35 ± 0.60** (WW +1.9±1.1, others +1.1±0.7) | 46 |
| their pick − mean of our projection-ranked top-3 FAs, VORP same days | **−6.2 ± 1.8** (our list did better) | 54 |
| their pick − top-3 adds in our stream-tool file that day | **+6.1 ± 2.4** (their picks did better) | 38 |
| their adds' minutes − projected mpg | +0.4 ± 0.8 | 46 |
- So they beat the average fringe FA by a little, but not our plain value ranking. Players they left on the wire produced:
  Thybulle (2.0 stl/g), Melton, R. Williams III, Vassell. Our **stream tool's** top adds did worse than their picks: Sexton −34
  VORP, Hauser, Grant. Its pair ranking is built for one week of our category needs and is a poor list of FAs to pick up.
- **They chased hot box scores, and it didn't work:** adds made after a hot last-5 games (z gap > +2/g, n=23) beat our projection by −2.1 z/g,
  no better than the pool (−0.1 edge). The other adds (n=27): +0.3 z/g, edge +2.6.

## 3. Which signals predict success? (large-n test: projected players, same-morning build, next 7 days; SE clustered by player)
| signal (before day d) → residual over the next 7 days | slope ± SE | n (players) | read |
|---|---|---|---|
| last-5 mpg − projected mpg → future mpg − projected | **FA 0.39 ± 0.10**; all 0.43 ± 0.07 | 1745 (191) / 3424 (339) | **under-reaction**: ~40% of a recent minutes gap persists beyond what ros-proj already absorbed |
| last-5 z/g − projected → future z/g resid | 0.10 ± 0.07 | 1745 | per-game box-score heat ≈ noise |
| projected mpg of a teammate newly out for the last 2 games → minutes resid | 0.035 ± 0.017 per min (≈ +1 min per 30-mpg absence) | 1745 | weak (2 SE); falls to 0.028 ± 0.017 controlling for minutes |
| games left this fantasy week vs average; category fit (their team's weakest 3 cats) | ρ −0.15 / +0.07 with add residual | 46 | nothing (small n) |
| unprojected players (Brazile ×2, Hezonja) | 3 adds, all below replacement so far | 3 | the coverage gap matters for *our* view, not as a rival edge yet |

## 4. Activity profile (standings rebuilt from box scores × daily lineups, weeks 1–3)
| team | moves | W-L | cats | active g/wk |  | team | moves | W-L | cats | active g/wk |
|---|---|---|---|---|---|---|---|---|---|---|
| Waiver Warrior | 20 | 2-1 | 15-12 | 38.3 | | Haagen Daz | 0 | 3-0 | 19-7 | 38.3 |
| FLAGG | 11 | 2-1 | 13-14 | 36.3 | | Ed's | 1 | 3-0 | 19-7 | 37.7 |
| Jeffrey | 8 | 0-3 | 6-20 | 35.7 | | Justin | 1 | 1-2 | 13-14 | 30.7 |
| Yes it's a mess | 5 | 0-3 | 11-16 | 37.3 | | Joshua / Christian / Yeet / Andrew | 3/3/2/4 | 0-3/1-2/2-1/2-1 | — | 30.7–37.3 |
- Moves vs category margin: Spearman −0.52 (n=12). Likely reverse causation: losing teams churn more. Moves vs active games: ρ 0.00.
  Waiver Warrior's 20 moves bought no more active games than Haagen Daz's 0 (38.3 both). This agrees with the bias audit (sim streams +6 g/wk, observed +2.3).

## 5. Suggested model / tool changes (by strength of evidence)
1. **Strong (t≈4–6, n>1700): react more to recent minutes.** Raise the minutes weight in ros-proj, or add a last-5 term to
   `mpg_next5`: the fitted ~0.4 extra persistence means moving ~0.65–0.75 of the observed gap, not ~0.45. Backtest out-of-sample (weeks 4–5) before merging.
   Dunn (+5 min, the 10-26 model_gap) is the case where this would have helped us.
2. **Medium: rank stream/opportunity candidates by season value too.** Today the stream tool's top adds trail a plain
   value-×-availability ranking by ~12 VORP over the same days (n=38, overlapping). Add the projection-ranked top-5 FAs to
   every daily verdict file and require them in the candidate set. Also put Thybulle, Melton and Vassell through `sim compare`.
3. **Medium (bias audit + Acuff, Lendeborg): rookies are under-projected.** Shrink rookies' per-minute rates toward
   observed faster. The Acuff trade looks "−9 pp for WW" partly because of this.
4. **Weak, cheap: a P(title) lens for trades.** The Haliburton deal is −4.6 pp P(playoffs) but +2.1 pp P(title) for WW.
   Show both in `trade-scan` and league-moves notes.
5. **Weak: injury news latency.** McCain for J. Williams: the news landed after our 07:30 build. The tip-off re-check
   (§6) should re-read injuries for *rival-relevant* FAs too, or the opportunity tool should run at 18:30 ET.
   The teammate-absence → minutes effect is small (+1 min per 30-mpg absence); don't build a reallocation model on it yet.
6. Don't add: hot-hand z (slope 0.10 ± 0.07), schedule-volume streaming value (ρ≈0, and the sim already over-credits streaming), or category-fit weighting.

## How to re-run (weekly)
```
source .venv/bin/activate   # from the repo root; read-only (git show + data files)
python <scratch>/moves/moves_review.py --start 2026-11-09 --end 2026-11-15 --out <dir>   # all-season: --start 2026-10-21
#  -> moves.csv (per move), signals_pool.csv, activity.csv, summary.json, sim_candidates.json
# optional team-perspective sims: pick rows of sim_candidates.json into a list like moves_sel.json, then
git archive HEAD src pyproject.toml | tar -x -C <scratch>/head && ln -sfn $PWD/data <scratch>/head/data
PYTHONPATH=<scratch>/head/src FANTASY_DB_URL=sqlite:///<scratch>/m.db python sim_moves.py --moves moves_sel.json --out sims.json
```
Caveats: moves come from capture diffs (pairing via league_moves CSV when it has a row, else inferred). A lineup capture
counts for a game day when it was taken ≤ 19:15 ET. Unprojected players get no ex-ante value. 3 weeks of data.
