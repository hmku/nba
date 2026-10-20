# Handoff 2026-10-20 - week 1, lineup crunch, streaming, trade scan (in-season analyst, cloud session)

## What was accomplished
- New package `src/fantasy/inseason/` (`inseason-0.1.0`), analysis only, never touches Yahoo:
  - `lineup.py`: `LineupSolver` (priority-greedy max matching of players to the 10 starting slots; memoised on
    availability bit masks), `simulate_lineups` (deterministic or Monte Carlo), per-day/per-player reports.
  - `possim.py`: `PositionAwareSimulator(SeasonSimulator)`, the season sim with slot-legal daily lineups (subclass;
    engine untouched, same seeds -> paired comparisons stay exact).
  - `stream.py`: per-game G-score values + position-aware window gain ranking (`candidates` = Yahoo FA list).
  - `trade.py`: `apply_trade` (both rosters, refill/trim to 13, `refill_skip` sensitivity), `evaluate_trades`
    (paired deltas for both teams + naive value).
  - CLI `fantasy inseason lineup|stream|trade` (registered in `src/fantasy/cli.py`); tests `tests/test_inseason.py`.
- Research note `docs/research/week1_and_trades_2026-10-20.md` with all numbers and caveats.

## Key findings (labels in the note)
- Category profile (simulated): ranks 1-2 in PTS/REB/STL/AST/3PM/BLK; weak FG% (9th) and TO (9th). Swing cats: TO, BLK, FT%.
- **Lineup crunch ≈ 0 games** in weeks 1-4 (schedule): 3 C-only + 2 PF/C always fit (C, C, Util, Util, PF/F).
  The binding constraint is the slot count (9 healthy bench games in 27 days). Position-aware sim changes P(title) −0.6 pp.
- Week 1 (Oct 20-25) vs Joshua's Excellent Team (slot 12): P(win) 0.728 (simulated); BLK and FT% decide it.
- Streaming: no add/drop recommended. Best streamers (Lively, G. Allen, R. Williams, M. Robinson) gain < 2.5 per-game
  G units in a week, while dropping Fears costs 2-4 pp P(playoffs) (paired sim).
- Trades: best is **Ware + VanVleet -> Jamal Murray (Yeet)**, ΔP(title) +0.086 ± 0.006 (optimistic refill) / +0.060
  ± 0.006 (conservative), ΔP(playoffs) +0.016 / −0.005; Yeet gains by rankings and in its sim (+3.4 pp playoffs).
  Second: Ware + Bailey -> Derrick White (Haagen Daz). Needs user approval; nothing proposed.

## Decisions & rationale
- Did not touch `src/fantasy/sim/*`, `data/manual/league_schedule.json`, `web/` (another agent's files, uncommitted in the
  shared working tree). Position awareness is a subclass instead of an engine change.
- Used the scratchpad league/schedule files (draft-slot ids; Yahoo ids mapped via the capture's `draft_slot`).

## Files changed
`src/fantasy/inseason/{__init__,lineup,possim,stream,trade,cli}.py`, `src/fantasy/cli.py` (one line),
`tests/test_inseason.py`, `docs/research/week1_and_trades_2026-10-20.md`, this handoff, `docs/STATUS.md` (one row).

## Tests performed
`python -m pytest -q` (all pass, incl. 7 new), `ruff check src tests` clean, CI mypy subset clean, `mypy src/fantasy/inseason` clean.

## Outstanding work / notes for next agent
- The lineup tool is not yet a lineup *setter*: it says who should start but nothing writes lineups (T-005 needs
  today's games + Yahoo state + a live run).
- `stream` uses opponent-agnostic G-scores; add matchup category weights (from `head_to_head`) next.
- Trade search was a script (scratchpad); if trades become routine, add `fantasy inseason trade-scan`.
- Unverified: why 2-for-1 consolidation moves P(title) much more than P(playoffs) (simulator mechanism, no real validation).
- Re-check Podziemski (Q, back) before Wednesday Oct 21 games; waivers clear Oct 22.
