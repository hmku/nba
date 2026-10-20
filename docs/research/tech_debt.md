# Tech-debt audit (2026-10-18, day before the live draft)

Scope: `src/fantasy/` (league, data, model, draft, ops, db, sim, research modules), `tests/`, `web/`,
`.claude/skills`, `docs/`. Audited at `b96d5e9`; the "safe now" fixes were pushed as `8d15b37..12d358a`
(rebased). **Constraint:** the live draft is 2026-10-19 22:00 ET, so nothing on the draft path changes
behaviour before then. Research files being edited concurrently (`sim/engine.py`, `model/lines.py`,
`sim/symmetric.py`, `sim/market_truth.py`, `valuesearch/`, their docs) were audited read-only.

## How this was checked

| Tool | Result (src only unless noted) |
|---|---|
| `ruff check --select ALL` (report only) | ~3,000 hits incl. tests; meaningful ones: 8 `BLE001` blind `except Exception`, 2 `S110/SIM105` try-except-pass, 5 `C901` (`projections._apply_manual` complexity 26, `optimizer.recommend` 15, `status.register` 17, `draft prepare_pick` 13, `draftcheck` 11), 9 `PLR0915` long functions, 129 public functions without docstrings, 37 `Any`. The configured rule set (`E,F,I,B,UP`) is clean except the research WIP. |
| `vulture --min-confidence 60` | Mostly Typer-command false positives. Real dead code: `NINE_CATS`, `valuation.cat_labels`, `ops.cli.RequiredRunId`, `names.slug` fallback (removed), `CategoryModel.expected_cat_wins` + unused `weights` args, `store.set_draft_status` (unused, see F5), `yahoo_api.iter_players`, `nba_api_ingest.to_bbref_like` (documented optional path). |
| `pytest --cov` | 82% overall. 0%: `data/bbref_github.py`, `data/nba_api_ingest.py`, `db/ddl.py`, `db/sqldump.py`. Low: `league/cli.py` 42% (now tested), `data/cli.py` 50%, `data/provenance.py` 52%, `ops/testing.py` 65%, `yahoo_auth.py` 70%, `sim/cli.py` 75%, `ops/cli.py` 76%. `draft watch` (used on draft night per `docs/runbooks/live_draft.md`) had **no test** (now tested). |
| `mypy src/fantasy --ignore-missing-imports` | 71 errors in 22 files. `mypy` is a dev dependency but is run nowhere (not in CI, not in the end-of-session checklist). |
| `npx knip` (web) | No unused files/routes. 18 unused exports (mostly `lib/sim.ts` parsers exported only for internal use; `ui.tsx` `Stat` component unused), 12 unused exported types. `eslint-config-next` flagged unused (false positive: loaded via FlatCompat). |
| Docs vs code | every `fantasy <group> <cmd>` referenced in skills/runbooks/README/ARCHITECTURE exists; every repo path referenced exists. Stale items listed in F10. |

## Golden-output protocol (used for every change here)

Script: scratchpad `bin/golden.sh <worktree> <out>` (copy in the handoff). Fresh SQLite; records JSON of
`settings show`, `values build --no-save --top 60`, `draft queue`, `draft recommend` on a mock at picks 1, 24
(picks 2-23 filled in ADP order) and 36, `draft board`, `draft simulate --sims 3 --seed 0 --no-save`,
`sim season --draft-id mock-g --complete-rosters --sims 200 --seed 0 --no-save`, and the sha256 of
`data build-projections`. Timestamps/run ids/git sha/elapsed are stripped. Two runs of the base were
byte-identical (deterministic), and base vs. after-cleanup were identical.

## Findings, ranked by risk x payoff

Label: **safe now** = done today (no behaviour change) · **after draft** = planned below.

