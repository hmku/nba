# Handoff 2026-10-19 - unified game-noise model (quant engineer subagent, cloud session)

## What was accomplished
- ONE game-to-game noise model for draft and simulator: `src/fantasy/model/noise.py` (`noise-var-1.0`),
  var(stat, m) = a·m + b·m² (the fitted section-2 coefficients, previously simulator-only). API: `game_var`,
  `game_var_matrix`, `ratio_var`.
- Consumers switched: valuation G-scores, catmodel (optimizer), sim/market_truth, sim/engine.
- Deleted:
  - `GAME_CV_PRIOR`, `GAME_CV_FITTED`, `GAME_CV`, `GAME_CV_SOURCE` and the `FANTASY_GAME_CV` env var;
  - `lines.game_variance`;
  - `calibration.GAME_VAR` (moved to `model.noise`) and `GAME_CV_POOLED`;
  - `SimParams.game_var`;
  - their tests.
- Versions: valuation-0.2.3 → **valuation-0.3.0**, optimizer-0.2.0 → **optimizer-0.3.0**, season-sim-0.4.0 →
  **season-sim-0.5.0**. Run params record `"noise_model"` instead of `"game_cv"`; `valuation_params()` is still
  the cache key.
- Evidence, draft effect and all numbers: `docs/research/sim_calibration.md` section 8. Script:
  `docs/research/noise_oos_2026-10-19.py`.

## Decisions & rationale
- The variance function beats a constant CV out of sample on all 11 stats, in both temporal splits (ΔLL per
  game +0.17 to +0.75, SE ≤ 0.03). Its weekly variance is calibrated by minutes tier, where the CV is ×1.5-2.8
  too small for 15-24 mpg players. The fit on ≥ 24 mpg is kept (not ≥ 15) because the draft population is mostly
  ≥ 24 mpg.
- Draft effect is immaterial:
  - picks 1/24/36/61 are unchanged; 37 and 60 swap near-ties (margins 0.002-0.003);
  - top-60 is the same set, Spearman 0.997;
  - draft-sim edge 0.095 → 0.098 ECW;
  - preseason P(playoffs) 0.930 → 0.947 and P(title) 0.400 → 0.413, both within noise.
- The value-search model is not retrained; the mismatch is noted in `value_search.md` (advisory only).

## Tests performed
- `python -m pytest -q` passed. `python -m pytest -q -m slow` passed. `ruff check src tests` is clean. The CI mypy
  subset is clean.
- New tests are in `tests/test_noise.py`: monotone in the mean, reference values, shapes, ratio_var, a static
  check that no constant-CV path is left in src, and catmodel/valuation/market_truth using the model.

## Notes for next agent
- Handoff notes from 2026-10-18 still mention `GAME_CV`/`FANTASY_GAME_CV`. They are historical records, so they
  were left unchanged.
- `src/fantasy/draft/policy_eval.py` needed no change, because the `market_implied_projections` API is unchanged.
- Open: bench-player (15-24 mpg) FGA/PTS/REB game variance is still under-stated by ×1.2-1.4 (minutes
  volatility). This matters for in-season streaming, not for the draft.
