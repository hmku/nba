# Handoff 2026-10-22 - in-season (rest-of-season) projections (side session "in-season updates", local)

## What was accomplished
- `fantasy data fetch-boxscores` (`src/fantasy/data/boxscores.py`): ESPN regular-season box scores, incremental, cached
  in `var/cache/espn/`; backfilled 2024-25, 2025-26 (1230 games each, NBA Cup final flagged `regular_season=False`) and
  2026-27 to date -> `data/raw/espn_boxscores/*.csv.gz` (+ provenance).
- `fantasy data update-projections [--fetch] [--as-of D] [--out P] [--alt/--no-alt]` (`src/fantasy/model/inseason.py`,
  `ros-proj-0.3.0+ros-avail-0.3+stat-shrink-0.1`): preseason projections + box scores -> 
  `data/processed/projections_2026_27.inseason.csv` (+ meta) and `var/projections_alt/market.inseason.csv`.
  `lines.load_projections()` now loads the in-season file by default (`FANTASY_PROJECTIONS=preseason` reverts; warns if
  > 7 days old or built from another preseason file). valuesearch train/validate pinned to the preseason file.
- Fits (all reproducible, `scripts/inseason_model/README.md`): availability per-k WLS on 2011-2024 timelines,
  OOS 2025-26 RMSE of remaining games 17.4 vs 18.6 static at k=1, 13.7 vs 15.4 at k=20 (gain z 3-7); per-game n0 per
  stat, 9-cat z error -14.4% ± 1.4% vs prior. Research note `docs/research/inseason_updates.md` (incl. Achilles = literature
  prior, not fitted). First live file as of 2026-10-21 (1 game day; Oct 21 games not yet final).
- Daily routine step 1.6 runs it; skills updated: daily-routine, projections-update (never add in-season `games_missed`),
  in-season-ops.

## Decisions & rationale
- Not an iid Beta-binomial: absences are spells (current-streak term) — B-B is worse than the static prior at k = 3-20.
- k = 0 deploys the preseason projection exactly; the fitted k=0 level shift (+2.8 games) had no OOS support, so it is
  removed at every k (changes = information from games only). Quant review finding.
- Fit population = opening-night rosters not injured coming in, since production handles known preseason injuries with
  `games_missed` windows (+ a 1-game window prior for preseason day-to-day players).
- In the in-season file `gp` = 82 × expected share of REMAINING games (what all consumers read as per-game play prob);
  `season_gp_exp` = season total; status from the ESPN injury feed (0-3 days old); playoff_mult renormalised.

## Files changed
- New: `src/fantasy/data/boxscores.py`, `src/fantasy/model/inseason.py`, `scripts/inseason_model/{timelines,fit_availability,fit_stats}.py`
  + reports + README, `tests/test_boxscores.py`, `tests/test_inseason_update.py`, `docs/research/inseason_updates.md`,
  `data/raw/espn_boxscores/`, `data/processed/projections_2026_27.inseason.{csv,meta.json}`.
- Edited: `src/fantasy/data/cli.py`, `src/fantasy/model/lines.py`, `src/fantasy/inseason/{__init__,cli}.py` (0.3.1: market
  set follows the in-season file), `src/fantasy/valuesearch/{train,validate}.py`, 3 skills, `docs/STATUS.md`.

## Tests performed
- `pytest` -> all pass except `tests/test_ops_transactions.py::test_idempotent_plan` (pre-existing, date-dependent, not
  this change; T-019). `ruff check src tests scripts/inseason_model` clean. Two quant-reviewer passes; all blockers fixed.
- Manual: before any game every player equals preseason gp/gp_sd (except gtd: ×81/82); `load_projections()` picks the
  in-season file, `FANTASY_PROJECTIONS=preseason` the old one.

## Problems encountered
- Review found: gp_sd collapse for injured players, a gp jump after the first game, stale preseason status, wrong dnp
  share between checkpoints, under-dispersed long absences — all fixed (+ tests). Context updated: research note,
  projections-update skill (`games_missed` semantics), daily-routine (low_confidence movers).
- I briefly overwrote `tests/test_inseason.py` (name clash) and restored it from git before anything else touched it.

## Outstanding work
- T-018 (P1) season sim from today with banked results (sim still simulates the full calendar).
- T-020 early-drop bias (e.g. Bruce Brown 59 -> 36 gp after missing the opener's box score; flagged `low_confidence`),
  rolling-origin priors, late-season variance for 10+ game absences.
- FT% n0 (116 attempts) < FG% (398) hints the projection core over-regresses FT% (T-012).
- Stress set (ESPN games) stays preseason by design.

## Suggested next steps
1. Daily routine runs `update-projections --fetch`; check `low_confidence` players before advice (local session).
2. T-018 before trusting in-season P(playoffs) levels.

## Notes for next agent
- DB: task ledger on Supabase (T-018..T-020 added). No runs/locks/txns opened (no Yahoo writes).
- Historical inputs (NocturneBear box scores, bbref totals) are not committed; README has the curl lines.
