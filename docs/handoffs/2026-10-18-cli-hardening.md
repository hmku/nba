# Handoff 2026-10-18 - CLI hardening before the live draft (cli-hardening agent, cloud session)

## What was accomplished
Fixed the 12 reviewer-reproduced CLI issues, investigated #13, and changed the Yahoo OAuth default
redirect URI (lead's extra request). All with pytest coverage on synthetic data + temp SQLite.

| # | Change | Exit codes / JSON |
|---|---|---|
| 1 | `fantasy run end` releases **every** lock held by the run (any end status, expired leases too); logs a note event | `released_locks: [...]` in JSON; text mentions them |
| 2 | `draft init`: ids starting `mock` need `--mock`; non-mock ids must match `nba.l.<digits>-<year>` unless `--force`. Warns if the draft already exists with a different `is_mock` | exit 2 |
| 2 | every `draft` command's JSON carries the resolved `draft_id` (`order`: null; `simulate`: null unless `--draft-id`) | |
| 3 | `prepare-pick` finds all draft_pick txns for (draft_id, overall): submitted/verified → refuse; `planned` for the same player → reused or **re-owned** from another run (e.g. dry run); planned for another player → cancelled; otherwise plan with attempt = max+1 (automatic retry after failed/cancelled) | exit 7 DO_NOT_CLICK (submitted/verified); output `attempt`, `txn_note` |
| 4 | `confirm-pick` on an already-`verified` txn records the pick (idempotent, `already_verified: true`, no evidence needed). Board conflicts now exit 2 instead of a traceback; non-draft_pick txn exits 2 | exit 0 |
| 5 | decision `choice` from `prepare-pick` includes `mode` (`dry_run`/`live`); output has `run_mode` | |
| 6 | `prepare-pick` refuses if `--overall` is not my slot's pick per the stored order, if the pick is already on the board, or if the player is already recorded as drafted; reports board `gaps` (+ warning) | exit 4 DO_NOT_CLICK (`my_picks` / `pick` in JSON) |
| 7 | `draft simulate --draft-id` (or `FANTASY_DRAFT_ID`) uses the draft's teams/slot and `custom_rounds` | `custom_order`, `order_type` in JSON; unknown draft exit 1 |
| 8 | `draft recommend --kappa K --punt cats` → `ValuationParams` (defaults unchanged) | `valuation_params` in JSON |
| 9 | `values build --punt`, `recommend --punt/--strategy/--objective`, `simulate --strategy` validate input; `set-order` bad round count / slots / junk | exit 2, JSON `valid: [...]` |
| 10 | `approval show --wait N` still pending after N s | exit 8, `{"ok": false, "error": "still_pending", "approval": ...}` |
| 11 | `"ok": true` added to `data build-projections`, `data top` (now `{"ok": true, "players": [...]}`), `values build`, `draft board/queue/order/simulate`, `recommend` | web/ and skills checked: only `projections-update` described the old `data top` list shape (updated) |
| 12 | `model/projections.py`: players in `player_overrides_2026_27.csv` are always kept past the `max_rows=450` volume cut (meta note "kept despite ... volume cut"). Rebuilt: 454 rows; Butler, Mark Williams, DiVincenzo, Jordan Miller now present (all `status=out`) | |
| + | Yahoo OAuth `DEFAULT_REDIRECT_URI` = `https://localhost:8080` (was `oob`); `--redirect-uri oob` still works for legacy apps | |

New exit code 8 (`EXIT_STILL_PENDING`) in `fantasy.ops.common`.

## Finding 13: `--objective matchup` vs `cat_wins` top-3 differ - expected, not a code bug
- `matchup` maximises P(win ≥5 of 9 cats) against a **league-average** opponent, with categories
  independent (`CategoryModel.matchup_win_prob`). A well-built roster already projects ~6.1-6.9
  expected category wins vs the average team, which puts P(majority) at 0.89 (real data) to 0.98
  (synthetic board): the objective is **saturated/flat**. On the synthetic board the top-3 matchup scores
  were identical to 4 decimals (0.9803 / 0.9802 / 0.9800) while their E[cat wins] differed by 0.09;
  the ordering there is numerical noise + candidate order (stable sort), so the top-3 "looks very different".
- On real projections the two objectives mostly agree (pick 1: same top-3 Jokić/Wembanyama/Dončić;
  pick 24: same candidates reordered within 0.002 matchup prob).
- The rollouts also differ: under `matchup` the greedy completion favours concentrating strength
  (push marginal cats over 0.5, ignore hopeless ones), so even for the same first player the planned
  roster's E[cat wins] can be lower (pick-36 synthetic: 6.53 vs 6.64).
- Units differ: `matchup` scores are probabilities, so the live-draft "treat within ~0.03 as ties" rule and
  `strategy_switch_margin=0.03` are much coarser there (almost everything ties).
- Recommendation: keep `cat_wins` as the draft objective. `matchup` would only be informative against a
  stronger opponent model (e.g. top-quartile team) and/or with category correlations (T-016 calibration).

## Decisions & rationale
- `prepare-pick` refusal for board problems is exit 4 (as specified) and is checked *before* the txn
  ledger, so "pick already recorded" is 4 even when a verified txn also exists.
- `simulate` resolves a draft only when `--draft-id`/`FANTASY_DRAFT_ID` is given (no DB access otherwise),
  keeping the previous default behaviour.
- Overrides force inclusion only for `player_overrides_2026_27.csv` (rookies/ADP rows were already all present).

## Files changed
- `src/fantasy/ops/{locks,ledger,cli,common,transactions}.py`, `src/fantasy/draft/cli.py`,
  `src/fantasy/model/{cli,projections}.py`, `src/fantasy/data/cli.py`, `src/fantasy/league/{yahoo_api,yahoo_auth}.py`
- `data/processed/projections_2026_27.{csv,meta.json}`
- tests: `tests/test_ops_cli_hardening.py`, `tests/test_draft_cli_hardening.py` (new), `tests/test_projections.py`, `tests/test_yahoo_api.py`
- docs/skills: `.claude/skills/{live-draft,agent-run,draft-prep,model-eval,projections-update,in-season-ops,yahoo-browser}/SKILL.md`,
  `docs/ARCHITECTURE.md`, `docs/runbooks/agent_ops.md`, `docs/research/yahoo_api.md`

## Tests performed
- `python -m pytest -q`: all 141 tests pass; `ruff check src tests`: clean.
- `fantasy data build-projections --json`: 454 rows; the four overridden players verified in the CSV.

## Problems encountered
- An automatic "WIP snapshot" committer captured several of my edits before I committed them
  (commits `cecdf62`, `a711334`, `18d3298`, `4bd2ef3`); content is identical and pushed.

## Outstanding work / next steps
- Rehearse with the new mock recipe (live-draft skill) in a local `claude --chrome` session.
- Consider a stronger opponent model for `matchup` (or drop it from the CLI) - see finding 13.
- `docs/STATUS.md` not touched (other sessions editing); add "CLI hardened" if useful.

## Notes for next agent
- Draft picks: `prepare-pick` → click → `confirm-pick`. Retry = run `prepare-pick` again; it handles attempts.
- `run end` frees locks; `approval show --wait` exit 8 means still pending - do not act.
