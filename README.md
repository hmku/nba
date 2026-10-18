# Fantasy NBA: optimizer + agent system

Autonomous-ish management of one Yahoo fantasy basketball team (league 44495, 12-team head-to-head
**9-category**, 2026-27, third-round-reversal draft). It is a hybrid: a **deterministic Python quant
engine** (the `fantasy` CLI: projections, valuation, draft optimizer, simulation) used as tools by
**Claude Code agents** that orchestrate, drive Yahoo in the browser, verify, and keep an audit trail;
**Supabase Postgres** holds durable state; a **Next.js dashboard on Vercel** shows it
(<https://fantasy-nba-ruby.vercel.app>, Vercel login required). Agents never re-derive valuations; they
run the tools and interpret the JSON.

> Basketball primer for quants: H2H 9-cat means each week you play one opponent and win or lose each of
> 9 categories (FG%, FT%, 3PM, PTS, REB, AST, STL, BLK, TO; lower TO is better). Win 5 of 9 to win the week.
> FG%/FT% are ratios, so they are modeled from makes and attempts, never averaged percentages.

## Architecture

```
             ┌──────────── Claude Code agent (local, with Claude in Chrome) ────────────┐
 Yahoo  <──► │ browser: read league/draft state, click picks, verify                    │
 (web)       │ terminal: `fantasy ...` CLI  ──►  JSON on stdout (recs, status, ledger)  │
             └───────────────┬──────────────────────────────────────────────────────────┘
                             │ SQLAlchemy (FANTASY_DB_URL)
                             ▼
   data/raw (cached sources, provenance)   Supabase Postgres  ◄── Next.js dashboard (web/, Vercel)
   data/processed (projections CSV)        (runs, events, recs,     reads with publishable key (RLS read-only),
                                            picks, locks, approvals) writes only via server routes (service key)
```

Full detail and design trade-offs: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Path | Purpose |
|---|---|
| `src/fantasy/league/` | `LeagueSettings` (categories, roster slots, draft order), Yahoo API client (read-only) |
| `src/fantasy/data/` | ingestion into `data/raw/` with provenance; `fantasy data ...` |
| `src/fantasy/model/` | projections, G-score valuation, category-win model |
| `src/fantasy/draft/` | draft order (3RR), ADP availability, optimizer, Monte Carlo sim, live draft state |
| `src/fantasy/ops/` | runs/events, locks, approvals, txn ledger, tasks, policy, `status` |
| `src/fantasy/db/` | `schema.py` (single source of truth), engine, DDL for Supabase |
| `supabase/migrations/` | generated from `schema.py` plus RLS policies |
| `web/` | Next.js dashboard (App Router, TS, Tailwind) |
| `data/manual/` | human-edited overrides: injuries, roles, rookies, ADP, positions |
| `data/processed/` | `projections_2026_27.csv` (per-game means; contract in ARCHITECTURE.md) |
| `.claude/skills/`, `.claude/agents/` | agent playbooks and read-only reviewer subagents |
| `docs/` | `STATUS.md`, `runbooks/`, `research/`, `handoffs/` |
| `tests/` | offline pytest suite on synthetic data (`tests/synthetic.py`) |

## Quickstart

```bash
git clone https://github.com/hmku/nba && cd nba
pip install -e ".[dev]"          # Python >= 3.11; adds the `fantasy` command
fantasy status                   # runs, locks, approvals, data freshness, active draft
```

**Database.** With `FANTASY_DB_URL` unset, everything uses local SQLite at `var/fantasy.db` (dev, tests).
Production is Supabase Postgres (project `fantasy-nba`): set `FANTASY_DB_URL` to its session-pooler URI.
That URL is a secret supplied by the owner (Supabase, Project Settings, Database); keep it in your shell
or an untracked `.env`, never commit it. If you worked on SQLite, `fantasy publish` copies rows up.

**Mock draft in four commands** (rehearsals always use a `mock-*` id and `--mock`):

```bash
fantasy draft init --draft-id mock-1 --mock
export FANTASY_DRAFT_ID=mock-1
fantasy draft recommend          # optimizer output (about 3 s)
fantasy draft pick <overall> <player>   # record a pick; also `draft board`, `draft undo`, `draft simulate`
```

**Checks:** `pytest` (offline), `ruff check src tests`.
**Dashboard:** `cd web && npm install && npm run dev` (see [docs/runbooks/dashboard.md](docs/runbooks/dashboard.md);
[web/README.md](web/README.md) is the stock create-next-app text).
Every command accepts `--json`; JSON is the agent interface.

## How the model works

1. **Projections** ([model/projections.py](src/fantasy/model/projections.py)). Per-minute rates from three
   seasons (weights 5/3/2), regressed to position-group means; makes and attempts projected separately;
   an age curve; recency-weighted minutes; games played shrunk toward a league prior (with `gp_sd`);
   then manual overrides from `data/manual/` (injuries, trades, roles, rookies, ADP, positions).
   Rebuild with `fantasy data build-projections`; inspect with `fantasy data top`.
2. **Valuation** ([model/valuation.py](src/fantasy/model/valuation.py)). G-scores (Rosenof 2024): z-scores
   over the draftable pool with a variance term that down-weights categories noisy week to week
   (STL, BLK, TO, 3PM). FG%/FT% use "impact" `makes - p_pool * attempts`. Availability is blended with
   streaming replacement and a replacement-level adjustment. `fantasy values build [--punt ft_pct]`.
3. **Category-win model** ([model/catmodel.py](src/fantasy/model/catmodel.py)). Weekly team totals are
   Normal; ratios use delta-method variance. The opponent is a league-average team (snake-drafted in ADP
   order). P(win category) is a Normal CDF; expected category wins is the sum, and matchup win
   probability comes from a Poisson-binomial (categories treated as independent).
4. **Draft optimizer** ([draft/optimizer.py](src/fantasy/draft/optimizer.py)). For each candidate pick, a
   rollout fills later picks greedily from players likely still available, then scores the final roster
   by expected category wins. Availability ([draft/availability.py](src/fantasy/draft/availability.py))
   models draft position as Normal(ADP, sd growing with ADP). Pick order, including third-round reversal
   (slot 1 picks 1, 24, 36, 37, 60, 61, ...), is in [draft/order.py](src/fantasy/draft/order.py); positional
   feasibility is in `draft/roster.py`. A punt-strategy scan is included.
5. **Monte Carlo sim** ([draft/simulate.py](src/fantasy/draft/simulate.py)). Noisy-ADP opponents; the optimizer
   is compared with a best-player-available baseline (`fantasy draft simulate`).

Caveats (details in STATUS.md): the opponent is league-average so win probabilities are optimistic;
`GAME_CV`, ADP sd and `USAGE` are uncalibrated priors; weekly variance ignores schedule and category
correlation; recommendation margins under about 0.03 are noise; the sim edge is in-sample. Every model
run is versioned (`model_runs`: version, git sha, params, input hashes); model changes bump `MODEL_VERSION`
and need tests (see the `model-eval` skill).

## Agents and safety

Skills in [.claude/skills/](.claude/skills/): `draft-prep`, `live-draft`, `yahoo-browser`,
`projections-update`, `model-eval`, `agent-run`, `handoff`, and `in-season-ops` (Phase 2 placeholder; most of
what it describes does not exist yet). Read-only subagents in `.claude/agents/`: `quant-reviewer`,
`data-auditor`, `draft-shadow`.

Operational safety is enforced by the ops ledger (`fantasy run|lock|approval|txn|task`), described in
[docs/runbooks/agent_ops.md](docs/runbooks/agent_ops.md):
- **Runs** record events, observations and decisions (with `agrees_with_model`). Mode is `dry_run` (default) or
  `live`; Yahoo roster state is never touched outside a `live` run.
- **Locks** (`yahoo:roster`, `yahoo:browser`, `draft:<id>`) are leases held before any Yahoo write.
- **Txn ledger** plans each write idempotently, then marks it verified after an independent re-read on
  Yahoo. A txn stuck in `submitted` is reconciled, never retried blindly.
- **Approvals:** drops, add/drops and trades always need the owner's approval for now. `fantasy approval decide`
  and `fantasy lock release --force` are human-only actions.
- No secrets, cookies or tokens in logs, events or commits.

## Draft night and Yahoo access

The draft is Mon 2026-10-19, 10:00 PM ET. Human runbook: [docs/runbooks/live_draft.md](docs/runbooks/live_draft.md)
(agent protocol: `live-draft` skill; day-of checklist: `draft-prep` skill). It needs a **local** Claude Code
session with Claude in Chrome (`claude --chrome`); cloud sessions cannot reach Yahoo or NBA sites.

Yahoo's API is **application-only since 2026** (manual approval, weeks of lead time) and **read-only**; there is
no write access. The OAuth client exists (`fantasy settings yahoo-auth`) but is untested against live Yahoo.
The **browser is the primary path** for all reads and writes. See [docs/research/yahoo_api.md](docs/research/yahoo_api.md) section 0.
League settings defaults are unverified until confirmed with `fantasy settings verify`.

## Status and roadmap

Current state and priorities: [docs/STATUS.md](docs/STATUS.md); backlog: `fantasy task list`.
- **Phase 1, draft MVP:** built; needs real-world verification (settings, draft order, ADP/position gaps, mock rehearsal).
- **Phase 2, in-season:** Yahoo roster sync, daily lineup optimizer, waivers/streaming, weekly matchup sim, calibration.
- **Phase 3:** trade evaluator, backtesting, scheduled morning agent.

## Contributing (humans and agents)

- Read [CLAUDE.md](CLAUDE.md) first (`AGENTS.md` is a symlink to it): start-of-session steps, safety rules, conventions.
- Orient from `docs/STATUS.md` and the newest notes in `docs/handoffs/`; claim work with `fantasy task claim T-00N --owner <you>`.
- Conventions: typed Python 3.11+, small modules, ingestion separate from modeling, tests on synthetic data with no network.
- Before finishing: `pytest`, `ruff check src tests`, `cd web && npm run build` if web changed.
- Write `docs/handoffs/YYYY-MM-DD-<topic>.md` (template in the `handoff` skill) and update STATUS.md.
- Always commit and push your own paths only: `git pull --rebase --autostash origin main && git push origin main`.
