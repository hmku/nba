# Handoff 2026-10-19 - draft policy: why BPA beat the optimizer, and the fix (draft-policy agent, cloud session)

## What was accomplished
- Graded draft decision rules in ONE world W used both for planning and for grading (user's consistency
  principle): rooms W_mixed (primary: 3 sharp + 8 noisy-ADP opponents, sharp seats random per room), W_adp,
  W_sym; full season simulator (400 seasons) on our projections, plus market / 50-50 truths as stress tests;
  slot 1, 200 paired drafts per world (value search in W_adp / W_sym: 120). Results:
  `docs/research/draft_policy.md`.
- Ablation (lead/user request): GreedyCat (objective only) ≈ BPA; the optimizer with availability simulated
  from W ≈ BPA; the default optimizer loses −2.6 pp P(playoffs) in W_mixed, −6.3 pp in W_sym → **the analytic
  ADP availability model is what loses value** (position-as-pick + ADP-only room), not the objective or a bug.
- New opt-in `OptimizerParams(availability="sim", room="adp|mixed|sym")` (`src/fantasy/draft/room_sim.py`;
  `fantasy draft recommend --availability sim --room mixed`); optimizer-0.3.1, default output byte-identical
  (checked on 6 boards). Sharp-drafter proxy fitted on 100 simulated symmetric rooms (disjoint seeds).
- Harness `fantasy.draft.policy_eval` (policy-eval-0.2.0): `mixed` room, arms greedycat / opt_sim /
  opt_sim_matchup / vs_sim / opt_oracle / opt_matchup, `rooms` subcommand (empirical room samples).

## Decisions & rationale
- **Default NOT changed** (brief). Recommendation for the user to approve before the 22:00 UTC freeze:
  `fantasy draft recommend --search value --availability sim --room mixed` (vs_sim). In W_mixed vs the default:
  +1.9 pp P(playoffs), +1.4 pp P(title) (z 8.7 / 4.7); ≥ BPA on P(title) in every world/truth; ≈2 s compute.
  Fallback without the value model: `--availability sim --room mixed`.
- Objective left as cat-wins: matchup / strength-aware proxies do not track the simulator better (§6).

## Files changed
- `src/fantasy/draft/room_sim.py` - new: Monte Carlo availability from the board under a room model.
- `src/fantasy/draft/optimizer.py` - `_pa()` hook, `availability`/`room`/`room_sims` params, payload `availability`; 0.3.1.
- `src/fantasy/draft/cli_recommend.py` - `--availability`, `--room`.
- `src/fantasy/draft/policy_eval.py` - mixed room, ablation arms, oracle availability, `rooms`.
- `tests/test_draft_policy.py` - room_sim properties, opt-in default, harness arms, oracle, CLI flags.
- `docs/research/draft_policy.md`, this note, `docs/STATUS.md` (priority 5).

## Tests performed
- `python -m pytest -q` → all passed; `ruff check src tests` → clean (details in the final commit).
- Determinism: harness re-runs reproduce BPA grades bit-for-bit; default optimizer payloads identical before/after.

## Problems encountered
- Main moved three times under the runs (GAME_CV default, noise model unification, review fixes); final grading
  is on the unified-noise code (valuation-0.3.0 / season-sim-0.5.0), checked numerically identical to `b7faa1b`.
- Heavy CPU contention (load 13-15 on 4 cores from other agents).
- My local commit was pushed by another agent's `pull --rebase && push` in the shared working tree (2063a7c).

## Outstanding work
- User decision on the default (needs approval before 22:00 UTC freeze). If approved: set
  `DEFAULT_SEARCH="value"` and default `availability="sim", room="mixed"` (one-line changes) and re-run tests.
- Slots 6/12 only graded on the earlier code (d07f903); same pattern there.

## Suggested next steps
1. Lead/user: decide the config for tonight (draft_policy.md TL;DR).
2. If adopted, rehearse one mock draft with the flags (live-draft skill) to confirm latency on the user's machine.
3. After the season starts: calibrate the room model against the real draft (which picks came from "sharp" teams).

## Notes for next agent
- No ops runs/locks/txns opened. Scratch results (jsonl per arm) are in the session scratchpad, not the repo.
- `room_sim.SHARP_*` constants are fitted, not priors; refit with `policy_eval rooms` + the fidelity check if the
  opponent model in the harness changes.
