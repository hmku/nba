# Optimism decomposition: how much of the pre-draft projection is real?

_2026-10-18. Code: `src/fantasy/sim/symmetric.py`, `src/fantasy/sim/market_truth.py`; CLI `fantasy sim symmetric`;
tests `tests/test_symmetric.py`. Season simulator `season-sim-0.2.0` with its default (uncalibrated) priors,
optimizer `optimizer-0.2.0`, valuation `valuation-0.2.1`, projections `data/processed/projections_2026_27.csv`
as of commit cc75484. Every number below is **fitted by simulation** from this experiment (100 draft seeds,
500 season sims per league per truth) unless marked as a prior._

> Note (2026-10-19): this analysis, and the scenario range in `web/src/content/optimism.json`, predate the 2026-10-19
> switch of the default projections to the 50/50 ours/ESPN blend (`projection_quality.md`). They were not recomputed.

## Plain-language answer

The dashboard's pre-draft number for slot 1 (≈ 96% playoffs / 55% title) comes from one scenario: **we
draft with the optimizer, the other 11 teams draft by Yahoo ADP plus noise, and the season is played with
our own projections as "the truth".** Taking that apart:

1. **Draft slot 1 is genuinely good, but only a little.** If all 12 teams drafted exactly like us, slot 1
   would make the playoffs 61% of the time (vs 50% for an average team) and win the title 13% (vs 8.3%).
   Third-round reversal does not fully neutralize the first pick in this model: slots 1–3 are best, slots
   7–11 worst. Graded by the market's view of players instead of ours, slot 1 is worth less (55% / 9%).
2. **Most of the 55% title odds comes from assuming opponents draft by ADP.** If all 11 opponents used our
   optimizer (on their own, slightly noisy, copies of our projections), slot 1 falls to 75% / 25%. With 3
   such opponents it is 92% / 38%, with 6 it is 87% / 31%.
3. **Grading ourselves with our own projections inflates the title odds a lot.** If the market (ADP) is
   right about who is good, the *same rosters* are worth 78% / 20%; with a 50/50 blend of our projections
   and the market's, 91% / 37%. We drafted players our model likes more than the market does; the
   simulator then rewards us for exactly that disagreement.
4. **The worst plausible case still beats an average team.** Eleven opponents who draft as cleverly as we do
   **and** believe the market, with the market being right: 67% playoffs / 12% title (average team: 50% /
   8.3%, slot 1 alone in that world: 55% / 9%).

So yes: the headline number largely assumes that everyone else drafts by ADP and that our projections are
right. A defensible central estimate (a few sharp opponents, truth between our projections and the market)
is roughly **83–89% playoffs and 28–35% title** (rooms C3 and C3m graded by the blend), with a plausible range of about **67–96% playoffs and
12–55% title**. All of this still sits on uncalibrated season-simulator priors (game-to-game variance,
injury spells, streaming; see `season_simulator.md`), which move title odds by several points on their own.

## Decomposition table (slot 1, 3RR snake, 12 teams)

P(playoffs) / P(title), SE in brackets (SE over draft seeds of per-seed means; each per-seed mean pools 500
season sims, so the SE includes both draft and season noise). n = 100 draft seeds (60 for C3m/C11m).
Baselines: an average team has P(playoffs) = 6/12 = 0.500 and P(title) = 1/12 = 0.083.

