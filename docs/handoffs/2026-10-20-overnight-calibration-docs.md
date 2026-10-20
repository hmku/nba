# Handoff 2026-10-20 (overnight subagent): T-016 draft calibration, in-season docs, tech-debt status

Worktree branch `worktree-agent-a29437f3c1896217b`. Not pushed; the orchestrator merges it. Nothing touched Yahoo. The
DB was read only, with SELECTs on `drafts`, `draft_picks` and `draft_recommendations` for `nba.l.44495-2026`.

## What was accomplished
- **T-016, first real sample:** `docs/research/draft_calibration_2026.md`. Script `docs/research/draft_calibration_2026.py`
  (`dump` = read-only SELECT into git-ignored `var/calibration/`, `analyse` = offline and deterministic, ~2.5 min). Opt-in
  helpers `src/fantasy/draft/calibration.py` (a new module that nothing on the draft path imports) and
  `tests/test_draft_calibration.py`.
  - The real room was much tighter around Yahoo ADP than our prior. One-step pick-likelihood MLE gives
    sd ≈ 1.0 + 0.12·rank, against the prior 2.5 + 0.18·rank. Slope CI 0.10-0.12; the floor is poorly identified
    (0.5-2.5). LR 61; per-pick gain +0.21 nats (SE 0.04). The Tobit fit agrees: slope 0.126 ± 0.012.
  - No "sharp" (our-value) drafters: mixture weight 0, CI [0, 0.10]. Every manager's Bayes factor favours ADP.
  - The live `prob_available_next` was too spread: forecasts of 0.1-0.3 came true 1 time in 28. The mixed room did not
    beat the old analytic model on this draft (Brier +0.008, CI −0.001..+0.017).
  - **No default changed.** Recommendation: before the next draft, add a fitted-spread room world to the policy
    harness and re-grade.
- **`in-season-ops` skill rewritten** as a capability map + approval policy. It points to `daily-routine`,
  `season-sim`, `league-loop`, `yahoo-browser` and `agent-run`. Every command was checked with `--help`;
  `fantasy advise` is not on main, so it is listed as in progress.
- **`league-loop` BUILT/PLANNED table updated:**
  - BUILT: roster sync, lineup plan, inseason stream/trade, conservative run.
  - Still PLANNED: Muse snapshot converter, action queue, `--approval-expires-in`.
  - The daily cadence row now points to `fantasy-daily`.
- **README and ARCHITECTURE** now cover `league/sync`, `lineup/`, `inseason/`, `scripts/yahoo/`, the PWA, the
  `daily-routine` skill and the in-season CLI. The draft-night section is now past tense.
- **`model-eval` skill:** the ADP-sd prior row now points to the calibration note.
- **`tech_debt.md`:** added "Status 2026-10-20". F2/F3/F4/F5/F7/F9/F11 are fixed; F1/F6/F8/F10/F12-F16 are partly fixed,
  with the rest listed; new items N1-N5.

## Proposed edits for files this agent does not own
- **STATUS.md** (main session):
  - Known limitations, the "ADP sd and `USAGE` are uncalibrated heuristics" sentence → "ADP sd: first real-draft sample
    says the room was ~0.6-0.7x the prior spread and had no sharp drafters
    (`docs/research/draft_calibration_2026.md`, one league, in-sample; defaults unchanged); `USAGE` uncalibrated."
  - Next priorities item 5: T-016 → "T-016: draft room done (first sample); in-season tag priors / rest_share open".
- **CLAUDE.md** (user-owned), line 32: replace `in-season-ops (Phase 2 placeholder),` with
  `daily-routine (scheduled fantasy-daily), in-season-ops (in-season capability map + approval policy),`.

## Backlog hygiene (the main session updates the DB; this agent did not write to it)
- Close as obsolete:
  - T-014 (Yahoo pre-draft rankings): the draft is over.
  - T-002 (mock rehearsal): superseded by the live draft; it never ran.
- Mark done if still open:
  - T-007 (weekly matchup sim; a handoff says it was closed via REST, please verify)
  - T-009 (Monte Carlo draft sim, `fantasy draft simulate`)
  - T-001 / T-015 (settings / draft order verified 2026-10-18/19)
  - T-004 (roster sync), T-005 (lineup optimizer)
  - T-011 (scheduled routine `fantasy-daily` + `daily-routine` skill)
  - T-013 (DB URL available locally via `.env`), if it is still open
- T-016: add a progress note: "draft-room part done (draft_calibration_2026.md); in-season tag priors / rest_share remain".
- Still open: T-003 (Yahoo OAuth; blocked on Yahoo app approval), T-006, T-008, T-010.

## Tests performed
`python -m pytest -q` and `ruff check src tests` are green (see the commit). The new tests are synthetic only.

## Notes for next agent
- Re-run the calibration after any future observable real draft. Do not pool mock drafts, because Yahoo bots autodraft.
- `var/calibration/results.json` has the full per-pick table (p under prior / fitted / sharp per pick).