| # | Finding | Risk if left | Payoff | Label |
|---|---|---|---|---|
| F1 | **Red `main` from auto "WIP snapshot" commits.** At `2038204` `pytest` has 2 failures (`test_draft_cli_hardening::test_input_validation` expects objectives `[cat_wins, matchup]`, value-search added 4 more; `test_valuesearch::test_cli_search_flag`) and at `b96d5e9` `ruff` failed on `sim/market_truth.py`. CI runs on push to main; a red main hides real regressions the day of the draft. Research also added `draft recommend --search` (default off) to the draft CLI. | high | high | flag to lead now; process fix after draft (snapshots to a branch, not main) |
| F2 | **Top-level CLI silently dropped sub-apps that failed to import** (`except (ImportError, AttributeError): continue`). A broken transitive import in e.g. `draft/cli.py` turned `fantasy draft recommend` into "No such command" with no reason. | high (draft night) | high | **safe now: fixed** (still skipped, reason printed to stderr; tested) |
| F3 | **Draft order is implemented twice** (Python `draft/order.py`, TS `web/src/lib/draftOrder.ts`, "mirror") with no shared test. Divergence = dashboard shows wrong "your next pick". Edge-case difference: TS `customRoundsOf` silently falls back to the formula on invalid `custom_rounds`, Python raises. | high | high | **safe now: parity fixture** `tests/fixtures/draft_order_parity.json` checked by pytest and `npm test` (5 configs incl. custom). Single source (Python writes `my_picks` to the draft row) after draft |
| F4 | **Three noisy-ADP opponent models**: `draft/simulate.py:205` and `sim/symmetric.py` scale by `sd_mult` (1.6x for players without ADP), `sim/league.py:128` does not. Same prior, different numbers between `draft simulate`, `sim preseason/draft-check` and the research rooms. | medium (calibration conclusions) | high | after draft (changes sim numbers; one `sample_market_order(ctx, rng, noise)` helper) |
| F5 | **`drafts.status` never becomes `complete`** (`store.set_draft_status` has no caller). Dashboard `/` and `/api/agent/state` work around it by counting picks; `draft board` keeps showing `[active]`. | medium (post-draft routing, in-season code that keys on status) | medium | after draft (`confirm-pick`/`pick` marks complete when the board is full) |
| F6 | **Inconsistent CLI JSON/exit conventions.** ops: `{"ok": false, "error": <code>, "message": ...}` + typed exit codes (`ops.common`); draft/sim/values: `"error": <human message>` (no code), literal exit ints; `settings show` has no `ok`; `settings verify` bad field raises Typer `BadParameter` (text, not JSON); `data` `_emit` vs `draft` `_emit` vs `sim` `_emit` vs `ops.emit` (4 copies, different `sort_keys`/indent). Agents must special-case per group. | medium | medium | after draft (one `fantasy.cli_common` with `emit/fail/check_choice/parse_punt`; error codes everywhere; keep `error` text in `message`) |
| F7 | **Three publish paths with different semantics**: SQL upsert (`ops/status.publish`), REST upsert (`db/rest.py`, well tested), `db/sqldump.py` (`ON CONFLICT DO NOTHING` = stale rows never updated; 0% coverage, run as `python -m`). | medium | medium | after draft (drop sqldump or give it upsert + test) |
| F8 | **Duplicated helpers**: JSON-safe conversion x4 (`draft.store._clean` — private but imported by `draft/cli` and `sim/cli` —, `ops.common.jsonable`, `db.rest.to_json_value`, `default=str` in data/model CLIs); NaN→"" x3 (`draft/cli._str`, `optimizer._s`, `store._nan_none`); `git_sha` x2 (`model.valuation`: short sha, cwd-dependent; `ops.common`: full sha) so `model_runs.git_sha` and `agent_runs.git_sha` have different formats; settings-from-draft-row x2 (`draft/cli._settings_for`, `sim/cli._settings`); `--punt` parsing x2 (`model/cli._parse_punt`, `draft/cli.parse_punt`). | low | medium | after draft |
| F9 | **Name slugs in TS vs Python**: `agentPure.slugify` differs from `data/ids.slugify` (no transliteration table, no alias map) for 4/456 players (`alexandre-sarr`, `carlton-carrington`, `egor-demin`, `ronald-holland-ii`). Muse API resolution still succeeds via the exact-name fallback (checked for all 456). | low | low | after draft (add the slug pairs to a parity fixture) |
| F10 | **Stale docs**: README `fantasy draft pick <overall> <player>` (wrong argument order; the real form is `pick <player> --overall N`), README/ARCHITECTURE missing `sim/` and `season-sim`, ARCHITECTURE listed non-existent skills (`draft-live, data-sync, browser-yahoo`), `browser_integration.md` the same, ARCHITECTURE had no exit-code contract / REST publish. CLAUDE.md says cloud sessions cannot reach bbref/Yahoo; STATUS says the public endpoints are reachable now (user-owned file, not edited). `web/README.md` is stock create-next-app text plus one important Vercel note. | low | medium | **safe now: fixed** (except CLAUDE.md, web/README) |
| F11 | **Dead code** (see vulture row). | low | low | **safe now: removed** the clearly-dead constants and the fallback; kept `CategoryModel.expected_cat_wins`/`weights` args (draft-model class; research agents import it) and the Yahoo API surface (Phase 2) |
| F12 | **Oversized modules/functions**: `draft/cli.py` 574 lines (`prepare_pick` 57 statements), `league/yahoo_api.py` 658, `model/projections.py` 602 (`_apply_manual` complexity 26), `ops/cli.py` 534, `optimizer.recommend` 72 statements, `sim/cli.py` 408; web `DraftClient.tsx` 1,312 lines, `lib/sim.ts` 508. | medium (change risk) | medium | after draft |
| F13 | **Swallowed exceptions**: deliberate and logged in `draft/cli._context`/`recommend` save/`simulate` save (DB trouble must not block a pick — keep). `draft watch` has **no** guard: one DB error during `store.save_recommendation` kills the loop. `yahoo_auth` try/except/pass; 3 blind excepts in `sim/cli.py`; `valuation.git_sha` catches everything. | low-medium | medium | after draft (watch: catch, warn, keep polling — behaviour change on the draft path, so not tonight) |
| F14 | **Tooling/config**: `mypy` unused (71 errors); `ruff` unpinned (rule set changes between versions; ran 0.16.8 here); no `pytest-cov` in `[dev]`; no `.env.example` though 10 Python env vars (`FANTASY_DB_URL`, `FANTASY_DRAFT_ID`, `FANTASY_RUN_ID`, `FANTASY_PUBLISH_URL`, `FANTASY_TEST_PG_URL`, `SUPABASE_URL` + key vars in `db/rest.KEY_ENVS`, `YAHOO_*` x4) and 5 web env vars exist. Default values duplicated between CLI options and `ValuationParams` (values CLI now references the dataclass). | low | medium | after draft (partly safe now: values CLI defaults) |
| F15 | **Test gaps**: 0% for ingestion (`bbref_github`, `nba_api_ingest`), `ddl.py` (renders the Supabase migration — drift is only caught by eye), `sqldump.py`; `data/cli`, `provenance` ~50%. Tests that read the repo's `data/manual/league_settings.json` are implicitly coupled to the real league file. | low | medium | after draft (a `ddl` snapshot test is the most valuable) |
| F16 | **web**: unused exports (`Stat`, `isReversed`, `lib/sim.ts` helpers), `/agents` and `/approvals` exist only as redirects (fine), `lib/categories.ts` duplicates the Python category list. | low | low | after draft |

