---
name: season-sim
description: League-aware season simulator (`fantasy sim ...`) - P(playoffs)/P(title) for every team from the actual rosters, my weekly H2H matchup odds and per-category gaps vs a specific opponent, opponents' category strengths, the effect of an add/drop on playoff/title odds, and a mid-draft advisory comparing candidates by season outcome. Use when asked about playoff or title chances, "how do I match up against X / in week N", which categories to target, whether a roster change or pickup helps, after the draft to assess the league, or to sanity-check draft candidates by season outcome. Not for live 60 s pick decisions (use `fantasy draft recommend`).
---

# Season simulator

Model, priors and limitations: `docs/research/season_simulator.md` (read the Limitations section
before quoting numbers to the user). Code: `src/fantasy/sim/`. Version `SIM_VERSION` in
`src/fantasy/sim/__init__.py` (bump it when the model changes; add tests in `tests/test_sim.py`).

## Commands (all take `--json`; `--sims` default 2000, `--seed` default 0 → deterministic)
| Purpose | Command |
|---|---|
| Season outlook (league table, my weekly matchups, opponents' profiles) | `fantasy sim season --draft-id <id> [--complete-rosters] [--my-plan optimizer] [--team N] --json` |
| One week's matchup by category | `fantasy sim matchup --week 9 --draft-id <id> [--opponent N] --json` |
| Roster change (add/drop; waiver/trade hook) | `fantasy sim compare --draft-id <id> --add "Naz Reid" --drop saddiq-bey --json` (comma-separated lists; `--team N` for other teams, e.g. both sides of a trade = two compares) |
| Pre-draft season projection (dashboard) | `fantasy sim preseason --leagues 20 --sims 2000 --json` (simulated drafts: me = optimizer, others noisy ADP; pooled by slot; SEs clustered by league; kind `season_sim`, `summary.scenario = "pre-draft projection"`, schema in `docs/research/dashboard_data.md`) |
| Mid-draft advisory | `fantasy sim draft-check --draft-id <id> --candidates "A,B,C" [--sims 800 --completions 4] --json` |
| Calendar / games per week / matchups | `fantasy sim schedule [--out schedule.json]` (writes a JSON template for Yahoo's real schedule) |
| Refresh NBA schedule (network, 7 polite requests) | `fantasy sim fetch-schedule` → `data/raw/bbref_schedule/` + provenance |
| Optimism decomposition (research) | `fantasy sim symmetric ...` (rooms where 0/3/6/11/all opponents use our optimizer; `docs/research/optimism_decomposition.md`) |
Rosters: `--draft-id` (default: latest non-mock draft, or `$FANTASY_DRAFT_ID`) or `--league-file rosters.json`
(`{"my_team": 1, "teams": {"1": {"name": "...", "players": [player_ids]}, ...}}`). Schedule:
`--schedule yahoo_schedule.json` (`{"source": "...", "weeks": [{"week": 1, "start": "2026-10-20", "end":
"2026-10-25", "matchups": [[1, 7], ...]}, ...]}`); default is a round robin with derived dates.
Other knobs: `--week1-end`, `--no-merge-allstar`, `--playoff-teams`, `--playoff-weeks 18,19,20`,
`--no-streaming`, `--no-il-replacement` (IL replacement of long-injured players is on by default since 0.6.0; priors, see `docs/research/il_replacement.md`), `--no-roster-cap` (roster cap on by default since 0.8.0; off = 0.7.0 behaviour, for comparison only), `--projections`, `--nba-schedule`. `season`, `compare`, `draft-check` record a
`model_runs` row (kind `season_sim`, summary JSON) unless `--no-save`; use `--no-save` and a scratch DB
for experiments.

## Interpreting outputs
- `my_team.p_playoffs` / `p_title` with `_se` (binomial SE over sims; it excludes model/prior error).
  `final_standing_dist`: P(finishing 1st..12th). `exp_cat_wins` over the regular season (17 weeks × 9).
- `my_team.weekly_matchups[]`: scheduled opponent, `p_win`/`p_tie`/`p_loss` (matchup = more categories),
  `exp_cats_won`, per-category `cat_win_probs`, and `my_player_games` vs `opp_player_games`
  (schedule-aware games that week, incl. streamers) — the games gap explains many weekly swings.
- `my_team.vs_opponents_all_play`: if I played team X in a random regular-season week: matchup win
  prob and per-category win rate (ties as 0.5). `team_profiles[t]`: weekly category means, rank (1 =
  best; TO lower is better), z, `strengths` / `weaknesses` — use for trade targeting (teams strong in
  what I lack and weak in what I have surplus of).
- `league_table[]`: every team's P(playoffs), P(bye) (top-2 seeds), P(final), P(title), exp rank.
- `compare`: `delta` with paired `delta_se` (common random numbers); |delta| < 2·SE is noise.
  - **P(title) deltas need more sims** (learned 2026-10-25): at 4000 sims SE ≈ 0.55 pp and single-seed title deltas swing
    ±0.7 pp between seeds. Before acting on a title delta use ≥ 3 seeds × 8000 (SE ≈ 0.23 pp).
  - **Title ≠ regular-season strength:** as a likely bye seed our title rides on the last two playoff weeks
    (`settings.playoff_weeks`), so a player whose NBA team plays 4+4 games there can raise P(title) while lowering
    P(playoffs) (2026-10-25: Risacher for Bailey, +0.63 ± 0.22 pp title / −1.3 pp playoffs, simulated). That is a
    modelled schedule effect, not a bug; say so when quoting it.
  - **What the baseline is:** read the compare `notes` (IL-replacement baseline, roster-cap releases) before quoting a delta.
- `draft-check`: per candidate P(playoffs)/P(title) and `*_diff_vs_first` ± `*_diff_se` (paired).
  It is advisory: ~3-4 s at 800 sims plus ~2 s start-up, so only use it when there is time (not on a
  60 s clock unless already prepared before the pick comes up); the live pick loop stays
  `fantasy draft recommend`.

## Caveats to state when reporting
1. **Optimistic for my team**: same projections as the optimizer; opponents drafted by ADP and never
   manage rosters. Prefer relative statements ("dropping X costs ~1.4 pp playoff odds") over absolute
   ones ("95% playoffs").
2. Mid-draft without `--complete-rosters` compares partial rosters (warned). With it, players marked
   `projected_players` are expected picks, not real.
3. Playoff weeks 18-20 (Mar 1-7, 8-14, 15-21, 2027; 6 teams, re-seeded each round, ties to the higher
   seed) are verified from the Yahoo settings page; since 2026-10-20 all week dates and the weeks 1-17 pairings
   come from Yahoo (`data/manual/league_schedule.json`; week 1 = Oct 20-25, two-week weeks 7 and 17). Without
   that file the dates are derived and the pairings an assumed round robin (warned in the output).
4. **Roster cap (season-sim-0.8.0).** Rosters are 13 non-IL + up to 3 IL (Out only). A team listed with more than 13
   (IL players included) keeps the extras only while someone is Out; from the first day it would exceed 13 non-IL,
   its lowest-value non-Out player is released for the season (`warnings` names the teams; `SimResult.cap_drop_day`).
   Before 0.8.0 such teams were inflated (us: +12.1 pp playoffs vs the same roster minus a bench player; now +0.9 pp). The
   league file's Yahoo `status` O/INJ/OFS/IR puts those players in a spell from day 0 (≥ 7 days; healthy-projected ones keep later absence risk). Trades (`apply_trade`) no longer trim Out players. When comparing "keep vs drop"
   for a 14-man roster, the cap already drops the lowest-value player on return: compare against dropping a
   *specific* player, and remember the release choice is by season value (`il_replacement.md` "Roster cap").
5. `SimParams` are fitted since season-sim-0.4.0 (`docs/research/sim_calibration.md`: projection error, game variance, within-game correlation; injury spells validated). Since season-sim-0.5.0 the per-game variance comes from `fantasy.model.noise` (the one noise model the draft also uses; there is no `game_var` option any more); `noise_corr` (within-game correlation) is simulator-only. Still not validated end-to-end: P(win matchup) against real fantasy outcomes, streaming, lineup behaviour; the season error is fitted on the baseline (not blended) projections.

## Post-draft league (done 2026-10-20; re-run after roster changes)
- Rosters: `fantasy sim export-league --draft-id nba.l.44495-2026 --names-file data/manual/league_team_names.json --out data/manual/league_rosters_2026-27.json`
  (needs the draft in the DB: SQL, or in a cloud session copy `drafts` + `draft_picks` from REST into a scratch SQLite;
  reports `unknown_players`). After waivers/trades the draft no longer equals the rosters: build the file from a Muse
  `rosters` snapshot instead (converter: PLANNED).
- Schedule: `data/manual/league_schedule.json` (Yahoo's real pairings weeks 1-17 + dates weeks 1-20, from
  `fantasy sim import-schedule --yahoo-capture data/raw/yahoo/league_schedule_2026-10-20.json`) is picked up automatically.
  Team sides may also be Yahoo team names; weeks without pairings stay an assumed round robin (`matchup_schedule.assumed_weeks`).
  Tests isolate it (autouse fixture in `tests/conftest.py`).
- Dashboard run: `fantasy sim season --league-file data/manual/league_rosters_2026-27.json --sims 12000 --scenario "post-draft: actual rosters" --json`
  on a scratch SQLite, then `fantasy publish --rest --source sqlite:///<scratch.db> --tables model_runs`.
  12k sims ≈ 15 s, SE(P(playoffs)) ≤ 0.46 pp. `my_team.weekly_h2h_matrix` = my odds vs every team in every week.
- Results and the schedule sensitivity (mid-table P(playoffs) ±10 pp across round-robin relabellings):
  `docs/research/season_simulator.md` "Post-draft league".

## Conservative run (report alongside the headline; `docs/research/conservative_sim.md`)
`fantasy data alt-projections --blend market --out var/projections_alt/market.csv` (ESPN+FantasyPros stats, our GP; `var/`
is git-ignored, third-party numbers stay out of git), then `fantasy sim season --league-file data/manual/league_rosters_2026-27.json
--projections var/projections_alt/market.csv --sims 12000 --seed 0 --no-save --json`. Post-draft: 88.0% playoffs / 27.9% title
vs 91.9% / 33.7% with the blend (simulated). Rule: act on an add/drop/trade only if it helps under both the blend and the market run.

## Post-draft setup from Yahoo pages (needs a local session with the browser, `yahoo-browser` skill)
1. Read every team's roster from Yahoo (league → each team page) → `rosters.json` with player ids
   resolved via `fantasy.draft.names.resolve_player` (team ids: pick one id scheme, e.g. Yahoo team ids,
   and use it in both files).
2. Read the league schedule (Yahoo "Schedule"/matchups page per week, or the API `league/{lk}/scoreboard;week=N`)
   and the week dates → `schedule.json` (start from `fantasy sim schedule --out schedule.json`).
3. `fantasy sim season --league-file rosters.json --schedule schedule.json --json`.
