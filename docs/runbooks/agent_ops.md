# Runbook: agent operations (runs, locks, approvals, transactions, tasks)

All state lives in the DB (`FANTASY_DB_URL`; Supabase in production, SQLite offline/tests).
Every command takes `--json` (one JSON object on stdout, always with `"ok"`). Agents should
always use `--json` and branch on the exit code.

| exit | meaning |
|---|---|
| 0 | ok |
| 1 | unexpected error |
| 2 | usage (bad args, missing run id, bad JSON) |
| 3 | conflict: lock held by another run / task claimed by another owner |
| 4 | not allowed: approval required/not granted, dry_run, or lock not held when submitting |
| 5 | duplicate transaction (JSON includes `existing`) |
| 6 | not found |
| 7 | invalid state transition (txn, already-decided approval, run not running) |

## 0. Orient first

```bash
fantasy status --json        # active/stale runs, locks, pending approvals,
                             # txns needing reconciliation, open tasks, model freshness, draft
```
If `txns_needing_reconciliation` is non-empty, do **section 5** before anything else.

## 1. Run lifecycle

```bash
export FANTASY_RUN_ID=$(fantasy run start --agent draft-manager --task "mock draft" \
    --mode dry_run --session-ref "$CLAUDE_SESSION_URL")
fantasy run event observation "pick 14 taken: X" --payload '{"pick":14}'
fantasy run decision draft_pick --model '[{"player_id":"a"},{"player_id":"b"}]' \
    --choice '{"player_id":"b"}' --rationale "need blocks"     # agrees_with_model computed
fantasy run heartbeat        # at least every ~5 min; runs silent > 15 min are "stale"
fantasy run end --status succeeded --summary "..."   # also releases all locks the run holds (released_locks)
```
- Event kinds: observation, command, model_output, decision, browser_action, verification, error, note.
- `mode=dry_run` (default) can never submit a transaction. Use `--mode live` only when the human
  has authorised live execution (e.g. the real draft).
- `fantasy run reap [--stale-minutes 15]` marks stale runs `abandoned`. A reaped run's heartbeat
  fails with exit 7: start a new run. Reaping does **not** release locks (they expire by TTL).

## 2. Locks (leases)

| resource | protects |
|---|---|
| `yahoo:browser` | driving the Yahoo browser tab (one agent at a time) |
| `yahoo:roster` | any roster-changing transaction (add/drop/trade/lineup) |
| `draft:<draft_id>` | making picks in that draft |

```bash
fantasy lock acquire yahoo:roster --ttl 300 --purpose "set lineup" --json   # exit 3 if held
fantasy lock renew yahoo:roster --ttl 300      # re-acquire by the holder also renews
fantasy lock release yahoo:roster              # holder only; exit 3 otherwise
fantasy lock show [--all]
fantasy lock release yahoo:roster --force      # HUMAN override only
```
- Acquisition is atomic (INSERT, else conditional UPDATE where expired) on SQLite and Postgres.
- Choose a TTL longer than the work between renewals; an expired lease can be taken over.
- Python: `with locks.lock("yahoo:roster", run_id, ttl_seconds=300): ...`.
- Clocks: lease times are written by the client (UTC). Hosts need roughly synced clocks.

## 3. Approvals & policy

Policy (`fantasy.ops.policy.check_action_allowed(kind, run_mode, approval_status)`):
- `dry_run` never executes.
- `lineup`, `draft_pick`: allowed for a `live` run (still needs the lock).
- `drop`, `add_drop`, `trade`, `add`, and anything unknown: need an **approved** approval.

```bash
fantasy approval list [--status pending|approved|rejected|expired|all]
fantasy approval show <id> [--wait 300]        # poll while pending; exit 8 if still pending
fantasy approval decide <id> --approve|--reject --by harry [--note ...]   # exit 7 if not pending
fantasy approval request other "summary" --payload '{...}' [--expires-in 3600]
```
`fantasy txn plan` files the approval automatically when policy requires one. Pending approvals
past `expires_at` (default 6h) become `expired`.

## 4. Transactions (idempotent, state machine)

