# Architecture

Hybrid system: **deterministic Python quant engine** (tools) + **Claude Code agents** (orchestration,
judgement, browser execution) + **Supabase** (durable state) + **Next.js dashboard on Vercel** (observability).

```
             ┌──────────── Claude Code agent (local, with a browser) ───────────────────┐
 Yahoo  <──► │ browser: read league/draft state, click picks, verify                   │
 (web)       │ terminal: `fantasy ...` CLI  ──►  JSON on stdout (recs, status, ledger)  │
             └───────────────┬───────────────────────────────────────────────────────────┘
                             │ SQLAlchemy (FANTASY_DB_URL)
                             ▼
   data/raw (cached sources, provenance)   Supabase Postgres  ◄── Next.js dashboard (web/, Vercel)
   data/processed (projections CSV)        (runs, events, recs,     reads with publishable key (RLS read-only),
                                            picks, locks, approvals) writes only via server routes (service key)
```

## Repository layout

| Path | Owner area | Purpose |
|---|---|---|
| `src/fantasy/league/` | model / in-season | `LeagueSettings` (categories, roster slots, draft order), defaults for league 44495; read-only Yahoo OAuth/API client; roster sync (`sync.py`, `sync_cli.py`): normalises browser captures (player ids, team codes, draft slots) and turns them into the league state `data/manual/league_rosters_2026-27.json` with a diff (`fantasy league import-capture|sync`) |
| `src/fantasy/lineup/` | in-season | daily lineup optimizer (`lineup-0.2.0`): player values from the matchup gradient (`value.py`), exact slot assignment under Yahoo position rules (`optimize.py`), day/week plan with IL moves, `target_slots`, `changes_vs_current`, stale-capture guard (`plan.py`); `fantasy lineup plan` (`docs/research/lineup_optimizer.md`) |
| `src/fantasy/inseason/` | in-season | `inseason-0.1.0`: position-aware games analysis (`lineup.py`, `possim.py`), streaming ranker (`stream.py`), paired two-team trade simulation (`trade.py`); `fantasy inseason lineup|stream|trade`. Never touches Yahoo |
| `scripts/yahoo/` | in-season | browser JS run by an agent in the logged-in Yahoo page: `capture_rosters.js`, `capture_free_agents.js` (runbook `docs/runbooks/roster_sync.md`) |
| `src/fantasy/data/` | data | ingestion from external sources → `data/raw/` with provenance; `fantasy data ...` CLI |
| `src/fantasy/model/` | model | projections, category valuation (makes/attempts for %), replacement level |
| `src/fantasy/draft/` | draft | draft order (3RR/custom), availability (`room_sim.py` simulated rooms = default; `availability.py` analytic ADP), draft optimizer, policies + paired policy evaluation (`policy.py`, `policy_eval.py`), Monte Carlo draft sim, live draft state (`store.py`), name resolution |
| `src/fantasy/valuesearch/` | model | value search: learned season-outcome model (`value-model-0.2.0` artifact) re-ranking the optimizer's top candidates; the default `draft recommend` policy |
| `src/fantasy/sim/` | model | league-aware season simulator (`fantasy sim ...`, `season-sim-0.6.0`): Yahoo schedule import, weekly H2H engine, injury spells + IL replacement (default), playoffs, add/drop compare, pre-draft projection, draft-check; research experiments live beside it |
| `src/fantasy/ops/` | infra | agent runs/events, locks, approvals, transaction ledger, tasks; `fantasy run|lock|approval|txn|task` (`--rest`: `rest_ledger.py`); Muse intel reader `fantasy intel` (`intel.py`) |
| `src/fantasy/db/` | infra | `schema.py` (single source of truth), `engine.py`, `ddl.py` (renders Supabase migration), `rest.py` (publish/ping over Supabase PostgREST), `sqldump.py` (upsert statements for SQL-only access, e.g. the Supabase MCP; snapshot-tested with `ddl.py`) |
| `src/fantasy/cli.py` | all | top-level Typer app; sub-apps registered from each area |
| `web/` | web | Next.js (App Router, TS, Tailwind) dashboard: Matchup / League / Activity / Draft tabs, Players, History, external-agent API `/api/agent/*` (see `docs/runbooks/dashboard.md`). Installable PWA: `src/app/manifest.ts`, icons, and a minimal service worker `public/sw.js` that pre-caches static assets and `offline.html` only. It never caches pages, `/api` or Supabase |
| `supabase/migrations/` | infra | generated from `schema.py` + RLS policies |
| `.claude/skills/` | infra | project skills: draft-prep, live-draft, yahoo-browser, projections-update, model-eval, season-sim, agent-run, handoff, daily-routine (scheduled `fantasy-daily`), league-loop, in-season-ops (capability map + approval policy) |
| `docs/` | all | architecture, runbooks, research, `STATUS.md`, `handoffs/` |

## Key contracts

