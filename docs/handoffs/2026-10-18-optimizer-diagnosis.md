# Handoff: draft optimizer vs BPA after the ADP refresh (2026-10-18)

## Summary
The "optimizer now loses to BPA" finding (4 sims, edge -0.008) was **noise**: a 4-sim paired
edge has SE of about 0.03-0.04 category wins. Measured properly, the old optimizer (greedy rollouts,
`optimizer-0.1`) beat BPA by about **+0.04 expected category wins** (SE 0.007, n=200). That is
real but smaller than the +0.07-0.10 seen before the refresh, which also came from a few sims.
Its matchup-win edge was about 0 to slightly negative.

The main weakness was **rollout optimism and the hard threshold** (candidate (a)). Changing the
rollout to an **expected-value rollout** (`optimizer-0.2.0`) roughly doubles the edge, to
**+0.074 (SE 0.006)**. It beats the old optimizer by +0.040 (SE 0.004) on paired seeds, it also
improves matchup-win probability, and it holds up in every robustness setting tested. Latency is
unchanged or lower: `recommend` takes 0.66 s of compute (2.6 s wall for the CLI) at pick 1.

## Method
- Paired harness: for each sim, sample one room (opponents' noisy-ADP order, the same process as
  `draft simulate`) and replay it for BPA and for every optimizer variant. Edge = variant -
  BPA expected category wins, with paired SE = sd/sqrt(n). Scripts and raw JSON are in the
  session scratchpad (`diag/harness.py`, `variants.py`, `exp*.py`). They are not committed.
- The evaluation is **in-sample**: the same category model chooses and scores the rosters, and
  the opponent is the league-average team. That is fine for comparing policies, but the absolute
  levels (about 6.3/9 wins, 90% matchup) are optimistic.
- Slot 1, 12 teams, 3RR, 13 rounds (picks 1, 24, 36, 37, 60, 61, 84, 85, 108, 109, 132, 133, 156).

## Results (expected category wins, edge vs BPA ± paired SE)
| Experiment | n | greedy 0.1 (old default) | expected 0.2 (new) | new - old |
|---|---|---|---|---|
| seed 7 | 200 | +0.039 ± 0.007 | +0.077 ± 0.006 | +0.038 |
| seed 21 (validation, implemented code) | 200 | +0.034 ± 0.006 | **+0.074 ± 0.006** | **+0.040 ± 0.004** |
| seed 11 | 120 | +0.040 ± 0.009 | +0.075 ± 0.009 | +0.035 ± 0.005 |
| opponents noisier (noise 1.5) | 120 | +0.092 ± 0.013 | +0.121 ± 0.013 | +0.030 ± 0.006 |
| opponents tighter (noise 0.5) | 120 | +0.030 ± 0.008 | +0.069 ± 0.008 | +0.039 ± 0.005 |
| room 30% by our value rank (ADP model misspecified) | 120 | +0.018 ± 0.009 | +0.032 ± 0.010 | +0.014 ± 0.006 |
| slot 6 | 100 | +0.068 ± 0.013 | +0.136 ± 0.012 | +0.068 ± 0.009 |

Matchup-win probability edge vs BPA (seed 21): greedy -0.0015 ± 0.0007, expected **+0.0033 ± 0.0007**.
P(optimizer roster > BPA roster in a sim) went from 0.64 to 0.81.

### Other levers (greedy rollouts, n=40 seed 1 and n=200 seed 7)
| Variant | Edge vs BPA | Note |
|---|---|---|
| avail_threshold 0.65 | +0.039 ± 0.007 (n=200) | same as 0.5 |
| avail_threshold 0.35 | +0.019 ± 0.007 (n=200) | worse (more optimistic) |
| avail_threshold 0.8 | +0.007 ± 0.018 (n=40) | worse (too pessimistic) |
| n_candidates 8 / 24 | +0.035 / +0.034 (n=40) | no effect: (c) is not the issue |
| strategy fixed `balanced` | identical to the scan | the scan always picked balanced: (d) is not the issue |
| hybrid "take BPA unless rollout beats it by >0.01/0.03" | +0.040 / +0.053 (n=40) | beats greedy, but worse than expected rollouts |
| expected rollout + hybrid margin 0.02 | +0.060 ± 0.005 (n=200) | worse than pure expected (+0.077) |
| expected, min_avail 0.10 / rollout_pool 80 / subtractive discount | +0.072 / +0.077 / +0.075 | robust to its knobs |

