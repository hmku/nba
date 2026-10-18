# Handoff 2026-10-18 - season simulator (season-sim subagent, cloud session)

## What was accomplished
- New package `src/fantasy/sim/` + CLI `fantasy sim` (registered in `src/fantasy/cli.py`, one line):
  `season`, `matchup --week N`, `compare --add/--drop`, `draft-check --candidates`, `schedule [--out]`,
  `fetch-schedule`. Model and priors: `docs/research/season_simulator.md`; usage: skill
  `.claude/skills/season-sim/SKILL.md`. Version `SIM_VERSION = "season-sim-0.1.0"`.
- League state from a draft (`draft_picks.team_slot`, works mid-draft) or rosters JSON; optional
  `--complete-rosters` (expected picks: others by noisy ADP keeping lineups fillable, mine by market or
  `--my-plan optimizer`), labelled as projected.
- NBA 2026-27 schedule fetched from basketball-reference (7 requests, 3.5 s apart) →
  `data/raw/bbref_schedule/nba_2027_games.csv` + provenance (1,200 games, 80/team; the 2 NBA-Cup TBD
  games per team are placed in the empty Dec 4-11 window). Fallback = uniform 3.5 games/week (warned).
- Calendar: week 1 = Tue Oct 20 - Sun Oct 25, Mon-Sun after; All-Star break (Feb 19-24) merges weeks
  into one long week 18 (Feb 15-28) by default. Round-robin matchups for weeks 1-17; playoffs top 6
  over weeks 18-20 with byes for seeds 1-2 (fixed bracket, ties to the higher seed).
- Results persisted to `model_runs` (kind `season_sim`, params, inputs incl. projections sha256,
  NBA-schedule source+sha, matchup source, calendar, rosters; summary JSON). No schema change.
- Consumes the concurrent availability work only through public outputs: `load_projections()` columns
  `gp`, `gp_sd`, `playoff_mult` (no edits to projections.py/valuation.py/lines.py).

## Sample (simulated full draft `mock-full`: my slot 1 by the optimizer, others noisy ADP; 2,000 sims, seed 0, 2.7 s)
```
Team 1: P(playoffs) 95.1% ±0.5%  P(title) 56.7%  exp cat W-L 99.4-53.6  exp rank 2.1
2nd best: Team 4 81.3% / 11.6%; worst: Team 2 27.9% / 0.9%
Week 9 vs Team 4: P(win) 65.3%; behind in FG% (35%) / FT% (44%), ahead in BLK (79%) / AST (71%)
compare --drop saddiq-bey --add aaron-gordon: P(po) -1.4 ± 0.4 pp, P(title) -5.8 ± 0.9 pp
draft-check at pick 24 (mock-mid, 4 candidates, 2,000 sims × 4 completions, 7.7 s): Murphy 89.3%,
  Holmgren 88.6%, Brunson 91.6% (+2.3 ± 0.7 pp vs Murphy), Leonard 90.2%
```
The absolute numbers are optimistic (same projections as the optimizer, ADP-drafted opponents that
never manage rosters) — use relative comparisons.

## Decisions & rationale
- Daily lineup cap (start best-valued healthy players up to 10 per day) instead of the suggested weekly
  "10 × game-days" cap: the weekly cap essentially never binds (≈6.5 starters/day on average); the loss
  happens on heavy days. Cost is negligible (per-week (N,T,R,7) bool arrays).
- Team-level Normal noise given simulated per-player games (exact sums of per-game variances) instead
  of per-player-per-game draws: 2,000 sims in ≈2.7 s. Makes | attempts sampled (FG%/FT% from makes and
  attempts); integers rounded so ties occur.
- Random streams keyed by player id / team id → common random numbers for `compare` / `draft-check`.
- draft-check averages K=4 completions of other rosters: a single completion showed a spurious
  +5.8 pp edge for one candidate that disappeared with 4.

## Files changed
- `src/fantasy/sim/{__init__,schedule,league,engine,context,report,draftcheck,persist,cli}.py` - new
- `src/fantasy/cli.py` - `("fantasy.sim.cli", "app", "sim")` in `_SUBAPPS`
- `tests/test_sim.py` - 20 tests (synthetic data, temp SQLite, no network)
- `data/raw/bbref_schedule/nba_2027_games.csv(+.provenance.json)` - cached schedule
- `docs/research/season_simulator.md`, `.claude/skills/season-sim/SKILL.md`, `docs/STATUS.md` (one bullet)

## Tests performed
- `python -m pytest -q` → all passed (incl. 20 in `tests/test_sim.py`: determinism, stronger team →
  higher P(playoffs), TO direction, FG% = makes/attempts, round-robin validity, calendar/All-Star merge,
  bracket + byes + seed tie-break, lineup cap, streaming, injury spells, CLI season/compare/matchup/
  draft-check + model_runs row). `ruff check src tests` → clean.

## Problems encountered
- bbref lists only 80 games/team (Cup TBD) → filled heuristically; Dec weeks 7-8 have few games.
- **Calendar conflict:** with Mon-Sun weeks from opening night, playoff weeks 18-20 are Feb 15 - Mar 14
  (All-Star merged); `src/fantasy/model/availability.py` assumes weeks 18-20 end a week before the NBA
  season (≈ Mar 15 - Apr 4). One of the two is wrong; Yahoo's actual week dates decide (settings unverified).

## Outstanding work
- Post-draft: Yahoo rosters + schedule (week dates and matchups) as JSON via browser/API (T-004);
  then `fantasy sim season --league-file ... --schedule ...`.
- Calibrate `SimParams` priors (T-010/T-016); P(title) moves ±5 pp across reasonable priors.
- `.claude/skills/in-season-ops/SKILL.md` still says matchup simulation / NBA schedule don't exist —
  update it to point at `season-sim` (not edited here: outside my assigned paths).

## Suggested next steps
1. (local, browser) After the draft: verify playoff weeks/week dates on Yahoo; export rosters + schedule JSON.
2. Waiver/streaming and trade evaluation built on `compare` (rest-of-season start date: simulate only
   weeks ≥ current, with actual standings so far as an offset — not built).
3. Positional lineup assignment and matchup-aware lineup priority; within-game category correlations.

## Notes for next agent
- Experiments used a scratch SQLite DB under the session scratchpad; no Supabase writes, no runs/locks/txns opened.
- `fantasy sim fetch-schedule` hits basketball-reference (7 requests); never call it from tests.
