# Handoff 2026-10-19 - optimizer rollout placeholders (research subagent, cloud session)

## What was accomplished
- Opt-in rollout placeholder variants in `src/fantasy/draft/optimizer.py`:
  - `OptimizerParams.filler`: `market` is the default; `board` is the market window re-centred on the
    actual board; `available` is the expected best-available-by-value player.
  - `OptimizerParams.refine_passes`: a complement-aware fixed point.
  - Supporting pieces: `DraftContext.board_filler` and `DraftOptimizer.candidate_rollout`.
  - CLI: `fantasy draft recommend --filler ... --refine k`.
  - Non-default runs add `filler` and `score_passes` to the payload.
- Paired evaluation harness `python -m fantasy.draft.filler_eval`. It imports the policy_eval opponents and
  is graded by the full season simulator. It runs with `--availability adp|sim`.
- Research note `docs/research/optimizer_placeholders.md`. It covers (a) taken players, (b) the specialist
  bias, refinement convergence, outcome tables for slots 1/6/12 and both availability models, and timings.
- STATUS item 6.

## Decisions & rationale
- No default changed (brief). The evidence also supports keeping it:
  - every variant is within ±0.65 pp P(playoffs) and P(title) of the default;
  - `available` and `board` have a few significant *losses* (z ≈ −2.2 to −2.8);
  - the specialist bias is ≤ 0.01 expected category wins.
- Default byte-identity was checked with `fantasy draft recommend --json` at picks 1/24/36 on a fresh
  SQLite DB, origin/main vs this branch: the output was identical except for timestamps and
  compute_seconds. The rollout keeps its float association order for this.

## Files changed
src/fantasy/draft/optimizer.py, src/fantasy/draft/cli_recommend.py, src/fantasy/draft/filler_eval.py (new),
tests/test_optimizer.py, tests/test_filler_eval.py (new), docs/research/optimizer_placeholders.md (new),
docs/STATUS.md, this note.

## Tests performed
`python -m pytest -q` (full), `ruff check src tests`, the CI mypy subset; default-output identity check above.

## Problems encountered
- The box was shared (load ≈ 12 on 4 cores), so the timings are ratios.
- Main moved twice during the work (noise unification; draft-policy `availability="sim"`). I rebased both
  times. `board_filler` now uses the optimizer's `_pa`, so it follows `availability="sim"`.

## Outstanding work
None required for the draft. The raw jsonl outputs are in the session scratchpad and not committed.
Reproduce them with §7 of the note.

## Suggested next steps
Placeholder work is not where the optimizer's gap is. See draft_policy.md (availability model) for that.

## Notes for next agent
`refine_passes` ≥ 2 exceeds 2× the default compute. `--refine` is capped at 5 in the CLI.
