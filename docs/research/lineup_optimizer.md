# Daily lineup optimizer (`fantasy lineup plan`, lineup-0.2.0, task T-005)

Code: `src/fantasy/lineup/` (`players.py` statuses / P(plays), `optimize.py` exact assignment, `value.py` weekly
category model + Monte Carlo, `plan.py` planner + baselines, `cli.py`). Tests: `tests/test_lineup.py`. Validation
script: `docs/research/lineup_optimizer_validation.py`.

Relation to `fantasy inseason lineup` (`src/fantasy/inseason/lineup.py`): that module counts games available vs
started under slot rules (priority-greedy maximum matching) for season/streaming analysis. This one is the
**decision** layer: which players go in which Yahoo slot today, given the matchup, Yahoo tags and the current
slots. The two share the eligibility rule (`slot_eligibility`). A test checks that they start the same number of
players (with positive values a max-weight assignment is a maximum matching, because matchable sets form a
transversal matroid). On week 1 both give 1/10/1/10/9/3 starts per day (34 of 36 player-games).

## 1. Daily decision: exact assignment
Rows = my 13 players. Columns = PG, SG, G, SF, PF, F, C, C, Util, Util, 3 x BN, 3 x IL. Score = the player's value
that day for an eligible starting slot (`SLOT_ELIGIBILITY`), 0 for BN, 0 for IL if the player is IL-eligible
(Yahoo O / out for season), forbidden otherwise. O/INJ/NA/SUSP players are never startable. Solved exactly with
`scipy.optimize.linear_sum_assignment` (tested against brute force). Tie-breaks: +1e-7 for keeping a player's
current slot (no needless moves for the browser executor), +1e-8 for an IL-eligible player in IL. A player with a
game is worth at least `GAME_FLOOR` = 1e-5 more than a player without one, so a player with a game is never
benched for one without (unless `--protect-ratios`, which allows benching a player whose matchup value is negative,
e.g. to protect FG%).

## 2. Value: matchup-aware marginal expected categories
Objective: **expected categories won** in this week's matchup. Reason: the league scores H2H *categories*
(`scoring_type = head`), so every category is a win or loss in the standings, and expected categories is the
regular-season objective. In the playoffs (one matchup decides) P(win matchup) is the right objective. It is
reported but not optimised yet.

Each category's final weekly margin (me - opponent) is treated as Normal(mu_c, sigma_c^2) from:
* raw totals already banked this week (`--accumulated`, default 0);
* projected remaining totals, sum over started player-days of P(plays) x per-game line;
* variance per player = k * var_game(m) (**fitted**, `model/noise.py`, noise-var-1.0) + Bernoulli games-played
  variance q * m^2 + season-level projection error (k m)^2 (vol_sd^2 + stat_sd^2) (**fitted**, `sim/calibration.py`
  ERROR_BY_MPG, STAT_SD). FG%/FT% use the delta method on makes - p_team x attempts (as `catmodel`), plus the fitted
  FG%/FT% shift sds.

Weights: w_c = sign_c phi(z_c) / sigma_c with z_c = sign_c mu_c / sigma_c (sign -1 for TO). This is dE[cats]/d(my
total). The per-game value of player i = sum_c w_c delta_ic, with delta = the per-game stat for counting cats and
(makes_i - p_me attempts_i) / A_me for percentages, linearised at the projected weekly totals. Value(i, day) =
P(plays) x per-game value. The variance a player adds is left out of the gradient (second order). The final plan is
still evaluated with full moments and by Monte Carlo.

Baseline lineup policy (used to linearise): a daily assignment by per-game G-score (valuation with gp = 82). Then
fixed point: weights -> re-solve all remaining days -> recompute weights, until my plan stops changing (week 1:
converged in 2 iterations). The opponent's lineup uses the per-game G-score policy (**assumption**: an attentive
manager who does not react to my lineup).

