# League loop: Muse reads, Claude thinks, the user approves, an executor clicks

_Design note, 2026-10-18. Status: design only. Nothing in the "PLANNED" rows below is built. Operating
protocol for agents: `.claude/skills/league-loop/SKILL.md`. Muse's instructions:
`docs/integrations/muse.md`. Current in-season capabilities and approval policy:
`.claude/skills/in-season-ops/SKILL.md`. Ledger mechanics: `.claude/skills/agent-run/SKILL.md`,
`src/fantasy/ops/{transactions,approvals,policy,locks}.py`._

## 1. Why
After the 2026-10-19 draft the team needs daily attention: lineups, streaming adds against the weekly
add limit, IL moves, trade offers. The quantitative work lives in the `fantasy` CLI (season sim, values),
which a **cloud** Claude session can run on a schedule. But cloud sessions cannot see the user's Yahoo
account (no browser, Yahoo account pages need a login), and an unattended laptop session is not a
documented setup (section 7). Meta Muse, the user's external assistant, *can* log in to Yahoo and
already talks to our API (`/api/agent/*`). So the loop splits the work by capability:

| Step | Who | Capability used | Thinks? | Writes to Yahoo? |
|---|---|---|---|---|
| READ | Meta Muse | logged-in browser on Yahoo, group chat | no | no (by instruction) |
| THINK | Claude, recurring cloud scheduled task | `fantasy` CLI, Supabase over HTTPS | yes | never |
| APPROVE | the user | dashboard `/approvals` | yes | no |
| EXECUTE | Muse, or a local `claude --chrome` session | browser | no (exact action only) | yes, approved txns only |
| VERIFY | next Muse snapshot + Claude | snapshot vs ledger | compares | no |

## 2. Data flow
```
            Yahoo league 44495 (user's account)          league Messenger group chat
                 |  read pages            ^ click (approved txn only)        | read
                 v                        |                                  v
          +---------------------------------------------------------------------+
          |  Meta Muse  (external; Bearer token; no reasoning about moves)     |
          +---------------------------------------------------------------------+
     POST /api/agent/snapshot | POST /api/agent/report      ^ GET /api/agent/actions      (PLANNED)
     (IN FLIGHT)              | (BUILT)                     | POST /actions/{id}/claim     (PLANNED)
                              v                             | POST /actions/{id}/result    (PLANNED)
          +---------------------------------------------------------------------+
          |  fantasy.harrisonku.com  (Next.js on Vercel, server routes)         |
          +---------------------------------------------------------------------+
                              |                             ^
                              v                             |
          Supabase Postgres: league_snapshots, agent_events, transactions, approvals, agent_runs
                 |  fantasy intel latest --rest            ^  fantasy txn plan --rest (+approval)
                 v                                         |  fantasy txn mark verified
          +----------------------------------+      +-------------------------------+
          | THINK: cloud scheduled task      |----->| dashboard /approvals: USER    |
          | (league-loop skill; sim, values) |      | approves / rejects            |
          +----------------------------------+      +-------------------------------+
   alt. executor: local `claude --chrome` session (agent-run + yahoo-browser skills, live run,
   yahoo:roster lock) reads the same approved txns from Supabase via FANTASY_DB_URL.
```
Pieces and status:
- `POST /api/agent/snapshot`, table `league_snapshots` (kinds `my_team, matchup, scoreboard, standings,
  transactions, rosters, free_agents, injuries, settings, other`; `payload_sha256` dedup with
  `last_seen_at`), `fantasy intel list|show|latest [--rest]`, dashboard "League intel" section:
  **IN FLIGHT** by another agent; lands via `docs/handoffs/2026-10-18-muse-intel.md`
  (`src/fantasy/ops/intel.py` and the schema table already exist on main).
- `POST /api/agent/report` (chat digests, injury news, trades as reports): **BUILT** (`docs/integrations/muse.md`).
- Txn ledger + approvals + dashboard approve/reject (`web/src/app/api/approvals/route.ts`): **BUILT**.
- REST ledger writes from cloud (`fantasy run|lock|approval|txn|task ... --rest`, single rows, CAS on
  state; `publish --rest` no longer overwrites ledger/state rows): **BUILT** 2026-10-18
  (`src/fantasy/ops/rest_ledger.py`, runbook `docs/runbooks/agent_ops.md` 5b/5c,
  handoff `docs/handoffs/2026-10-18-rest-ledger.md`).
