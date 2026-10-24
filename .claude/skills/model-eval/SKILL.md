---
name: model-eval
description: Evaluate and improve the quant models - Monte Carlo draft simulation (optimizer vs best-player-available baseline), sensitivity to kappa / punts / objective / ADP noise, where the uncalibrated priors live (ADP sd, USAGE, stream_recovery; game noise is fitted in model/noise.py), backtesting (T-010) and calibration (T-016), and the rule that model changes bump MODEL_VERSION and need tests. Use when asked whether the model/optimizer is good, to tune or calibrate parameters, to compare strategies, or before merging any change under src/fantasy/model or src/fantasy/draft.
---

# Model evaluation & improvement

Pipeline: projections (`model/projections.py`, per-game means) -> per-game lines & variance
(`model/lines.py`) -> valuation (`model/valuation.py`: G-scores over replacement, makes/attempts for
FG%/FT%, availability + streaming; game noise `model/noise.py`) -> category win model (`model/catmodel.py`) ->
rollout draft optimizer (`draft/optimizer.py`, `optimizer-0.4.0`) with simulated-room availability (default
`sim`/room `mixed`, `draft/room_sim.py`; analytic Normal(ADP) `draft/availability.py` via `--availability adp`) ->
value search re-ranking the optimizer's top 6 (`src/fantasy/valuesearch/`, `value-model-0.2.0`; together the
default `recommend-0.2.0` policy) -> simulation (`draft/simulate.py`; policy evaluation under the season
simulator: `draft/policy_eval.py`, `docs/research/draft_policy.md`). The user is a quant: be rigorous, report uncertainty (sd / n), don't
over-claim from small simulations.

## 1. Draft simulation (optimizer vs BPA)
```bash
fantasy draft simulate --sims 40 --seed 1 --json > sim.json        # ~4 s per sim
fantasy draft simulate --sims 40 --seed 1 --slot 6 --json          # other slots
fantasy draft simulate --sims 40 --seed 1 --noise 1.5 --json       # noisier opponents (ADP sd x1.5)
fantasy draft simulate --sims 40 --seed 1 --strategy punt_ft --json  # force a strategy
```
Each run is recorded in `model_runs` (kind `draft_sim`, display-ready `summary` schema `draft_sim/1`,
documented in `docs/research/dashboard_data.md`; the dashboard shows the latest) unless `--no-save` -
use `--no-save` for experiments. `summary` also has `edge.{expected_category_wins,matchup_win_prob}`
{mean, se, p_optimizer_better, p_tie}, `picks_distribution`, `availability`, `category_win_probs`,
`example_rosters`, `explanations`. Legacy fields: `optimizer.expected_category_wins_mean/_sd`, `optimizer.matchup_win_prob_mean`,
`baseline_bpa.*` (same simulated room, my team takes best available by value), `edge_category_wins`
(optimizer - BPA, paired), `first_pick_counts`, `strategy_counts`; `sims[]` has each roster and per-cat
win probs. Compute a paired SE from `sims[]` (`expected_category_wins` vs
`baseline_bpa_expected_category_wins`) before calling an edge real. Opponents draft by noisy ADP and
the opponent in the win model is a league-average team, so absolute win probabilities are optimistic;
compare relatively. `simulate` replays the optimizer only (`value_search_replayed: false`); to compare full
policies (value search, BPA, rooms) under the season simulator use
`python -m fantasy.draft.policy_eval run|summary` (docstring; results in `docs/research/draft_policy.md`).
Order: without `--draft-id` the formula order from settings; with `--draft-id` (or `FANTASY_DRAFT_ID`)
that draft's custom order from `draft set-order` (`custom_order: true`).

## 2. Sensitivity
| Lever | Command | Notes |
|---|---|---|
| kappa (0 = z-scores, 1 = G-scores) | `fantasy values build --kappa 0 --no-save --top 40 --json` | compare `top[]` ranks vs default |
| punt | `fantasy values build --punt ft_pct,tov --no-save --json` | category keys: `fg_pct ft_pct fg3m pts reb ast stl blk tov`. Unknown names exit 2 with the `valid` list. `fantasy draft recommend --kappa K --punt ...` applies the same valuation params to recommendations. |
| strategy in the draft | `fantasy draft recommend --strategy punt_ft --no-save --json`; `strategy.scan` in a normal recommend | names: balanced, punt_ft, punt_fg, punt_tov, punt_ast, punt_pts, punt_fg3m, punt_ft_tov. Unknown name -> exit 2 with `valid`. |
| objective | `fantasy draft recommend --objective matchup --no-save --json` (default `cat_wins`) | on a 2026-10-18 test board the two objectives gave very different top-3 - worth investigating before trusting `matchup`. With the default value search `--objective` also accepts the value objectives `blend` (default) / `title` / `playoffs` / `rank`; unknown values exit 2 with `valid`. |
| opponent noise | `simulate --noise` | |
Use `--no-save` for experiments so they don't pollute `model_runs` / `draft_recommendations`, and a
scratch DB (`export FANTASY_DB_URL=sqlite:///<scratch>/eval.db; fantasy db init`) plus a `--mock`
draft for recommend experiments - never experiment on the real draft record.