## 3. P(plays) (PRIORS unless stated)
| Yahoo tag | P(plays next game) | later games this week | label |
|---|---|---|---|
| none (healthy) | 1 - 0.25 (1 - gp/82) | same | prior: rest_share 0.25 is the season simulator's prior; gp is the projection |
| P | 0.9 | healthy value | prior |
| GTD / DTD | 0.6 | healthy value | prior |
| Q | 0.5 | healthy value | prior |
| D | 0.25 | healthy value | prior |
| O / INJ / out for season / NA / SUSP | 0 | 0 | prior (O assumed to last the week) |

Healthy uses only the one-game "rest" share of missed games, not gp/82 itself. gp/82 includes multi-week injury
spells, and a player who is healthy today is not in one, so gp/82 would count those missed games twice. The
**decide-at-tip-off** list holds tagged players with a game today. Yahoo locks each player at his own tip-off, so a
Q player in a late game can be swapped in or out once his status is known. Each day of the week plan also lists
its own decide-at-tip-off names. `il_moves`: O / out-for-season players the assignment puts in a free IL slot.
Moving them frees a bench spot, and the executor decides.

## 4. Evaluation
* Analytic: sum_c Phi(z_c). P(win matchup) is the Poisson-binomial over categories, assumed independent.
* Monte Carlo (default 20,000 sims; the season simulator's generative model): per-player lognormal season factors,
  Bernoulli games, makes = attempts x shifted %, weekly game noise with the fitted within-game correlation
  (`cal.NOISE_CORR`), PTS from the components. Counts are rounded and % to 3 decimals, and ties score 0.5. Common
  random numbers across plans keyed by player id, so the paired differences have small SEs.

## 5. Validation (real data, 2026-10-20)
`fantasy lineup plan --date 2026-10-20 --week --json` (week 1 vs slot 12 "Joshua's Excellent Team", Yahoo
capture 2026-10-20, Podziemski Q):

| plan | E[cats] MC (SE) | P(win) MC (SE) | analytic E[cats] / P(win) |
|---|---|---|---|
| optimized | 4.913 (0.012) | 0.588 (0.003) | 4.92 / 0.621 |
| naive per-game G-score | 4.833 (0.012) | 0.566 (0.004) | 4.85 |
| current Yahoo lineup held all week | 3.476 (0.011) | 0.226 (0.003) | 3.51 |

Paired: optimized - naive = +0.080 cats (SE 0.003), +0.022 P(win) (SE 0.002). Optimized - static = +1.44 cats
(SE 0.009). The whole week-1 gain over naive is one decision. On Wed 10-21 (11 players with games, 10 slots) the
optimizer benches Podziemski (Q, P(play) 0.5, a **prior**) rather than Fears: 0.5 x 0.217 < 0.191 per-game value.
The naive G-score ranks Podziemski far above Fears (1.24 vs 0.23) because Fears's FG%/TO hurt him on generic
weights. This week's weights (FG% is already a likely loss, P(win) 0.23) make them close. If Podziemski is
confirmed at tip-off, swapping him in is better. So the gain depends on the Q prior.

Weeks 1-17 vs the real opponents (rosters as drafted, everyone healthy after week 1, 4,000 sims): the optimized
and naive plans differ only in week 1. Mean gain +0.004 cats/week (MC SE 0.0005). Over the season, matchup-aware
weighting rarely changes who sits. With this roster only 0-5 player-games a week exceed the slots (16 in 17 weeks;
the C crunch costs ~0 games, matching `fantasy inseason lineup`). The value comes from setting the lineup daily
(vs a static lineup: ~1.4 cats in week 1) and from handling statuses. Matchup weighting will matter more once
streaming adds create a crunch and mid-week accumulated totals make some categories settled.

## 6. Operational safeguards (lineup-0.2.0, after the independent quant review)
* **Stale slots refused.** `changes_vs_current` is only meaningful against slots read from Yahoo *today*. The CLI
  finds when the slot data were read and converts that to an ET date. The source is the league file's sync
  `lineup` map (`captured_at`, then "(captured ...)" in `source`, then `synced_at`), else the capture's
  `captured_at`, else the timestamp in its `source` text (a time without a zone is taken as UTC). It exits 2 with
  JSON `code: "stale_capture"` unless that date equals `--date`, or the caller passes `--current-slots <json>`
  (`{player_id: slot}`) or `--allow-stale`. With `--allow-stale`, a STALE warning is emitted. Example: the
  2026-10-20 03:25 UTC capture is 2026-10-19 ET, so it is stale for a 10-20 plan. `--capture` is an alias of
  `--roster`.
* **`target_slots`**: `{player_id: {slot, name, yahoo_player_id}}` for every roster player, i.e. the full final state.
  The executor should set it rather than replay a diff.
* **Locks.** Run the plan before the first tip-off of the day. After that, pass `--locked id,...` for players
  whose games have started. They are pinned to their current slot (their value counts if it is a starting slot).
* **Tip-off notes depend on the slot.** A benched Q player gets "if confirmed active, start him in place of X
  (only before X's tip-off)", where X is the weakest starter with a game in a slot he can fill. This appears only
  when his P(plays) = 1 value beats X; otherwise he stays benched. A starting Q player gets "swap him out for Y if
  he is ruled out".
* **Monotone value transform.** Values of players with a game are shifted, v - min + GAME_FLOOR, instead of being
  clipped at 0. Clipping made negative-value players tie, so the keep-current-slot tie-break decided. The shift
  keeps the order. For a transversal matroid the best feasible starter set depends only on that order among
  maximum sets.
* **IL tie-break.** IL_BONUS (2e-7) > KEEP_BONUS (1e-7), so an O player on the bench is moved to a free IL slot.
* **Infeasible roster.** When there are more non-IL-eligible players than starting + bench slots (e.g. a player
  back from IL), the CLI exits 2 with `code: "infeasible_roster"` and `action: "drop ... or keep ... in IL"`.
* **Warnings.** A mid-week plan without `--accumulated` warns that banked stats are taken as 0. Positions or NBA
  team filled from projections for a Yahoo row that lacks them (tagged rows) are also warned.
* **Availability is explicit.** `expected.availability_assumption` states the P(plays) model. `--rest-share`
  (default 0.25, a **prior**) sets the healthy P(plays) = 1 - rest_share (1 - gp/82).

### Sensitivity of P(win) to the availability prior (week 1, Monte Carlo 20k sims, SE ~0.003)
| rest_share | optimized E[cats] | P(win) | naive P(win) | static P(win) |
|---|---|---|---|---|
| 0.25 (default prior: a healthy player misses only one-game rests) | 4.913 | 0.588 | 0.566 | 0.226 |
| 1.0 (season-average gp/82, like `fantasy sim`) | 5.480 | 0.731 | 0.712 | 0.411 |

The reviewer traced the 0.59 (this tool) vs 0.73 (`fantasy sim` week 1) gap entirely to availability:
rest_share = 1.0 reproduces the simulator's ~0.725. The opponent's roster has low projected gp (A. Davis 40,
Morant 40, J. Williams 50). Under gp/82 they miss many week-1 games. Under rest_share 0.25 they are assumed
healthy unless tagged. Which is right depends on whether a player with no Yahoo tag misses games this week at his
season-average rate. This is uncalibrated; check it against week-1 actual DNPs. The *decisions* barely depend on
it. Today's lineup and all week-1 days are identical under both values, except the Fri 10-23 bench pick (Fears at
0.25, Bailey at 1.0, a near tie).

## 7. Limitations / open
* Weekly variance: the noise model under-states the weekly variance of FGA/PTS/FGM at 24-30 mpg by 14-46%
  (sim_calibration.md section 8). It is not corrected here, so sigma is slightly low and weights slightly too
  peaked.
* Tag priors (Q 0.5, GTD 0.6, P 0.9, D 0.25, O = out all week) are uncalibrated. Calibrate them from Yahoo tag
  history against actual DNPs.
* No game times: the decide-at-tip-off flag cannot yet say whether a swap is still possible (needs tip-off times).
* The opponent model is static and attentive, and neither side streams. Mid-week, pass the real totals via
  `--accumulated`.
* Analytic P(win) assumes independent categories (0.621 vs MC 0.588). Use the MC number.
* Playoff weeks should optimise P(win matchup). Not implemented.
* No max-games cap exists in this league (no setting in `league_settings.json`), so none is modelled.
