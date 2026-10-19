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
| Availability | games-played share f ~ Beta(mean gp/82, sd gp_sd/82) (projections' fitted gp / gp_sd; gp_sd ≤ 0 → 12). Missed share 1-f: 75% as injury **spells** (one spell, or with prob 0.5 two spells with a U(0.2, 0.8) split; placed uniformly, non-overlapping, on the NBA-season day axis; `out`/`out_for_season` players start the season in their spell), 25% (`rest_share`) as iid one-game rests. If projections carry `playoff_mult` < 1, playoff-week games are additionally missed with prob 1 - playoff_mult. | `engine._player_arrays` |
| Daily lineup | each day, healthy players whose NBA team plays start in order of projected value until the 10 starting slots are full. Positions ignored (2 Util slots make it rarely binding). This is a *daily* cap; a weekly "10 × game-days" cap almost never binds (13 players × ~0.5 games/day ≈ 6.5 starters/day on average) and would miss the real loss, which happens on heavy days. | `engine.run` |
| Streaming | each team adds `min(adds_per_week × games_per_add × stream_usage, open starting slot-days)` replacement-level games per week (6 × 2 × 0.5 = 6 by default); replacement line = valuation's replacement level. Same for every team, so it mostly compresses differences and fills injured teams' holes. | `engine.run` |
| Weekly totals | given counted games k per player: team attempts and counting stats ~ Normal(Σk·mean, Σk·var(mean)) with the fitted per-game variance var = a·m + b·m² (`fantasy.model.noise`, the one noise model shared with the draft since 0.5.0; the 0.3.0 constant-CV option was deleted); noise drawn at team level, stream keyed by team id, correlated across stats with the fitted within-game correlation (`noise_corr`); makes \| attempts ~ Normal(p_team·A, binomial + mix variance); counts rounded to integers, percentages to 3 decimals (ties happen: ~3% STL/BLK, ~1% PTS). | `engine.run` |
| H2H + standings | per scheduled pair, 9 category comparisons (TO lower wins), ties = 0.5. Standings by category wins + 0.5·ties (Yahoo H2H categories), random tie-break. | `engine._score` |
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

## Limitations (read before acting on outputs)
- **In-sample optimism.** The simulator uses the same projections the optimizer drafted with, and
  opponents are drafted by noisy ADP with no strategy. A team that "beats the market" by our own
  projections will look dominant (e.g. 95% playoffs / 57% title on `mock-full`). The market's ADP
  carries information our projections lack; treat absolute probabilities as optimistic and use the
  tool for **relative** comparisons (candidate A vs B, add vs drop, week-N category gaps).
  Quantified in `optimism_decomposition.md` (`fantasy sim symmetric`): slot 1 is 61%/13% in a room where
  everyone drafts like us, and the status-quo 96%/55% drops to 78%/20% if the market's player ranking is right.
- No in-season management by anyone: rosters are frozen except streaming; no trades, no waiver
  pickups of breakout players, no IL juggling beyond "an injured player frees a starting slot".
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
  extra merge is before March). Load Yahoo's actual week dates + matchups with `--schedule` after the
  draft. NBA games after Mar 21 (Mar 22 - Apr 11) do not count in this league.
- Team ids are draft slots; Yahoo team ids differ — the league/schedule JSON must use one consistent id set.
- Mid-draft completion uses a single noisy market order per completion; `draft-check` averages 4
  completions (one completion produced a spurious +5.8 pp "edge" in testing that vanished with 4).
- Players not in projections are simulated at the replacement line with 60 expected games (warned).
- Draft-check reserves the candidate for me at my next pick (it answers "if I take X there", not
  "will X be there").