## 3. Priors that need calibration (T-016)
| Prior | Where | Current | Calibrate on |
|---|---|---|---|
| Game-to-game noise var(stat, m) = a·m + b·m² (ONE model for valuation G-scores, the optimizer's category model and the season simulator; `noise_model` = noise-var-1.0 in run params) | `src/fantasy/model/noise.py` | **fitted** (valuation-0.3.0, season-sim-0.5.0); beats a constant CV out of sample on every stat; the constant-CV models and `FANTASY_GAME_CV` were deleted 2026-10-19 (git history keeps them) | done: `docs/research/sim_calibration.md` sections 2 and 8. Open: 15-24 mpg FGA/PTS/REB game variance still ~10-40% under-stated (minutes volatility) |
| ADP sd = `SD_FLOOR + SD_SLOPE * adp` (analytic `--availability adp` and the noisy-ADP drafters in the simulated rooms) | `src/fantasy/draft/availability.py` | 2.5 + 0.18*adp (prior, unchanged) | **first real sample done** (`docs/research/draft_calibration_2026.md`, 143 opponent picks): fitted sd ≈ 1.0 + 0.12·rank (slope CI 0.10-0.12, floor poorly identified), i.e. the real room was ~0.6-0.7x the prior spread; no evidence for "sharp" (our-value) drafters (weight 0, CI [0, 0.10]). One league, in-sample: re-grade policies in a fitted-spread room before changing defaults; re-run `docs/research/draft_calibration_2026.py` after every observable real draft |
| `USAGE` share of rostered games that count | `src/fantasy/model/catmodel.py` | 0.85 | in-season lineup data |
| `stream_recovery` share of missed games recovered by streamers | `ValuationParams` in `src/fantasy/model/valuation.py` | 0.5 | waiver activity / replacement-level games |
| Projection params (`Params`, `AGE_CURVE`, `MPG_GROWTH`, gp prior 68) | `src/fantasy/model/projections.py` | heuristics | backtest (T-010) |
Fit offline in a script or notebook outside `src/` first; move into code only with tests.

## 4. Backtesting (T-010, not built)
Goal: replay past seasons (e.g. build 2024-25 projections from 2021-24 data in
`data/raw/bbref_sumitrodatta/player_totals.csv`) and measure per-category projection error
(MAE / bias by minutes tier, age), gp calibration (coverage of gp_sd), and decision quality (value of
drafted teams under realised stats vs alternatives). Must avoid leakage: only data available before the
target season; manual overrides can't be replayed, so backtest the baseline only and say so.

## 5. Rules for model changes
- **Bump the version** of the component whose outputs change: `MODEL_VERSION` in
  `src/fantasy/model/projections.py` (projections), `PROJECTIONS_VERSION` in `model/blend.py` (blend),
  `MODEL_VERSION` in `src/fantasy/model/valuation.py` (valuation; also for `lines.py`/`catmodel.py`/`noise.py`
  changes that move values), `NOISE_MODEL_VERSION` (`model/noise.py`), `MINUTES_MODEL_VERSION` (`model/minutes.py`; also bump `INSEASON_MODEL_VERSION` in `model/inseason.py`), `OPTIMIZER_VERSION`
  (`draft/optimizer.py`, room sim), `RECOMMEND_VERSION` (`draft/cli_recommend.py`, default policy),
  `SIM_VERSION` (`sim/__init__.py`), value model `MODEL_VERSION` (`valuesearch/train.py`; artifact + the
  live-draft guard's expected version). `model_runs` records valuation, `draft_sim` and `season_sim` runs;
  recommendations carry `optimizer_version` / `recommend_policy`.
- **After every merged model change, refresh everything downstream (user requirement, 2026-10-24)** — before telling
  the user it's done: (1) rebuild projections (`fantasy data build-projections` if the preseason side changed, then
  `fantasy data update-projections`); (2) re-simulate the rest of the season (`fantasy sim season --league-file
  data/manual/league_rosters_2026-27.json --sims 12000 --scenario "<model change>: actual rosters <date>" --json`);
  (3) re-simulate the rest of this week (`fantasy lineup plan --date <today ET> --week --save --json`, on today's
  roster/matchup capture — re-capture first if stale); (4) check the dashboard shows the new runs (League / This week /
  Lineup tabs; `cd web && node scripts/league-state.mjs` if league state changed), and report old → new P(playoffs),
  P(title) and P(win week) with the model versions in the final message and the daily log.
- **Tests**: add or update tests with synthetic data (`tests/synthetic.py`, `tests/test_scoring.py`,
  `tests/test_optimizer.py`, `tests/test_projections.py`); no network; deterministic seeds.
- **Commit the fit script** next to the constants it produces (e.g. `scripts/gp_model/`, with its JSON report) — gp-ols-0.1's
  scripts lived in a scratchpad and had to be rebuilt to refit it.
- **Fit population = deployment population**: list who the deployed model is applied to (e.g. < 500-minute or lost-season
  players) and check residuals for every such group; gp-ols-0.1 silently extrapolated to them (−9.5 games).
- **Censored targets**: outcomes that need future data to be observed (a 0-game season counts only once the player returns)
  are missing from the newest seasons; don't calibrate on them and caveat their bias numbers.
- **Selection honesty**: if the specification was chosen on residuals that include the test years, only the coefficients are
  out of sample; say so and quote a deflated gain.
- Percent categories stay makes/attempts based; never average percentages.
- Keep `recommend` under ~3-5 s (60 s draft clock): check `compute_seconds`.
- Report before/after: top-30 rank changes, simulation edge with SE, latency. Ask the `quant-reviewer`
  subagent to review the diff. Don't change the model on draft day without the user's OK (draft-path code
  under `src/` and `web/` is frozen while the draft is running).
