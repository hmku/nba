# Handoff 2026-10-26 - weekly context review (scheduled, unattended, repo only)

## Fixed (small factual drift)
- Model versions in capability tables brought to the code: lineup-0.4.1, season-sim-0.8.1 (+ roster cap),
  inseason-0.3.x (trade-scan built, T-008), ros-proj-0.5.0 + minutes-0.2.0, opportunity-0.1.1 —
  `.claude/skills/in-season-ops/SKILL.md`, `.claude/skills/league-loop/SKILL.md`, `docs/ARCHITECTURE.md`,
  `.claude/skills/projections-update/SKILL.md` (example version).
- `docs/STATUS.md` (last updated 10-21): projections/in-season/sim/tools/lineup rows, latest sim run (86.9 / 27.6 %),
  privacy migration applied, advisor live, priority 2 (Champagnie done; Leonard IL -> one drop pending), dropped "07:10".
- `docs/USER_TODO.md`: draft header, Muse league check and advisor ticked.
- daily-routine: capture transfer via text + count/id-sum check (CSP lesson 10-24), FA script default N=150 vs top 50
  used, add/drop timing verified (10-24 11:18 ET), stale "once available" stream pointer.
- in-season-ops: dashboard tab names, projection updates no longer PLANNED, privacy-migration note removed.
  league-loop/agent-run: daily 07:00 (was ~10:00), `/approvals` -> `/inbox`.

## Proposed (needs the user; the auto-mode check blocked the first two as self-modification)
1. **Contradiction:** `in-season-ops/SKILL.md` §approvals says a scheduled run "never" executes approved add/drops;
   `daily-routine` (user decision 2026-10-20 / audit 10-25) and `docs/runbooks/dashboard.md` say it may if approved,
   unexpired and still matching Yahoo. Align in-season-ops to daily-routine (single home).
2. CLAUDE.md wording: "in-season-ops (Phase 2 placeholder)" -> "(capability map + approval policy)"; "Moves tab" -> "Inbox tab".
3. **IL eligibility:** code/skills treat only Out as IL-eligible (my P/Q/untagged IL players -> `infeasible_roster[mine]`),
   but Yahoo kept P/Q players in IL with no warning on 10-25. Verify Yahoo's IL rule, then decide whether my IL players
   are "sticky" in `lineup/plan.py` (10-22 moved Podziemski Q IL->BN unnecessarily).
4. **Two approvals per move:** `txn plan` files its own approval, so the user clicks twice (advice + txn). Either a
   `txn plan --approval-id` option or document the two-click flow in agent-run §5.
5. daily-routine §6 late window: add the early exit the 10-25 run used ("if none of my players tip ≥ 21:00, one line, exit").
6. Unexplained Yahoo lineup changes (10-25, no ledger txn, probably the user): rule = log, attribute, don't revert, re-plan.
7. Daily log length rule (≤ 25 lines) is not followed (10-24 ≈ 75 lines): enforce, or "morning ≤ 25, each §6 ≤ 8".
8. Housekeeping: 5 stale agent worktrees in `.claude/worktrees/`; CI mypy scope item in USER_TODO still open.

## Checked and fine
- `tests/test_doc_commands.py` (all fantasy commands/flags exist); backticked paths exist (web paths relative to `web/`).
- Scheduled-task crons = context times (07:00, 18:30, 20:45, Sat/Sun 14:30, Mon 05:30 ET); prompts only name a skill
  section/window. No mismatches.
- Roster sizes (10 + 3 BN + 3 IL) consistent; CLI flags spot-checked against `--help`.

## Tests
`pytest -q` pass, `ruff check src tests` clean, CI mypy clean. Docs-only change.
