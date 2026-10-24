# IL replacement in the season simulator (2026-10-20)

Code: `src/fantasy/sim/engine.py` (`SimParams.il_replacement`, `il_slots`, `il_min_spell_days`, `il_rep_availability`;
step 4b of the module docstring). `PositionAwareSimulator` (`src/fantasy/inseason/possim.py`) inherits it unchanged.
CLI: `fantasy sim season|matchup|compare --il-replacement [--il-min-spell-days 7]`. Tests: `tests/test_sim.py::test_il_*`.
Version: **opt-in, default off**, so `season-sim-0.5.0` output is unchanged. A bit-for-bit check against the
HEAD engine on the real league (1,500 sims, seed 0) gave identical totals, games, streamers, ranks, playoffs, champion
and runner-up arrays.

## Question
League 44495 has 3 IL slots, which only players listed Out or Out-for-season may use, plus 6 adds a week and 13-man
rosters. A manager whose player is out for a long spell moves him to IL and adds a full-time replacement. The
sim (0.5.0) already modelled injury spells, daily best-available lineups, and streaming of up to 6
replacement-level games per team-week. It did **not** model IL replacement: an injured player kept his roster spot,
and a team's extra games were capped at about 6 a week however many of its players were hurt. The hypothesis was
that this understates injury-prone rosters (slot 12: Morant, A. Davis, Gordon, J. Williams), overstates our odds,
and over-penalises injury-prone trade targets (Jamal Murray).

> **season-sim-0.7.0 (2026-10-22):** spells are now placed on the fantasy window as a circle (flat availability at
> gp/82; `season_simulator.md`, "Availability timing fix"). A spell that wraps past the last fantasy day is stored as
> two IL intervals (≤ 4 per player); the ≥ `il_min_spell_days` test uses the whole spell. The results below were
> computed with 0.6.0 placement (U-shaped availability, too few mid-season spells), so IL games there are biased.

> **season-sim-0.8.0 (2026-10-24): roster cap.** See "Roster cap" below. It fixes a bug in 0.6.0/0.7.0: a team with
> 13 + k IL players played 14+ players all season (and got IL replacements on top). `--no-roster-cap` = 0.7.0.

## Roster cap (season-sim-0.8.0, 2026-10-24)
**Bug.** Yahoo rosters are 13 active (10 starters + 3 bench) + up to 3 IL; a player may sit in IL only while Yahoo
lists him Out, and once he is healthy the team must get back to 13 non-IL players (drop someone) before any move.
0.7.0 treated every listed player as a roster member all season, so a team with 13 + k IL players effectively played
13 + k players after the IL players returned, and IL replacement still added replacement-level players during
spells. Found 2026-10-24: our team with 14 (Podziemski on IL) had P(playoffs) 0.892 vs 0.767 with Ace Bailey removed
(6000 sims, simulated). Teams 1, 6, 9, 10 (14 players) and 11 (15) were all inflated.

**Rule (an assumption, documented; `SimParams.roster_cap`, `active_roster` = settings `num_rounds` = starters +
bench = 13; IL slots = settings `roster_slots["IL"]` = 3; `engine._apply_roster_cap`).** On each fantasy day, per team:
- *Out* = rostered and inside an IL-qualifying spell (>= `il_min_spell_days`, or any spell of a player who starts
  the season listed out). Up to `il_slots` Out players sit in IL.
