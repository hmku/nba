# Fantasy NBA: optimizer + agent system

Autonomous-ish management of one Yahoo fantasy basketball team (league 44495, 12-team head-to-head
**9-category**, 2026-27, third-round-reversal draft). It is a hybrid: a **deterministic Python quant
engine** (the `fantasy` CLI: projections, valuation, draft optimizer, simulation) used as tools by
**Claude Code agents** that orchestrate, drive Yahoo in the browser, verify, and keep an audit trail;
**Supabase Postgres** holds durable state; a **Next.js dashboard on Vercel** shows it
(<https://fantasy.harrisonku.com>; fallback <https://fantasy-nba-ruby.vercel.app>, Vercel login required). Agents never re-derive valuations; they
run the tools and interpret the JSON.

> Basketball primer for quants: H2H 9-cat means each week you play one opponent and win or lose each of
> 9 categories (FG%, FT%, 3PM, PTS, REB, AST, STL, BLK, TO; lower TO is better). Win 5 of 9 to win the week.
> FG%/FT% are ratios, so they are modeled from makes and attempts, never averaged percentages.

## Architecture

```
             ┌──────────── Claude Code agent (local, with a browser) ───────────────────┐
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
| `src/fantasy/league/` | `LeagueSettings` (categories, roster slots, draft order), Yahoo API client (read-only), roster sync from browser captures (`fantasy league import-capture|sync`) |
| `src/fantasy/lineup/` | daily lineup optimizer: exact position-aware slot assignment, matchup-weighted (`fantasy lineup plan`, `lineup-0.3.0`; banked matchup totals from `fantasy league score`) |
| `src/fantasy/inseason/` | in-season analysis: games lost to slot rules, streaming ranker, paired trade evaluation (`fantasy inseason lineup|stream|trade`) |
| `scripts/yahoo/` | browser JS that captures all rosters / free agents from Yahoo pages (run by an agent in the logged-in browser) |
| `src/fantasy/data/` | ingestion into `data/raw/` with provenance; `fantasy data ...` |
| `src/fantasy/model/` | projections, G-score valuation, category-win model |
| `src/fantasy/draft/` | draft order (3RR), availability (simulated rooms; analytic ADP), optimizer, Monte Carlo sim, policy evaluation, live draft state |
| `src/fantasy/valuesearch/` | value search: learned season-outcome model that re-ranks the optimizer's top candidates (default in `draft recommend`) |
| `src/fantasy/sim/` | season simulator: P(playoffs)/P(title), weekly matchups, roster-change comparison (`fantasy sim ...`) |
| `src/fantasy/ops/` | runs/events, locks, approvals, txn ledger, tasks, policy, `status` |
| `src/fantasy/db/` | `schema.py` (single source of truth), engine, DDL for Supabase, REST publish |
| `supabase/migrations/` | generated from `schema.py` plus RLS policies |
| `web/` | Next.js dashboard (App Router, TS, Tailwind): Matchup / League / Activity / Draft tabs; installable as an app (PWA) |
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
fantasy draft recommend          # default policy: value search + simulated availability (about 3 s)
fantasy draft pick <player> --overall N   # record a pick; also `draft board`, `draft undo N`, `draft simulate`
```

**In season** (all offline except the browser capture; skills `daily-routine`, `season-sim`, `in-season-ops`):

```bash
fantasy league import-capture --raw <raw.json> --kind rosters   # normalise a Yahoo browser capture (scripts/yahoo/)
fantasy league sync --capture data/raw/yahoo/league_rosters_<date>.json --write   # -> data/manual/league_rosters_2026-27.json
fantasy lineup plan --date <YYYY-MM-DD> --week          # today's position-aware lineup + IL moves + rest of week
fantasy sim season --league-file data/manual/league_rosters_2026-27.json   # P(playoffs)/P(title), weekly odds
fantasy sim compare --add "<FA>" --drop <player_id> --league-file data/manual/league_rosters_2026-27.json
fantasy inseason stream --start <d1> --end <d2>         # streaming candidates; `inseason trade` for trades
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
   by expected category wins. Availability (default since 2026-10-19, [draft/room_sim.py](src/fantasy/draft/room_sim.py))
   = 300 simulated rooms from the current board (3 "sharp" + 8 noisy-ADP drafters); the older analytic
   Normal(ADP, sd growing with ADP) model ([draft/availability.py](src/fantasy/draft/availability.py)) is
   `--availability adp`. A value search ([valuesearch/](src/fantasy/valuesearch/), `value-model-0.2.0`) then
   re-ranks the optimizer's top 6 by simulated season outcome (`docs/research/draft_policy.md`). Pick order, including third-round reversal
   (slot 1 picks 1, 24, 36, 37, 60, 61, ...), is in [draft/order.py](src/fantasy/draft/order.py); positional
   feasibility is in `draft/roster.py`. A punt-strategy scan is included.
5. **Monte Carlo sim** ([draft/simulate.py](src/fantasy/draft/simulate.py)). Noisy-ADP opponents; the optimizer
   is compared with a best-player-available baseline (`fantasy draft simulate`).
6. **Season simulator** ([sim/](src/fantasy/sim/), `season-sim-0.6.0`). Plays the real rosters through Yahoo's real
   schedule (`data/manual/league_schedule.json`) and playoffs with fitted priors (`docs/research/sim_calibration.md`):
   game-level noise, injury spells, and IL replacement for long absences (default since 0.6.0). It gives
   P(playoffs)/P(title) for every team, weekly matchup odds, and paired add/drop deltas (`fantasy sim ...`).
7. **In-season decisions.** The lineup optimizer ([lineup/](src/fantasy/lineup/)) assigns today's slots exactly under
   Yahoo's position rules, weighting each category by how much it moves this week's expected categories won. Yahoo
   injury tags set P(plays) priors. The `inseason` tools ([inseason/](src/fantasy/inseason/)) rank streamers and
   evaluate trades with paired simulations for both teams.

Caveats (details in STATUS.md): the opponent is league-average so win probabilities are optimistic;
ADP sd and `USAGE` are priors (game-to-game noise is fitted: `model/noise.py`; the first real draft suggests the room
was ~0.6-0.7x the prior ADP spread, one sample: `docs/research/draft_calibration_2026.md`); the draft's weekly
variance ignores schedule and category correlation; recommendation margins under about 0.03 are noise; the sim edge is in-sample. Every model
run is versioned (`model_runs`: version, git sha, params, input hashes); model changes bump `MODEL_VERSION`
and need tests (see the `model-eval` skill).

## Agents and safety

Skills in [.claude/skills/](.claude/skills/): `draft-prep`, `live-draft`, `yahoo-browser`,
`projections-update`, `model-eval`, `season-sim`, `agent-run`, `handoff`, `daily-routine` (the scheduled
`fantasy-daily` morning run: sync, lineup + IL moves on Yahoo, recommendations), `league-loop` (in-season Muse -> Claude ->
approve -> execute -> verify loop), and `in-season-ops` (capability map of the in-season tools + approval policy). Read-only subagents in `.claude/agents/`: `quant-reviewer`,
`data-auditor`, `draft-shadow`.

Operational safety is enforced by the ops ledger (`fantasy run|lock|approval|txn|task`), described in
[docs/runbooks/agent_ops.md](docs/runbooks/agent_ops.md):
- **Runs** record events, observations and decisions (with `agrees_with_model`). Mode is `dry_run` (default) or
  `live`; Yahoo roster state is never touched outside a `live` run.
- **Locks** (`yahoo:roster`, `yahoo:browser`, `draft:<id>`) are leases held before any Yahoo write.
- **Txn ledger** plans each write idempotently, then marks it verified after an independent re-read on
  Yahoo. A txn stuck in `submitted` is reconciled, never retried blindly.
- **Approvals:** drops, add/drops and trades always need the owner's approval for now. Lineup changes and IL moves are
  pre-authorised (live run, verified by a re-read). `fantasy approval decide` and `fantasy lock release --force` are
  human-only actions.
- No secrets, cookies or tokens in logs, events or commits.

## Yahoo access

The draft ran Mon 2026-10-19 (13/13 picks verified on Yahoo; `docs/handoffs/2026-10-20-draft.md`). Draft runbook for
next time: [docs/runbooks/live_draft.md](docs/runbooks/live_draft.md) (agent protocol: `live-draft` skill; day-of
checklist: `draft-prep` skill). Anything that reads league pages or changes the roster needs a **local** Claude session
with browser tools (the desktop app's built-in browser or Claude in Chrome): roster captures, lineups
([docs/runbooks/roster_sync.md](docs/runbooks/roster_sync.md), `daily-routine` skill). Cloud sessions have no browser
and cannot drive Yahoo (some read-only data endpoints are reachable from the cloud; see `docs/STATUS.md`, Known limitations).

Yahoo's API is **application-only since 2026** (manual approval, weeks of lead time) and **read-only**; there is
no write access. The OAuth client exists (`fantasy settings yahoo-auth`) but is untested against live Yahoo.
The **browser is the primary path** for all reads and writes. See [docs/research/yahoo_api.md](docs/research/yahoo_api.md) section 0.
League settings are verified from Yahoo (`data/manual/league_settings.json`, all fields incl. draft order and slot as of 2026-10-19).

## Status and roadmap

Current state and priorities: [docs/STATUS.md](docs/STATUS.md); backlog: `fantasy task list`.
- **Phase 1, draft:** done (real draft 2026-10-19; first room-model calibration in `docs/research/draft_calibration_2026.md`).
- **Phase 2, in-season (live):** built: Yahoo roster sync, daily lineup optimizer, scheduled morning routine (`fantasy-daily`),
  season/matchup simulator with IL replacement, conservative market-projection run, streaming ranker and paired trade
  evaluation. Next: matchup-weighted streaming (T-006), trade scan (T-008), in-season projection updates, calibration of
  in-season priors (T-016).
- **Phase 3:** backtesting (T-010), Muse action queue for executing approved moves.

## Contributing (humans and agents)

- Read [CLAUDE.md](CLAUDE.md) first (`AGENTS.md` is a symlink to it): start-of-session steps, safety rules, conventions.
- Orient from `docs/STATUS.md` and the newest notes in `docs/handoffs/`; claim work with `fantasy task claim T-00N --owner <you>`.
- Conventions: typed Python 3.11+, small modules, ingestion separate from modeling, tests on synthetic data with no network.
- Before finishing: `pytest`, `ruff check src tests`, `cd web && npm run build` if web changed.
- Write `docs/handoffs/YYYY-MM-DD-<topic>.md` (template in the `handoff` skill) and update STATUS.md.
- Always commit and push your own paths only: `git pull --rebase --autostash origin main && git push origin main`.
