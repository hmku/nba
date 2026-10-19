# Handoff 2026-10-19 - default draft policy switch (value search + simulated availability)

## What was accomplished
- **User decision (2026-10-19):** the recommended draft policy is now the default for tonight's live draft:
  `fantasy draft recommend` with no flags = `--search value --availability sim --room mixed`
  (evidence: `docs/research/draft_policy.md`, +1.9 pp P(playoffs), +1.4 pp P(title) vs the old default in W_mixed,
  200 paired simulated drafts, slot 1 - simulation evidence on our projections, not a guarantee).
- `DEFAULT_SEARCH = "value"` (`valuesearch/search.py`, only that line); `OptimizerParams(availability="sim",
  room="mixed")` by default -> **optimizer-0.4.0**.
- `draft recommend` and `draft watch` now share one `Recommender` (`cli_recommend.py`: optimizer -> `--policy`
  -> value search) built from one `RecommendConfig`, so watch precomputes exactly what recommend returns
  (test `test_watch_saves_what_recommend_returns`). Watch caches the value-search engine (projections,
  featurizer, model) across polls and only recomputes on a board/order change; it gained `--search`,
  `--availability`, `--room`, `--value-model`.
- Every recommendation payload (saved in `draft_recommendations`) has a **`recommend_policy`** block
  (`recommend-0.2.0`): `search`, `value_search_status` (ok|failed|off), `availability`, `room`, `room_sims`,
  `policy`, objectives, rollouts, `value_model_version`, `is_default`, `top_pick_decided_by`
  (optimizer|value_search|policy:<name>), `fallback`. Text output prints a `policy:` line.
- Fallback: value model missing/incompatible or search throwing -> warning + optimizer (sim-availability) order,
  `value_search_status: failed` (tests for both cases).
- `--policy bpa|hybrid|...` without `--search` now defaults to `--search advise` (a policy decides the top itself);
  explicit `--policy X --search value` is still a usage error. `opt_valavail` / `opt_sd50` pin `availability="adp"`
  (they are variants of the analytic model) -> draft-policy-0.2.0.
- Research / training harnesses pin `availability="adp"` explicitly so their published numbers, cost, and the
  concurrent value-model retraining (datagen via `valuesearch/policies.py`) are unchanged: `policy_eval`,
  `valuesearch/{policies,validate}.py`, `sim/{symmetric,draftcheck,cli}.py`. `filler_eval` already passes it.
- `draft simulate` (pre-draft sim): its "me" optimizer follows the new default availability (sim:mixed); cost is
  unchanged (5 sims: 14.9 s vs 18.2 s on main). Value search is **not** replayed there (≈1 s x 13 picks per sim;
  graded instead by `policy_eval` arm `vs_sim`); the summary records `optimizer_availability` and
  `value_search_replayed: false`.
- Mock rehearsals (`mock-N`) go through the same `draft recommend`, so they exercise the new default.
- Docs: live-draft skill (default, `recommend_policy` / `availability` / `value_search` fields, fallbacks),
  `docs/runbooks/live_draft.md` ("How to read a recommendation"), dashboard `HowItWorks.tsx` (availability +
  value search text, version fallback 0.4.0) and the DraftModel availability tooltip, `docs/STATUS.md`,
  `docs/research/draft_policy.md` (adopted note).

## Measurements (fresh SQLite, mock board slot 1 / 3RR, others take next in ADP (market) order, I take the new
## default's top; this 4-core container, wall time of `python -m fantasy.cli draft recommend --json`)
| pick | new default wall / compute (s) | old default wall (s) | old default top (advise + adp) | new default top |
|---|---|---|---|---|
| 1 | 2.22 / 0.95 | 3.48 | Nikola Jokić | Nikola Jokić |
| 24 | 2.13 / 0.90 | 2.72 | Kawhi Leonard | Bam Adebayo |
| 36 | 2.22 / 0.87 | 2.50 | Derrick White | Derrick White |
| 37 | 2.14 / 0.84 | 2.24 | Desmond Bane | Desmond Bane |
| 60 | 2.40 / 0.77 | 2.30 | Kel'el Ware | Michael Porter Jr. |
| 61 | 2.43 / 0.72 | 2.55 | Kel'el Ware | Kel'el Ware |
| 108 | 1.92 / 0.62 | 2.13 | Brandin Podziemski | DeMar DeRozan |
All ≤ 2.9 s (target ≤ 10 s). The new default's output equals origin/main run with the explicit flags
`--search value --availability sim --room mixed` at all 9 of my picks (same tops and top-3).
Room simulation measured at 0.05 s per call (300 sims, pick 1; optimizer recommend 0.34 s sim vs 0.51 s adp), so no extra caching was needed beyond watch's per-board recompute.

## Tests performed
- `python -m pytest -q` (454 tests) pass; `-m slow` (2) pass; `ruff check src tests` clean; CI mypy subset clean;
  `cd web && npm run lint && npm run build && npm test` (46) pass.
- New tests (`tests/test_valuesearch.py`): defaults applied + provenance saved + deterministic + old flags
  reachable + `--policy bpa` default; fallback for missing model and for a throwing search; watch == recommend.
  Updated: objective-validation expectations (value objectives valid by default), `test_draft_policy.py`
  (`BAL` pins adp; default-is-sim test). E2E recommend -> prepare-pick -> confirm-pick (`tests/test_e2e_live_draft.py`) passes.

## Outstanding / notes for next agent
- Rehearse one mock draft locally (`fantasy draft init --draft-id mock-N --mock`) to confirm latency on the
  user's machine; check `recommend_policy.value_search_status == "ok"` (the real artifact must load).
- The concurrent value-model retrain (value-model-0.2.0) may change `valuesearch/model.py:DEFAULT_MODEL`; nothing
  here depends on the path. If its feature names change without the featurizer, recommend falls back with a warning.
- No ops runs/locks/txns opened.