- Action queue (`/api/agent/actions*`), snapshot-to-rosters converter,
  lineup/streaming/trade tools: **PLANNED** (section 6).

## 3. Trust boundaries
**What Muse is:** an external LLM assistant with (a) a Bearer token for `/api/agent/*` and (b) a browser
session logged in to the user's Yahoo account. We do not control its code, prompt handling or model.

| Boundary | Enforced by | Strength |
|---|---|---|
| Muse can only call `/api/agent/*` with its token; it cannot decide approvals (that route requires the dashboard writer session, `requireWriter`) | server code + secret token | **technical** |
| Muse cannot plan txns or create approvals (no endpoint lets it) | API surface | technical |
| Muse can mark submitted / post results only for txns that are approved, unexpired, unclaimed or claimed by it (PLANNED checks in the action routes) | server code | technical once built |
| Muse reads Yahoo **read-only** | **instruction only** (`docs/integrations/muse.md`) | **weak**: once logged in, Muse's browser can click anything the user could, including drops, trades and messages. Nothing on our side can stop that. |
| Muse executes only the exact approved action | instruction + verification afterwards | weak preventively, strong detectively: every snapshot is diffed against the ledger; unplanned roster changes are flagged |
| Group-chat / Yahoo text never steers agents | agent instructions (`UNTRUSTED_NOTE` in `intel.py`, skills) | instruction; injection attempts are recorded as data |
| Token leakage | token only in Vercel env + Muse's private config; revocation = rotate `AGENT_API_TOKEN` | technical, manual rotation |

Consequences:
1. The approval gate protects against **our** agents acting wrongly. It does **not** protect against
   Muse acting outside the protocol in Yahoo directly; only detection (VERIFY step diffing every
   `my_team`/`transactions` snapshot against verified txns) and the user's trust in Muse do.
2. Detection depends on Muse's own snapshots, so a misbehaving Muse could also misreport. A periodic
   independent read by a local `claude --chrome` session (or Yahoo's own transaction e-mails to the
   user) is the only check outside Muse. Suggest: weekly, and after any mismatch.
3. Muse's result posts are **claims**. A txn becomes `verified` only when a later snapshot (or a local
   browser read) shows the change, and Claude marks it.
4. Yahoo also lets other managers propose trades; accepting is a write and always needs approval.

## 4. Action-queue API (PLANNED)
Same base, auth and envelope as the existing agent API: `https://fantasy.harrisonku.com/api/agent`,
`Authorization: Bearer <AGENT_API_TOKEN>`, JSON `{ok: true|false, error?}`, 401/503/429 as today.
Optional second token `AGENT_EXEC_TOKEN` (suggested): lets the user revoke Muse's *execute* rights
without cutting its read/report access. Rate limit as `/report` (60/h).

### 4.1 `GET /api/agent/actions?status=ready`
Returns txns an executor may perform now. Server-side filter (all must hold):
`transactions.status = 'awaiting_approval'` (the ledger keeps approved txns there until submitted) AND the
linked `approvals.status = 'approved'` (after `expire_overdue`) AND `payload.expires_at > now()` AND
`kind in executor_kinds` (start with `add`, `drop`, `add_drop`, `lineup`; **not** `trade` until trades are
routine) AND no live claim by another executor.
```json
{"ok": true, "server_time": "2026-11-03T15:02:11Z", "actions": [{
  "txn_id": "txn-8f2c...", "kind": "add_drop", "attempt": 1,
  "approval": {"approval_id": "apr-41d0...", "decided_at": "2026-11-03T14:55:00Z"},
  "expires_at": "2026-11-03T23:00:00Z",
  "league": {"league_key": "466.l.44495", "url": "https://basketball.fantasysports.yahoo.com/nba/44495"},
  "team": {"yahoo_team_key": "466.l.44495.t.1", "name": "..."},
  "steps": [
    {"op": "add",  "player": {"name": "Naz Reid", "nba_team": "MIN", "yahoo_player_id": "6430"}, "slot": "BN"},
    {"op": "drop", "player": {"name": "Saddiq Bey", "nba_team": "WAS", "yahoo_player_id": "5739"}}
  ],
  "preconditions": ["Naz Reid shows 'FA' (not 'W' waivers) on Yahoo",
                    "Saddiq Bey is on my roster", "adds used this week < 6"],
  "on_precondition_failure": "report failed; do not substitute"
}]}
```
(Ids and keys above are illustrative.) `yahoo_player_id` is present only when it came from a snapshot;
otherwise the executor matches exact name + NBA team. `steps` is derived from the txn payload by the
server, never free text, so the executor needs no interpretation. Lineup txns: `steps = [{"op":"move",
"player":..., "from":"BN", "to":"UTIL", "date":"2026-11-03"}, ...]`.