| Room | Who drafts how | Truth = ours | Truth = 50/50 blend | Truth = market |
|---|---|---|---|---|
| adp_all | all 12 by noisy ADP (me too) | 0.542 (.016) / 0.104 (.006) | 0.539 (.013) / 0.103 (.005) | 0.539 (.012) / 0.103 (.005) |
| **A** A_sym | all 12 our optimizer, each on its own noisy view | **0.612 (.008) / 0.129 (.004)** | 0.582 (.007) / 0.110 (.004) | 0.547 (.010) / 0.094 (.004) |
| A_bpa | all 12 value-BPA (fast proxy), own views | 0.575 (.008) / 0.116 (.004) | 0.579 (.008) / 0.115 (.005) | 0.576 (.011) / 0.115 (.006) |
| **C** C11 | me optimizer; 11 opponents our optimizer (our-like views) | 0.752 (.006) / 0.251 (.005) | 0.718 (.006) / 0.217 (.004) | 0.679 (.008) / 0.176 (.006) |
| C6 | me optimizer; 6 such opponents, 5 ADP | 0.869 (.004) / 0.307 (.005) | 0.788 (.005) / 0.232 (.004) | 0.678 (.007) / 0.135 (.004) |
| C3 | me optimizer; 3 such opponents, 8 ADP | 0.921 (.003) / 0.385 (.006) | 0.830 (.005) / 0.282 (.005) | 0.694 (.006) / 0.155 (.004) |
| C11m | me optimizer; 11 opponents our optimizer on **market-like** views | 0.940 (.003) / 0.469 (.006) | 0.845 (.005) / 0.283 (.005) | **0.672 (.007) / 0.120 (.003)** |
| C3m | me optimizer; 3 market-informed optimizers, 8 ADP | 0.961 (.002) / 0.530 (.007) | 0.894 (.004) / 0.347 (.006) | 0.754 (.007) / 0.173 (.005) |
| **B** status quo | me optimizer; 11 noisy ADP (= `fantasy sim preseason`) | **0.965 (.001) / 0.548 (.006)** | 0.906 (.003) / 0.372 (.005) | 0.783 (.005) / 0.197 (.004) |

The column "Truth = ours" of row B reproduces the dashboard's pre-draft projection (93–99% / 40–55%).

### Implied edges (slot 1; paired differences, same draft seeds and season seeds)

| Edge | Definition | ΔP(playoffs) | ΔP(title) |
|---|---|---|---|
| Total status-quo edge | B − average team | +0.465 | +0.465 |
| **Edge from slot** (symmetric room) | A_sym − baseline, truth ours | +0.112 (.008) | +0.046 (.004) |
| ... same, truth market | A_sym − baseline, truth market | +0.047 (.010) | +0.011 (.004) |
| ... in an all-ADP room | adp_all − baseline | +0.042 (.016) | +0.021 (.006) |
| **Edge from strategy vs ADP** | B − C11 (opponents ADP instead of our optimizer) | +0.212 (.006) | +0.296 (.007) |
| ... vs market-informed sharp opponents | B − C11m | +0.025 (.003) | +0.084 (.008) |
| ... same, truth market | B − C11m (market) | +0.106 (.008) | +0.079 (.006) |
| Information edge (in-sample) | C11 − A_sym: me exact projections vs everyone noisy | +0.141 (.009) | +0.123 (.006) |
| ... with half the view noise (prior) | same, view sds halved (n = 30) | +0.020 (.018) | +0.055 (.009) |
| **Edge from in-sample grading** | B graded by ours − B graded by market | +0.182 (.004) | +0.351 (.004) |
| ... vs 50/50 blend | B ours − B blend | +0.059 (.002) | +0.176 (.003) |
| Edge over symmetric room, truth market | B − A_sym (market) | +0.236 (.011) | +0.103 (.007) |

Telescoping the status quo under our projections: 0.083 (average) → +0.046 slot → +0.123 information
(me exact vs everyone noisy) → +0.296 opponents drafting by ADP = 0.548. For P(playoffs): 0.500 → +0.112 →
+0.141 → +0.212 = 0.965. The pieces overlap with the grading question: under market truth the same chain is
0.083 → +0.011 → +0.082 → +0.021 = 0.197, i.e. most of the "information" and "strategy" edges in the
title odds are our projections grading themselves.

### Slot value in a symmetric room (A_sym, all 12 slots)

Truth = ours (SE ≈ 0.010 for P(playoffs), ≈ 0.004 for P(title)):

| Slot | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P(playoffs) | .612 | .589 | .542 | .510 | .488 | .481 | .451 | .482 | .448 | .450 | .455 | .491 |
| P(title) | .129 | .120 | .100 | .088 | .075 | .077 | .067 | .074 | .064 | .064 | .067 | .076 |
| E[rank] | 5.57 | 5.73 | 6.14 | 6.42 | 6.59 | 6.68 | 6.91 | 6.66 | 6.94 | 6.91 | 6.88 | 6.57 |

