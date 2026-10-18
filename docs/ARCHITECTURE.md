# Architecture

Hybrid system: **deterministic Python quant engine** (tools) + **Claude Code agents** (orchestration,
judgement, browser execution) + **Supabase** (durable state) + **Next.js dashboard on Vercel** (observability).

```
             ┌──────────── Claude Code agent (local, with Claude in Chrome) ────────────┐
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
| `src/fantasy/league/` | model | `LeagueSettings` (categories, roster slots, draft order), defaults for league 44495; read-only Yahoo OAuth/API client |
| `src/fantasy/data/` | data | ingestion from external sources → `data/raw/` with provenance; `fantasy data ...` CLI |
| `src/fantasy/model/` | model | projections, category valuation (makes/attempts for %), replacement level |
| `src/fantasy/draft/` | draft | draft order (3RR/custom), availability model (ADP), draft optimizer, Monte Carlo draft sim, live draft state (`store.py`), name resolution |
| `src/fantasy/sim/` | model | league-aware season simulator (`fantasy sim ...`): schedule, weekly H2H engine, playoffs, pre-draft projection, draft-check; research experiments live beside it |
| `src/fantasy/ops/` | infra | agent runs/events, locks, approvals, transaction ledger, tasks; `fantasy run|lock|approval|txn|task` |
| `src/fantasy/db/` | infra | `schema.py` (single source of truth), `engine.py`, `ddl.py` (renders Supabase migration), `rest.py` (publish/ping over Supabase PostgREST), `sqldump.py` (INSERT statements for SQL-only access) |
| `src/fantasy/cli.py` | all | top-level Typer app; sub-apps registered from each area |
| `web/` | web | Next.js (App Router, TS, Tailwind) dashboard: Draft / Matchup / League / Activity tabs, Players, History, external-agent API `/api/agent/*` (see `docs/runbooks/dashboard.md`) |
| `supabase/migrations/` | infra | generated from `schema.py` + RLS policies |
| `.claude/skills/` | infra | project skills: draft-prep, live-draft, yahoo-browser, projections-update, model-eval, season-sim, agent-run, handoff, in-season-ops |
| `docs/` | all | architecture, runbooks, research, `STATUS.md`, `handoffs/` |

## Key contracts

### Processed projections CSV (`data/processed/projections_2026_27.csv`)
One row per player, **per-game means** for the 2026-27 season:
`player_id,name,team,positions,age,gp,gp_sd,mpg,fgm,fga,ftm,fta,fg3m,pts,reb,ast,stl,blk,tov,adp,status,injury_note,source`
- `player_id`: lowercase ascii slug of the name (`nikola-jokic`). `fantasy.data.ids.slugify`.
- `positions`: Yahoo eligibility, pipe-separated (`PG|SG`). G/F/Util derived by code, not stored.
- `adp`: Yahoo ADP if known (blank otherwise). `gp`: expected games (of 82).
- Percent categories are derived from makes/attempts; never store averaged percentages as inputs.

### CLI (all commands accept `--json`; JSON is the agent interface, text is for humans)
- `fantasy settings show` – effective league settings and whether they're verified.
- `fantasy values build` – run valuation from projections → model run + player values.
- `fantasy draft init --draft-id ... [--mock]` (mock ids need `--mock`; real ids `nba.l.<digits>-<year>`) / `fantasy draft pick <player> --overall N` / `fantasy draft undo N` / `fantasy draft recommend [--kappa --punt]` / `fantasy draft board` / `fantasy draft queue` / `fantasy draft set-order` / `fantasy draft watch` / `fantasy draft simulate [--draft-id]` / `fantasy draft prepare-pick|confirm-pick`. Every draft command's JSON carries `ok` and the resolved `draft_id`.
- `fantasy sim schedule|season|matchup|compare|draft-check|preseason|fetch-schedule` – season simulator (skill `season-sim`).
- `fantasy status` – one-shot overview (freshness, pending approvals, locks, active draft).
- `fantasy run start|event|end`, `fantasy lock acquire|release|show`, `fantasy approval request|list|decide`,
  `fantasy txn plan|mark|list`, `fantasy task list|add|claim|done` (ops).
- `fantasy publish [--rest]` – push local state (sqlite) to Supabase: SQL to `FANTASY_PUBLISH_URL`, or `--rest` over HTTPS/PostgREST (cloud sessions). `fantasy db init|ping [--rest]`.
- Exit codes (ops contract, `fantasy.ops.common`): 0 ok, 1 error, 2 usage, 3 conflict, 4 not allowed, 5 duplicate, 6 not found, 7 invalid state, 8 still pending.

### Durable state
All runtime state goes through `fantasy.db` tables (see `schema.py`). Markdown is for knowledge
(docs, handoffs), the DB is for state (runs, decisions, recommendations, transactions, locks).

## Decisions & trade-offs
- **Supabase Postgres is the system of record**; SQLite (same schema) is used for tests/offline.
  Single schema in SQLAlchemy → generated DDL, so no drift.
- **Dashboard RLS is read-only** for the public key. Fantasy data is low-sensitivity; anything that
  writes (approvals, manual draft picks) goes through Next.js server routes holding the service key,
  gated by a dashboard passphrase.
- **No custom browser bot.** Claude Code drives Yahoo via Claude in Chrome (the user's logged-in
  Chrome). Python never touches Yahoo's UI.
- **Draft picks are recomputed deterministically** after every observed pick (`fantasy draft recommend`);
  the agent interprets and executes, it does not re-derive valuations.
