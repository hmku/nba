# Handoff 2026-10-19 - review4 fixes: displayed prob_available_next, stale-board warning (cloud session)

## What was accomplished
1. **F1 - displayed availability** (`draft/room_sim.py`, `draft/optimizer.py`). With the default policy
   (`--availability sim`), the simulated "me" takes the best-value player at my current pick. That player
   then showed `prob_available_next = 0` even at a back-to-back pick. `simulate_availability(...,
   skip_my_current=True)` now skips my own pick at `current` (`k == current -> continue`, same seeds and
   draws). It is used ONLY for the displayed `prob_available_next` / `prob_available_at_pick` (and so the
   `reason` text) when I am on the clock. The optimizer's decision table, scores, ranking and plans are
   unchanged.
   - Evidence: a fresh SQLite mock (slot 1, 3RR, others by ADP, real projections) at my picks 1/24/36/37/60/61/84/85.
     `recommend --json` is identical to origin/main (862cc74) apart from `prob_available_next`, `reason` and
     the volatile `generated_at`/`model_run_id`/`compute_seconds`. Ware 60->61: 0.0 -> 1.0. Knueppel 24->36:
     0.0 -> 0.733. 36->37: 0.0 -> 1.0.
   - Cost: one extra room simulation on my own turn (~0.3-0.5 s; the runs were concurrent, so timing is noisy).
2. **M1** (`draft/cli_pick.py`). prepare-pick now warns "latest recommendation is for an older board;
   re-run recommend" when the saved recommendation's `as_of_pick` != max(taken).
3. **Docs**: in the live-draft skill, step 3 now says that at back-to-back picks every candidate is available
   next, so the tie-break is irrelevant: take `recommendations[0]`.
4. Tests: `tests/test_draft_review4_fixes.py`.

## Next
- Nothing new. The live draft is tonight (02:00 UTC).