Truth = market: P(playoffs) .547 .540 .554 .514 .480 .503 .483 .503 .474 .464 .461 .478; P(title) .094
.096 .111 .094 .073 .088 .080 .084 .075 .071 .066 .068. The all-ADP room (adp_all) is flatter: P(playoffs)
.542 .506 .520 .493 .468 .499 .493 .477 .518 .487 .486 .510 (truth ours). The value-BPA proxy (A_bpa)
gives nearly the same curve as the optimizer room (.575 .575 .545 .512 .496 .498 .462 .483 .452 .463 .460
.479), so for slot-value questions the cheap proxy is adequate (slot 1: −0.037 (.009) P(playoffs), −0.013
(.005) P(title) vs A_sym).

Reading: the 3RR order (round 3 reverses, then alternates) gives slot 1 picks 1, 24, 36, 37, 60, 61, ...
— the first pick plus late-round-pairs — and in this model that is worth about +5 to +11 pp of playoff odds
over an average slot, depending on whose projections are right. Slots 7–11 are the worst seats.

## Method

**Common random numbers.** A *draft seed* s fixes: (i) the noisy market order that ADP drafters follow
(each player's draft position ~ Normal(market rank, sd = 2.5 + 0.18·rank), sd ×1.6 for players without Yahoo
ADP; the same prior as `fantasy draft simulate`, uncalibrated, backlog item "calibrate opponent drafting
noise"); (ii) each team's private projection view (below); (iii) which opponents are "smart" in mixed rooms
(a random permutation of slots 2–12; C3 ⊂ C6 ⊂ C11); (iv) the season-simulation seed. Every room and
every truth for seed s therefore shares the same randomness, and room/truth differences are paired over
seeds. The season simulator keys its randomness by player id and team id, so identical players on
identical teams draw identical seasons across rooms.

**Drafters.** `opt` = `DraftOptimizer.recommend` top pick (full strategy scan, as in the live draft);
`opt_bal` = the same optimizer restricted to the balanced strategy; `bpa` = best available by the
drafter's own value, keeping the starting lineup completable (the BPA baseline of `fantasy draft
simulate`); `adp` = next player in the noisy market order (no positional check, as in `simulate`). I am
always slot 1 with the full optimizer on our exact projections, except in A_sym/A_bpa/adp_all where I am
treated exactly like everyone else. Each optimizer still assumes the room drafts by ADP when it predicts
availability — that is "our strategy" as it would be used for real.

**Private views ("smart opponents").** A team's view = our projections with independent season-level
errors of the same form and size as the season simulator's projection-error prior: volume factor
exp(N(0, 0.08)) on all counting stats and attempts, per-stat factors exp(N(0, 0.08)) on FGA, FTA, 3PM, REB,
AST, STL, BLK, TO, FG% shift N(0, 0.015), FT% shift N(0, 0.03), PTS recomputed from makes
(`perturb_projections`). These sds are **priors** (`SimParams.vol_sd` etc.), not fitted. Without them a
symmetric room is deterministic (12 identical optimizers produce one draft). C3m/C11m use the same noise
around the *market-implied* projections instead of ours.

**Full optimizer vs proxy.** Measured (unloaded container, one core): full optimizer ≈ 0.26 s per pick (40 s
per 156-pick symmetric draft); balanced-only ≈ 0.037 s per pick (5.8 s per draft); BPA < 0.05 s per draft.
On 9 seeds the all-full-optimizer room (A_full) produced **exactly the same 156 picks** as the balanced
proxy in every seed (1,404/1,404 picks identical — the strategy scan never left "balanced"), so opponents use
the balanced optimizer and A_full was not run on more seeds.

**Market-implied truth** (`market_implied_projections`). Quantile map on total value: the player with the
r-th best Yahoo ADP gets the r-th best of our values among ADP players as his target value; one multiplier
m_i on all of his per-game counting stats and attempts (percentages and category mix unchanged) is solved so
that his G-score value hits the target (value is affine in m_i with the pool held fixed; 3 re-valuation
iterations; m clipped to [0.5, 2]). Players without ADP (163 of 456, all outside the draftable top) are
unchanged. Result: Spearman(value, ADP) over the top-156 ADP players rises from 0.777 (ours) to 0.999;
multipliers in the pool: median 1.03, 10–90% range 0.87–1.24, mean |log m| = 0.118; none clipped. Largest
upward moves: Khaman Maluach ×1.90, Jimmy Butler ×1.85, Darius Acuff Jr. ×1.63, Giannis Antetokounmpo ×1.53,
Trae Young ×1.47; largest downward: Fred VanVleet ×0.80, Myles Turner ×0.82, VJ Edgecombe ×0.82. The blend
truth is the linear 50/50 mix of the two stat lines. Each truth gets its own season-simulator context
(values, replacement line and lineup priority recomputed from that truth).

**Sample and SEs.** 100 draft seeds (0–99) for all main rooms, 60 seeds (0–59) for C3m/C11m, 9 seeds for
A_full, 30 seeds for the half-noise sensitivity; 500 season sims per league per truth. Reported SEs are
sd(per-seed values)/√n; paired SEs use per-seed differences. Compute: ≈ 45 min for the main run on 3
processes in a shared 4-core container (other agents were running), ≈ 15 min for C3m/C11m, ≈ 8 min for the
sensitivity.

Reproduce: `fantasy sim symmetric --drafts 100 --sims 500 --workers 3 --full-subset 9 --out var/symmetric.jsonl`
(resumable; `--summary-only` re-summarizes a results file). The C3m/C11m rooms and the half-noise run were
driven through `fantasy.sim.symmetric.run_experiment(..., rooms=["C3m", "C11m"])` and `build_experiment(...,
perturb={...})`.

## Limitations

- **Everything is graded by the season simulator**, whose variance priors (then a constant game CV, injury spells,
  streaming, projection error) were uncalibrated when this was run (game noise, projection error and spells
  are fitted since season-sim-0.4.0/0.5.0, `sim_calibration.md`); its in-season model gives every team the same passive
  management. Real leagues have trades, waivers and lineup skill differences; these compress (or widen)
  standings in ways not modelled here.
- **"Smart opponents" are a construct.** Their views are our projections plus prior-sized noise (C rooms) or
  market-implied projections plus the same noise (C*m rooms). The information edge C11 − A_sym depends
  strongly on that noise size (+0.123 title at the prior sd, +0.055 at half). Real sharp managers have their
  own projection systems, punt plans and positional habits.
- **Market-implied truth only moves volume.** The market's disagreement is attributed entirely to per-game
  volume; category mix, percentages, games played and positions stay ours. So even "market truth" agrees with
  us on the category profile of every player — part of why our optimizer still beats market-informed
  opponents in C11m under market truth. ADP also mixes in punt-build demand (e.g. a poor-FT% star drafted
  early by punt-FT teams) and injury pricing that a single projection set cannot represent; large multipliers
  (Butler ×1.85) are artefacts of forcing injury/role discounts into volume.
- **Neither truth is the truth.** "Ours" and "market" bracket the answer; the 50/50 blend is a convenience,
  not an estimate of the right weight (no backtest of our projections vs ADP exists yet; T-010).
- ADP drafters ignore positions and never react to the board; the ADP noise sd is a prior. A tighter real
  room (lower noise) would reduce the strategy edge.
- Only my slot = 1 was decomposed (status quo for other slots was not run); the symmetric slot curve covers
  all 12 slots.
- Rosters are frozen at the draft (13 players, no IL juggling beyond "an injured player frees a slot").

## Implications

- The dashboard's pre-draft projection should not be presented as a forecast. It is the optimistic end of a
  range. Better: headline a central scenario (e.g. truth = 50/50 blend with a few sharp opponents: ≈ 83–89%
  playoffs, 28–35% title) and show the range from the pessimistic case (sharp market-informed opponents,
  market truth: 67% / 12%) to the status quo (96% / 55%), with the symmetric-room slot value (61% / 13%) as
  "what the draft slot alone is worth".
- For decisions nothing changes: draft-time comparisons are relative and remain valid under the in-sample
  caveat. The biggest open question is whether our projections beat ADP out of sample (T-010 backtest) — it
  determines where between the "ours" and "market" columns reality lies, and it is worth up to ~35 pp of
  title odds in this table.