## What changed today (safe now)

| Commit (on main) | Change | Proof of no behaviour change |
|---|---|---|
| `Tech debt: drop dead code, warn when a CLI sub-app fails to import, ...` | removed `NINE_CATS`, `cat_labels`, `RequiredRunId`, unreachable `names.slug` fallback (`fantasy.data.ids` is in the same package, the `ImportError` branch could not run); `fantasy/cli.py` prints why a sub-app is missing; `values build` option defaults reference `ValuationParams` (identical values); new tests `tests/test_cli_untested_paths.py` (`draft watch`, `settings show|verify`, broken sub-app). | golden outputs identical; pytest + ruff green on the change |
| `Test: cross-language draft order parity fixture` | `tests/fixtures/draft_order_parity.json` (generated from Python), checked by `tests/test_draft_order.py` and `web/tests/draftOrder.test.mjs`. | tests only; `npm run lint && npm run build && npm test` green |
| `Docs: fix stale README/ARCHITECTURE ...` | F10 items. | docs only |

## Plan status (2026-10-19, before the draft)

The user asked for the plan to be done before the draft. Every step was golden-checked against `origin/main`
(script in `docs/handoffs/2026-10-18-tech-debt.md`; per-commit results in `docs/handoffs/2026-10-19-tech-debt-fixes.md`).