### 4.2 `POST /api/agent/actions/{txn_id}/claim`
Body `{"executor": "muse", "run_ref": "<opaque id of Muse's attempt>"}`. Atomically, in one SQL statement
or RPC: re-check every 4.1 condition, then create (or reuse) an executor run in `agent_runs`
(`agent = "muse-executor"`, `mode = "live"`), take the `yahoo:roster` lock in `locks` for that run
(TTL 300 s), and transition the txn `awaiting_approval -> submitted` with `verification.submitted_by`,
`submitted_at`, `executor`. This is exactly `transactions.mark_submitted` (live run + policy + lock), done
server-side. Responses: `200 {"ok":true,"txn":{...},"lock_expires_at":...}`; `409 already_submitted`
(includes current status; the executor must **not** click); `410 expired`; `403 not_approved`;
`423 lock_held` (another executor/session holds `yahoo:roster`). Muse clicks only after a 200.

### 4.3 `POST /api/agent/actions/{txn_id}/result`
```json
{"outcome": "done" | "failed",
 "reason": "precondition_failed: Naz Reid already on Team 7 roster",
 "confirmation_text": "Transaction completed: Added Naz Reid, Dropped Saddiq Bey",
 "screenshot_description": "My Team page, bench shows Naz Reid (MIN - PF,C); Bey absent",
 "observed_at": "2026-11-03T15:04:40Z", "page_url_path": "/nba/44495/1"}
```
- `failed` -> txn `submitted -> failed` with `error = reason` (Muse only reports `failed` when it is sure
  nothing was changed: precondition failed before clicking, or Yahoo showed an explicit error).
- `done` -> txn **stays `submitted`**; the evidence is stored in `verification.executor_result` and an
  `agent_events` row (`kind = verification`, run = executor run). Only Claude moves it to `verified`
  after an independent snapshot.
- Unsure (timeout, page reload) -> `outcome: "unknown"`: stays `submitted`, needs reconciliation
  (`agent-run` section 7). Never retried by the executor.
- Release the lock and end the executor run. Text fields capped (2000 chars), no images, no URLs with
  query strings (crumbs/tokens), validated server-side.

### 4.4 `POST /api/agent/requests` (PLANNED, small)
THINK -> Muse back-channel: `{"want": ["my_team","free_agents"], "reason": "stale >12h", "by": "..."}`,
listed in `GET /api/agent/state` as `requests_open`, closed when a matching snapshot arrives. Until
built, THINK writes a note event and tells the user.

### 4.5 Mapping onto the existing ledger
| Concept | Existing table/code | Change needed |
|---|---|---|
| recommendation | `transactions` row (`kind`, `payload`, `idempotency_key`, `status`) via `transactions.plan` | payload schema with `expires_at`, `steps`-derivable fields, `evidence` (`league-loop` skill section 3) |
| approval | `approvals` row filed by `plan` (`policy.requires_approval`) | approval expiry should be min(6 h, `expires_at`): add `--approval-expires-in` to `txn plan` |
| ready queue | `status='awaiting_approval'` + `approvals.status='approved'` | a SQL view `ready_actions` (or server query) |
| executor claim | `mark_submitted` (live run + lock + policy) | server-side port (TS or a Postgres function) with identical checks |
| executor result | `mark_failed` / `verification` JSON | `verification.executor_result` |
| verification | `mark_verified(evidence)` by Claude | evidence = snapshot id + what it shows |
| idempotency | `idempotency_key` (kind, payload, scope, attempt); compare-and-set transitions | claim is CAS on status, so two executors cannot both submit |
| expiry | approvals expire lazily (`expire_overdue`) | also check `payload.expires_at` at list and claim time; THINK cancels expired txns |

## 5. Open questions (must be answered before relying on the loop)
1. **Can Muse browse authenticated Yahoo pages reliably**, on a schedule, without the user present? (Session
   lifetime, "Are you sure it's you?" re-auth, CAPTCHAs.) Test: ask Muse to post a `my_team` snapshot
   daily for a week and look at gaps.
