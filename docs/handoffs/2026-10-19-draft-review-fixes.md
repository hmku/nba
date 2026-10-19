# Handoff 2026-10-19 - draft-path review fixes (draft-fix engineer, cloud session)

## What was accomplished
An independent review of the draft path found issues A1-A4, B1, B3 and B5. Each one now has a regression
test that failed before the fix. Most tests are in `tests/test_draft_review_fixes.py`. Existing JSON keys
and exit-code meanings are kept, and every new key or code is additive.

- **A1 `draft watch` reloads the order.** It re-reads the draft row on every poll. The change key now includes
  `(order_slots, my_slot)`, and when that changes watch rebuilds the context and optimizer and prints "draft order
  changed". Test: `test_watch_reloads_order_after_set_order` (3RR -> snake -> record pick 25 -> next save is
  for #48, not #36).
- **A2 `set-order` refuses to silently re-own recorded picks.** `store.set_draft_order` compares each recorded
  pick's stored `team_slot`/`round` with the new order. If any differ, it exits 2 with code
  `order_conflicts_with_recorded_picks` and `conflicts` = `[{overall, old_slot, new_slot, player_id, player_name}]`,
  and nothing is written. `--force` rewrites those rows and lists them in `reassigned_picks` (always present, `[]`
  normally). A set-order before any picks behaves as before. Tests: `test_set_order_refuses_to_reassign_recorded_picks`,
  `test_set_order_before_picks_and_no_conflict_unchanged`.
- **A3 the board can be behind the clock.** `prepare-pick` computes `behind` = unrecorded picks before `--overall`.
  If any exist it returns exit 4 `DO_NOT_CLICK` with the message `board not synced: picks [...] not recorded`
  and `behind`. `board` and `recommend` add `behind` (relative to my next pick) plus a warning. `gaps` is unchanged.
  Test: `test_prepare_pick_refuses_when_board_behind_the_clock`. Two old tests that expected CLICK with gaps
  were updated: `test_prepare_pick_board_checks_and_gaps` and `test_sync_gap_warning_everywhere`.
- **A4 unknown names never overwrite.** There is a new `draft pick --allow-unknown`, and `--force` is now only its
  deprecated alias. Overwriting needs the explicit `--overwrite` and is reported as `overwrote` (always present,
  `null` otherwise). The duplicate-player check is never skipped, not even with `--overwrite`. Store:
  `record_pick(..., overwrite=)`, where the `force=` argument is an alias. Tests: `test_unknown_name_never_overwrites`,
  `test_unknown_without_flag_still_unresolved`, and `test_undo_and_rerecord` (now `--overwrite`).
- **B1 aliases.** `resolve_player` applies `ids.ALIASES` after slugging ("Cam Johnson", "Moe Wagner").
  Unknown ids are `unknown:<canonical_id>` on both sides: `names.unknown_player_id` == `agentPure.unknownId`, used by
  `/api/agent/pick` and now by the manual route for names not in the list (it used to store a null player_id).
  The parity fixture gained `resolve` and `unknown` sections and `extra: true` on edge-case names. Tests:
  `test_resolver_applies_aliases`, `test_unknown_id_is_canonical`, `test_fixture_resolve_and_unknown_match_python`,
  and the web tests "resolvePlayer: Yahoo spellings ..." and "unknownId equals ...".
- **B3 the web manual pick route** (`/api/draft/pick` POST) reads the board and fails with a 500 on a read error.
  It then decides with the pure `planManualPick`: the same player at the same pick is `unchanged`, the same player
  at another pick is always 409, and a different player at the pick is 409 unless `overwrite`. Without overwrite
  it does a plain `insert`, and 23505 becomes 409. With overwrite it upserts and reports `overwrote`. Web test:
  "planManualPick: no silent overwrite ...".
- **B5** `undo` of a missing pick exits 6 with `code: not_found` (`ok:false`, `removed:0` kept). `recommend` no
  longer repeats the optimizer's gaps warning. Test: `test_undo_missing_pick_is_not_found_and_single_gaps_warning`.