```
planned ─► awaiting_approval ─► submitted ─► verified
   │             │                  └──────► failed
   └─► cancelled / failed ◄─────────┘ (from planned/awaiting_approval)
```
```bash
fantasy txn plan lineup --payload @lineup.json [--scope 2026-10-21] --json
#   exit 5 + "existing" if the same (kind, payload, scope, attempt) was planned before.
#   default scope: UTC date; draft_pick: "<draft_id>#<overall_pick>" (payload needs draft_id).
#   retry after `failed`: --attempt 2.
fantasy txn check <txn_id>                 # would submitting be allowed now? (no state change)
fantasy txn mark <txn_id> submitted        # IMMEDIATELY BEFORE clicking in Yahoo
#   requires: running live run, policy OK (approval approved if needed),
#   lock held by this run (yahoo:roster, or draft:<id> for draft_pick). Exit 4 otherwise.
fantasy txn mark <txn_id> verified --evidence '{"roster_after":[...],"screenshot":"..."}'
fantasy txn mark <txn_id> failed --error "Yahoo said roster locked" [--evidence ...]
fantasy txn mark <txn_id> cancelled --error "human rejected"
fantasy txn list [--status ...] [--kind ...] ; fantasy txn show <txn_id>
```
Execution recipe for any Yahoo write:
1. `run start --mode live` → `lock acquire yahoo:browser` + `yahoo:roster` (or `draft:<id>`).
2. `txn plan` → if `awaiting_approval`, stop and wait for the human (`approval show --wait`).
3. `txn mark submitted` → perform the action in the browser → observe the result in Yahoo.
4. `txn mark verified --evidence ...` (or `failed`) → `run event verification ...`.
5. Release locks; `run end`.

## 5. Recovery from crashes

- **Txn stuck in `submitted`** (`fantasy txn reconcile`): the action may or may not have happened.
  NEVER resubmit blindly. Acquire the lock, look at Yahoo (roster / transaction log / draft
  board), then mark `verified` (with evidence) or `failed`. Only after `failed` may you plan the
  same action again with `--attempt N+1`.
- **Dead run holding a lock**: wait for the TTL to expire (status shows `expires_at`); a human may
  `lock release --force`. Then `fantasy run reap`.
- **Approval never answered**: it expires; the txn stays `awaiting_approval` — cancel it.
- **Offline work** (no Supabase): run with the local SQLite DB, then
  `FANTASY_PUBLISH_URL=postgresql://... fantasy publish` (laptop) or `fantasy publish --rest`
  (cloud session, section 5b). The SQL publish upserts every table by primary key except `locks`
  (ephemeral) -- including ledger rows, so only run it from a DB that is newer than Supabase.
  `publish --rest` applies the per-table policy of section 5b (ledger/state rows never overwritten).
  Tasks/transactions whose title/idempotency key collide with a *different* row online are skipped
  and reported, never overwritten.

## 5b. Publishing from cloud sessions (HTTPS / PostgREST)

Cloud Claude Code sessions only have an HTTPS proxy (Postgres hosts do not even resolve), so
`FANTASY_DB_URL=postgresql://...` and `fantasy publish --target ...` only work on the laptop. In
the cloud, work on local SQLite and publish over HTTPS:

```bash
fantasy db ping --rest --json          # GET rest/v1/tasks?select=task_id&limit=1
#   exit 0 auth_ok; error "rest_unreachable" = host not in the environment's Allowed domains;
#   "rest_auth" (401/403) = no/invalid key -> see docs/USER_TODO.md (never ask for the key in chat)
fantasy publish --rest --dry-run       # counts rows/batches per table, sends nothing
fantasy publish --rest [--source sqlite:///...] [--tables players,model_runs ...] --json
```
- Source defaults to `FANTASY_DB_URL` if it is SQLite, else `var/fantasy.db`. Target `SUPABASE_URL`
  (default `https://bdzpkdkhlzjzddkptjex.supabase.co`).
- `POST /rest/v1/<table>?on_conflict=<pk cols>`, 500 rows per request, FK-safe order
  (`metadata.sorted_tables`), `locks` never. Datetimes as ISO UTC, JSON columns as JSON, NaN as
  null. 5xx/network errors are retried twice (both modes are idempotent); proxy denials are not retried.
