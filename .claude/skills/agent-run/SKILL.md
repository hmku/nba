---
name: agent-run
description: Structure of every operational agent run - start a run (dry_run vs live), take locks, log events/decisions, request and wait for approvals, drive the idempotent txn ledger, end the run, and recover after a crash ("previous session died mid-transaction"). Use before doing anything that touches Yahoo or the ops ledger, whenever `fantasy status` shows stale runs / held locks / txns needing reconciliation, or when another skill says "start a run".
---

# Agent run protocol (runs, locks, approvals, transactions)

Purpose: every operation that observes or changes Yahoo state is wrapped in a **run** so the
dashboard and the next agent can see what happened, and so writes are idempotent, gated by policy
and verified. Background and design: `docs/runbooks/agent_ops.md` (read once). Domain skills
(`live-draft`, `draft-prep`, `yahoo-browser`, `in-season-ops`) build on this one.

## Prerequisites
- `fantasy` on PATH (`pip install -e ".[dev]"`). DB: `FANTASY_DB_URL` = Supabase pooler URL in
  production (secret, from the user; never echo it), unset = local SQLite `var/fantasy.db`.
  If you worked on SQLite, the dashboard sees nothing until `fantasy publish` (asks the user).
- **Cloud sessions** cannot reach Postgres (HTTPS proxy only): work on SQLite, then
  `fantasy db ping --rest --json` and `fantasy publish --rest [--dry-run] --json` (PostgREST upsert,
  same skip rules; runbook section 5b). `rest_unreachable` = host not allowed in the environment's
  network settings, `rest_auth` = key missing: point the user to docs/USER_TODO.md, never ask for keys in chat.
- For Yahoo writes: a **local** session with Claude in Chrome (see `yahoo-browser` skill).
- Always pass `--json` and branch on the **exit code**, then read the JSON. Every failure has `ok: false`,
  a machine `code` (e.g. `lock_held`, `not_allowed`, `usage`, `not_found`) and a human `message`; prefer `code`
  over `error` (in non-ops groups such as `draft`, `error` holds the message, not a code).

| exit | meaning | what you do |
|---|---|---|
| 0 | ok | continue |
| 1 | unexpected error | read `error`, fix input, do not loop |
| 2 | usage: bad args, unresolved player name (JSON has `candidates`), missing run id, invalid `--strategy/--objective/--punt` or `set-order` rounds (JSON has `valid`), mock-looking draft id without `--mock` | fix args / use player_id |
| 3 | conflict: lock held by another run, task owned by someone else | STOP that action; report holder |
| 4 | not allowed: dry_run, approval missing/pending, lock not held | do NOT perform the Yahoo action |
| 5 | duplicate txn (JSON has `existing`) | inspect `existing.status`; never re-plan to dodge it (`draft prepare-pick` handles draft-pick retries itself) |
| 6 | not found | check ids |
| 7 | invalid state transition / run not running / approval already decided | re-read state (`txn show`, `run show`) |
| 8 | still pending: `approval show --wait N` timed out with the approval still `pending` | keep waiting or stop; do NOT act |

## 1. Orient
```bash
fantasy status --json
```
Output is `{"ok": true, "status": {...}}` — every key below is nested under `status`
(e.g. `jq '.status.txns_needing_reconciliation'`): `runs.active`, `runs.stale`, `locks`, `approvals_pending`,
`txns_needing_reconciliation`, `txns_awaiting_approval`, `tasks_open`, `model_freshness`, `active_draft`
(a `pending`/`live` draft; a finished draft is `complete` and no longer listed here).
**If `txns_needing_reconciliation` is non-empty, do section 7 (recovery) before anything else.**

