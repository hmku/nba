# Handoff 2026-10-19 - value-model-0.2.0 review follow-ups (engineer agent, cloud session)

## What was accomplished
- **Live-draft value-model guard**: `.claude/skills/live-draft/SKILL.md` (setup, mock recipe, loop) and
  `docs/runbooks/live_draft.md` now require, on every recommend, `recommend_policy.value_search_status == "ok"`
  and `value_model_version == "value-model-0.2.0"`; otherwise stop and tell the user before picking (short clock:
  take `recommendations[0]`, log an observation, tell the user after). Setup and mock recipe start with
  `unset FANTASY_VALUE_MODEL` plus a one-line check.
- `draft recommend` (text mode) prints `!!! VALUE SEARCH FAILED ...` when the value search failed. JSON is unchanged.
- **Shipped-artifact test** `test_shipped_default_artifact_matches_current_features`: loads the real
  `data/models/value_search/value-model-0.2.0.npz`. It asserts that the feature names equal
  `features.feature_names(settings.categories, 12)` and the card's feature names, that `features_version ==
  FEATURES_VERSION`, that all 4 targets are present and that predictions are finite. Default suite, no network, <1 s.
- **Card** (`value-model-0.2.0.card.json`, the .npz is untouched): `notes` explains why the recorded optimizer-0.3.1
  is equivalent to 0.4.0 here. `data.sha256` and `data.sha256_sorted_lines` hold the hashes of train_final.jsonl and
  holdout.jsonl. Also added: `data.n_lines`, `data.train_rooms_missing` (400940, 400943, 400944) and `data.regenerate`
  (the commands).
- `docs/research/value_search.md`: runs were on a frozen snapshot of `177675e` (the card's git_sha), not `23e308b`.
  The Reproduce section points to the card hashes.
- `valuesearch/train.py`: restored the numpy-vs-sklearn max-abs-diff check for the plain variant. It goes into the
  card as `numpy_vs_sklearn_max_abs_diff` and prints a stderr warning if > 1e-6. It only affects future training runs.
  A smoke run on 400 leagues gave diffs ≤ 3e-13.

## Decisions & rationale
- Parity mismatch only warns and does not abort: it is a post-training check, so the run's output stays inspectable.
- The regeneration note gives sorted-line hashes because datagen writes lines in worker-completion order.

## Tests performed
- `pytest` -> 461 passed, 2 deselected (slow); `ruff check src tests` clean; CI mypy subset clean.
- Fresh SQLite, `FANTASY_VALUE_MODEL` unset: `draft init --draft-id nba.l.44495-2026`; `draft recommend --json` ->
  pick 1, `value_search_status` ok, `value_model_version` value-model-0.2.0, top Nikola Jokić.

## Notes for next agent
- Training data lives only in the training session's scratchpad (`.../scratchpad/vm2/`). It is not committed, so use the card hashes to check a regenerated file.