- If rostered − min(#Out, il_slots) > 13, the lowest-value players (same value order as the daily lineup) that are
  not Out are **released for the rest of the season** from that day. Released players never return.
- So for 13 + 1 IL: when the IL player's spell ends, the 14th-best goes; if another player is Out that day the team
  legally keeps 14 (he takes the IL slot) and the release happens on the first day nobody is Out; if the spell runs
  to the end of the window nobody is released.
- IL replacement now fills only below 13 healthy non-IL: replacements per day = max(0, min(#Out, il_slots) −
  (rostered − 13)^+). A 14-man team with one Out gets none (its 14th man *is* the replacement).
- Players the league file lists as Out on Yahoo (`status` O/INJ/OFS/IR → `LeagueState.out_now`) start the season in
  their spell, like projections status `out` (Podziemski, Flagg and Banchero were `healthy` in the projections but
  Out on Yahoo, so 0.7.0 had them playing from day 0).
- No new random numbers (pure function of the existing spell draws): on/off and roster variants stay paired.
  Mid-season swaps in `PositionAwareSimulator` (windows) count only the window's members, so a swap is 13 → 13.

**Not modelled.** A real manager may drop someone other than the lowest season-value player, trade, or re-add later;
an IL-slotted player listed Q (team 11's Anthony Black, team 7's Coby White) counts as not Out, so the team is over
the cap from day 0 if it has more than 13 (Yahoo would block its moves until it fixes that).

### Before/after (simulated; real rosters `data/manual/league_rosters_2026-27.json` captured 2026-10-24 16:49Z, Yahoo
schedule, blend projections, 6000 sims, seed 0, paired; P(po) SE ≤ 0.65 pp, P(title) SE ≤ 0.6 pp, binomial over sims)
| team (players) | 0.7.0 P(po) / P(title) | cap, no Yahoo-Out | **0.8.0** P(po) / P(title) | released (P over sims) |
|---|---|---|---|---|
| 1 Harrison (14) | 0.892 / 0.284 | 0.836 / 0.236 | **0.837 / 0.235** | Fears 0.68 (median first release day 106 of 153) |
| 2 Justin (13) | 0.381 / 0.031 | 0.464 / 0.051 | **0.466 / 0.052** | – |
| 3 Shalom Jerome (13) | 0.461 / 0.056 | 0.560 / 0.085 | **0.567 / 0.086** | – |
| 4 Haagen Daz (13) | 0.670 / 0.074 | 0.776 / 0.131 | **0.775 / 0.129** | – |
| 5 jesse (13) | 0.406 / 0.049 | 0.507 / 0.083 | **0.510 / 0.082** | – |
| 6 Yeet (14) | 0.627 / 0.200 | 0.524 / 0.147 | **0.520 / 0.145** | Sharpe 0.31 |
| 7 Andrew (13) | 0.146 / 0.008 | 0.219 / 0.019 | **0.220 / 0.020** | – |
| 8 Ed (13) | 0.450 / 0.041 | 0.536 / 0.067 | **0.538 / 0.066** | – |
| 9 Jeffrey (14) | 0.498 / 0.042 | 0.403 / 0.035 | **0.405 / 0.034** | G. Santos 0.51 |
| 10 Christian (14) | 0.558 / 0.088 | 0.454 / 0.074 | **0.463 / 0.074** | Kuminga 0.43 |
| 11 SCOTTIER (15) | 0.582 / 0.105 | 0.302 / 0.033 | **0.284 / 0.037** | Drummond 0.84, Schröder 0.50 |
| 12 Joshua (13) | 0.330 / 0.022 | 0.418 / 0.040 | **0.417 / 0.041** | – |

- The 13-man teams gain 7-11 pp because the inflated teams lose; nearly all of the change is the cap itself
  ("cap, no Yahoo-Out" column), the league-file Out status adds little.
- **Our team vs the same roster with Ace Bailey removed** (paired, 14-man minus 13-man): 0.7.0 ΔP(po) **+0.126 ±
  0.004**, ΔP(title) +0.104 ± 0.005 (the bug); 0.8.0 ΔP(po) **+0.013 ± 0.003**, ΔP(title) −0.009 ± 0.004. The small
  playoff gain is the real IL-period depth. The 14-man run releases Fears (lowest season value), not Bailey, so the
  two variants are not the same 13 afterwards; the title sign suggests Bailey is the better playoff-week keep — a
  value-ordering question, not a cap effect.
- IL replacement games per team-season (0.7.0 → 0.8.0): team 1 113 → 79, 6 141 → 92, 9 131 → 88, 10 136 → 90,
  11 135 → 62; 13-man teams unchanged (no double counting any more).
- `--no-roster-cap` reproduces 0.7.0 exactly (identical league table on both files). Cost: 5.8 s → 7.3 s at 6000 sims.
- Reproduce: `fantasy sim season --league-file <file> --sims 6000 --seed 0 --no-save --json [--no-roster-cap]`;
  decomposition script `decomp.py` (session scratchpad `rcap/`). Tests: `tests/test_sim.py::test_roster_cap_*`.

## Method
- **Trigger.** The engine already draws each player's injury spells (one or two per season, validated in
  `sim_calibration.md` §4). An IL replacement exists for every day inside a spell of at least `il_min_spell_days`
  days. Spells this long are used as a proxy for the "Out" designation that IL requires. Players listed out or
  out-for-season start the season inside a spell, so they qualify immediately.