| Step | Status | Commit(s) |
|---|---|---|
| 1. Keep `main` green (F1) | **partly**: pytest + ruff (+ web checks) green at every push here. **Deferred:** moving the WIP-snapshot automation to a `wip/*` branch (orchestrator process; snapshots still land on main); `test_draft_cli_hardening::test_input_validation` still pins a literal objective list (passes today). | - |
| 2. One CLI output/error module (F6, F8) | **done** (`fantasy.cli_common`; existing keys and exit codes kept, `code`/`message` added) | `9eaf969` |
| 3. One opponent sampler (F4) | **done** (`season-sim-0.3.0`; changes sim numbers, before/after in the commit) | `0914116` |
| 4. Draft lifecycle (F5) | **done** (complete when the board is full; e2e xfail removed) | `1233764`, `bb88c4b` |
| -- `--overall 0` / out-of-range `--overall` (e2e xfails) | **done** (JSON usage error, exit 2) | `bb88c4b` |
| 5. `draft watch` resilience (F13) | **done** | `2d88621` |
| 6. Split `draft/cli.py` (F12) | **done** (`cli_board`/`cli_recommend`/`cli_pick`; `--help` byte-identical) | `a514acd` |
| 7. Publish paths (F7, F15) | **done** (sqldump upserts; DDL snapshot test) | `44e7cb9` |
| 8. Draft order single source (F3) | **done** (`drafts.config.order_slots` written by Python; web reads it, TS mirror = fallback) | `28c5f27` |
| 9. Typing/tooling (F14) | **done for a clean subset**: CI runs mypy on `ops db data league cli cli_common`; ruff/mypy pinned; `pytest-cov`; `.env.example`; `follow_imports = "silent"` so imports of not-yet-clean modules cannot turn the step red. **Deferred:** the remaining 39 mypy errors in `draft/ model/ sim/ valuesearch/` (draft-path freeze, files owned by other agents). | `c452ac8` |
| 10. Projections module split (F12) | **done** (projections sha identical) | `23936eb` |
| 11. Name parity + web cleanups (F9, F16) | **done** (TS slugify/aliases mirror Python, 456-name fixture; unused exports removed). **Deferred:** `lib/categories.ts` still duplicates the Python category list. | `524c16e` |
| 12. Unify `git_sha` (F8) | **done** (one helper in `ops.common`, full sha resolved from the repo root; `model_runs.git_sha` changes from short to full sha; the dashboard truncates both) | the commit "One git_sha ..." right after `524c16e` |
| Other F8 duplicates (JSON-safe x4, NaN->"" x3, settings-from-draft-row, `--punt` parsing) | partly via step 2; rest **deferred** (low risk, draft path) | |

## Refactor plan (after the draft), in order

Each step: one small PR-sized commit, golden diff (script above, extended with the new commands) +
`pytest` + `ruff` (+ web checks), no behaviour change unless the step says so.

1. **Keep `main` green (F1)** — 0.5 h. Point the WIP-snapshot automation at a `wip/*` branch; fix the two
   failing tests with the value-search owner (test should read `OBJECTIVES` instead of a literal list).
   Risk: none. Test: CI on main.
2. **One CLI output/error module (F6, F8 part)** — 3-4 h. `fantasy/cli_common.py`: `emit`, `fail(code, error, message, **extra)`,
   `check_choice`, `parse_punt`, `json_safe` (merge `store._clean`/`jsonable`/`to_json_value`), exit codes from
   `ops.common`. Migrate `data`, `values`, `settings`, `sim`, then `draft`. Behaviour change: error JSON gains a
   machine `error` code (message moves to `message`) — update `live-draft`/`agent-run` skills and web
   `/api/agent` callers first. Test: one parametrised test asserting every command's `--json` failure shape and exit code.