## 2. Start the run
```bash
export FANTASY_RUN_ID=$(fantasy run start --agent <name> --task "<what>" --mode dry_run)
# live only when the human explicitly authorised live execution IN THIS SESSION:
export FANTASY_RUN_ID=$(fantasy run start --agent draft-manager --task "live draft" --mode live)
```
- Plain output = the run id; `--json` gives `{"ok", "run", "run_id"}`. `--session-ref <url>` links
  the Claude session. `dry_run` is the default and can never submit a transaction (exit 4 /
  `"action": "DO_NOT_CLICK"`). A dry run may still **read** Yahoo and record observations.
- `fantasy run heartbeat` at least every ~5 min (runs silent > 15 min show as stale and can be
  reaped; a reaped run's heartbeat exits 7 -> start a new run).

## 3. Locks (leases)
```bash
fantasy lock acquire yahoo:browser --ttl 1800 --purpose "<why>" --json   # exit 3 = someone else: STOP
fantasy lock acquire yahoo:roster  --ttl 300  --purpose "<why>" --json   # any roster change
fantasy lock acquire draft:<draft_id> --ttl 10800 --purpose "live draft" --json
fantasy lock renew <resource> --ttl <s>       # before the TTL runs out
fantasy lock show --json                      # all held locks (+ --all for expired)
fantasy lock release <resource> --json        # holder only (uses FANTASY_RUN_ID)
```
- **`fantasy run end` releases every lock the run still holds** (any end status, expired leases too)
  and lists them in `released_locks`. Releasing explicitly as soon as you no longer need a lock is
  still good practice (e.g. `yahoo:browser` after the browser work, before a long analysis).
- `lock release --force` is a HUMAN override. Only use it when the user tells you to, or when the
  holder is provably your own dead run (same machine, `run show` says ended/abandoned) - and log a note.

## 4. Log as you go (the dashboard shows these)
```bash
fantasy run event observation "pick 14 taken: X" --payload '{"pick":14}'
fantasy run event browser_action "clicked Draft for X" --payload '{"url":"...","element":"Draft button"}'
fantasy run event verification "draft log shows #24 X -> my team" --payload '{"source":"draft log"}'
fantasy run event error "<what failed>" ; fantasy run event note "<anything>"
fantasy run decision <draft_pick|lineup|add_drop|trade|other> \
    --model '<model recommendation JSON>' --choice '<what you chose JSON>' --rationale "<why>"
```
Event kinds: observation, command, model_output, decision, browser_action, verification, error, note.
`--payload` takes JSON, `@file` or `-`. `agrees_with_model` is computed by comparing `player_id`s.
Never put cookies, tokens, passwords, emails or the DB URL in events.

## 5. Approvals & policy
Policy (`src/fantasy/ops/policy.py`): `dry_run` never executes; `lineup`, `draft_pick` allowed in a
live run (lock still required); `drop`, `add`, `add_drop`, `trade`, unknown kinds always need an
**approved** human approval.
```bash
fantasy approval request <drop|add_drop|trade|other> "summary" --payload '{...}' [--expires-in 3600]
fantasy approval list [--status pending|approved|rejected|expired|all] --json
fantasy approval show <apr_id> --wait 300 --json
```
- `approval show --wait N` exits **8** (`"error": "still_pending"`, approval included) if it is still
  pending after N seconds; exit 0 once decided - then read `approval.status`
  (`approved|rejected|expired`). Without `--wait` it always exits 0. Default expiry 6 h.
- `fantasy txn plan` files the approval for you when policy requires it - don't request twice.
- **Never run `fantasy approval decide` yourself** - that is the human's action (dashboard
  `/approvals` or they type it). `.claude/settings.json` makes it prompt.

