# Handoff 2026-10-18 - value search (value-search subagent, cloud session)

## What was accomplished
- New package `src/fantasy/valuesearch/` (AlphaGo-style draft search): draft engine + policies (noisy ADP,
  optimizer with diversity, BPA, GreedyCat proxy), permutation-invariant league featurizer (69 features),
  HistGB value model exported to numpy (`.npz`, no sklearn/pickle at draft time), draft-time search,
  data generator, trainer, paired validation harness. Method, metrics, tables: `docs/research/value_search.md`.
- Artifact `data/models/value_search/value-model-0.1.0.npz` (0.56 MB) + `.card.json` (seeds, n, features,
  hyperparams, metrics, validation summary, git sha).
- `fantasy draft recommend`: `--search none|advise|value` (default **advise**: adds an advisory
  `value_search` block, ordering unchanged), `--rollouts`, `--search-candidates`, `--opponents adp|ours`,
  `--value-model`; `--objective` additionally accepts blend|title|playoffs|rank, only with explicit
  `--search value|advise` (default validation unchanged - fixed `test_input_validation` after the lead's note).
- `pyproject.toml`: optional extra `valuesearch = ["scikit-learn==1.5.2"]` (training only).
- Live-draft skill note on the advisory block.

## Results (fitted by simulation; SEs paired over 200 draft seeds)
- Data: 716 leagues = 8,592 rows (160-season labels), ~25 min on 3 workers; holdout 100 leagues with 2,000 seasons.
- Holdout vs 2,000-season simulator: P(playoffs) RMSE 0.042 (R² 0.96), P(title) 0.023 (R² 0.95), E[rank] 0.33 (R² 0.97).
  Within-room difference resolution weak (P(title) paired-diff corr 0.20 in ADP rooms).
- Latency (CLI wall, loaded 4-core box): 2.1–4.2 s (`adp`), ≤4.6 s (`ours`).
- VS − optimizer: (i) ADP P(title) +0.87 ± 0.35 pp (z 2.5), P(playoffs) +0.15 ± 0.09 pp (z 1.6);
  (ii) symmetric +0.6 / +0.8 pp (z≈1.6); (iii) market truth +1.1 / +1.1 pp (z≈4).
- **Decision: not adopted** as default (P(playoffs) fails z ≥ 2 in (i)); advisory + opt-in.
- **Side finding: BPA beats the optimizer** under the season simulator: symmetric rooms −6.1 ± 0.5 pp
  P(playoffs), −3.7 ± 0.4 pp P(title); ADP rooms −0.6 pp P(playoffs) (z −7), title n.s.

## Decisions & rationale
- Labels from all 12 teams per league (12× data); permutation invariance via sorted/mean opponent features.
- Blend objective 0.7·P(title) + 0.3·P(playoffs) is a prior (documented), z_min = 1 override rule a prior.
- Reused the optimism-decomposition agent's `perturb_projections` (symmetric opponents) and
  `market_implied_projections` (truth) instead of my own proxy (deleted).
- Stopped data generation at 716 leagues (flat learning curve) to give CPU to validation.

## Files changed
`src/fantasy/valuesearch/*` (new), `src/fantasy/draft/cli.py` (recommend flags), `tests/test_valuesearch.py`,
`data/models/value_search/*`, `pyproject.toml`, `docs/research/value_search.md`, this note,
`.claude/skills/live-draft/SKILL.md`, `docs/STATUS.md` (one priority line).

## Tests performed
`python -m pytest -q` all passed; `ruff check src tests` clean. New tests: featurization permutation
invariance, numpy-vs-sklearn tree export, save/load roundtrip, search determinism + pairing, board
completion, CLI flag (modes, objective validation, broken artifact → warning only).

## Problems encountered
- Shared 4-core box (load 6–11): OpenMP oversubscription made HGB training ~20× slower → `threadpool_limits(1)`.
- Validation took ~1.6 h for 200 seeds × 2 regimes.

## Outstanding work / suggested next steps
1. **Before the draft: decide what to do about optimizer < BPA** (lead/user). Options: margin rule vs BPA,
   room-aware availability model, or default to BPA-by-value with optimizer as advisory.
2. Value model: more training data near strong teams; train on within-room pairs (ranking loss) since search
   needs differences, not levels; replace GreedyCat completions with the optimizer's plan.
3. Re-run validation after the SimParams/GAME_CV calibration lands.

## Notes for next agent
Scratch (not committed): `/tmp/claude-0/-home-user-nba/3d957df9-5f21-539c-aee3-a111143dc70f/scratchpad/vs/`
(`train.jsonl`, `holdout.jsonl`, `val.jsonl`, `table.py`, `relevance.py`). No DB writes, runs, locks or txns.
