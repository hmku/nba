# Handoff 2026-10-19 - value model 0.2.0 (value-model retrain agent, cloud session)

## What was accomplished
- Retrained the value-search value model for the current world: 50/50 blended projections, `noise-var-1.0`,
  `valuation-0.3.0`, `season-sim-0.5.0`, opt_sim (`availability="sim"`, matching room) for my slot, rooms = the
  policy-eval worlds weighted towards W_mixed (55 % mixed / 25 % ADP / 20 % sym), paired candidate completions per room
  (K = 4, common season seeds), 500 seasons per label. 942 rooms = 3,768 leagues = 45,216 rows. Fresh holdout: 100 rooms ×
  4 completions at 2,000 seasons.
- Variants: plain refit, `fe` (tree within-room fixed-effects correction), `fel` (linear within-estimator correction).
  Selected **plain** (pairwise variants within ±0.01 of it on within-room correlation; tie → simpler).
- Validation vs `value-model-0.1.0`: level accuracy, within-room difference correlation (holdout pairs and the stored
  policy-eval leagues), and decision quality of `vs_sim` (`--search value --availability sim --room W`) in W_mixed / W_adp / W_sym,
  200 paired drafts each, full season-sim grader. Tables: `docs/research/value_search.md` "Value model 0.2.0 (2026-10-19)".
- **Adopted**: `value-model-0.2.0` is the default value-search artifact. vs_sim + 0.2.0 vs vs_sim + 0.1.0 (200 paired drafts per world, truth ours): W_mixed +0.49 ± 0.17 pp P(playoffs) (z 2.8), +1.18 ± 0.31 pp P(title) (z 3.8); W_adp +0.30 ± 0.11 / +0.67 ± 0.27; W_sym +1.75 ± 0.36 / +0.91 ± 0.29. Within-room P(title) corr: holdout 0.47 → 0.64 (± 0.05 paired), W_mixed policy leagues 0.22 → 0.35 (± 0.04). `draft recommend --search value --availability sim --room mixed`: 2.0–2.5 s wall; picks #1 Jokić / #24 Bam Adebayo unchanged.

## Decisions & rationale
- Adoption rule fixed by the task before the runs (W_mixed Δ ≥ −1 SE on P(title) and P(playoffs), within-room corr improves,
  no z < −2 in W_adp/W_sym). All five checks pass (table in the research note).
- 0.1.0's stored vs_sim runs were reused as arm A after a bit-for-bit reproduction check (6 seeds, W_mixed + W_sym) on `23e308b`.
- Data generation stopped at 942/1,400 rooms (time; learning curve flattening, +0.0005 P(title) RMSE per doubling).

## Files changed
- `src/fantasy/valuesearch/datagen.py` - `--gen v2` paired-room generator (`make_room_v2`, `REGIMES_V2`).
- `src/fantasy/valuesearch/train.py` - room-level split, `within_room` metric, `fit_variant` (plain | fe | fel), `--variant/--lam/--ridge/--fe-rows/--version`, versions in the card; `MODEL_VERSION = value-model-0.2.0`.
- `src/fantasy/valuesearch/model.py` - `DEFAULT_MODEL` → `value-model-0.2.0.npz` (one line); `FANTASY_VALUE_MODEL` env override (version name or path) for rollback; `TreeEnsemble.combine`, optional linear term; `fit_hgb(sample_weight=)`.
- `data/models/value_search/value-model-0.2.0.npz` + `.card.json` - new artifact (data, seeds, versions, selection, validation metrics).
- `tests/test_valuesearch.py` - combine / fe variant / within_room / env-override tests.
- `docs/research/value_search.md` - section "Value model 0.2.0 (2026-10-19)"; `docs/STATUS.md` - one line.

## Tests performed
- `pytest` -> 460 passed, 2 deselected (after rebase onto `4940316`); `ruff check src tests` -> clean; CI mypy subset -> clean. Manual: 0.1.0 vs_sim reproduction (6 seeds, bit-identical); latency of `draft recommend --search value --availability sim --room mixed` at picks 1 / 24 with both models (2.0-2.5 s wall); after the rebase, plain `fantasy draft recommend` (new default policy) loads 0.2.0: 2.9 s / 2.1 s wall at picks 1 / 24; the rollback env var switches it to 0.1.0.

## Problems encountered
- None blocking. My first stop command (`pkill -f`) matched its own shell; the generator stopped anyway (942 complete rooms, no partial room).

## Outstanding work
- Data generation could continue to 1,400+ rooms (`--n 1400` resumes) for a small further gain; the search's priors (blend weights, z_min, N, M) were not re-tuned with the new model.

## Suggested next steps
1. Lead: review; the swap is the model file only (no search/optimizer change). Rollback: `FANTASY_VALUE_MODEL=value-model-0.1.0`.
2. After the season starts, the model is only advisory for in-season tools (it is a draft-time artefact).

## Notes for next agent
- Scratch data (not committed): `/tmp/claude-0/-home-user-nba/3d957df9-5f21-539c-aee3-a111143dc70f/scratchpad/vm2/` (`train_final.jsonl`, `holdout.jsonl`, `eval_*.jsonl`, `exp.py`, `wr_compare.py`, `policy_wr.py`, `compare.py`).
- No DB, runs, locks or txns were touched.