- **Per-table policy** (`fantasy.db.rest.publish_policy`, JSON key `policy`):

  | policy | PostgREST | tables | why |
  |---|---|---|---|
  | merge | `Prefer: resolution=merge-duplicates` (upsert) | players, league_settings, data_snapshots, model_runs, projections, player_values, draft_picks, draft_recommendations | the publishing copy owns them (reference data, versioned model outputs, the draft board) |
  | insert | `Prefer: resolution=ignore-duplicates` (insert missing rows only; `inserted` = how many were new) | agent_runs, approvals, transactions, tasks, drafts, agent_events, decisions, league_snapshots, **and any table not listed** | rows change state online after creation (dashboard approvals, txn state machine, run end, task claims, draft status/order) or are immutable logs; a stale local copy must never overwrite them |
  | skip | - | locks | ephemeral leases |

  Consequence: a status change made locally (e.g. a txn marked verified in SQLite) does **not** reach
  Supabase via `publish --rest`. Make such changes online with the `--rest` commands (section 5c).
  A draft created in the cloud publishes once; later `drafts` status/order changes need the laptop
  (`FANTASY_DB_URL` = Supabase) during the draft.
- Same safety rule as SQL publish: tasks/transactions whose title/idempotency key belongs to a
  different PK online (checked with a GET first) are skipped and listed under `skipped`. Other
  per-table failures (e.g. 409) land in `errors` and the command exits 1.
- Auth: `SUPABASE_SECRET_KEY` (or `SUPABASE_SERVICE_ROLE_KEY`) is sent as `apikey` (plus
  `Authorization: Bearer` for legacy `eyJ...` JWT keys); with neither set no auth header is sent and
  the environment's network secret is expected to inject `apikey`. The key is never printed.
- Python: `fantasy.db.rest.publish_rest(source_url, tables=..., dry_run=...)`, `rest.ping()`.

## 5c. Ledger commands over HTTPS (`--rest`)

Cloud sessions run the ledger directly against Supabase, one row per request, no local DB:
```bash
export FANTASY_RUN_ID=$(fantasy run start --agent league-loop --task "..." --mode dry_run --rest)
fantasy run event|decision|heartbeat|end|list|show ... --rest
fantasy lock acquire|renew|release|show ... --rest        # no --force over REST
fantasy approval request|list|show [--wait N] ... --rest   # no `decide` over REST (human-only)
fantasy txn plan|mark|check|list|show|reconcile ... --rest
fantasy task add|list|show|claim|update|done ... --rest
```
- Same rules, JSON keys and exit codes as the local commands; the business logic (kinds, state machine
  `TRANSITIONS`, idempotency keys, policy, required locks, `agrees_with_model`) is imported from the
  ops modules, only storage differs (`src/fantasy/ops/rest_ledger.py`).
- Every state change is a conditional PATCH carrying the expected current state, with
  `Prefer: return=representation`; an empty result means someone changed the row first, and the command
  fails exactly like the local compare-and-set: txn `PATCH transactions?txn_id=eq.X&status=in.(...)`
  -> exit 7; run end/heartbeat `status=in.(running,abandoned)` / `eq.running` -> 7; lock takeover
  `or=(expires_at.lt.<now>,holder_run_id.eq.<me>)` -> 3; task claim -> 3/7; `task done` also CASes on
  the status/owner it read (3 if it changed). Inserts are plain POSTs: a 409 on `idempotency_key` -> exit 5
  with `existing`, on a lock -> takeover path.
- `approval show/list --rest` lazily expires overdue approvals with
  `PATCH approvals?status=eq.pending&expires_at=lt.<now>`: it can never touch a decided approval.
- Not available with `--rest`: `approval decide` (human-only), `lock release --force` (human override),
  `run reap`, `task seed`, `fantasy status` (use the list/reconcile commands above).
- Auth: same as publish (network secret injects an `sb_secret_` key = Supabase `service_role`, which
  bypasses RLS; the anon/publishable key has SELECT-only policies and could not write).

## 6. Tasks (backlog)

```bash
fantasy task seed                              # initial backlog, idempotent by title
fantasy task list [--status open|todo|in_progress|blocked|done|all] [--area ...]
fantasy task claim T-004 [--owner name]        # owner defaults to run id; exit 3 if taken
fantasy task update T-004 --status blocked --description "..." [--release]
fantasy task done T-004
fantasy task add "title" --area model --priority 1 --description "..."   # exit 5 if title exists
```

## 7. DB setup

- `fantasy db init`: SQLite → creates tables; Postgres → prints how to apply `supabase/migrations`.
- Tests: ops tests use a temp SQLite DB; set `FANTASY_TEST_PG_URL` to a **throwaway** Postgres
  (schema applied) to run them against Postgres — they TRUNCATE every table.