### Processed projections CSV (`data/processed/projections_2026_27.csv`)
One row per player, **per-game means** for the 2026-27 season:
`player_id,name,team,positions,age,gp,gp_sd,mpg,fgm,fga,ftm,fta,fg3m,pts,reb,ast,stl,blk,tov,adp,status,injury_note,source,playoff_mult`
(default file = 50/50 ours/ESPN per-game blend per `data/manual/projection_blend.json`; unblended model in `projections_2026_27.base.csv`)
- `player_id`: lowercase ascii slug of the name (`nikola-jokic`). `fantasy.data.ids.slugify`.
- `positions`: Yahoo eligibility, pipe-separated (`PG|SG`). G/F/Util derived by code, not stored.
- `adp`: Yahoo ADP if known (blank otherwise). `gp`: expected games (of 82).
- Percent categories are derived from makes/attempts; never store averaged percentages as inputs.

### CLI (all commands accept `--json`; JSON is the agent interface, text is for humans)
- `fantasy settings show` – effective league settings and whether they're verified.
- `fantasy values build` – run valuation from projections → model run + player values.
- `fantasy draft init --draft-id ... [--mock]` (mock ids need `--mock`; real ids `nba.l.<digits>-<year>`) / `fantasy draft pick <player> --overall N [--allow-unknown|--overwrite]` / `fantasy draft undo N` / `fantasy draft recommend [--kappa --punt]` / `fantasy draft board` / `fantasy draft queue` / `fantasy draft set-order` / `fantasy draft watch` / `fantasy draft simulate [--draft-id]` / `fantasy draft prepare-pick|confirm-pick`. Every draft command's JSON carries `ok` and the resolved `draft_id`.
- `fantasy sim schedule|season|matchup|compare|draft-check|preseason|symmetric|fetch-schedule|import-schedule|export-league` – season simulator (skill `season-sim`).
- `fantasy league import-capture --raw <capture> --kind rosters|free_agents` / `fantasy league sync --capture <file> [--write]` – Yahoo roster sync from browser captures (runbook `roster_sync.md`).
- `fantasy lineup plan --date D [--week] [--locked ids] [--accumulated f]` – daily lineup + IL moves (exit 2 `stale_capture` / `infeasible_roster`).
- `fantasy inseason lineup|stream|trade` – games lost to slot rules, streaming ranker, paired trade evaluation.
- `fantasy data alt-projections --blend market|espn-only|... --out <csv>` – alternative projections for conservative sims (`--projections`).
- `fantasy intel latest|list|show [--rest]` – read Muse's league snapshots / reports (read-only).
- `fantasy status` – one-shot overview (freshness, pending approvals, locks, active draft).
- `fantasy run start|event|end`, `fantasy lock acquire|release|show`, `fantasy approval request|list|decide`,
  `fantasy txn plan|mark|list`, `fantasy task list|add|claim|done` (ops; `--rest` on all but `approval decide`,
  `lock release --force`, `run reap`, `task seed`).
- `fantasy publish [--rest]` – push local state (sqlite) to Supabase: SQL to `FANTASY_PUBLISH_URL`, or `--rest` over HTTPS/PostgREST (cloud sessions). `fantasy db init|ping [--rest]`.
- Exit codes (ops contract, `fantasy.ops.common`): 0 ok, 1 error, 2 usage, 3 conflict, 4 not allowed, 5 duplicate, 6 not found, 7 invalid state, 8 still pending.
- Failure JSON (every group, `fantasy.cli_common`): `{"ok": false, "code": <machine code>, "message": <text>, "error": ...}`
  on stdout with `--json` (stderr `error: ...` without). Branch on the exit code, then `code` (`usage`, `not_found`,
  `conflict`, `unresolved_player`, `draft_complete`, `order_conflicts_with_recorded_picks`, `not_allowed`, `lock_held`, `invalid_state`, `still_pending`,
  `rest_auth`, ...). `error` is kept for old callers: it equals `code` in ops commands (and `prepare-pick`/`confirm-pick`
  ledger refusals) but is the human message in `draft`/`sim`/`values`/`settings`.

### Durable state
All runtime state goes through `fantasy.db` tables (see `schema.py`). Markdown is for knowledge
(docs, handoffs), the DB is for state (runs, decisions, recommendations, transactions, locks).

## Decisions & trade-offs
- **Supabase Postgres is the system of record**; SQLite (same schema) is used for tests/offline.
  Single schema in SQLAlchemy → generated DDL, so no drift.
- **Dashboard RLS is read-only** for the public key. Fantasy data is low-sensitivity; anything that
  writes (approvals, manual draft picks) goes through Next.js server routes holding the service key,
  gated by a dashboard passphrase.
- **No custom browser bot.** Claude drives Yahoo in the user's logged-in browser (the desktop app's
  built-in browser or Claude in Chrome). Python never touches Yahoo's UI.
- **Draft picks are recomputed deterministically** after every observed pick (`fantasy draft recommend`);
  the agent interprets and executes, it does not re-derive valuations.
- **In season, the local session is the executor.** The scheduled `fantasy-daily` task (`daily-routine` skill) captures
  Yahoo in the browser, syncs (`fantasy league sync`), plans (`fantasy lineup plan`), and sets lineup + IL moves in a
  `live` run (pre-authorised by the user, verified by a server re-read). Add/drops and trades are only recommended
  (paired `sim compare` / `inseason trade` with SEs) and need the user's approval. The cloud/Muse loop (`league-loop`)
  reads intel and plans; its executor queue is not built yet.