3. **One opponent-draft sampler (F4)** — 2 h. `draft/availability.sample_market_order(market_rank, sd_mult, rng, noise)`
   used by `draft/simulate`, `sim/league`, `sim/symmetric`. Changes `sim` numbers (adds `sd_mult` in `sim/league`):
   bump `SIM_VERSION`, record before/after P(playoffs) in the handoff. Test: same seed → identical draws across callers.
4. **Draft lifecycle (F5)** — 1 h. Mark `complete` when the board is full (`record_pick`/`confirm-pick`), expose in
   `draft board`; dashboard keeps the count fallback. Test: fill a 2-team x 2-round mock.
5. **`draft watch` resilience (F13)** — 0.5 h. Catch DB errors per iteration, warn, keep polling; add a
   `--max-iterations` for tests. Test: monkeypatched `save_recommendation` raising once.
6. **Split `draft/cli.py` (F12)** — 3 h. `draft/cli_pick.py` (`prepare-pick`, `confirm-pick`, txn logic) and
   `draft/cli_board.py`; break `prepare_pick` into check/plan/submit helpers. Golden + `test_draft_cli_hardening`.
7. **Publish paths (F7)** — 1-2 h. Delete `db/sqldump.py` (REST covers cloud; MCP can call `publish --rest`) or
   switch it to upsert + test. Add a `ddl.py` snapshot test against `supabase/migrations/` (F15).
8. **Single source for draft order on the web (F3)** — 2 h. Python stores `my_picks`/full order in `drafts.config`
   on `init`/`set-order`; web reads it and only falls back to `draftOrder.ts`. Keep the parity fixture.
9. **Typing (F14)** — 3 h. Fix the 71 mypy errors module by module (`ops` and `db` first), then add
   `mypy src/fantasy/ops src/fantasy/db src/fantasy/draft` to CI; pin `ruff` (`ruff==0.x.*`), add `pytest-cov` to dev;
   add `.env.example` listing every env var (names only).
10. **Projections module split (F12)** — 3 h. `_apply_manual` into one function per override file (injuries,
    trades, roles, rookies, ADP, positions). Projections sha must stay identical (golden `proj.sha`).
11. **Name parity + web cleanups (F9, F16)** — 1 h. Extend the parity fixture with slug pairs; port the
    transliteration/alias table or have Python publish `players.aliases`; drop unused web exports.
12. **Unify `git_sha` (F8)** — 0.5 h. One helper resolving from `REPO_ROOT`, full sha; note the format change in
    `model_runs` (dashboard shows it truncated).

Top items 1-6 are seeded as backlog tasks (`fantasy task seed`, area `infra`).

## Status 2026-10-20 (post-draft re-check against the code)

I checked each finding against `main` at `1e7de6b` by reading the code, not the plan table above. Another agent is
doing a behaviour-preserving cleanup of `src/fantasy/draft/` right now; anything it lands later is not reflected here.