Skill and runbook changes:
- `live-draft` §1 has a new mid-draft order-change paragraph, and the Muse `draft_order` step now covers conflicts,
  `--force` only for a real re-assignment, and restarting watch. §2.1 uses `--allow-unknown`, says `gaps` **and**
  `behind` must both be empty, and lists the new exit-4 reason. §3 covers undo exit 6.
- `agent-run` exit table, `docs/runbooks/live_draft.md`, `docs/runbooks/dashboard.md` (allow_unknown id),
  `docs/ARCHITECTURE.md`, and the `cli_common` code list.

## Verification
- `pytest`: 403 passed. `pytest -m slow`: 2 passed. `ruff check src tests` is clean, and the CI mypy subset is clean
  (the touched draft modules are also mypy-clean). `web`: `npm run lint`, `npm run build` and `npm test` (40 pass).
- The review's scenario script was adapted to
  `/tmp/claude-0/-home-user-nba/3d957df9-5f21-539c-aee3-a111143dc70f/scratchpad/review1/bin/scen_fix.sh` (output
  `.../review1/scen_fix.txt`) and runs every issue on real projections. The original `scen.sh` was also run
  against the fixed tree, and its key sets differ from the old run only by the additive `behind`, `draft_id` and
  `overwrite` keys, plus "Cam Johnson" now resolving.

## Not done / open
- Skipped the other review items as instructed (B2, B4 and the remaining B5 cosmetics).
- Ordering choice: when an earlier pick is missing, `prepare-pick` refuses with the behind reason (exit 4) before it
  looks at the txn ledger. A pick that already has a submitted txn shows exit 7 only once the board is synced.
  Both outcomes mean do not click.
- The dashboard UI does not display `overwrote` or `behind` yet (it computes its own gaps).
- No DB/task state was changed (no `fantasy task` work; tests used temp SQLite).

## Addendum: re-review fixes (M1, M2, m1-m5)
- **M1** The dashboard manual pick route (`web/src/app/api/draft/pick/route.ts`) now resolves a typed name with
  `resolveManualPlayer` (new, `web/src/lib/agentPure.ts`), using the same resolver as `/api/agent/pick` over the
  full `playerPool` (drafted players included). "Cam Johnson" now becomes `cameron-johnson`, so a duplicate is
  caught as a 409. An ambiguous or unmatched name returns 400 with `reason` and `candidates`. `unknown:<id>` is
  used only with `allow_unknown`, which the panel sends from its "Record … as an unlisted player" button. The
  panel search also shows drafted players (flagged "drafted #N"), and a typed name that matches a recorded pick
  shows "already drafted at #N". The stale "(no player_id)" hint is gone.
- **m3** A 409 now carries `reason` (`pick_taken` | `duplicate_player` | `concurrent`). The panel offers
  "Overwrite?" only for `pick_taken`.
- **m4** A name without letters or digits is rejected: `draft pick --allow-unknown` exits 2 with
  `code: invalid_name`, and the web returns 400 (`reason: invalid_name`, also in `/api/agent/pick`).
- **m1** A `prepare-pick` refusal for `behind` now includes `behind_mine` (`[{overall, txn_id}]`). The message ends
  with "(your #N: confirm-pick <txn>)" when one of my own picks has a submitted txn.
- **m5** In `draft watch`, a context rebuild after an order change that fails with a non-DB error now logs one
  warning. Recommending pauses (a stale recommendation is never published), and the rebuild is retried on every poll.
- **M2/m2** The `live-draft` time budget now says to never click outside prepare-pick when it refuses with
  `behind`. The `agent-run` exit-4 row lists "board not synced (behind)".
- Tests: `tests/test_draft_review3_fixes.py` (`test_prepare_pick_behind_names_my_unconfirmed_pick`,
  `test_allow_unknown_rejects_name_without_letters_or_digits[3]`, `test_watch_survives_failed_context_rebuild`).
  In `web/tests/agent.test.mjs`: the alias test, the duplicate-409 test, the ambiguous-candidates test, and the
  no-letters test.
- Draft outputs are byte-identical: `draft recommend` at picks 1/24/36 on fresh SQLite matches origin/main
  after stripping ids and timestamps.
