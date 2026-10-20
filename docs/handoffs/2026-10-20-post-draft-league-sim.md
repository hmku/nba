# Handoff 2026-10-20 - post-draft league simulation (cloud session, post-draft-league-sim)

## What was accomplished
- **Actual league built**: `data/manual/league_rosters_2026-27.json` (`--league-file` format) from all 156 `draft_picks` of
  `nba.l.44495-2026` (team id = draft slot) via the new `fantasy sim export-league`. All 156 players are in the projections
  (none at the replacement line). The rosters are identical, team by team, to the local session's Yahoo capture
  `data/raw/yahoo/league_rosters_2026-10-20.json`. Team names: `data/manual/league_team_names.json`, using current Yahoo
  names. Slot 11 was renamed from "Daniel's Definitive Team" to **"SCOTTIER BARNEY"**. Slot order was confirmed against
  the Muse snapshot `snap_fa3331d5` (data only).
- **Schedule conversion is DONE**: `data/raw/yahoo/league_schedule_2026-10-20.json` → `data/manual/league_schedule.json`
  via the new `fantasy sim import-schedule --yahoo-capture ...`. It maps Yahoo team ids to draft slots and holds the real
  pairings for weeks 1-17 and Yahoo's dates for weeks 1-20: week 1 = Oct 20-25, the two-week weeks are 7 (Nov 30-Dec 13)
  and 17 (Feb 15-28), and the playoffs are Mar 1-21. Every `fantasy sim` command now loads this file by default
  (`--schedule` overrides it). Sides may be slot ids or exact Yahoo team names. Weeks without pairings fall back to a
  round robin and are flagged (`matchup_schedule.assumed_weeks`, `weekly_matchups[].opponent_assumed`).
- **Post-draft season sim published**: `sim-20261020033348-d0613f` (kind `season_sim`, `params.command = season`,
  `summary.scenario = "post-draft: actual rosters"`, season-sim-0.5.0 fitted defaults, 12,000 sims, seed 0, max SE of
  P(playoffs) 0.46 pp). It was written to a scratch SQLite and then `fantasy publish --rest --tables model_runs`. It is
  verified in Supabase. Per-team table, category profile and weekly odds: `docs/research/season_simulator.md`
  "Post-draft league". Headline (simulated, in-sample optimistic): me 91.9% playoffs / 64.7% bye / 33.7% title / exp.
  rank 2.63. Next best: Haagen Daz Center at 76.0% / 11.8%.
  The earlier run `sim-20261020032154-05ce4d` used an assumed round robin and is superseded; it remains in `model_runs`.
- New sim outputs: `my_team.weekly_h2h_matrix`, which gives my odds (matchup and per category) vs every team in every
  week. Also `summary.matchup_schedule`, `summary.draft_id`, `--scenario`.
- **Dashboard**: the readers accept `post-draft…` scenarios (`web/src/lib/postDraft.ts`, unit-tested) and read 60 rows,
  so what-if compares can't hide the season run.
  - League: "post-draft · actual rosters" badge, new Bye column, real team names on rosters, a pointer to the pre-draft
    projection (still labelled pre-draft under Draft › Model & sims), and an amber note only while pairings are assumed.
  - Matchup: real weekly opponents, plus a "vs every opponent" table for the selected week (week P(win), season P(win)
    and 9 category-odds cells). `?opp=T` opens a what-if vs any team, and an amber banner shows only while pairings are
    assumed.
  - I checked it rendering locally against live Supabase (with the round-robin run).
- **Draft row status**: `drafts.status` = `complete`, `updated_at` 03:11:28 UTC. It read `live` only during 03:11:21-03:11:28.
  The cause was the undo + re-pick of #155 (unknown:j-butler-iii → jimmy-butler): undo turns `complete` into `live`, and
  the re-pick filled the board again. The status logic works as designed (`store.status_for`), and `fantasy draft board`
  is the supported heal. No fix was needed. This matches `2026-10-20-draft.md`.
- Muse request and USER_TODO: done. The schedule item is ticked because the local capture fulfilled it. The Muse note
  (`docs/integrations/muse.md` = `web/src/content/muse.md`) still asks for a week-1 scoreboard plus the schedule, to
  verify and catch changes.
- T-007 (weekly matchup simulation) closed via REST.

## Decisions & rationale
- Team id = draft slot everywhere (rosters, schedule, dashboard), with Yahoo ids converted at import. The dashboard and
  `draft_picks` already use slots.
- 12k sims: binomial SE ≤ 0.5 pp at p = 0.5 needs n ≥ 10,000. The run takes 15 s.
- Draft grades were skipped: no tool exists (`fantasy draft simulate` is optimizer-vs-BPA only).
- Evidence for the schedule's importance (simulated): two random round-robin relabellings moved mid-table P(playoffs) by
  up to 10.6 pp (mine ~1 pp, P(title) ≤ 1.3 pp). The real schedule moved Justin's from 54.5% to 43.2%.
- `drafts.config.team_names` was not written (there is no supported CLI path). The pages fall back to the sim's names.

## Files changed
- `src/fantasy/sim/{cli,context,league,report,schedule}.py` (export-league, import-schedule, default schedule file,
  names/partial schedules, H2H matrix, scenario), `tests/test_sim.py`, `tests/conftest.py` (autouse: tests ignore the
  repo's league_schedule.json).
- `data/manual/{league_rosters_2026-27,league_team_names,league_schedule}.json`.
- `web/src/lib/{postDraft,sim}.ts`, `web/src/app/{league,matchup}/page.tsx`, `web/tests/postDraft.test.mjs`, `web/src/content/muse.md`.
- Docs: `docs/research/{season_simulator,dashboard_data}.md`, `docs/runbooks/dashboard.md`, `docs/integrations/muse.md`,
  `docs/USER_TODO.md`, `docs/STATUS.md`, `.claude/skills/season-sim/SKILL.md`.

## Tests performed
- `python -m pytest -q`: all pass. `ruff check src tests`: clean. CI mypy subset: clean. `mypy src/fantasy/sim` has 7
  errors, the same 7 as before this work.
- `cd web && npm run lint && npm test && npm run build`: pass (50 node tests, 4 new).

## Problems encountered
- Cloud sessions can't read `draft_picks` over SQL. I copied `drafts` + `draft_picks` from REST into a scratch SQLite to
  run `export-league`; a `--rest` read path for drafts would remove this step.
- Week 7 includes the 2 NBA-Cup-dependent games per team, which are TBD on bbref and placed Dec 4-11 by
  `fill_tbd_games`. That week's game counts are the least certain.

## Outstanding work
- After the first waiver/trade the draft no longer matches the rosters. Build `league_rosters` from a Muse `rosters`
  snapshot or a Yahoo capture (converter PLANNED; `data/raw/yahoo/league_rosters_*.json` already carries `player_id`s).
- Playoff pairings (weeks 18-20) come from seeding, as simulated; nothing to load.

## Notes for next agent
- `src/fantasy/sim/*` is free to edit again; this session is finished with it.
- Re-run recipe: see the season-sim skill, "Post-draft league".