- **Cap.** Each team has at most `il_slots` replacements at once (default: settings `roster_slots["IL"]`, 3). With
  more long-injured players than slots, the extras simply miss games, as before.
- **Replacement's games.** The replacement uses the existing replacement line (valuation's replacement level, the
  same line streamers use) and the same per-game noise model. On each fantasy day he plays the expected games
  of a typical NBA team: the share of the 30 teams that play that day, times `il_rep_availability` (0.85, a prior
  for the replacement's own rest days and minor injuries). These games fill open starting slots after the
  roster's healthy players and before streamers. Streaming is unchanged and stays capped at
  `min(6, remaining open slot-days)`.
- **Symmetry and pairing.** The model applies to every team. It draws no new random numbers: spell intervals come
  from the existing player-keyed draws, so off/on runs are paired with common random numbers.
- **Not modelled.** IL adds are not deducted from the 6-add budget. Use is about 0.7 per team-week (see the
  sanity check), and streaming already uses only 3 of the 6 adds (`stream_usage` 0.5, a prior). Not modelled
  either: managers who are slow to fill IL slots, a replacement who is better than replacement level, and
  replacements who must match a position.

### Priors (all guesses, not fitted)
| parameter | value | basis |
|---|---|---|
| `il_min_spell_days` | 7 | Assumed delay before "Out" + IL move. Sensitivity 3 / 14 below. |
| `il_rep_availability` | 0.85 | The replacement also rests or gets hurt; he is roughly a 70-game player. Sensitivity 0.6 below. |
| `il_slots` | 3 | League setting (fact). Values 1 and 2 stand in for less diligent managers. |
| replacement line | valuation replacement level | Same assumption as streaming. |

## Results (simulated; real rosters `data/manual/league_rosters_2026-27.json`, Yahoo schedule `data/manual/league_schedule.json`, blend projections, 12,000 sims, seed 0, paired; SE = paired SE over sims, excludes model/prior error)

### Season, off → on (`il_min_spell_days` 7)
| team | P(po) off | ΔP(po) ± SE | P(title) off | ΔP(title) ± SE | exp. rank off | Δ rank ± SE | IL games / season |
|---|---|---|---|---|---|---|---|
| **1 Harrison (us)** | 0.919 | **-0.007 ± 0.002** | 0.337 | **-0.015 ± 0.004** | 2.63 | +0.06 ± 0.01 | 115 (lowest) |
| 6 Yeet | 0.482 | +0.036 ± 0.003 | 0.105 | +0.018 ± 0.003 | 6.68 | -0.24 ± 0.01 | 147 |
| 2 Justin | 0.432 | +0.029 ± 0.003 | 0.040 | +0.008 ± 0.002 | 7.08 | -0.22 ± 0.01 | 150 |
| 5 jesse | 0.480 | +0.027 ± 0.003 | 0.076 | +0.002 ± 0.002 | 6.73 | -0.23 ± 0.01 | 148 |
| 10 Christian | 0.408 | +0.026 ± 0.003 | 0.056 | +0.004 ± 0.002 | 7.28 | -0.21 ± 0.01 | 147 |
| 9 Jeffrey | 0.428 | -0.031 ± 0.003 | 0.027 | -0.004 ± 0.002 | 7.13 | +0.22 ± 0.01 | 137 |
| 7 Andrew | 0.282 | -0.028 ± 0.002 | 0.025 | -0.005 ± 0.002 | 8.31 | +0.26 ± 0.01 | 137 |
| 4 Haagen Daz | 0.760 | -0.027 ± 0.002 | 0.118 | -0.007 ± 0.003 | 4.41 | +0.20 ± 0.01 | 128 |
| 11 SCOTTIER | 0.373 | -0.019 ± 0.003 | 0.040 | -0.007 ± 0.002 | 7.58 | +0.14 ± 0.01 | 138 |
| 3 Thomas | 0.430 | -0.005 ± 0.003 | 0.060 | +0.003 ± 0.002 | 7.11 | +0.03 ± 0.01 | 149 |
| 8 Ed | 0.529 | +0.002 ± 0.003 | 0.068 | +0.001 ± 0.002 | 6.29 | -0.03 ± 0.01 | 142 |
| 12 Joshua | 0.474 | -0.004 ± 0.003 | 0.048 | +0.002 ± 0.002 | 6.76 | +0.02 ± 0.01 | 145 |

- **The hypothesis is mostly not supported for slot 12.** Joshua's IL use (145 games) is close to the league mean
  (140), so the change barely moves him. In the sim every team has about 3 long-injured players on an average
  day (see the sanity check), so his injury-prone names are not unusual. The teams that gain are those with
  above-average IL use *and* moderate strength (Yeet, Justin, jesse, Christian).
- **We lose a little**, because our healthy roster gets the fewest replacement games (115 vs a 140 mean). The
  effect is -0.7 pp playoffs and -1.5 pp title (on 33.7%). The direction matches the hypothesis; the size is small.

### Sensitivity, our team (Δ vs off, ± paired SE)
| variant | ΔP(po) | ΔP(title) | league IL games / team-season |
|---|---|---|---|
| min spell 3 d | -0.006 ± 0.002 | -0.014 ± 0.004 | 142 |
| **min spell 7 d (default prior)** | **-0.007 ± 0.002** | **-0.015 ± 0.004** | **140** |
| min spell 14 d | -0.006 ± 0.002 | -0.015 ± 0.004 | 136 |
| 2 IL slots (less diligent managers) | -0.003 ± 0.001 | -0.007 ± 0.004 | 103 |
| 1 IL slot | -0.001 ± 0.001 | +0.002 ± 0.003 | 56 |
| replacement availability 0.6 | -0.004 ± 0.001 | -0.007 ± 0.004 | 100 |

The threshold barely matters, because simulated long spells are long (median 28 days). What matters is how many
replacement games every team gets: slots used × availability.

### Week 1 vs slot 12 (Joshua), Oct 20-25
`fantasy sim matchup --week 1` (12,000 sims, seed 0): P(win) **72.7% → 71.7%** (Δ -1.07 pp ± 0.22 paired; tie
2.5% both). Expected categories won 5.43 → 5.38 (Δ -0.05 ± 0.005). Expected replacement games that week: us 0.9,
Joshua 1.4. Same sign at 3 days (-1.05 pp) and 14 days (-1.13 pp). Caveat: week-1 spells are random draws. Nobody on
either roster is listed out in the projections, so the real week-1 effect depends on actual injury news, which
the daily lineup tool handles.

### Murray trade (slot 1 gives Ware + VanVleet to slot 6 for Murray; slot 6 drops Sharpe; we refill), position-aware sim, 12,000 sims, seed 0
| refill | IL off: ΔP(po) / ΔP(title) | IL on: ΔP(po) / ΔP(title) | on − off (diff-in-diff), title / playoffs |
|---|---|---|---|
| Julian Champagnie | +0.012 ± 0.002 / +0.087 ± 0.004 | +0.013 ± 0.002 / +0.087 ± 0.004 | -0.000 ± 0.005 / +0.001 ± 0.002 |
| Santi Aldama | -0.012 ± 0.002 / +0.065 ± 0.004 | -0.002 ± 0.002 / +0.079 ± 0.004 | +0.014 ± 0.005 / +0.010 ± 0.002 |

For Yeet, the trade's title effect is -2.5 pp with IL off and -3.5 pp with it on (Champagnie refill). IL
replacement does not change the trade verdict. With the Champagnie refill the effect is unchanged. With Aldama it
improves by about 1 pp. Murray is projected for 61 games, about the league mean of 56-63, so in this projection
set he is not unusually injury-prone, and the old model was not penalising him much.

## Sanity check vs reality
Simulated, IL on, 7 days:
- **IL occupancy.** On average 2.4 of 3 IL slots are filled per team per day. Without the cap, 3.2 rostered
  players per team are inside a ≥7-day spell on an average day. P(more than 3) = 0.42 and P(none) = 0.06, so
  the cap binds often.
- **IL moves.** 1.05 long spells per rostered player-season, which is 13.6 per team-season or about 0.7 per
  team-week. That fits easily inside 6 adds a week.
- **Replacement games.** 140 per team-season on average (p10 109, p90 166), about 7 per team-week. Total
  games rise from 742 to 883 per team-season (+19%); streamers stay at 120.
- **Is this level plausible?** It follows from the projected games played, not from the IL model. Rostered players
  average about 56 projected games, so 13 players miss about 330 games per team, and three quarters of those
  missed games fall in spells. The games-played model is fitted (`availability_model.md`: rotation players
  averaged about 56 games over 2022-26, with prediction bias within ±2). The spell structure is validated
  against 2,306 real player-seasons (`sim_calibration.md` §4: 47.5% of missed games fall in runs of 10+). So the
  simulated occupancy is consistent with fitted inputs.
- **No direct data.** We have no Yahoo data on real IL use or manager diligence. That "most active managers keep
  2-3 IL slots filled most of the season" is a **prior/guess**. One caveat: "missed" games in the gp data also
  include coach's decisions and rest, which never earn an Out tag. That argues for the lower-use variants
  (2 slots or availability 0.6), which roughly halve the effect on us.

## Recommendation
Make IL replacement the default (`il_replacement=True`, 7 days, 3 slots, availability 0.85) in a
**`season-sim-0.6.0`** bump, once the user agrees. Reasons:
- It models a real league rule that every active manager uses.
- The occupancy it implies is consistent with the fitted games-played and spell models.
- It is conservative for us: it lowers our odds slightly.

The effect on our decisions is small: -0.7 pp playoffs, -1.5 pp title, week-1 P(win) -1.1 pp, trade verdict
unchanged. So this is a realism fix, not a strategy change. Until it is the default, run `--il-replacement` as a
sensitivity check on roster decisions that involve injury-prone players. For a lower bound on manager diligence,
compare against `il_slots=2` (Python API).

The default was **not** changed here. Changing it means flipping `SimParams.il_replacement` and the CLI default,
bumping `SIM_VERSION`, re-running `fantasy sim season`, and re-publishing the dashboard numbers.

## Reproduce
Scripts are in the session scratchpad (`il/measure.py`, `il/slots.py`, `il/trade.py`, `il/ana.py`). They build
the context as `fantasy sim season` does (blend projections, `data/manual/league_schedule.json`, settings
`num_teams` = 12), then run `SeasonSimulator` (or `PositionAwareSimulator` for the trade) with
`SimParams(n_sims=12000, seed=0, il_replacement=...)`. CLI equivalent: `fantasy sim season --league-file
data/manual/league_rosters_2026-27.json --sims 12000 --seed 0 [--il-replacement] --no-save --json`.
