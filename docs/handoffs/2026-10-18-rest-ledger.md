# Handoff 2026-10-18 - REST ledger + safe publish (backend engineer, cloud session)

## What was accomplished
- **`publish --rest` is safe for ledger/state tables.** Per-table policy in `fantasy.db.rest`
  (`PUBLISH_MERGE`, `PUBLISH_INSERT_ONLY`, `publish_policy()`):
  - merge (upsert, `resolution=merge-duplicates`, unchanged): players, league_settings, data_snapshots,
    model_runs, projections, player_values, draft_picks, draft_recommendations.
  - insert-if-missing (`resolution=ignore-duplicates`, never overwrites): agent_runs, approvals,
    transactions, tasks, drafts, agent_events, decisions, league_snapshots, **and any unlisted/future table**.
  - skip: locks.
  JSON output gains `policy` and `inserted` (rows that were new online, from `return=representation`).
- **REST ledger** (`src/fantasy/ops/rest_ledger.py`, new): `--rest` on
  `run start|event|decision|heartbeat|end|list|show`, `lock acquire|renew|release|show`,
  `approval request|list|show`, `txn plan|mark|check|list|show|reconcile`,
  `task add|list|show|claim|update|done`. One row per request, no local DB. State changes are conditional
  PATCHes (`status=in.(<expected>)`, `Prefer: return=representation`; empty result = lost the race ->
  same error/exit code as the local compare-and-set). Not offered: `approval decide` (human-only),
  `lock release --force`, `run reap`, `task seed`, `status`.
- Docs: agent-run + league-loop skills (cloud = always `--rest`; section 6 rewritten), runbook
  `docs/runbooks/agent_ops.md` 5b (policy table) + new 5c, `docs/research/league_loop.md` step 4 DONE,
  STATUS.md line.

## Decisions & rationale
- **New module, ops core untouched.** Draft-path freeze is 2026-10-19 22:00 UTC and `draft prepare-pick/
  confirm-pick` use `ops.transactions/ledger/locks`. So no repository refactor of the ops modules: the REST
  classes import all rules (TRANSITIONS, idempotency_key, default_scope, required_lock, policy,
  agrees_with_model, kinds/statuses) and only re-implement the storage calls with the same method names.
  `git diff` touches only `ops/cli.py` (adds `--rest` + `b = _backend(use_rest)`, where `_backend(False)`
  returns the very same modules) and two text/docstring spots in `ops/status.py`. After the draft, a real
  repository interface behind the ops modules could remove the parallel storage code (S-M).
- CAS failures keep the local exit codes (txn/run transitions 7, lock/task conflicts 3, duplicate plan 5)
  instead of inventing a new code.
- `drafts` is insert-only (it has state: status, order config). Consequence: draft status/order changes
  made in a local SQLite no longer propagate via `publish --rest`; the live draft uses
  `FANTASY_DB_URL` = Supabase (live_draft runbook), so unaffected.
- RestTasks.done CASes on the status/owner it read (local `done` is a plain update): strictly safer.
- SQL `fantasy publish` (laptop) and `python -m fantasy.db.sqldump` are unchanged (still upsert every
  table) to keep non-REST behaviour identical before the draft; documented in the runbook/sqldump docstring.

## Files changed
- `src/fantasy/ops/rest_ledger.py` - new: RestStore + RestLedger/RestLocks/RestApprovals/RestTransactions/RestTasks.
- `src/fantasy/ops/cli.py` - `--rest` option on the commands above; backend switch.
- `src/fantasy/db/rest.py` - publish policy, `RestClient.insert_missing`, `policy`/`inserted` in results.
- `src/fantasy/ops/status.py` - publish text output per policy; docstring.
- `src/fantasy/db/sqldump.py` - docstring only.
- `tests/fake_postgrest.py` - new: in-process PostgREST subset over a SQLite DB with the real schema (409s, filters, or=, in.(), ordering, hooks to simulate concurrent writers).
- `tests/test_rest_ledger.py` - new (17 tests); `tests/test_rest_publish.py` - +3 tests, 1 assertion updated (drafts now "insert-if-missing").
- Docs listed above.

## Tests performed
- After rebasing onto origin/main d07f903: `python -m pytest -m "not slow"` -> 374 passed (incl. the
  non-slow E2E draft/ops/publish tests); `python -m pytest -m slow` -> 2 passed (E2E live draft);
  `ruff check src tests` clean; `mypy` (CI subset) -> no errors in my files (5 pre-existing errors in
  `model/availability.py`, `model/projections.py`, `draft/order.py`, identical on origin/main).
- New tests: `tests/test_rest_ledger.py` (17: run lifecycle, output-key parity with local, idempotent
  re-plan, lost insert race -> duplicate, add_drop approval flow + submit gates, dry_run refusal,
  concurrent cancel vs mark failed -> exit 7 not clobbered, concurrent submit race, expire_overdue vs
  decided approvals, no `decide` over REST, locks, tasks + quoting, task done CAS, 401 mapping, local path
  never builds a REST client); `tests/test_rest_publish.py` +3 (stale local `pending` approval does not
  overwrite remote `approved`; merge tables still upsert; every table has a policy).
- Live check (read-only): `fantasy db ping --rest` -> HTTP 200 via the injected network secret. Supabase
  catalog (MCP `execute_sql`, SELECT only): all ledger tables have RLS with only a SELECT policy for
  anon/authenticated; `service_role` has UPDATE/DELETE grants and `rolbypassrls = true`. Supabase edge
  logs (MCP `query_logs`) for one GET sent from this session show the injected key is an `sb_secret_...`
  key (= service_role). So PATCH/DELETE from cloud sessions will be authorised. No write was sent to
  production to test it.

## Problems encountered
- The shared checkout `/home/user/nba` was mid-rebase with other agents' uncommitted work, so this work
  was done in a separate worktree (`/home/user/nba-rest`, branch `rest-ledger`) and rebased onto main.

## Outstanding work
- No end-to-end run against production yet (first real use: the next league-loop THINK run with `--rest`).
- `fantasy status --rest` (whole overview over REST) not built; use the list/reconcile commands.

## Suggested next steps
1. First cloud THINK run: `run start --rest` ... `run end --rest`; check rows on the dashboard Activity page.
2. After the draft: repository interface behind the ops modules (one implementation of the flows),
   apply `publish_policy` to SQL publish and sqldump too.
3. league_loop build-plan step 5 (`--approval-expires-in`, payload schema) and 6 (action queue).

## Notes for next agent
- Tests use `rest_ledger.default_client` monkeypatched to a `httpx.MockTransport(FakePostgrest(...))`.
- A run started with `--rest` exists only in Supabase; keep using `--rest` for it.
- No runs/locks/txns were created in Supabase by this session.
