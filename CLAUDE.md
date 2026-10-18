# Fantasy Basketball Agent — project instructions

Autonomous management of the user's Yahoo fantasy basketball team (league 44495, "Thomas's Tip-Top
League", 2026-27, 12-team H2H 9-cat, user drafts #1 with third-round reversal). The user is a
quant (strong Python/ML) who knows little basketball: explain basketball context briefly; be
rigorous about statistics.

**Division of labour:** deterministic Python (`fantasy` CLI) does all quantitative work; agents
orchestrate, investigate, decide within policy, drive Yahoo in the browser, verify, and record.
Never re-derive valuations in your head — run the tools and interpret their JSON.

## Start of every session (mandatory)
The SessionStart hook prints `fantasy status` and the newest handoffs. Use the `handoff` skill at the
start/end of each session and the `agent-run` skill for any run/lock/txn work.
1. Read `docs/STATUS.md` (current state + next priorities), `docs/USER_TODO.md` (actions only the user can do — add to it, don't ask in chat only) and the newest notes in `docs/handoffs/`.
2. `pip install -e ".[dev]"` if `fantasy` is not on PATH; then `fantasy status` (runs, locks, approvals,
   transactions needing reconciliation, data freshness, active draft).
3. `fantasy task list` → claim what you work on: `fantasy task claim T-00N --owner <you>`.
4. For agent *operations* (anything touching Yahoo), start a run: `export FANTASY_RUN_ID=$(fantasy run start --agent <name> --task "..." --mode dry_run|live)`.

## End of every session (mandatory)
- Tests green: `pytest` and `ruff check src tests` (and `cd web && npm run build` if web changed).
- Write a handoff: `docs/handoffs/YYYY-MM-DD-<topic>.md` (template in `.claude/skills/handoff/SKILL.md`).
- Update `docs/STATUS.md` if priorities/state changed; `fantasy task done|update`.
- **Always commit and push** (user requirement): commit only your paths, `git pull --rebase --autostash origin main && git push origin main`.

## Layout
See `docs/ARCHITECTURE.md`. Python package `src/fantasy/` (league, data, model, draft, ops, db);
dashboard `web/` (Next.js → Vercel); DB schema `src/fantasy/db/schema.py` → `supabase/migrations/`.
Research notes: `docs/research/`. Runbooks: `docs/runbooks/`.
Skills (`.claude/skills/`): draft-prep, live-draft, yahoo-browser, projections-update, model-eval,
agent-run, handoff, in-season-ops (Phase 2 placeholder),
league-loop (in-season Muse READ -> Claude THINK -> user APPROVE -> EXECUTE -> VERIFY loop; design in `docs/research/league_loop.md`). Subagents (`.claude/agents/`): quant-reviewer,
data-auditor, draft-shadow (all read-only).

## Key commands (all accept `--json`; prefer JSON when acting on output)
| Purpose | Command |
|---|---|
| Overview | `fantasy status` |
| League settings / verify | `fantasy settings show`, `fantasy settings verify <fields> --source browser` |
| Projections | `fantasy data build-projections`, `fantasy data top` |
| Valuation | `fantasy values build [--punt ft_pct]` |
| Draft | `fantasy draft init|board|pick|undo|recommend|watch|simulate|order` |
| Ops ledger | `fantasy run ...`, `fantasy lock ...`, `fantasy approval ...`, `fantasy txn ...`, `fantasy task ...` |

## Environments & data
- `FANTASY_DB_URL` unset → local SQLite `var/fantasy.db` (dev/tests). Production = Supabase Postgres
  (project `fantasy-nba`, ref `bdzpkdkhlzjzddkptjex`); set `FANTASY_DB_URL` to its pooler URL (secret, from the user).
  Dashboard reads Supabase; if you worked on SQLite, `fantasy publish` copies rows up.
- Cloud sessions: the environment allowlist covers basketball-reference, nba.com (stats.nba.com times out),
  Yahoo public pages/API, ESPN, CBS, Rotowire, FantasyPros, GitHub and package registries. Postgres is NOT
  reachable from cloud (HTTPS proxy only): publish with `fantasy publish --rest` (Supabase auth injected by a
  network secret). Yahoo *account* operations (league pages, draft room) require a **local** Claude Code
  session with Claude in Chrome (`claude --chrome`).
- Research agents: don't leave main red — run `python -m pytest -q` and `ruff check src tests` before pushing.

## Safety rules (non-negotiable)
- Mode `dry_run` is the default. Never touch Yahoo roster state outside a `live` run.
- Before any Yahoo write (draft pick, add/drop, lineup, trade): hold the right lock
  (`yahoo:roster` / `yahoo:browser` / `draft:<id>`), plan it in the txn ledger (idempotent), get approval if policy
  requires (drops, add/drops, trades ALWAYS need the user's approval for now), then verify the result
  independently on Yahoo and `fantasy txn mark <id> verified`.
- A txn stuck in `submitted` is never retried blindly: re-read Yahoo state, then mark verified/failed.
- Never substitute a different player than the one decided on, and never take unrelated actions. If
  information changes (injury news), re-run the optimizer and record the new decision + rationale.
- Record observations, commands, model outputs, decisions (with `agrees_with_model`), browser actions and
  verifications with `fantasy run event|decision`. No secrets/cookies/tokens in logs, events or commits.
- League settings: defaults in `src/fantasy/league/settings.py` are the user's recollection; `fantasy settings verify`
  creates `data/manual/league_settings.json` once fields are confirmed on Yahoo.
- Rehearsals always use `fantasy draft init --draft-id mock-N --mock` and `export FANTASY_DRAFT_ID=mock-N`.
- Never run `fantasy approval decide` or `fantasy lock release --force` yourself — those are human actions.

## Evidence standard (user requirement)
- Every quantitative claim to the user must say where it comes from: **fitted** (data, method, sample, SE —
  link the research note), **literature/source** (cite it), or **prior/guess** (say so explicitly).
- Never present a prior as fact. New model parameters start as documented priors and get calibrated
  (`docs/research/`), with out-of-sample checks where possible.

## Engineering conventions
- Python ≥3.11, typed, small modules; ingestion (`data/`) separate from modeling (`model/`).
- Percent categories are always computed from makes/attempts.
- Every model output is versioned (`model_runs`: model_version, git sha, params, input hashes).
- Tests use synthetic data (`tests/synthetic.py`) and temp SQLite; never hit the network in tests.
- Don't build infrastructure ahead of need; prefer native Claude Code features (skills, subagents, hooks).
