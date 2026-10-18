# Handoff 2026-10-18 - league settings from Yahoo screenshots (league-settings subagent, cloud session)

## What was accomplished
- **Settings verified** from the user's screenshots of the Yahoo league settings page (league 44495).
  `LeagueSettings` (`src/fantasy/league/settings.py`) has new fields: `draft_type`, `max_season_adds`
  (None = no max), `max_trades`, `trade_end_date` 2027-03-04, `allow_draft_pick_trades` False,
  `trade_review` commissioner, `trade_reject_days` 2, `waiver_mode` standard, `il_direct_add` True,
  `cant_cut_list`, `post_draft_players`, `weekly_deadline` daily_today, `start_scoring_week` 1,
  `lock_benched_players` False, `playoff_end_date` 2027-03-21, `playoff_reseed` True,
  `playoff_tiebreak` higher_seed, `lock_eliminated_teams`, `divisions`, `cash_league`, `auto_renew`,
  `publicly_viewable`, `verified_at`. **Roster: IL = 3 (not 4)**; drafted rounds still 13.
  `data/manual/league_settings.json` written via `save_settings` (source `yahoo_settings_screenshot`).
  All screenshot fields are marked verified. `draft_order_type` and `my_slot` are **not** verified.
  `fantasy settings show --json` → `unverified: ["draft_order_type", "my_slot"]`, `my_picks` 1, 24, 36, 37, 60, …
- IL=4 grep: the Yahoo API test fixture/test (synthetic copy of our league) and `docs/research/draft_order.md`
  were changed to 3. No code depended on the IL count; `num_rounds` excludes IL.
- **Sim calendar** (`season-sim-0.2.0`): `fit_week1_end` fits the length of week 1 so that the last playoff
  week ends on `settings.playoff_end_date`. Playoffs are now re-seeded after each round (top remaining seed
  plays the lowest) per `settings.playoff_reseed`, and ties go to the higher seed (`playoff_tiebreak`).
  `SimParams.reseed=None` follows the settings.
- **playoff_mult refit** on the league's real window: 3 weeks ending 21 days before the NBA season ends
  (Mar 1-21, 2027). Version `playoff-mult-0.2`. Projections rebuilt as `baseline-0.2.1`, where only the
  `playoff_mult` column changed. Valuation is now `valuation-0.2.1`.

## Derived week calendar (assumption, documented)
Week 1 = Tue 2026-10-20 to Sun 2026-11-01 (13 days). Weeks 2-16 are Mon-Sun. Week 17 = Feb 15-28
(All-Star break Feb 19-24 merged). **Week 18 = Mar 1-7, week 19 = Mar 8-14, week 20 = Mar 15-21.**
Basis: Yahoo's 2025-26 calendar had a *short* week 1 and an All-Star double week 17 (Feb 9-22, 2026, per
RotoWire; Yahoo default playoffs were weeks 21-23, Mar 16 - Apr 5). Applying that to 2026-27 would end
week 20 on Mar 14. The verified Mar 21 end therefore implies exactly one more merged week. We assume it
is week 1, because the season starts on a Tuesday. Long week 1 *without* the All-Star merge would need a
3-week week 1, which is implausible. Weeks 18-20 do not depend on which early week is the long one. A web
search did not find Yahoo's 2026-27 week table (help.yahoo.com is not reachable from the cloud). The sim
prints this assumption as a warning. Load the real dates with `--schedule` after the draft.

## playoff_mult: old vs new (season-cluster bootstrap SEs, n = 2,339 player-seasons, 2011-2024)
| | const | tank (0.5 - prev wpct) | injury (max(0,60-g1)/10) | window/season ratio |
|---|---|---|---|---|
| old (window ends 7 d before season end) | 0.958 ± 0.007 | -0.077 ± 0.045 | -0.035 ± 0.015 | 0.946 ± 0.007 |
| new full model (ends 21 d before) | 0.982 ± 0.006 | -0.023 ± 0.034 | +0.018 ± 0.014 | 0.974 ± 0.006 |
| **chosen** (injury dropped) | **0.984 ± 0.005** | **-0.019 ± 0.034** | 0 | |
The script reproduces the old fit exactly at offset 7. The injury sign is unstable across eras (2011-17
-0.005, 2018-24 +0.035), so it was dropped. `playoff_mult` is now 0.978-0.989 for every player (was
0.75-0.98). Late-season tanking and resting falls mostly after this league's fantasy season.

## Rank changes (top 60, `compute_values` defaults)
Rank Spearman 0.99993. The top 15 is unchanged. 30 of the top 60 move by 1-8 places: Walker Kessler
45→37, Anthony Davis 51→46, Tyler Herro 62→57 (in), Derik Queen 61→59 (in), Durant 39→42, Pritchard
40→43, Brandon Miller 56→60, Avdija 59→62 (out), VanVleet 60→63 (out). Full table:
scratchpad `settings/rank_changes.csv`.

## Decisions & rationale
- The settings defaults in code were updated to the screenshot values too, so the defaults and the file agree.
- `stream_recovery` stays at 0.5 (unfitted). The availability note now explains how 3 IL slots, direct IL
  adds, 6 adds/week and daily lineups bear on it.
- `PLAYOFF_CALENDAR_SHARE` stays at 3/20 (counted in matchups). The day share is 0.137, which makes no
  difference at the new multipliers.

## Files changed
`src/fantasy/league/settings.py`, `data/manual/league_settings.json`, `src/fantasy/sim/{schedule,context,engine,__init__}.py`,
`src/fantasy/model/{availability,projections,valuation}.py`, `data/processed/projections_2026_27.{csv,meta.json}`,
`tests/{test_scoring,test_sim,test_availability,test_yahoo_api}.py`, `tests/fixtures/yahoo/league_settings.json`,
`docs/research/{availability_model,season_simulator,draft_order}.md`, `.claude/skills/{draft-prep,season-sim}/SKILL.md`,
`docs/STATUS.md`, `docs/USER_TODO.md`.

## Tests performed
`python -m pytest -q` all pass. New tests cover the committed settings file, the calendar fitted to the
playoff end date, context calendar following settings, and reseeding vs the fixed bracket.
`ruff check src tests` is clean.

## Problems encountered
- `fantasy task list` is empty on local SQLite (the production DB is not configured here), so T-001 was
  not updated in the ledger.

## Outstanding work
- **Draft order / 3RR and my slot** remain unverified (USER_TODO sub-item; draft-prep step 4).
- The dates of the regular-season weeks are an assumption until Yahoo's schedule is loaded.
- Supabase holds the old projections until someone runs `fantasy publish` with `FANTASY_DB_URL`.

## Notes for next agent
Scratch: `/tmp/claude-0/-home-user-nba/3d957df9-5f21-539c-aee3-a111143dc70f/scratchpad/settings/`
(`playoff_refit.py <gp dir> <offsets>`, `refit_out.txt`, `rank_changes.csv`). No runs, locks or txns were opened, and Yahoo was not touched.
