# Draft room calibration on the real 2026 draft (T-016, first sample)

_2026-10-20. Research only: **no model default was changed.** Script: `docs/research/draft_calibration_2026.py`
(helpers `src/fantasy/draft/calibration.py`, tests `tests/test_draft_calibration.py`). Raw outputs:
`var/calibration/results.json` (git-ignored; regenerate with the two commands in the script docstring, ~2.5 min)._

## Question
The draft's availability model rests on two uncalibrated priors (`docs/research/draft_policy.md`, `model-eval` skill §3):

1. **ADP spread.** An opponent's pick position is modelled as `Normal(market_rank, sd)` with
   `sd = (2.5 + 0.18 · market_rank) · sd_mult`. `sd_mult` is 1.6 for players without a Yahoo ADP
   (`fantasy.draft.availability`). The question is whether the spread grows with ADP, and by how much.
2. **Room composition.** The default `recommend-0.2.0` availability (`sim:mixed x300`) assumes 3 of the 11 opponents are
   "sharp", i.e. they take the best available player by a noisy view of *our* values with a 30 % lean towards the market
   (`fantasy.draft.room_sim`). The other 8 opponents are modelled as noisy-ADP drafters.

The real draft is now in the DB: `nba.l.44495-2026`, 156 picks, 12 teams, custom 3RR order, and we pick from slot 1.
There are 143 opponent picks. Market rank = rank of Yahoo's effective ADP (`data/manual/adp_yahoo_2026_27.csv`, fetched
2026-10-18), exactly as the live model used it. The live context was rebuilt from the repo at this commit, and the
projections and ADP are unchanged since the draft. As a check, the replayed `sim:mixed` availability matches the 106 stored
`prob_available_next` values to within 0.0003, which is rounding.

**Sample-size warning.** This is one draft: 143 opponent picks by 11 managers, of whom 13 picked each. Model-level
questions such as the spread and the share of sharp drafters are reasonably identified. Per-manager questions are only
weakly identified. Calibration of the stored availability numbers rests on 7 decision points, and any bootstrap over 7
clusters understates the uncertainty. Read those CIs as rough.

## Results

### 1. Pick − market-rank residuals (opponent picks; fitted on this draft)
| Market rank | n | mean | sd (95 % CI) | robust sd = 1.48·MAD (95 % CI) | prior sd at bin mean |
|---|---|---|---|---|---|
| 1-24 | 22 | +0.1 | 2.8 (1.5-3.7) | 1.5 (0.7-2.2) | 4.8 |
| 25-60 | 34 | +0.6 | 7.6 (5.9-8.9) | 6.7 (3.7-10.4) | 10.2 |
| 61-108 | 44 | +2.3 | 11.1 (8.8-12.9) | 10.4 (6.7-14.1) | 17.8 |
| 109+ | 43 | −7.2 | 23.0 (12.3-33.0) | 11.9 (7.4-17.8) | 27.3 |
| all | 143 | −1.3 (SE 1.3) | 15.0 | 8.9 | |

80 % of picks landed within ±12 of market rank. The tails are heavy: Gui Santos went at #148 with market rank 262
(residual −114), Tre Jones at #131 (rank 189), and Jusuf Nurkić at #152 (rank 121). The negative mean in the 109+ bin is
selection, not bias: only the late players who went *early* are inside 156 picks.

Selection and rank-compression make raw SDs hard to compare with the prior. So I ran a **posterior-predictive check**:
1,000 drafts simulated under the prior all-ADP room (`simulate_adp_draft`, which uses the same sampler as `draft simulate`),
with the same statistics computed on the same pick positions (ours excluded). Under the prior (scale 1.0), the observed SD
falls at the **0.0-0.9th percentile in every bin**, and the robust SD at the 0.0-3.1th percentile. In other words, the real
room was much tighter around ADP than the prior assumes. Scaling the prior spread down to **0.5-0.6×** puts the observed
SDs at the 38th-97th percentiles. The top bin (picks 1-24) is tighter still: robust SD 1.5 versus about 3 simulated even at 0.6×.

### 2. Does the spread grow with ADP? Yes, but more slowly than assumed (fitted)
Two independent fits agree.

