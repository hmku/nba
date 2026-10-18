---
name: model-eval
description: Evaluate and improve the quant models - Monte Carlo draft simulation (optimizer vs best-player-available baseline), sensitivity to kappa / punts / objective / ADP noise, where the uncalibrated priors live (GAME_CV, ADP sd, USAGE, stream_recovery), backtesting (T-010) and calibration (T-016), and the rule that model changes bump MODEL_VERSION and need tests. Use when asked whether the model/optimizer is good, to tune or calibrate parameters, to compare strategies, or before merging any change under src/fantasy/model or src/fantasy/draft.
---

# Model evaluation & improvement

Pipeline: projections (`model/projections.py`, per-game means) -> per-game lines & variance
(`model/lines.py`) -> valuation (`model/valuation.py`: G-scores over replacement, makes/attempts for
FG%/FT%, availability + streaming) -> category win model (`model/catmodel.py`) -> rollout draft
optimizer (`draft/optimizer.py`) with ADP availability (`draft/availability.py`) -> simulation
(`draft/simulate.py`). The user is a quant: be rigorous, report uncertainty (sd / n), don't
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
compare relatively. Caveat: `simulate` uses the formula order from settings, not a custom order
stored with `draft set-order`.

## 2. Sensitivity
| Lever | Command | Notes |
|---|---|---|
| kappa (0 = z-scores, 1 = G-scores) | `fantasy values build --kappa 0 --no-save --top 40 --json` | compare `top[]` ranks vs default |
| punt | `fantasy values build --punt ft_pct,tov --no-save --json` | category keys: `fg_pct ft_pct fg3m pts reb ast stl blk tov`. Unknown names exit 2 with the `valid` list. `fantasy draft recommend --kappa K --punt ...` applies the same valuation params to recommendations. |
| strategy in the draft | `fantasy draft recommend --strategy punt_ft --no-save --json`; `strategy.scan` in a normal recommend | names: balanced, punt_ft, punt_fg, punt_tov, punt_ast, punt_pts, punt_fg3m, punt_ft_tov. Unknown name -> KeyError traceback (exit 1). |
| objective | `fantasy draft recommend --objective matchup --no-save --json` (default `cat_wins`) | on a 2026-10-18 test board the two objectives gave very different top-3 - worth investigating before trusting `matchup`. Unknown values are not validated. |
| opponent noise | `simulate --noise` | |
Use `--no-save` for experiments so they don't pollute `model_runs` / `draft_recommendations`, and a
scratch DB (`export FANTASY_DB_URL=sqlite:///<scratch>/eval.db; fantasy db init`) plus a `--mock`
draft for recommend experiments - never experiment on the real draft record.

## 3. Priors that need calibration (T-016)
| Prior | Where | Current | Calibrate on |
|---|---|---|---|
| `GAME_CV` per-game coefficient of variation per stat | `src/fantasy/model/lines.py` | fitted `GAME_CV_FITTED` is the default since valuation-0.2.3 (pts 0.42, stl 0.97; no change to picks 1/24/36); old prior (pts 0.35, stl 0.85) via `FANTASY_GAME_CV=prior` | done for the simulator: `docs/research/sim_calibration.md` (variance ∝ mean, not constant CV) |
| ADP sd = `SD_FLOOR + SD_SLOPE * adp` | `src/fantasy/draft/availability.py` | 2.5 + 0.18*adp | Yahoo draft results vs ADP (pick - ADP residuals) |
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
  `src/fantasy/model/projections.py` (projections) or `src/fantasy/model/valuation.py` (valuation;
  also bump it for changes in `lines.py`/`catmodel.py`/`optimizer.py` that change values or
  recommendations - only valuation runs are recorded in `model_runs`, and recommendations link to them).
- **Tests**: add or update tests with synthetic data (`tests/synthetic.py`, `tests/test_scoring.py`,
  `tests/test_optimizer.py`, `tests/test_projections.py`); no network; deterministic seeds.
- Percent categories stay makes/attempts based; never average percentages.
- Keep `recommend` under ~3-5 s (60 s draft clock): check `compute_seconds`.
- Report before/after: top-30 rank changes, simulation edge with SE, latency. Ask the `quant-reviewer`
  subagent to review the diff. Don't change the model on draft day without the user's OK.