| # | Status | Evidence in the code |
|---|---|---|
| F1 | **mostly fixed** | No "WIP snapshot" commit since 2026-10-18 (`git log`), and main has been green at each push since. Still open: `tests/test_draft_cli_hardening.py:80-82` pins a literal `["cat_wins", "matchup"]` instead of reading `OBJECTIVES` (passes today). |
| F2 | **fixed** | `src/fantasy/cli.py:35-37` prints why a sub-app is unavailable (`warning: \`fantasy <name>\` unavailable (...)`). |
| F3 | **fixed** | Python writes `drafts.config.order_slots`, and the web reads it with `web/src/lib/draftOrder.ts` as the fallback. Parity fixtures in `tests/fixtures/draft_order_parity.json` + `draft_status_parity.json`. |
| F4 | **fixed** | One sampler, `fantasy.draft.availability.sample_market_positions`, used by `draft/simulate.py:209` and `sim/league.py:166`; `sim/symmetric.py` goes through `sim.league`. |
| F5 | **fixed** | `store.status_for` (complete iff picks ≥ teams × rounds; live after an undo from complete) runs via `_sync_status` on every `record_pick` / `undo_pick`, and `draft board` heals it. The real draft row reads `complete` (`docs/handoffs/2026-10-20-draft.md`); the web mirror is `web/src/lib/draftStatus.ts`. |
| F6 | **fixed (core)** | `fantasy.cli_common` (`emit`, `fail`, `check_choice`, `parse_punt`) with `code`/`message` everywhere. Remaining nit: `data/cli.py:14` still has a private `_emit`. |
| F7 | **fixed** | `db/sqldump.py` upserts (`merge`), and ledger/state tables are insert-only (`insert`); DDL snapshot `tests/fixtures/schema_ddl.sql`. |
| F8 | **partly fixed** | One `git_sha` (`ops/common.py:112`). JSON-safe conversion is down to two helpers: `ops.common.json_safe`/`jsonable` (store imports `json_safe`) plus `db.rest.to_json_value`. One `parse_punt` (`cli_common`). **Remaining:** NaN→"" ×3 (`draft/store._nan_none`, `draft/cli_pick._str`, `draft/optimizer._s`), settings-from-draft-row ×2 (`draft/cli._settings_for`, `sim/cli._settings`). |
| F9 | **fixed** | `tests/fixtures/player_id_parity.json` (TS slugify/aliases mirror Python). |
| F10 | **fixed** (README/ARCHITECTURE refreshed again 2026-10-20 for league sync, lineup, inseason, PWA). CLAUDE.md now describes the cloud allowlist correctly. **Remaining:** `web/README.md` is still stock create-next-app text; CLAUDE.md line 32 still calls `in-season-ops` a "Phase 2 placeholder" and omits `daily-routine` (user-owned file, see N3). |
| F11 | **fixed** | Dead code removed 2026-10-18. |
| F12 | **partly fixed** | `draft/cli.py` split (291 + `cli_pick` 255 + `cli_recommend` 480 lines); `model/projections.py` split (projections sha identical). **Remaining** large files: `league/yahoo_api.py` 658, `draft/optimizer.py` 644 (`recommend`), `ops/cli.py` 608, `sim/cli.py` 530; web `app/draft/DraftClient.tsx` 1,394, `lib/sim.ts` 521. |
| F13 | **partly fixed** | `draft watch` survives DB errors (`cli_recommend.py:377`, `:396`). Deliberate "recording must never block the answer" excepts in `sim/cli.py:151/173/471` are kept by design. **Remaining:** `league/yahoo_auth.py:129-130` `except Exception: pass` (headless browser open; harmless). |
| F14 | **partly fixed** | CI runs ruff + mypy on `ops db data league cli cli_common` (`.github/workflows/ci.yml:17`); ruff/mypy pinned, `pytest-cov`, `.env.example` all present. **Remaining:** mypy does not cover `draft/ model/ sim/ valuesearch/` or the new in-season packages `lineup/` and `inseason/`. |
| F15 | **partly fixed** | DDL snapshot test exists. **Remaining:** no tests for `data/bbref_github.py`, `data/nba_api_ingest.py`, `data/provenance.py`. |
| F16 | **partly fixed** | Unused web exports removed. **Remaining:** `web/src/lib/categories.ts` still duplicates the Python category list. |

New since the audit:

| # | Finding | Risk | Status |
|---|---|---|---|
| N1 | `fantasy status` shows `draft: mock-check pending`. `ops/status.py:74-77` picks the newest `live`/`pending` draft without excluding `is_mock`, so the leftover mock row from the 2026-10-19 setup check is reported as the active draft. | low (misleading only) | open: skip `is_mock` drafts when a real one exists, or delete the row with the user's OK |
| N2 | `.claude/skills/in-season-ops/SKILL.md` was a stale placeholder after sync/lineup/daily routine shipped. | low | **fixed** 2026-10-20 (capability map + approval policy; league-loop table updated) |
| N3 | CLAUDE.md skill list: `in-season-ops (Phase 2 placeholder)`, no `daily-routine`. | low | open (user-owned file; proposed wording in the 2026-10-20 docs handoff) |
| N4 | Draft room priors (ADP spread, 3 sharp drafters) were uncalibrated. | medium for the next draft | first real sample analysed: `docs/research/draft_calibration_2026.md` (room ~0.6-0.7x the prior spread, no sharp drafters). No default changed. |
| N5 | Muse runs `muse-2026-10-18` / `-19` stay `running` with stale heartbeats (Muse never ends runs). | low | open: `fantasy run reap` (human) or have Muse end runs |
