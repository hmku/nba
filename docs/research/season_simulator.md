# Season simulator (`fantasy sim`, `season-sim-0.2.0`)

League-aware Monte Carlo of the whole Yahoo H2H 9-cat season: every team's actual roster, the H2H
matchup schedule, NBA games per team per day, injuries as absence spells, a daily lineup cap,
streaming, standings and the playoff bracket. Code: `src/fantasy/sim/` (`engine.py` holds the model;
`schedule.py`, `league.py`, `report.py`, `draftcheck.py`, `cli.py`). Unlike the draft's
`CategoryModel` (season-level Normal approximation vs a *league-average* opponent), this simulates
**specific opponents week by week**, so it answers "P(playoffs)", "P(title)", "who do I play in week
9 and in which categories am I behind", "what does this add/drop do to my title odds".

## Model (one simulation = one season)
| Step | What | Where |
|---|---|---|
| Rosters | team id → player ids, from `draft_picks.team_slot` (works mid-draft) or a JSON file (Yahoo sync). Mid-draft: `--complete-rosters` fills remaining picks with *expected* picks (others: noisy market/ADP order, sd = `availability.adp_sd`, keeping the starting lineup fillable; mine: market or the optimizer's plan). Labelled `projected_players`. | `league.py` |
| NBA schedule | 2026-27 regular season from basketball-reference (1,200 dated games = 80/team; the 2 NBA-Cup-dependent games per team are "TBD" and are placed on the empty Dec 4-11 window, alternating days). Fallback: every team plays every other day (3.5/week) with a warning. | `schedule.py` |
| Calendar | Mon-Sun weeks; week 1 starts on opening night (Tue 2026-10-20). The All-Star break (Feb 19-24, 2027, detected from the schedule; `ALLSTAR_BREAK_GUESS` for the synthetic fallback) merges the two weeks it touches into one long week (Yahoo practice; `--no-merge-allstar`). Week 1's length is **fitted to `settings.playoff_end_date`** (Yahoo settings page, verified: weeks 18-20 end Sun 2027-03-21): that needs a two-week week 1 (Oct 20 - Nov 1), so week 17 = Feb 15-28 (All-Star) and playoffs = Mar 1-7, 8-14, 15-21 (`fit_week1_end`; `--week1-end` overrides). Regular season = weeks before the first playoff week. JSON (`--schedule`) can give explicit week dates. | `schedule.py`, `context.py` |
| Matchups | Circle-method round robin for weeks 1-17 (11-round cycle, repeated for weeks 12-17), or Yahoo's schedule from JSON. | `schedule.py` |
| Season-level projection error | per player-sim: volume factor `exp(N(0, vol_sd))` on all counting stats + attempts; per-stat factor `exp(N(0, stat_sds[s]))` on FGA, FTA, 3PM, REB, AST, STL, BLK, TO; additive FG% and FT% shifts. **Fitted (season-sim-0.4.0, `sim_calibration.md`):** vol_sd / FG / FT sd by projected mpg = 0.15 / 0.022 / 0.038 (30+), 0.25 / 0.027 / 0.041 (24-30), 0.33 / 0.033 / 0.049 (<24); stat sds FGA 0.15, FTA 0.28, 3PM 0.37, REB 0.05, AST 0.24, STL 0.06, BLK 0.18, TO 0.16 (0.3.0 priors: 0.08 / 0.08 / 0.015 / 0.03). Makes = attempts × shifted %; PTS = 2·FGM + 3PM + FTM rescaled to projected PTS. | `engine._player_arrays` |
| Availability | games-played share f ~ Beta(mean gp/82, sd gp_sd/82) (projections' fitted gp / gp_sd; gp_sd ≤ 0 → 12). Missed share 1-f: 75% as injury **spells** (one spell, or with prob 0.5 two spells with a U(0.2, 0.8) split; placed on the **fantasy window as a circle** since season-sim-0.7.0: uniform rotation and a uniform gap between the two spells, a spell past the last fantasy day continues on day 0, so every day is in a spell with probability exactly (1-rest_share)·(1-f) and the realised window missed share is exactly 1-f; `out`/`out_for_season` players start the season in their spell, no wrap — see "Availability timing fix" below), 25% (`rest_share`) as iid one-game rests. If projections carry `playoff_mult` < 1, playoff-week games are additionally missed with prob 1 - playoff_mult. | `engine._player_arrays` |
| Daily lineup | each day, healthy players whose NBA team plays start in order of projected value until the 10 starting slots are full. Positions ignored (2 Util slots make it rarely binding). This is a *daily* cap; a weekly "10 × game-days" cap almost never binds (13 players × ~0.5 games/day ≈ 6.5 starters/day on average) and would miss the real loss, which happens on heavy days. | `engine.run` |
| Streaming | each team adds `min(adds_per_week × games_per_add × stream_usage, open starting slot-days)` replacement-level games per week (6 × 2 × 0.5 = 6 by default); replacement line = valuation's replacement level. Same for every team, so it mostly compresses differences and fills injured teams' holes. | `engine.run` |
| IL replacement (`il_replacement`, default on since season-sim-0.6.0) | while a rostered player is in an injury spell ≥ `il_min_spell_days` (7, prior) his team carries a replacement-level player (typical-team games × `il_rep_availability` 0.84, fitted: `il_replacement.md`), up to `il_slots` (3) at a time, all teams; fills open slots before streamers. CLI `--il-replacement`. Results: `il_replacement.md`. | `engine.run` |
| Roster cap (`roster_cap`, default on since season-sim-0.8.0) | 13 non-IL players (settings starters + bench) + up to `il_slots` Out: a team carrying more releases its lowest-value non-Out player(s) for the rest of the season from the first day they would exceed the cap (e.g. when its IL player returns); IL replacement only fills below 13 healthy non-IL; league-file Yahoo Out players start in a spell. CLI `--no-roster-cap` = 0.7.0. Results: `il_replacement.md` "Roster cap". | `engine._apply_roster_cap` |
| Weekly totals | given counted games k per player: team attempts and counting stats ~ Normal(Σk·mean, Σk·var(mean)) with the fitted per-game variance var = a·m + b·m² (`fantasy.model.noise`, the one noise model shared with the draft since 0.5.0; the 0.3.0 constant-CV option was deleted); noise drawn at team level, stream keyed by team id, correlated across stats with the fitted within-game correlation (`noise_corr`); makes \| attempts ~ Normal(p_team·A, binomial + mix variance); counts rounded to integers, percentages to 3 decimals (ties happen: ~3% STL/BLK, ~1% PTS). | `engine.run` |
| H2H + standings | per scheduled pair, 9 category comparisons (TO lower wins), ties = 0.5. Standings by category wins + 0.5·ties (Yahoo H2H categories), random tie-break (**unverified**: Yahoo's real regular-season tie-break rule — e.g. head-to-head record or points — is not modelled). | `engine._score` |
| Playoffs | top 6 (`playoff_teams`) over weeks 18/19/20, byes for the top 2^rounds - n seeds (seeds 1-2). **Re-seeded after every round** (`settings.playoff_reseed`, verified yes: the top remaining seed plays the lowest; `SimParams.reseed` overrides). Winner = more categories won; category-count ties go to the higher seed (`settings.playoff_tiebreak = "higher_seed"`, verified). | `engine._playoffs` |

Determinism: all randomness comes from `np.random.default_rng([seed, key])` with `key` = hash(player
id) for player draws and team id for weekly noise, so a run is reproducible given the seed and a roster
change keeps **common random numbers** for every other player/team (`compare` and `draft-check`
report paired deltas with paired SEs).

Speed (12 teams × 13 players, 20 weeks, cloud container): 500 sims 0.5 s, 2,000 sims ≈ 2.7 s,
5,000 sims ≈ 8 s. `compare` ≈ 8 s wall, `draft-check` 4 candidates × 2,000 sims × 4 completions ≈ 8 s
compute (default 800 sims ≈ 3-4 s), plus ~2 s CLI start-up/valuation.

## Priors to calibrate (all in `SimParams`, `src/fantasy/sim/engine.py`)
> **Calibrated 2026-10-18 (`season-sim-0.4.0`), see `sim_calibration.md`:** season-level error, FG%/FT% shifts, game
> variance and within-game correlation are fitted; `rest_share`/`two_spell_prob` validated (kept). Opponent/pace effects
> were measured and are negligible for weekly team totals (≈ 0%). The table below lists the 0.3.0 priors.
| Prior | Value | Calibrate on |
|---|---|---|
| `vol_sd`, `stat_sd` (season-level projection error, log scale) | 0.08, 0.08 | backtest residuals (T-010): realised per-game rate / projected, by minutes tier |
| `fg_pct_sd`, `ft_pct_sd` | 0.015, 0.03 | season-to-season % changes for players with stable volume |
| `rest_share` (iid share of missed games) | 0.25 | game logs: distribution of absence run lengths |
| `two_spell_prob`, split U(0.2, 0.8) | 0.5 | same (number of distinct absence spells per season) |
| `default_gp_sd` (when projections lack gp_sd) | 12 | n/a — fitted gp_sd now exists (`model/availability.py`) |
| game-to-game variance (was `GAME_CV`, constant CV) | fitted a·m + b·m² since 0.4.0; `model/noise.py` since 0.5.0 | done (`sim_calibration.md` sections 2 and 8) |
| `stream_usage`, `games_per_add` | 0.5, 2.0 | in-season transaction logs |
| lineup priority | projected value | in-season lineup data |

Sensitivity on a simulated full draft (`mock-full`: my slot drafted by the optimizer, others by noisy
ADP; 2,000 sims, seed 0; SE of P(title) ≈ 0.011):

| Variant | my P(playoffs) | my P(title) | 2nd-best P(po) | worst P(po) |
|---|---|---|---|---|
| default (bbref schedule) | 0.951 | 0.567 | 0.813 | 0.279 |
| uniform 3.5 games/week | 0.951 | 0.525 | 0.785 | 0.320 |
| no season-level error | 0.950 | 0.593 | 0.829 | 0.278 |
| season-level error ×2 | 0.934 | 0.513 | 0.801 | 0.288 |
| no streaming | 0.954 | 0.566 | 0.812 | 0.284 |
| rest_share 0.6 | 0.958 | 0.576 | 0.831 | 0.271 |
| one spell only | 0.946 | 0.637 | 0.826 | 0.272 |

P(playoffs) is robust; P(title) moves by ±0.04-0.07 with the schedule source, the projection-error
prior and the injury-spell structure — calibrate those before trusting title odds to better than ±5 pp.

## Availability timing fix (season-sim-0.7.0, 2026-10-22)
**Bug (0.2.0-0.6.0).** Spell starts were min/max of two U(0, free) draws on the NBA-season day axis (Oct 20 - Apr 11,
174 days), and never before day 0. Spell coverage therefore ramped up from day 0, peaked mid-season and fell again, and
the fantasy window (first 153 days, to Mar 21) cut off the low right edge: availability was **U-shaped over the
season and below gp/82 on average**. `sim_calibration.md` §4 validated spell *lengths* (run-length distribution,
weekly variance), not their *timing*, so this was not caught.
**Fix.** Spells live on the fantasy window treated as a circle (uniform rotation, uniform gap between two spells).
By rotation symmetry every day is in a spell with probability exactly (1-rest_share)·q, and the realised in-window
missed share equals q in every sim, so the fitted games-played sd (Beta, `gp_sd`) is not inflated (a clip-at-the-
edges placement would add ~±6 games of sd at gp 60). Cost: a spell that runs past the last fantasy day reappears at
day 0 (a mild early/late correlation for that player-sim). IL intervals are stored as ≤ 2 pieces per spell.
**Before/after** (synthetic schedule, one player, gp_sd 10, 20,000 sims, `use_playoff_mult` off; simulated;
one-off script, not committed; the same check is `tests/test_sim.py::test_availability_flat_at_gp_share_every_week`):

| gp (target gp/82) | rest_share | 0.6.0 mean / min-max week | 0.7.0 mean / min-max week |
|---|---|---|---|
| 30 (0.366) | 0.25 | 0.321 / 0.19-0.59 | 0.365 / 0.36-0.37 |
| 45 (0.549) | 0.25 | 0.509 / 0.39-0.73 | 0.548 / 0.54-0.56 |
| 60 (0.732) | 0.25 | 0.709 / 0.64-0.83 | 0.732 / 0.73-0.74 |
| 75 (0.915) | 0.25 | 0.908 / 0.88-0.94 | 0.914 / 0.91-0.92 |
| 30 (0.366) | 0 | 0.286 / 0.14-0.79 | 0.365 / 0.36-0.37 |
| 60 (0.732) | 0 | 0.696 / 0.59-0.90 | 0.732 / 0.73-0.74 |

0.6.0 week profile at gp 60: 0.83 (wk 1) … 0.64 (wks 5-7) … 0.80 (wk 20). Effect: injury-prone players were
undervalued by 3-12% of their games in 0.6.0, most in mid-season and least in weeks 1 and 18-20 (playoffs), so
season/playoff odds and add/drop/trade deltas from earlier runs carry that bias; rerun before relying on them.

## Limitations (read before acting on outputs)
- **Streaming cap per matchup week (assumption).** `stream_cap` = 6 replacement games per *matchup week*, also in the
  two-week weeks 7 (Nov 30 - Dec 13) and 17 (All-Star, Feb 15-28). Whether Yahoo's `max_weekly_adds` resets per
  calendar week or per matchup week in those weeks is unverified; if per calendar week, those weeks get up to 12.
- **Regular-season tie-break** is random in the simulator; Yahoo's rule is unverified (see the H2H row).
- **Season-level projection error does not shrink in-season.** The error sds (`sim_calibration.md` §1) are fitted for
  preseason projections and are applied unchanged to the in-season (rest-of-season) projections, whose stat
  shrinkage uses box scores to date. Early in the season this is nearly right; later it overstates remaining-season
  uncertainty (spread of outcomes too wide, odds too close to 50/50). Not modelled yet.
- **In-sample optimism.** The simulator uses the same projections the optimizer drafted with, and
  opponents are drafted by noisy ADP with no strategy. A team that "beats the market" by our own
  projections will look dominant (e.g. 95% playoffs / 57% title on `mock-full`). The market's ADP
  carries information our projections lack; treat absolute probabilities as optimistic and use the
  tool for **relative** comparisons (candidate A vs B, add vs drop, week-N category gaps).
  Quantified in `optimism_decomposition.md` (`fantasy sim symmetric`): slot 1 is 61%/13% in a room where
  everyone drafts like us, and the status-quo 96%/55% drops to 78%/20% if the market's player ranking is right.
- No in-season management by anyone: rosters are frozen except streaming; no trades, no waiver
  pickups of breakout players; IL handling = IL replacement + the 0.8.0 roster cap (surplus released by season
  value when an IL player returns, never re-added; real managers may drop someone else or trade instead).
- Positional eligibility ignored in daily lineups (slightly optimistic usage); lineup priority uses
  season value, not matchup needs; no hot/cold streaks beyond the season-level factor.
- Categories correlate through shared games/availability, the season factor, PTS = f(FGM, 3PM, FTM) and
  (0.4.0) the fitted within-game noise correlation (`noise_corr`). Opponent and pace effects are not modelled:
  they explain ≈ 0% of weekly team-total variance (`sim_calibration.md` section 3).
- Playoff weeks (18-20) and their end date (Sun 2027-03-21) are verified from the Yahoo settings page
  (2026-10-18). The dates of the *other* weeks are derived: Yahoo's 2025-26 calendar had a short week
  1 (Oct 21-26) and a two-week All-Star week 17 (Feb 9-22, 2026; RotoWire), but for 2026-27 a short
  week 1 plus the All-Star merge would end week 20 on Mar 14, so the verified Mar 21 implies one more
  merged week; we assume it is week 1 (Oct 20 - Nov 1). Only the week-1 length and the regular-season
  week boundaries depend on that assumption; weeks 18-20 = Mar 1-7, 8-14, 15-21 do not (provided the
  extra merge is before March). **Resolved 2026-10-20:** Yahoo's real dates and pairings are loaded from
  `data/manual/league_schedule.json` (week 1 = Oct 20-25; week 7 = Nov 30 - Dec 13 is the extra two-week week, not week 1); see "Post-draft league". NBA games after Mar 21 (Mar 22 - Apr 11) do not count in this league.
- Team ids are draft slots; Yahoo team ids differ — the league/schedule JSON must use one consistent id set.
- Mid-draft completion uses a single noisy market order per completion; `draft-check` averages 4
  completions (one completion produced a spurious +5.8 pp "edge" in testing that vanished with 4).
- Players not in projections are simulated at the replacement line with 60 expected games (warned).
- Draft-check reserves the candidate for me at my next pick (it answers "if I take X there", not
  "will X be there").

## Post-draft league (2026-10-20, `sim-20261020033348-d0613f`)
**Inputs.** Rosters: all 156 picks of `nba.l.44495-2026` (Supabase `draft_picks`, team id = draft slot) →
`data/manual/league_rosters_2026-27.json` (`fantasy sim export-league --draft-id nba.l.44495-2026 --names-file
data/manual/league_team_names.json --out ...`). Every player matched the projections (nobody at the replacement line), and
the rosters are **identical, team by team, to the Yahoo roster capture** `data/raw/yahoo/league_rosters_2026-10-20.json`
(independent check of `draft_picks`). Team names: current Yahoo names (slot 11 "Daniel's Definitive Team" was renamed
"SCOTTIER BARNEY"; slot order first from Muse snapshot `snap_fa3331d5`).
**Schedule: Yahoo's real one** (`data/raw/yahoo/league_schedule_2026-10-20.json`, captured read-only by the local session;
converted with `fantasy sim import-schedule --yahoo-capture ...` → `data/manual/league_schedule.json`, Yahoo team ids →
draft slots): pairings for weeks 1-17 and dates for weeks 1-20. Yahoo's calendar differs from our earlier derived one:
week 1 = Oct 20-25 (6 days), **week 7 = Nov 30 - Dec 13 (two weeks, NBA Cup)**, week 17 = Feb 15-28 (All-Star, two weeks),
playoffs Mar 1-7 / 8-14 / 15-21 (as verified). The derived "two-week week 1" assumption is retired.
Projections `projections_2026_27.csv` (0.3.0 blend), NBA schedule bbref, `season-sim-0.5.0` defaults (fitted priors,
`sim_calibration.md`), 12,000 sims, seed 0. Binomial SEs: P(playoffs) 0.25-0.46 pp, P(title) 0.14-0.43 pp (they exclude
model/projection error).
Command: `fantasy sim season --league-file data/manual/league_rosters_2026-27.json --sims 12000 --scenario "post-draft: actual rosters" --json`
(the schedule file is picked up by default).

**Results (simulated; in-sample optimistic for my team: same projections that drafted it, opponents never manage).**

| slot | team | P(playoffs) | P(bye) | P(title) | exp. rank |
|---|---|---|---|---|---|
| 1 | Harrison's Best Team (me) | 91.9% | 64.7% | 33.7% | 2.63 |
| 4 | Haagen Daz Center | 76.0% | 34.5% | 11.8% | 4.41 |
| 8 | Ed's Extraordinary Team | 52.9% | 15.5% | 6.8% | 6.29 |
| 6 | Yeet | 48.2% | 12.2% | 10.5% | 6.68 |
| 5 | jesse's Phenomenal Team | 48.0% | 11.5% | 7.6% | 6.73 |
| 12 | Joshua's Excellent Team | 47.4% | 12.6% | 4.8% | 6.76 |
| 2 | Justin's Groovy Team | 43.2% | 9.2% | 4.0% | 7.08 |
| 3 | Thomas's Dandy Team | 43.0% | 9.7% | 6.0% | 7.11 |
| 9 | Jeffrey's Great Team | 42.8% | 9.9% | 2.7% | 7.13 |
| 10 | Christian's Bad Team | 40.8% | 8.4% | 5.6% | 7.28 |
| 11 | SCOTTIER BARNEY | 37.3% | 7.0% | 4.0% | 7.58 |
| 7 | Andrew's Awesome Team | 28.2% | 4.8% | 2.5% | 8.31 |

Yeet's title odds (10.5%) are high for its playoff odds: higher variance / playoff-week schedule (not investigated).
My category ranks (1 = best of 12; z vs league): FG% 9 (-0.82), FT% 5 (+0.32), 3PM 2 (+1.69), PTS 1 (+1.61), REB 1 (+2.31),
AST 2 (+1.15), STL 1 (+2.05), BLK 2 (+1.29), TO 9 (-0.59; more turnovers). Most player-games of any team (40.6/week vs
34.7-38.9). Season-average P(win a week) vs each opponent: toughest Haagen Daz Center 63.0%, Ed's Extraordinary 68.6%,
jesse's Phenomenal 70.5%; easiest Andrew's Awesome 76.5%, SCOTTIER BARNEY 74.7%.

My regular season (Yahoo pairings): wk1 Joshua's 73%, 2 Andrew's 77%, 3 Justin's 72%, 4 Christian's 83%, 5 Thomas's 77%,
6 Jeffrey's 64%, 7 jesse's 77% (two weeks), 8 Haagen Daz 68%, 9 Yeet 74%, 10 SCOTTIER BARNEY 76%, 11 Ed's 68%, 12 Joshua's
69%, 13 Andrew's 77%, 14 Justin's 73%, 15 Christian's 66%, 16 Thomas's 82%, 17 Jeffrey's 76% (P(win the matchup)).

**Schedule sensitivity (why loading the real schedule mattered; simulated).** Before the capture the run used an assumed
round robin (`sim-20261020032154-05ce4d`, superseded). Two random relabellings of that round robin (12k sims each) moved
mid-table teams' P(playoffs) by up to 10.6 pp, mine by ~1 pp, P(title) by ≤ 1.3 pp; the real schedule moved e.g. Justin's
54.5% → 43.2% and Ed's 48.2% → 52.9%. With the real schedule loaded this uncertainty is gone; if a file is partial again,
weeks without pairings fall back to the round robin and are flagged (`matchup_schedule.assumed_weeks`,
`weekly_matchups[].opponent_assumed`; the dashboard shows an amber banner).

**Schedule file format** (`data/manual/league_schedule.json`, default `--schedule` of every `fantasy sim` command when
present): as in `schedule.py`, team sides as slot ids **or exact Yahoo team names**, e.g. `{"source": "...", "weeks":
[{"week": 1, "start": "2026-10-20", "end": "2026-10-25", "matchups": [[1, 12], ...]}, ...]}`. Week dates are used only
if they cover weeks 1-20. From a Muse `scoreboard` snapshot: `matchups = [[m.team_a, m.team_b] for m in payload.matchups]`.

**New outputs** (`summary`): `scenario`, `draft_id`, `matchup_schedule {source, assumed_weeks, all_assumed, note}`;
`my_team.weekly_h2h_matrix[]` = my P(win)/P(tie)/exp. cats/category odds against **every** opponent in **every**
regular-season week (games per week differ by week), for what-ifs and for planning around a known opponent.
Draft grades: no tool exists for "projected value vs expectation" per team (`fantasy draft simulate` is the
optimizer-vs-BPA Monte Carlo), so they were skipped.
