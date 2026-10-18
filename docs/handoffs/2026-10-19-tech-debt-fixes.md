# Handoff 2026-10-19 - tech-debt fixes before the draft (tech-debt engineer, cloud session)

## What was accomplished
The 12-step plan in `docs/research/tech_debt.md` was carried out before the draft (the user asked for it). Two
agents did the work: the first was killed by a container restart after the sqldump step, and the second
finished the stored draft order and the remaining steps. Status per step, with commits, is in
`docs/research/tech_debt.md` ("Plan status").

Draft-night criticals (all on main before this session resumed):
- `draft watch` survives transient DB errors (`2d88621`).
- Drafts are marked `complete` when the board is full (`1233764`).
- `draft pick --overall 0` / out-of-range `--overall` give a JSON usage error with exit 2 (`bb88c4b`). The
  three strict xfails in `tests/test_e2e_*` were removed, and no xfails remain in `tests/`.
- One CLI output/error module, `fantasy.cli_common`, keeps the existing keys and exit codes and adds `code`/`message` (`9eaf969`).

This session:
- `28c5f27` Draft order, single source: `draft init|set-order` store `drafts.config.order_slots`, the team slot
  of every overall pick, computed by `fantasy.draft.order`. `draft init` on an existing draft backfills it. The
  web (`DraftClient`, `/api/draft/pick`, `/api/agent/pick`, `/api/agent/state`) reads it through
  `orderForDraft`/`pickInfoForDraft`/`picksForDraft`. Those functions validate it (N×R entries, each round a
  permutation of 1..N) and fall back to the TS mirror.
- `c452ac8` Tooling: CI runs mypy on `ops db data league cli.py cli_common.py`, which is clean. These were
  type-only fixes: overloads on `row_dict`/`as_utc`, `cast`, and annotations. ruff is pinned to `0.16.*` and
  mypy to `2.4.*`, `pytest-cov` was added to dev, and a root `.env.example` was added.
- `524c16e` Web player ids: `agentPure.slugify` now mirrors `fantasy.data.ids.slugify`, and the new
  `canonicalId` applies `ids.ALIASES`. `resolvePlayer` tries the canonical id. The fixture
  `tests/fixtures/player_id_parity.json` covers 456 projected names plus edge cases and is checked by pytest
  and `npm test`. The unused exports `Stat`, `KIND_NAMES` and `POSITIONS` were removed.
- (last commit) One `git_sha`: `fantasy.ops.common.git_sha` resolves from the repo root and returns the full sha.
  `fantasy.model.valuation.git_sha` re-exports it. `model_runs.git_sha` changes from a short sha to the full
  sha for new rows. The dashboard truncates both formats. The value is cached per process and has no subprocess
  timeout. With a timeout, `Popen` polls with `time.sleep`, and `test_draft_watch_resilience` records
  every `time.sleep` call, so the full suite failed when git was slow under load (found before pushing).

## Golden diff results (vs origin/main at the time; ids/timestamps normalised)
Script: scratchpad `td/bin/golden.sh` (described in `docs/handoffs/2026-10-18-tech-debt.md`). It covers
settings show, values build --top 60, draft init, queue, recommend @1/24/36, board, error shapes plus exit
codes, draft simulate --sims 3 --seed 0, sim season (200 sims), and the build-projections hash.

| Commit | Result |
|---|---|
| `2d88621` watch, `1233764` lifecycle, `bb88c4b` --overall, `a514acd` cli split, `23936eb` projections split, `44e7cb9` sqldump | all identical |
| `9eaf969` cli_common | success outputs identical. Error JSON gained `code`/`message` keys, and exit codes are identical |
| `0914116` opponent sampler | identical except `sim season` (intended; season-sim-0.3.0). P(playoffs) for my team over 10 completion seeds: 0.517±0.053 → 0.443±0.038 (paired diff −0.075±0.044, ≈1.7 SE) |
| `28c5f27` stored order | identical except `draft init` JSON, which gains `config.order_slots` (intended) |
| `c452ac8` tooling | all identical |
| `524c16e` web ids | no Python source change. web lint/build/37 tests green |
| git_sha commit (+ mypy `follow_imports = "silent"`, docs) | all identical (base `f2f02ba`). The golden run executes on a git-less copy, so `git_sha` is None on both sides. pytest, `-m slow`, ruff and mypy subset green |

Every commit passed `python -m pytest -q` and `ruff check src tests`. Each commit that touched web/ also passed
`npm run lint && npm run build && npm test`. `pytest -m slow` (e2e) passed before the stored-order and
git_sha pushes.

## Decisions & rationale
- mypy runs on a clean subset only. The other 39 errors are in `draft/` and `model/` (code freeze tonight) and in
  `sim/` and `valuesearch/` (owned by other agents). Most are Optional-narrowing noise, not bugs.
- `git_sha` lives in `ops.common`, and valuation re-exports it, so the callers (draft.store, sim.persist,
  draft.simulate, valuesearch.train) are untouched.
- The lead relayed a report that the CI mypy step failed on model/availability, model/projections and draft/order. I could not reproduce it: CI on main is green and the local subset is clean. It happens when a listed module imports a not-yet-clean one, so `[tool.mypy] follow_imports = "silent"` now limits reports to the listed modules.
- Pushes were batched after the lead reported the Vercel 100-deployments/day limit.

## Outstanding work (deferred)
- Move the WIP-snapshot automation off main (orchestrator).
- Fix the remaining mypy errors module by module, then widen the CI list.
- `lib/categories.ts` duplicates the Python category list.
- Remaining F8 duplicates: NaN→"" ×3, settings-from-draft-row ×2, JSON-safe variants in db.rest.
- `test_draft_cli_hardening::test_input_validation` pins a literal objective list.

## Notes for next agent / rehearsal
- **Re-verify in the mock rehearsal:** on the dashboard draft page, check "your next picks" and the board grid
  against Yahoo's draft order. The order now comes from `config.order_slots`. For the real draft, run
  `fantasy draft set-order` (or `draft init` again) against the production DB after this change, so the row
  carries `order_slots`. Without it the page silently uses the TS fallback, which is still correct.
- Muse/agent name resolution: "Alex Sarr"-style alias spellings now resolve by id.
