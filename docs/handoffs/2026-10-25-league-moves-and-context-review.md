# Handoff 2026-10-25 - league-moves review + context review (main session, local)

## What was accomplished
- daily-routine §2: **persist verdicts** (`data/processed/verdicts/<tool>_$D.json`) and a **league moves review** of
  other teams' pickups/trades (expected value at decision time; classes not_available/noise/neutral/decision_miss/
  model_gap/hindsight; ledger `data/processed/league_moves_2026_27.csv`). First run done by a subagent (4 moves, no
  miss); its 8 procedure fixes are folded into the skill (pre-move league file, diff vs previous state, team-id kinds,
  waiver priority, baseline = open spot or planned drop, separate `league-moves_` prefix).
- New skill `context-review` (weekly sweep of the agentic context vs code; small fixes pushed, larger ones proposed).
- New test `tests/test_doc_commands.py`: every `fantasy` command/flag in CLAUDE.md, skills, agents and runbooks must
  exist in the CLI (413 invocations, 20 files). Fixed drift: `inseason stream --start/--end` (in-season-ops,
  league-loop), `approval ... --wait` (agent_ops runbook); `intel export-league` allowlisted (PLANNED).

## Problems / outstanding
- Creating the scheduled task `fantasy-context-review` (Mondays 05:30 ET, prompt "run the context-review skill") was
  blocked by the auto-mode permission check: the user must create it (or allow the tool).
- Possible model issue (not investigated): for Wiggins/Risacher adds, our P(title) rises (+0.7/+0.85 ± 0.55 pp) while
  P(playoffs) clearly falls. Worth a model-eval look.
- A helper `fantasy league undo-moves` (rebuild the pre-move league) would make the review cheaper; build it if the
  manual rebuild proves error-prone.

## Tests performed
- `tests/test_doc_commands.py` 25 passed; full `pytest -q` passed (subagent run, before the final skill edit);
  `ruff check src tests` clean.