## 6. Transactions (idempotent state machine)
```
planned -> awaiting_approval -> submitted -> verified
   |            |                    \-----> failed
   \-> cancelled / failed  (from planned / awaiting_approval)
```
Recipe for any Yahoo write. **Draft picks: use `fantasy draft prepare-pick` / `fantasy draft confirm-pick`
(see `live-draft`), not this generic recipe** - only `confirm-pick` records the pick on the board.
```bash
fantasy txn plan <draft_pick|add|drop|add_drop|lineup|trade> --payload @p.json --summary "..." --json
#   status planned or awaiting_approval; exit 5 + existing if same (kind,payload,scope,attempt) exists.
#   scope default: UTC date; draft_pick: "<draft_id>#<overall>" (payload needs draft_id, overall_pick).
fantasy approval show <approval_id> --wait 300 --json      # if awaiting_approval; stop if rejected/expired
fantasy txn check <txn_id> --json                          # dry check: would submit pass? (exit 4 + reason)
fantasy txn mark <txn_id> submitted --json                 # IMMEDIATELY before the click
#   ... perform exactly that action in Yahoo, nothing else ...
#   ... re-read Yahoo independently (roster page / transactions log / draft log) ...
fantasy txn mark <txn_id> verified --evidence '{"roster_after":[...],"page":"..."}' --json
fantasy txn mark <txn_id> failed --error "Yahoo said ..." --json      # or this
fantasy run event verification "..."
```
- After approval is granted the txn **stays `awaiting_approval`** until you mark it submitted (that is
  allowed; `txn check` tells you whether it would pass).
- Retrying after `failed`: plan again with `--attempt 2` (new idempotency key; needs a fresh approval
  if policy requires one). Never re-plan a `submitted` txn. For draft picks `prepare-pick` does this
  automatically (next attempt after failed/cancelled; re-owns a `planned` txn left by e.g. a dry run).
- Cancel abandoned plans: `fantasy txn mark <id> cancelled --error "<reason>"`.

## 7. Recovery: "previous session died mid-transaction"
1. `fantasy status --json`. Note stale runs, locks (holder, `expires_at`), `txns_needing_reconciliation`.
2. Start a new run (`dry_run` is enough to reconcile; marking verified/failed needs no lock or live mode).
3. For each txn in `fantasy txn reconcile --json` (status `submitted`): the click may or may not have
   happened. **Never resubmit.** Read Yahoo (draft log / roster / league transactions page) via the
   `yahoo-browser` skill, then:
   - happened -> draft pick: `fantasy draft confirm-pick <txn_id> --evidence "<what you saw>"`
     (this also records the pick on the board). Other kinds: `fantasy txn mark <id> verified --evidence ...`.
   - did not happen -> draft pick: `fantasy draft confirm-pick <txn_id> --failed "<reason>"`;
     others: `fantasy txn mark <id> failed --error "<reason>"`. Only then may you retry with `--attempt 2`.
   - **Draft picks are reconciled with `fantasy draft confirm-pick`**, not `txn mark`: `txn mark verified`
     does **not** record the pick on the board. If it was already used, run `fantasy draft confirm-pick <txn_id>`
     anyway - on a verified txn it just records the pick (idempotent, exit 0, `already_verified: true`).
4. `txns_awaiting_approval` from the dead run: if still wanted, keep waiting (any run may submit it);
   otherwise `txn mark <id> cancelled`.
5. Locks held by the dead run: wait for `expires_at`, or ask the user to approve a `--force` release.
   If the dead run is provably yours, `fantasy run end --run-id <dead_run> --status failed` releases its locks.
6. `fantasy run reap --stale-minutes 15 --json` marks silent runs `abandoned` (does not release locks).
7. Log what you found and did (`run event note`), then continue normally.

## 8. End the run
```bash
fantasy run end --status succeeded|failed|abandoned --summary "<1-3 sentences>" --json
#   -> {"run": {...}, "released_locks": ["draft:...", "yahoo:browser"]}  (all locks it held)
unset FANTASY_RUN_ID
```
Then the `handoff` skill (note, STATUS.md, tasks, commit+push).

## What to record (minimum)
Every model output you acted on (`model_output` or inside `decision --model`), every decision with a
rationale (mandatory when you deviate from the model), every browser write (`browser_action`) and its
independent check (`verification`), every error. Evidence JSON: page name/URL, what was read, time.
