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
between model-predicted and simulated *within-room* differences was <!--REL-->
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

<!--TABLES-->

## 6. Adoption decision
<!--DECISION-->

## Side finding: BPA beats the current optimizer under the full season simulator
<!--BPA-->

## Limitations
- **In-sample**: training labels, validation (i)/(ii) and the search all use our projections and the
  same season simulator (uncalibrated `SimParams`/`GAME_CV` priors, being calibrated concurrently). (iii)
  is the only out-of-sample-style check and it is a proxy.
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