## Diagnosis
- **(a) Rollout optimism: the root cause.** The greedy rollout gives me the best player with
  P(avail) ≥ 0.5 *for certain* at every later pick and ignores everyone below 0.5. With real ADP
  for about 290 players, the availability curves are sharp and informative, so this binary cliff
  matters more: the optimizer passes a value player at 24 because it "knows" a P=0.55 player will
  be there at 36, and when he is gone the plan breaks. The expected rollout weights each ranked
  candidate by the probability it is the best one left. Thresholds can't fix this: 0.35 is
  worse and 0.65/0.8 are no better.
- (b) The unconditional-ADP mixture (adp≈168 for rarely drafted players) is internally consistent:
  the simulated room uses the same market rank, so "waiting" on Aldama/O'Neale and similar
  players pays off in the sim. Whether real humans let them last is untested, but the 30%-value-room
  test (opponents partly draft by our values) still shows a positive edge.
- (c) Candidate pre-filtering and (d) strategy switching have no effect (see table).
- (e) Filler: not changed; with expected rollouts the leftover probability mass at a pick gets the
  filler line, which is what filler is for.
- (f) Opponents draft from the same ADP model the optimizer uses. This is why the sim favours the
  optimizer, and it is why the misspecified-room row is the most honest number: +0.03 vs BPA.

Where the optimizer differs from BPA (seed 21, 200 sims, new version): round 1 never differs
(Jokić). In round 2 (#24) it differs 65% of the time: Holmgren/Kawhi instead of Derrick White,
because White (ADP 45) usually lasts to 36/37 and it takes him there 110/200 times at #37. At
#60/61 it prefers Dyson Daniels/Okongwu over VJ Edgecombe/Knueppel, and late it targets Myles
Turner and VanVleet. Category profile vs BPA: +FG% (+0.06), +BLK, +REB, +TOV, at the cost of
3PM (-0.05) and AST (-0.03). Total G-score value is slightly *lower* (49.5 vs 49.9), so it gives
up raw value for category fit.

## What changed (code)
- `src/fantasy/draft/optimizer.py`: `OPTIMIZER_VERSION = "optimizer-0.2.0"`. New
  `OptimizerParams.rollout_mode="expected"` (default) and `min_avail=0.02`. `rollout()` dispatches
  to `rollout_expected` (new) or `rollout_greedy` (old code, `rollout_mode="greedy"`). The
  recommendation payload now carries `optimizer_version` and `rollout_mode`. `plan` /
  `planned_picks` are now the **most likely** pick at each later slot (argmax of q, no repeats),
  not a certain one. Players' later availability is discounted by the chance I already took them
  (multiplicative, independence approximation).
- `src/fantasy/draft/simulate.py`: the summary adds `optimizer_version` and `edge_category_wins_se`
  (paired SE).
- `tests/test_optimizer.py`: tests for the version in the payload, a distinct and available plan,
  expected == greedy when everyone is available, and a complete expected rollout.
- The valuation `MODEL_VERSION` is unchanged: values did not change, only recommendations. The
  model-eval skill suggests bumping it for optimizer changes, so the lead may want to do that
  (outside my paths).

## Risks for the draft (2026-10-19)
- The gain is measured against a room that drafts by Yahoo ADP plus noise. Real humans may differ.
  With 30% of the room following our values, the edge shrinks to +0.03 ± 0.01 but stays positive
  and above greedy.
- Absolute sizes are small: about +0.04 extra category wins per week over the old optimizer,
  around +0.5 percentage points of matchup-win probability. The sign is robust; the size is model-dependent.
- `planned_picks` are now "most likely" targets. Treat them as guidance, not as guaranteed
  availability.
- Rollback: `OptimizerParams(rollout_mode="greedy")` restores optimizer-0.1 behaviour exactly.
  There is no CLI flag for it.
- Latency: 0.66 s compute per `recommend`, 2.6 s CLI wall time including startup (limit 5 s).