2. **Can Muse send custom HTTP headers** (`Authorization: Bearer`) and JSON bodies from its tool, or only
   GET URLs? If only GET, the API needs a different auth (e.g. per-request signed URL), which weakens it.
   The pre-draft `/report` flow is the first test.
3. **Yahoo 2FA**: if Yahoo asks for a code, only the user can enter it; how often does that happen for
   Muse's session? Executions in that window fail with `precondition_failed: login required`.
4. Can Muse take a screenshot and describe it, and reliably read exact player names, `FA` vs `W`
   (waivers) flags and lineup lock states?
5. Lineup lock type (daily vs weekly), waiver type and FAAB in our league: unverified (`fantasy settings
   show`); determines expiry rules and whether adds are immediate.
6. Does the user want Muse to execute at all, or only read (local `claude --chrome` executes)? The
   design works either way; the queue is useful for both executors.
7. Yahoo terms of service on automated access by third-party assistants: the user's call.

## 6. Build plan (after the 2026-10-19 draft; sizes are rough guesses)
| # | Step | Size | Depends on |
|---|---|---|---|
| 1 | Land muse-intel (snapshot API, `league_snapshots`, `fantasy intel`, dashboard section); have Muse post `rosters` + `my_team` daily | in flight | handoff 2026-10-18-muse-intel |
| 2 | `fantasy intel export-league`: snapshots -> `rosters.json` / `schedule.json` for `fantasy sim` (name -> player_id resolution, unresolved list) + tests on synthetic snapshots | S (½ day) | 1 |
| 3 | THINK job, report-only: cloud scheduled task running the `league-loop` skill daily 10:00 ET, output = text recommendations, no txns | S | 2 |
| 4 | **DONE 2026-10-18.** REST ledger: `fantasy run|lock|approval|txn|task ... --rest` (PostgREST, CAS on status via `PATCH ...?status=in.(...)`), and `publish --rest` is insert-if-missing for ledger/state tables, so cloud runs no longer round-trip through SQLite/publish and a stale copy cannot clobber a decision | M | — |
| 5 | Txn payload schema + `--approval-expires-in`; dashboard approval card shows evidence, expected deltas, expiry | S–M | 4 |
| 6 | Action queue: `GET /actions`, `POST /actions/{id}/claim`, `POST /actions/{id}/result` (+ `ready_actions` view, executor run + lock server-side, tests mirroring `test_transactions`) | M (1–1.5 days) | 4, 5 |
| 7 | VERIFY automation: diff each `my_team`/`transactions` snapshot against verified/submitted txns; flag unplanned changes on the dashboard | S–M | 1, 6 |
| 8 | Dry-run the full loop with a lineup move (lowest risk), executor = local `claude --chrome`; then with Muse; then add/drop | S + calendar time | 6, 7 |
| 9 | Tools to make THINK useful: lineup optimizer (T-005), streaming (T-006), trade evaluator (T-008) | L | 2 |
| 10 | `/api/agent/requests` back-channel; update `docs/integrations/muse.md` with the executor protocol (coordinate with its owner) | S | 6 |

## 7. Laptop alternative (unattended local `claude --chrome`)
Claude in Chrome on the user's laptop is the trusted executor today (we control the agent; the browser is
the user's). Running it **unattended** is not a documented configuration:
- Desktop-app local scheduled tasks run only while the desktop app is open and the computer is awake
  (https://code.claude.com/docs/en/desktop-scheduled-tasks).
- Whether Claude in Chrome is available inside such a scheduled task is **not documented**; needs a test
  (https://code.claude.com/docs/en/chrome).
- `claude -p --chrome` (headless) under cron/launchd is **not documented** (https://code.claude.com/docs/en/headless).
- Cloud routines run in Anthropic's cloud and cannot reach the laptop's browser
  (https://code.claude.com/docs/en/routines).
- Unattended permission prompts stall or are denied depending on the permission mode
  (https://code.claude.com/docs/en/permission-modes); a browser site-permission prompt mid-action would
  stall the run.

So treat it as an **experiment**: first a read-only, `dry_run` scheduled task on the laptop that posts a
`my_team` snapshot through the same API (it then plays Muse's READ role, with an agent we control), and
only after that works for a week consider letting it execute approved lineup txns from the queue.