- **One-step pick likelihood.** This is the main method, because it is the model the room simulator actually uses. For each
  opponent pick k, I computed the probability that an ADP drafter takes exactly that player from the board as it stood. The
  drafter takes the smallest latent among the available players, with latents truncated to > k − 0.5, as in `room_sim`. The
  probability comes from numerical integration (`p_first_min`), so there is no Monte Carlo floor. I evaluated it on a grid
  of `sd = floor + slope · market_rank`.
  - MLE: **floor 1.0, slope 0.12**, with profile 95 % CI for the slope 0.10-0.12 (grid step 0.02).
  - The joint 95 % region spans floor 0.5-2.5, so the floor is poorly identified, but the slope is 0.10-0.14 throughout.
  - Prior (2.5, 0.18) vs MLE: 2·ΔlogL = 61.3. The fitted spread raises the log-likelihood of 80 % of the picks, by a mean
    of +0.21 nats per pick (SE 0.04 over picks, z ≈ 5).
- **Censored-Normal (Tobit) fit** of pick on market rank, with undrafted players censored at 156.5 and our 13 players removed:
  - floor 0.93 ± 0.51, slope 0.126 ± 0.012, bias +0.5 ± 0.4 (Hessian SEs).
  - Dropping the 3 largest outliers gives floor 1.9 ± 0.8 and slope 0.101 ± 0.015.
  - Under the prior room, the same estimator recovers the prior: median floor 2.56, slope 0.181 over 200 simulated drafts.
    So the Tobit is not biased by rank compression, and the observed slope is below all 200 simulated values.

Conclusion: the spread does grow roughly linearly with ADP, so the functional form is fine. Its size is about **0.6-0.7× the
prior**: sd ≈ 1 + 0.12·rank, versus 2.5 + 0.18·rank. That is about 8 vs 13 at rank 50 and about 16 vs 26 at rank 125.
This is one league and one ADP snapshot.

`NO_ADP_SD_MULT = 1.6` cannot be tested, because every drafted player had a Yahoo ADP.

### 3. Room composition: no evidence for "sharp" drafters (fitted, with an important caveat)
- **Log-likelihood of the 143 picks:**
  - all-ADP room, prior spread: −425.2
  - mixed room (3/11 sharp, prior spread): −427.7
  - all-sharp: −601.5
  - all-ADP, fitted spread: −394.5

  Mixing in sharp drafters at the fitted spread gives a **MLE sharp weight of 0.00, 95 % CI [0, 0.10]**. The prior
  3/11 = 0.27 is excluded.
- **Per manager:** the log Bayes factor of sharp vs ADP drafter runs from −4.2 to −37 for every one of the 11 opponents.
  With the 3/11 prior, the posterior P(sharp) is ≤ 1 % for each of them.
- **Descriptive:** the typical pick was the 5th-best available by market rank (median) but the 15th-best by our value.
  21 % of picks were exactly the next player in the market order, 43 % were within the market top 3, and 21 % were within
  our value top 3.
- **Caveat:** "sharp" here means "drafts by *our* projections". A manager who is good by another system, such as Yahoo's own
  rankings or their own spreadsheet, would look like a noisy-ADP drafter or like noise. What the data does say is that
  nobody in this room drafted like our model, and that Yahoo's market order explains the picks far better. That is
  plausible in a Yahoo league, where the draft room's default list and autodraft follow Yahoo's rankings, which are
  themselves closely tied to Yahoo ADP. Whether any managers autodrafted is unobserved.

### 4. Per-manager tendencies (weak evidence)
Residuals were standardised by the fitted spread (z = residual / (1 + 0.12·rank)), with 13 picks per manager. A
Kruskal-Wallis test of "same distribution for all 11" gives p = 0.035. That is weak evidence of heterogeneity, and it
comes with 11 groups and no correction for having looked.

