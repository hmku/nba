# Value search: a learned season-outcome model + rollout search at draft time (`value-search-0.1.0`)

AlphaGo-style idea (the user's): simulate many full 12-team leagues, label each with the season
simulator, learn a fast **value model** (12 rosters → my P(playoffs), P(title), E[rank]), then at draft
time search over the optimizer's top candidates with rollouts that complete the draft, scored by the
value model. Code: `src/fantasy/valuesearch/` (`policies`, `features`, `model`, `search`, `datagen`,
`train`, `validate`). Artifact: `data/models/value_search/value-model-0.1.0.npz` + `.card.json`.

**Bottom line** (details and SEs below): the value model is accurate for *levels* (holdout R² 0.95–0.97
vs 2,000-season simulator estimates), but value search did **not** pass the adoption rule against the
current optimizer, so it ships **opt-in** (`--search value`) and the default `recommend` payload carries
an **advisory** `value_search` block. The same validation found that **best-player-available (BPA) beats
the current optimizer under the full season simulator**, most clearly in symmetric rooms — see
"Side finding".

## 1. Data (fitted: simulation; method below)
- Generator: `python -m fantasy.valuesearch.datagen` (seeds 100000–100715). Each league draws a regime:
  `adp` 40% (opponents noisy ADP, noise × U(0.6, 1.6); slot 1 and sometimes one more slot use the
  optimizer), `mixed` 25% (slot 1 + 2–10 random slots use the optimizer), `sym` 20% (all 12 use the
  optimizer — the user's "what if everyone drafts like us" worry), `proxy` 15% (others ADP / GreedyCat /
  BPA). Optimizer teams use `OptimizerParams(strategy="balanced")` (≈8× faster than the scan, which picks
  balanced in practice; optimizer-diagnosis handoff) plus **diversity**: softmax over the top-5 scores with
  temperature U(0, 0.03) expected category wins, and with prob 0.1 a uniform choice among the top 5.
- Labels: the season simulator (`season-sim`, default `SimParams`, our projections, 160 seasons per
  league): P(playoffs), P(title), expected final rank, expected regular-season category wins — **for all
  12 teams**, so each league gives 12 rows (features are computed from each team's point of view).
- Size: **716 leagues = 8,592 rows**, ≈25 min wall on 3 workers (the box was shared with two other agents;
  ≈5.9 s CPU per league for drafting, 0.7 s for 160 seasons). Learning curve on the holdout below: 290 → 580
  leagues improved P(title) RMSE only 0.0234 → 0.0217, so data generation was stopped at 716 to free CPU
  for validation.
- Holdout: 100 independent leagues (seeds 900000–900099, same generator) labelled with **2,000 seasons**.

## 2. Features (permutation-invariant over opponents) — `features.py`, `vs-features-1`, 69 features
Per team, from the draft category model (effective lines, weekly Normal moments): category win
probabilities against **each specific opponent** `P[i,j,c]` and matchup win probabilities `M[i,j]`
(Poisson-binomial over 9 categories), averaged over opponents (9 + 2), my `M` vs opponents **sorted**
(11), opponents' field strengths **sorted** (11), my strength rank and gap to the best opponent, z-scored
category means and variances vs the league (18), games-played sums incl. playoff-week availability and
injured-out counts (mine and opponents' mean), value sums, and summary shape features (min category
share, #categories above 50%, …). Only sorted vectors and means over opponents enter, so permuting
opponents leaves a team's features unchanged (tested to 1e-12).

## 3. Model — `model.py`, `train.py`
scikit-learn `HistGradientBoostingRegressor` (400 iterations, lr 0.05, 15 leaves, min leaf 40, l2 1.0;
squared error on the Monte Carlo labels, which are unbiased), one per target. The fitted trees are
exported to a plain `.npz` and evaluated in numpy (no sklearn or pickle at draft time; max |numpy −
sklearn| = 2e-13). sklearn 1.5.2 is a pinned optional extra (`pip install -e ".[valuesearch]"`), only for
training. Artifact 0.57 MB.

| target | league-split test (72 leagues, 160-season labels): RMSE / R² | **holdout vs 2,000-season simulator**: RMSE / R² / corr | slot-1 rows (optimizer team) holdout RMSE | MC noise of a 160-season estimate |
|---|---|---|---|---|
| P(playoffs) | 0.057 / 0.933 | **0.042 / 0.961 / 0.980** | 0.030 | 0.036 |
| P(title) | 0.029 / 0.921 | **0.023 / 0.953 / 0.976** | 0.046 | 0.020 |
| E[rank] | 0.41 / 0.951 | **0.33 / 0.966 / 0.983** | 0.26 | – |
| E[cat wins] | 1.94 / 0.957 | **1.55 / 0.971 / 0.985** | 1.44 | – |

Reading: across the league the model is about as accurate as a ~100–160-season simulation of the same
league, in ~0.2 ms instead of ~0.25 s. It is worse on the *top* teams' P(title) (slot-1 RMSE 0.046).
**What matters for search is resolving small differences between candidate rosters in the same room**,
and there it is weak: on the validation leagues (three policies' leagues per room), the correlation
between model-predicted and simulated *within-room* differences was **0.20 for P(title) and 0.39 for P(playoffs) in ADP rooms (sign agreement 60% / 63%), 0.46 / 0.85 in
symmetric rooms (65% / 84%)** (200 rooms × 2 alternative policies each; level RMSE on these leagues 0.054
for P(title), 0.014 for P(playoffs) in ADP rooms)
(the simulated differences themselves carry 400-season noise, so these correlations are attenuated, but
the sign agreement on P(title) is only modestly above a coin flip).

## 4. Search — `search.py`, `fantasy draft recommend --search value|advise`
For each of the top-N optimizer candidates (default 6) and M rollouts (default 48; 24 with
`--opponents ours`): take the candidate, complete the draft — opponents by the opponent model (`adp`:
next available of a noisy-ADP room order, default; `ours`: every opponent uses GreedyCat, a one-step
category-model proxy of our strategy) and my later picks by GreedyCat — then featurize the 12 rosters and
score my team. Rollout m uses the same room for every candidate (common random numbers), so candidate
differences are paired (SE = sd/√M). **Decision rule:** the best mean replaces the optimizer's #1 only if
its paired gain is ≥ 1 SE (z_min = 1.0), a prior to avoid churning on noise.

**Objective** (`--objective blend|title|playoffs|rank`, default `blend = 0.7·P(title) + 0.3·P(playoffs)`):
a **prior**, not fitted. Rationale: the user's goal is the title, so it dominates; P(playoffs) is a
prerequisite, is predicted more accurately for strong teams (slot-1 holdout RMSE 0.030 vs 0.046) and is
smoother, so a minority weight regularises the noisier title signal. No grid over the weights was run
(compute went to the validation).

**Latency** (`fantasy draft recommend --search value`, real CLI on a mock draft, 4-core box at load
average ≈ 8 from other agents): wall **2.1–4.2 s** with `adp` opponents and **1.8–4.6 s** with `ours`
(worst: pick 84 with `ours`; pick 1: 4.2 s / 4.6 s; default without search 1.5–3.6 s). Search alone:
0.2–0.7 s (`adp`), 0.1–2.6 s (`ours`). All within the 10 s budget.

## 5. Validation (decides adoption) — `validate.py`
Paired simulated drafts, my slot 1, 3RR, 13 rounds. Per draft seed s, the noisy-ADP room order, the
opponents' private projection views and the season seed are fixed, and my three policies — **optimizer**
(`DraftOptimizer`, default params = `fantasy draft recommend`), **value search** (default `SearchParams`,
adp opponent model, z_min 1), **BPA** (best value keeping the lineup completable; `draft simulate`
baseline) — each draft against that room. Every final league is graded by the **full season simulator**
(400 seasons, same seed for all policies), not by the value model. Means ± SE over seeds; differences are
paired (± paired SE, z in parentheses).
- (i) **ADP room**: opponents noisy ADP; graded with our projections.
- (ii) **Symmetric room**: all 11 opponents use our strategy — balanced optimizer on their own perturbed
  projection view (`fantasy.sim.symmetric.perturb_projections`, the optimism-decomposition agent's C11
  proxy: as smart as us, independent errors); graded with our projections.
- (iii) **Market-is-right truth**: the (i) drafts graded with `fantasy.sim.market_truth.market_implied_projections`
  (ADP order made the true value order by rescaling each ADP player's volume; built by the
  optimism-decomposition agent, reused here).

**(i) ADP room, our projections** (n = 200 paired drafts)

| metric | optimizer | value search | BPA | VS − opt (z) | opt − BPA (z) | VS − BPA (z) |
|---|---|---|---|---|---|---|
| P(playoffs) | 0.966 ± 0.001 | 0.967 ± 0.001 | 0.972 ± 0.001 | +0.0015 ± 0.0009 (1.61) | -0.0060 ± 0.0008 (-7.27) | -0.0045 ± 0.0009 (-4.89) |
| P(title) | 0.551 ± 0.004 | 0.560 ± 0.004 | 0.557 ± 0.004 | +0.0087 ± 0.0035 (2.48) | -0.0063 ± 0.0037 (-1.72) | +0.0024 ± 0.0035 (0.67) |
| E[rank] | 1.854 ± 0.014 | 1.844 ± 0.015 | 1.796 ± 0.012 | -0.0095 ± 0.0105 (-0.91) | +0.0580 ± 0.0101 (5.73) | +0.0485 ± 0.0107 (4.54) |
**(ii) symmetric room (11 opp. = our optimizer on own views)** (n = 200 paired drafts)

| metric | optimizer | value search | BPA | VS − opt (z) | opt − BPA (z) | VS − BPA (z) |
|---|---|---|---|---|---|---|
| P(playoffs) | 0.744 ± 0.005 | 0.752 ± 0.005 | 0.805 ± 0.003 | +0.0080 ± 0.0050 (1.59) | -0.0611 ± 0.0047 (-12.9) | -0.0531 ± 0.0045 (-11.79) |
| P(title) | 0.245 ± 0.003 | 0.251 ± 0.004 | 0.282 ± 0.004 | +0.0061 ± 0.0039 (1.57) | -0.0374 ± 0.0043 (-8.77) | -0.0313 ± 0.0044 (-7.06) |
| E[rank] | 4.373 ± 0.042 | 4.337 ± 0.041 | 3.862 ± 0.032 | -0.0365 ± 0.0432 (-0.85) | +0.5112 ± 0.0430 (11.88) | +0.4747 ± 0.0422 (11.24) |
**(iii) ADP room, market-is-right truth** (n = 200 paired drafts)

| metric | optimizer | value search | BPA | VS − opt (z) | opt − BPA (z) | VS − BPA (z) |
|---|---|---|---|---|---|---|
| P(playoffs) | 0.784 ± 0.003 | 0.795 ± 0.003 | 0.799 ± 0.003 | +0.0111 ± 0.0027 (4.19) | -0.0151 ± 0.0025 (-5.96) | -0.0039 ± 0.0027 (-1.46) |
| P(title) | 0.196 ± 0.003 | 0.207 ± 0.003 | 0.200 ± 0.003 | +0.0105 ± 0.0027 (3.87) | -0.0034 ± 0.0028 (-1.2) | +0.0071 ± 0.0027 (2.67) |
| E[rank] | 3.995 ± 0.029 | 3.910 ± 0.030 | 3.886 ± 0.028 | -0.0857 ± 0.0229 (-3.74) | +0.1095 ± 0.0222 (4.94) | +0.0238 ± 0.0230 (1.03) |

Search diagnostics (5,200 value-search decisions): it kept the optimizer's #1 at 75% of picks in ADP rooms
and 67% in symmetric rooms, yet produced a different 13-man roster in 97% / 99.5% of drafts. Per-call
search time in these runs: mean 0.8 s, max 7.1 s (ADP) / 12.8 s (symmetric) — the maxima occurred with
3 validation workers plus two other agents' jobs on 4 cores (load average 6–9); the CLI measurements in
§4 are the latency reference.

## 6. Adoption decision
Rule (set before the run): change the default only if value search beats the optimizer on **both**
P(title) and P(playoffs) with paired z ≥ 2 in (i), and does not lose in (ii)/(iii).

| check | result | pass? |
|---|---|---|
| (i) P(title) VS − opt | +0.0087 ± 0.0035 (z 2.48) | yes |
| (i) P(playoffs) VS − opt | +0.0015 ± 0.0009 (z 1.61) | **no** |
| (ii) doesn't lose | +0.008 ± 0.005 P(po), +0.006 ± 0.004 P(title) (z ≈ 1.6) | yes |
| (iii) doesn't lose | +0.011 ± 0.003 P(po) (z 4.2), +0.011 ± 0.003 P(title) (z 3.9) | yes |

**Decision: not adopted as the default ordering.** `fantasy draft recommend` keeps the optimizer's
order; its payload now carries an **advisory** `value_search` block by default (`--search advise`,
`DEFAULT_SEARCH` in `search.py`; adds ≈0.5–1 s). `--search value` is the opt-in that lets value search
pick the top; `--search none` restores the old payload exactly. Value search is never worse than the
optimizer in any regime and is clearly better under the market-is-right truth (+1.1 pp title, +1.1 pp
playoffs), so it is a reasonable opt-in, but the P(playoffs) gain in the main regime is not established
and the value model's within-room resolution is weak (§3). A failure of the search (e.g. missing
artifact) only adds a warning; it never blocks a recommendation.

## Side finding: BPA beats the current optimizer under the full season simulator
Not the question asked, but the most decision-relevant result of the run (same 200 paired drafts, full
season simulator):

| regime | opt − BPA P(playoffs) | opt − BPA P(title) | opt − BPA E[rank] (lower = better) |
|---|---|---|---|
| (i) ADP room | −0.0060 ± 0.0008 (z −7.3) | −0.0063 ± 0.0037 (z −1.7) | +0.058 ± 0.010 (z 5.7) |
| (ii) symmetric room | **−0.061 ± 0.005 (z −12.9)** | **−0.037 ± 0.004 (z −8.8)** | +0.51 ± 0.04 (z 11.9) |
| (iii) market-is-right | −0.015 ± 0.003 (z −6.0) | −0.003 ± 0.003 (z −1.2) | +0.11 ± 0.02 (z 4.9) |

The optimizer's +0.07 expected-category-wins edge over BPA (optimizer-diagnosis handoff) is measured by the
same league-average category model it optimizes; the season simulator — specific opponents, injuries as
spells, daily lineup cap, playoffs — does not reproduce it. In symmetric rooms the gap is large: the
optimizer waits on players its ADP availability model says will last, but opponents who also use the
optimizer take them. Value search recovers only a small part (it re-ranks the optimizer's candidates).
**Recommendation for the lead/user (not changed here):** treat BPA-by-value as at least as good as the
optimizer's #1 for the real draft; investigate before trusting the optimizer's reaches (e.g. require the
optimizer's pick to beat BPA by a margin, or make the availability model room-aware). Caveat: in-sample
w.r.t. our projections and uncalibrated simulator priors; the optimism-decomposition work
(`fantasy.sim.symmetric`) is the place to cross-check.

## Limitations
- **In-sample**: training labels, validation (i)/(ii) and the search all use our projections and the
  same season simulator (then-uncalibrated `SimParams` and constant game-CV priors, calibrated since). (iii)
  is the only out-of-sample-style check and it is a proxy.
- **Known mismatch (2026-10-19): noise model.** `value-model-0.1.0` was trained on rosters, values and labels
  produced under the old constant-CV game noise (valuation-0.2.x, season-sim-0.4.0). Since valuation-0.3.0 /
  season-sim-0.5.0 both use the one fitted variance function (`fantasy.model.noise`). Its features (category
  model moments) are now computed under the new noise, so its scale is slightly off. It is advisory only and was
  not retrained (the draft-time effect of the noise change is small: `sim_calibration.md` section 8).
- The value model sees rosters only through the category model's Normal moments and availability sums;
  it cannot know about schedule details beyond what the 160-season labels average out.
- Rollouts complete my roster with GreedyCat (no lookahead), not with the optimizer; the search therefore
  favours candidates that work with myopic completion. The `ours` opponent model is a proxy as well.
- Training distribution: slot-1-like optimizer teams are ~1/12 of rows; the model is least accurate for
  the strongest teams, which is where my team usually is (slot-1 P(title) RMSE 0.046).
- Blend weights, z_min = 1, N = 6, M = 48 are priors (not tuned).
- Validation used 400 seasons per league; per-draft simulation noise is included in the SEs.

## Reproduce
```bash
pip install -e ".[dev,valuesearch]"
python -m fantasy.valuesearch.datagen --n 716 --seed0 100000 --seasons 160 --workers 3 --out train.jsonl
python -m fantasy.valuesearch.datagen --n 100 --seed0 900000 --seasons 2000 --workers 3 --out holdout.jsonl
python -m fantasy.valuesearch.train --data train.jsonl --holdout holdout.jsonl --out data/models/value_search/value-model-0.1.0.npz
python -m fantasy.valuesearch.validate --seeds 0:200 --regimes adp,sym --model data/models/value_search/value-model-0.1.0.npz --out val.jsonl
python -m fantasy.valuesearch.validate --summary --model x --out val.jsonl
```
Set `OMP_NUM_THREADS=1` on a shared box (OpenMP oversubscription made HGB training ~20× slower).