| Slot | Team | mean z (SE) | median residual | reaches (z<−1) / waits (z>1) |
|---|---|---|---|---|
| 3 | Thomas's Dandy Team | **−0.70 (0.19)** | −8 | 5 / 0 |
| 12 | Joshua's Excellent Team | −0.32 (0.32) | 0 | 5 / 1 |
| 4 | Haagen Daz Center | −0.21 (0.17) | −5 | 0 / 1 |
| 11 | SCOTTIER BARNEY (Daniel's at draft time) | **+0.64 (0.22)** | +6 | 0 / 3 |
| 7 | Andrew's Awesome Team | +0.33 (0.30) | +1 | 1 / 2 |
| 8 | Ed's Extraordinary Team | +0.18 (0.13) | +2 | 0 / 0 (tightest to ADP) |
| others (2, 5, 6, 9, 10) | | −0.09 to +0.15 | | |

Read this as: slot 3 reached ahead of ADP consistently, slot 11 tended to let players fall, and slot 8 hugged ADP.
None of it is actionable in-season (it describes draft behaviour only). It is a weak prior for next year's draft if the
same managers return.

### 5. Were the live availability numbers calibrated? (stored `draft_recommendations`)
Each recommendation stored P(available at my next pick if I pass now) (`prob_available_next`) and, for recommendations
prepared before my turn, P(available at my decision pick) (`prob_available_at_pick`). Outcome = not taken by an opponent
in between.

| Forecast set | n (decisions) | Brier, live `sim:mixed` | Brier, old analytic `adp` | diff (cluster-bootstrap 95 % CI) |
|---|---|---|---|---|
| stored `prob_available_next`, candidates only | 106 (7) | 0.121 | 0.104 | +0.017 (−0.010, +0.042) |
| stored `prob_available_at_pick` | 116 (6) | 0.101 | 0.102 | −0.000 (−0.008, +0.005) |
| replay: every board player at the same 7 decisions, 0.02 < p < 0.98 | 451 (7) | 0.105 | 0.097 | +0.008 (−0.001, +0.017) |
| replay, all-ADP room at the **fitted** spread (in-sample) | 451 (7) | 0.090 | | −0.015 vs mixed (−0.026, −0.002) |

Reliability of the stored `prob_available_next`:
- predicted 0.1-0.3 (n = 28) → observed **0.04**
- predicted 0.0-0.1 (n = 21) → observed 0
- predicted 0.3-0.7 (n = 43) → roughly right (0.37-0.63)
- predicted ≥ 0.7 (n = 14) → observed 0.86-1.0

So the live model was too spread out: players it gave a 20 % chance to fall essentially never fell. With the fitted
spread, the replay's 0.1-0.3 bin is calibrated (0.19 predicted vs 0.19 observed, n = 36). Its 0.5-0.7 bin still
over-predicts (0.60 vs 0.49, n = 45, about 1.5 SE).

The mixed room did **not** forecast better than the old analytic model on this draft. The point estimate is slightly worse,
but the CIs include 0. Its simulated advantage (`draft_policy.md`) was measured in a world that contains sharp drafters,
and this room had none.

## What can and cannot be concluded
- **Can (moderate confidence):**
  - This room followed Yahoo's market order much more tightly than our prior: about 0.6-0.7× the spread, with sd growing
    about 0.12 per rank (fitted, LR 61, per-pick z ≈ 5).
  - Nobody drafted like our model: sharp weight 0, CI [0, 0.10].
- **Cannot:**
  - Generalise to other leagues or years. One league and one ADP snapshot, and Yahoo's default ranking list probably drove
    the tightness.
  - Tell "no sharp managers" apart from "sharp by a different value system".
  - Pin down the floor (0.5-2.5), `NO_ADP_SD_MULT`, or individual manager styles beyond the two extreme slots.
  - Say whether our draft *outcome* would have differed. That needs the policy harness, see below.
- **In-sample:** the fitted-spread Brier gain (−0.015) was evaluated on the same draft it was fitted on. There is no
  out-of-sample check until another real draft exists. Mock drafts do not count, because their bots are Yahoo's autodraft.

## Recommendations (no default changed; the user decides)
1. **Keep the defaults for now.** The draft is over and nothing in-season uses the room model.
2. **Before the next draft**, add a room world `W_fit` to the policy harness (`fantasy.draft.policy_eval`), with all-ADP
   drafters and sd = (1.0 + 0.12·rank)·sd_mult. Re-grade the default policy (`sim:mixed`) against
   `sim:adp` at the fitted spread, in W_fit and in W_mixed.
   - If the fitted-spread policy is no worse in W_mixed and better in W_fit, propose switching the availability room to
     `adp` with `SD_FLOOR = 1.0`, `SD_SLOPE = 0.12` (an `optimizer` version bump plus tests).
   - Until then, treat `availability_model` sd as a **prior with one fitted data point**, not as fitted.
3. **Mid-draft sanity rule (prior, cheap):** treat a displayed `prob_available_next` in 0.1-0.3 as "probably gone". On this
   draft it materialised 1 time in 28.
4. Re-run this script after every real Yahoo draft we can observe, for example if the user joins a second league. Pool the
   picks across drafts and refit the floor and slope with a per-draft random effect before trusting either beyond one league.
