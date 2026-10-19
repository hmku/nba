# Draft policy: which decision rule to use tonight (`draft-policy-0.1.0`, `policy-eval-0.2.0`)

_2026-10-19, draft-policy research agent. Everything quantitative here is **fitted by simulation** (method in §2;
sample sizes and paired standard errors in every table) unless labelled prior/guess._

## TL;DR (recommendation for the lead/user; the default was NOT changed)

- **Primary world W_mixed** (the user's description of the league: 3 sharp drafters on random seats + 8 casual
  noisy-ADP drafters), graded by the full season simulator on our projections (the world the optimizer itself
  assumes): the current default optimizer loses **−2.6 pp P(playoffs)** to plain BPA (z −11) and gains only
  +0.5 pp P(title) (z 1.4).
- **The cause is the availability model, not the objective.** GreedyCat (one-step expected-category-wins, no
  lookahead, no availability model) ties BPA on P(playoffs) (−0.2 pp, z −1.1) and gains +0.6 pp P(title); the
  same optimizer with **availability simulated from the room W** (`availability="sim"`) recovers +2.1 pp
  P(playoffs) over the default (z 9.9) and is within 0.5 pp of BPA. The analytic `prob_available` treats a
  sampled Normal(ADP) position as the pick number and knows only ADP drafters; it under-estimates how long
  players last, so the optimizer reaches (mean ADP of its roster 84 vs BPA 90 in W_mixed) and passes value it
  would have got anyway.
- **Adopted as the default 2026-10-19 (user-approved; optimizer-0.4.0 / recommend-0.2.0,
  `docs/handoffs/2026-10-19-default-policy-switch.md`).** Recommended configuration for tonight:
  `fantasy draft recommend --search value --availability sim --room mixed` ("vs_sim": value search re-ranking
  the candidates of the sim-availability optimizer). In W_mixed vs the current default: **+1.9 pp P(playoffs)
  (z 8.7), +1.4 pp P(title) (z 4.7)** on our projections; also ≥ the default in W_adp (+0.3 / +0.4 pp) and W_sym
  (+5.0 / +2.0 pp). Better than BPA on P(title) in all 9 world × truth cells (+0.9 to +5.4 pp); its worst
  P(playoffs) cell vs BPA is −1.3 pp (W_sym, z −3.4; −0.7 pp in W_mixed). ≈2 s compute (≈5 s wall) per pick.
  Fallback with no value-model dependency: `--availability sim --room mixed` alone ("opt_sim").
- **Caveat that decides between them and the old default:** if the market (ADP) is closer to the truth than our
  projections, the default optimizer's reaches are a hedge: graded under market-implied truth it beats opt_sim
  on P(title) by 2–6 pp. vs_sim keeps most of that hedge (vs the default under market
  truth: −0.1 pp P(title) in W_mixed, −0.2 pp in W_adp, −1.4 pp in W_sym, z −3.8) while opt_sim keeps little
  (−2.8 / −1.7 / −5.5 pp). We have no out-of-sample evidence that ADP beats our 50/50 ours+ESPN blend; market truth is a stress test.
- Picks on the current board (deterministic ADP room, slot 1): **#1 Jokić and #36 Derrick White under every
  rule; #24 changes: default optimizer Kawhi Leonard → recommended config Bam Adebayo** (BPA by value: Kon
  Knueppel). Details §8.

- **Room-sim self-pick correction (§11, 2026-10-19): not adopted.** Letting the simulated me skip its own
  current pick (`room_self="skip"`) or take the rollout's planned player (`"plan"`) instead of pre-committing the
  best-value player is not better in W_mixed/truth ours for vs_sim (skip −0.3 pp P(playoffs), z −2.0 with
  value-model-0.1.0, a tie with 0.2.0; plan a tie under both); the options remain opt-in for research.

## 1. Code version and inputs
Graded on main at `4716e7b` + this note's commit (room model incl. the fitted sharp-drafter market lean) (numerically identical to `b7faa1b`, checked: same optimizer payloads at
6 boards and bit-identical season-sim grades): **valuation-0.3.0, optimizer-0.3.0 default path (0.3.1 adds the
opt-in `availability="sim"`), season-sim-0.5.0 with the unified game-noise model noise-var-1.0**, projections
`data/processed/projections_2026_27.csv` sha256 `6acf9cce…` (50/50 ours+ESPN blend), value model
`value-model-0.1.0`. Each run used a frozen copy of `src/` + `data/` so concurrent pushes could not leak in.
An earlier full run on `d07f903` (valuation-0.2.3, season-sim-0.4.0, fitted GAME_CV) is the cross-version check
in §7.

## 2. Method — one world W for planning and grading
The user's consistency principle: pick a world W (how the 11 opponents draft + what we maximise), let the
optimizer plan in W and grade every technique in W.

**Rooms (W).** `fantasy.draft.policy_eval.build_room`, seed-keyed (common random numbers):
- **W_adp**: every opponent takes the next available player of one noisy market order (position ~ Normal(market
  rank, adp_sd·sd_mult), sorted; = `draft simulate`).
- **W_sym**: every opponent is our balanced optimizer on its own perturbed projections
  (`fantasy.sim.symmetric.perturb_projections`: as smart as us, independent errors).
- **W_mixed (primary)**: 3 W_sym drafters on seats drawn per room (`rng([seed, 13])`), 8 W_adp drafters sharing
  one market order.

**Grader.** Each final 12-team league is played by the full season simulator (400 seasons; injuries as spells,
daily lineup cap, schedule, playoffs) with the same season seed for every arm. Truth `ours` = our projections
plus the simulator's fitted projection error (the consistent-W grade). Robustness truths: `market`
(`market_truth.market_implied_projections`: ADP order made the true value order) and `mid` (50/50 of the two
stat lines). n = 200 paired drafts per world (value search in W_adp/W_sym: 120), slot 1 (3RR picks 1, 24, 36, 37, 60, …).
Differences are paired over seeds (SE = sd of per-seed difference/√n).

**Arms** (all lineup-feasible; balanced strategy — the default 8-strategy scan chose `balanced` at 676/676
decisions on d07f903 and 195/195 decisions with sim availability on the current code):
| arm | what it is | isolates |
|---|---|---|
| BPA | best available by our value (G-score) keeping the lineup completable (`--policy bpa`) | baseline |
| optimizer | current default `recommend`: rollouts, Normal(ADP) availability, expected cat wins vs league-average opponent | – |
| GreedyCat | one-step max expected cat wins (roster + player + market filler), no lookahead, no availability model | objective |
| opt_sim | optimizer with `availability="sim"`, room = the W it is graded in | availability |
| opt_sim_matchup | opt_sim with objective P(win the weekly matchup) | objective |
| value search | `--search value` (re-ranks the default optimizer's candidates by learned season value + rollouts) | – |
| vs_sim | `--search value --availability sim --room W` | – |

**availability="sim"** (`src/fantasy/draft/room_sim.py`, opt-in): from the current board, 300 Monte Carlo
continuations of the room to each of my future picks. ADP drafters: latent positions of the players still on
the board drawn from the Normal truncated at the current pick, drafted by **rank** (the same sampler as the
rooms). Sharp drafters (proxy for the nested optimizer, too expensive to run inside a recommendation): best
available by a noisy view of our value, view sd fitted to the value spread of perturbed views
(0.55 + 0.08·value, clipped 0.35–1.2) and centred 30 % towards the market (`SHARP_MARKET_WEIGHT`, fitted on 100
simulated symmetric rooms, seeds 10000–10099, disjoint from the evaluation seeds). The simulated me takes BPA at
my own picks. Cached per board; ≈0.2–0.5 s.

**Proxy fidelity** — mean |P̂(available at my pick) − empirical| over the top 150 by ADP, vs 100 simulated drafts
per room type with me = BPA (binomial noise floor ≈ 0.01):

| room (empirical, n drafts) | model | MAE @24 | MAE @36 | MAE @60 | MAE @84 | MAE @108 | mean |
|---|---|---|---|---|---|---|---|
| adp (100) | normal_adp | 0.012 | 0.023 | 0.026 | 0.033 | 0.037 | 0.026 |
| adp (100) | sim_adp | 0.007 | 0.010 | 0.013 | 0.017 | 0.016 | 0.013 |
| adp (100) | sim_mixed | 0.013 | 0.021 | 0.032 | 0.036 | 0.039 | 0.028 |
| adp (100) | sim_sym | 0.055 | 0.081 | 0.113 | 0.115 | 0.124 | 0.098 |
| mixed (100) | normal_adp | 0.014 | 0.031 | 0.039 | 0.051 | 0.059 | 0.039 |
| mixed (100) | sim_adp | 0.011 | 0.022 | 0.027 | 0.039 | 0.043 | 0.029 |
| mixed (100) | sim_mixed | 0.009 | 0.013 | 0.017 | 0.019 | 0.020 | 0.016 |
| mixed (100) | sim_sym | 0.047 | 0.064 | 0.091 | 0.085 | 0.091 | 0.076 |
| sym (100) | normal_adp | 0.047 | 0.085 | 0.113 | 0.115 | 0.124 | 0.097 |
| sym (100) | sim_adp | 0.046 | 0.081 | 0.110 | 0.108 | 0.123 | 0.094 |
| sym (100) | sim_mixed | 0.039 | 0.064 | 0.086 | 0.077 | 0.092 | 0.071 |
| sym (100) | sim_sym | 0.025 | 0.030 | 0.037 | 0.036 | 0.036 | 0.033 |

The analytic model is off by 0.026 even in the room it was written for (position-as-pick, the reviewer's
finding: market #23 at pick 24 is available 0.61 in the simulated room vs 0.47 analytic) and by 0.10 in W_sym;
the sim model for the right room is at 0.013 / 0.016 / 0.033.

## 3. Results (slot 1; P = mean ± SE over paired drafts; Δ = paired difference ± SE, z in parentheses)

### 3a. W_mixed — primary
**W_mixed, graded under truth `ours`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.904 ± 0.002 | 0.291 ± 0.003 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.878 ± 0.003 | 0.296 ± 0.003 | -0.026 ± 0.002 (-11.2) | +0.005 ± 0.003 (+1.4) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.901 ± 0.002 | 0.297 ± 0.003 | -0.002 ± 0.002 (-1.1) | +0.006 ± 0.003 (+2.2) | +0.024 (+10.2) / +0.001 (+0.4) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.899 ± 0.002 | 0.294 ± 0.003 | -0.005 ± 0.002 (-2.5) | +0.002 ± 0.003 (+0.9) | +0.021 (+9.9) / -0.002 (-0.7) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.897 ± 0.002 | 0.292 ± 0.004 | -0.007 ± 0.002 (-3.8) | +0.000 ± 0.003 (+0.1) | +0.019 (+8.5) / -0.004 (-1.3) |
| value search (`--search value`) | 200 | 0.891 ± 0.002 | 0.307 ± 0.003 | -0.013 ± 0.002 (-5.9) | +0.016 ± 0.003 (+4.6) | +0.013 (+6.4) / +0.011 (+3.3) |
| value search over the availability=sim optimizer | 200 | 0.896 ± 0.002 | 0.310 ± 0.003 | -0.007 ± 0.002 (-3.4) | +0.019 ± 0.003 (+5.8) | +0.019 (+8.7) / +0.014 (+4.7) |

**W_mixed, graded under truth `mid`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.814 ± 0.003 | 0.196 ± 0.002 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.802 ± 0.003 | 0.226 ± 0.003 | -0.012 ± 0.003 (-4.3) | +0.029 ± 0.003 (+9.9) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.815 ± 0.003 | 0.209 ± 0.003 | +0.001 ± 0.003 (+0.5) | +0.012 ± 0.002 (+5.1) | +0.013 (+4.6) / -0.017 (-5.5) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.813 ± 0.003 | 0.207 ± 0.003 | -0.001 ± 0.002 (-0.4) | +0.011 ± 0.002 (+4.7) | +0.011 (+4.2) / -0.018 (-6.3) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.811 ± 0.003 | 0.204 ± 0.003 | -0.003 ± 0.002 (-1.2) | +0.008 ± 0.002 (+3.2) | +0.009 (+3.5) / -0.021 (-7.1) |
| value search (`--search value`) | 200 | 0.816 ± 0.003 | 0.234 ± 0.003 | +0.003 ± 0.003 (+0.9) | +0.038 ± 0.003 (+13.9) | +0.015 (+5.7) / +0.008 (+3.0) |
| value search over the availability=sim optimizer | 200 | 0.820 ± 0.003 | 0.231 ± 0.003 | +0.006 ± 0.003 (+2.3) | +0.035 ± 0.003 (+12.8) | +0.018 (+6.8) / +0.005 (+2.0) |

**W_mixed, graded under truth `market`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.687 ± 0.004 | 0.111 ± 0.002 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.706 ± 0.004 | 0.151 ± 0.002 | +0.019 ± 0.003 (+5.4) | +0.039 ± 0.002 (+15.7) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.695 ± 0.004 | 0.124 ± 0.002 | +0.007 ± 0.003 (+2.2) | +0.013 ± 0.002 (+6.1) | -0.011 (-3.2) / -0.026 (-10.4) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.694 ± 0.004 | 0.123 ± 0.002 | +0.007 ± 0.003 (+2.4) | +0.011 ± 0.002 (+5.4) | -0.012 (-3.5) / -0.028 (-11.2) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.690 ± 0.004 | 0.122 ± 0.002 | +0.003 ± 0.003 (+0.9) | +0.011 ± 0.002 (+5.2) | -0.016 (-4.5) / -0.028 (-11.2) |
| value search (`--search value`) | 200 | 0.722 ± 0.004 | 0.156 ± 0.002 | +0.035 ± 0.004 (+9.5) | +0.045 ± 0.003 (+17.6) | +0.016 (+5.1) / +0.006 (+2.4) |
| value search over the availability=sim optimizer | 200 | 0.718 ± 0.004 | 0.149 ± 0.002 | +0.030 ± 0.003 (+8.8) | +0.038 ± 0.003 (+14.7) | +0.012 (+3.4) / -0.001 (-0.5) |

### 3b. W_adp — robustness (casual room)
**W_adp, graded under truth `ours`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.942 ± 0.001 | 0.384 ± 0.004 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.935 ± 0.002 | 0.406 ± 0.004 | -0.006 ± 0.001 (-4.4) | +0.022 ± 0.003 (+7.2) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.940 ± 0.001 | 0.396 ± 0.004 | -0.002 ± 0.001 (-1.3) | +0.012 ± 0.003 (+4.1) | +0.005 (+3.4) / -0.010 (-3.4) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.936 ± 0.002 | 0.397 ± 0.004 | -0.005 ± 0.001 (-4.4) | +0.013 ± 0.003 (+5.2) | +0.001 (+0.9) / -0.009 (-3.2) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.936 ± 0.002 | 0.397 ± 0.004 | -0.006 ± 0.001 (-5.1) | +0.013 ± 0.002 (+5.8) | +0.001 (+0.6) / -0.009 (-2.8) |
| value search (`--search value`) | 120 | 0.939 ± 0.002 | 0.412 ± 0.005 | -0.003 ± 0.002 (-1.6) | +0.029 ± 0.004 (+6.9) | +0.004 (+2.5) / +0.006 (+1.5) |
| value search over the availability=sim optimizer | 200 | 0.939 ± 0.001 | 0.409 ± 0.004 | -0.003 ± 0.001 (-2.2) | +0.026 ± 0.003 (+8.5) | +0.003 (+2.5) / +0.004 (+1.3) |

**W_adp, graded under truth `mid`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.869 ± 0.002 | 0.249 ± 0.003 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.874 ± 0.002 | 0.286 ± 0.003 | +0.006 ± 0.002 (+2.7) | +0.037 ± 0.003 (+13.2) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.872 ± 0.002 | 0.261 ± 0.003 | +0.004 ± 0.002 (+2.1) | +0.012 ± 0.003 (+4.7) | -0.002 (-1.2) / -0.025 (-9.8) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.872 ± 0.002 | 0.271 ± 0.003 | +0.003 ± 0.002 (+1.9) | +0.021 ± 0.003 (+8.6) | -0.003 (-1.4) / -0.015 (-5.8) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.871 ± 0.002 | 0.268 ± 0.003 | +0.002 ± 0.002 (+1.2) | +0.019 ± 0.002 (+8.2) | -0.004 (-2.0) / -0.018 (-6.8) |
| value search (`--search value`) | 120 | 0.880 ± 0.003 | 0.291 ± 0.004 | +0.012 ± 0.003 (+4.4) | +0.042 ± 0.004 (+10.3) | +0.006 (+2.9) / +0.006 (+1.8) |
| value search over the availability=sim optimizer | 200 | 0.879 ± 0.002 | 0.285 ± 0.003 | +0.010 ± 0.002 (+5.1) | +0.036 ± 0.003 (+12.4) | +0.004 (+2.3) / -0.001 (-0.3) |

**W_adp, graded under truth `market`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.753 ± 0.003 | 0.139 ± 0.002 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.782 ± 0.003 | 0.177 ± 0.003 | +0.028 ± 0.003 (+9.9) | +0.039 ± 0.003 (+15.3) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.764 ± 0.003 | 0.150 ± 0.003 | +0.010 ± 0.003 (+4.0) | +0.012 ± 0.002 (+5.4) | -0.018 (-7.0) / -0.027 (-11.8) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.768 ± 0.003 | 0.160 ± 0.002 | +0.015 ± 0.002 (+6.3) | +0.021 ± 0.002 (+11.3) | -0.014 (-5.8) / -0.017 (-7.6) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.764 ± 0.003 | 0.158 ± 0.003 | +0.011 ± 0.002 (+4.5) | +0.020 ± 0.002 (+9.8) | -0.017 (-6.8) / -0.019 (-8.0) |
| value search (`--search value`) | 120 | 0.789 ± 0.004 | 0.184 ± 0.003 | +0.037 ± 0.004 (+9.7) | +0.045 ± 0.004 (+12.7) | +0.008 (+2.9) / +0.009 (+3.0) |
| value search over the availability=sim optimizer | 200 | 0.781 ± 0.003 | 0.175 ± 0.003 | +0.028 ± 0.003 (+10.4) | +0.036 ± 0.002 (+15.1) | -0.000 (-0.2) / -0.002 (-0.9) |

### 3c. W_sym — robustness (everyone sharp)
**W_sym, graded under truth `ours`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.773 ± 0.004 | 0.215 ± 0.003 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.710 ± 0.005 | 0.205 ± 0.003 | -0.063 ± 0.005 (-13.2) | -0.011 ± 0.003 (-3.6) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.759 ± 0.004 | 0.215 ± 0.003 | -0.014 ± 0.004 (-3.6) | -0.001 ± 0.003 (-0.3) | +0.049 (+10.4) / +0.010 (+3.5) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.773 ± 0.004 | 0.218 ± 0.003 | +0.000 ± 0.004 (+0.1) | +0.002 ± 0.003 (+0.8) | +0.063 (+13.6) / +0.013 (+4.1) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.774 ± 0.003 | 0.223 ± 0.003 | +0.001 ± 0.004 (+0.3) | +0.008 ± 0.003 (+2.6) | +0.064 (+13.8) / +0.019 (+5.9) |
| value search (`--search value`) | 120 | 0.743 ± 0.005 | 0.217 ± 0.004 | -0.032 ± 0.005 (-6.0) | -0.001 ± 0.004 (-0.3) | +0.029 (+5.5) / +0.011 (+3.0) |
| value search over the availability=sim optimizer | 200 | 0.760 ± 0.004 | 0.225 ± 0.003 | -0.013 ± 0.004 (-3.4) | +0.009 ± 0.003 (+3.0) | +0.050 (+10.9) / +0.020 (+6.2) |

**W_sym, graded under truth `mid`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.689 ± 0.004 | 0.151 ± 0.002 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.687 ± 0.004 | 0.185 ± 0.003 | -0.002 ± 0.004 (-0.6) | +0.034 ± 0.003 (+12.1) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.697 ± 0.004 | 0.165 ± 0.002 | +0.008 ± 0.004 (+2.1) | +0.014 ± 0.003 (+5.1) | +0.011 (+2.3) / -0.020 (-6.9) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.702 ± 0.004 | 0.159 ± 0.002 | +0.013 ± 0.004 (+3.4) | +0.008 ± 0.003 (+2.9) | +0.015 (+3.6) / -0.026 (-8.9) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.702 ± 0.004 | 0.165 ± 0.003 | +0.013 ± 0.004 (+3.6) | +0.014 ± 0.003 (+4.9) | +0.015 (+3.8) / -0.021 (-6.8) |
| value search (`--search value`) | 120 | 0.713 ± 0.004 | 0.190 ± 0.003 | +0.018 ± 0.005 (+3.9) | +0.036 ± 0.004 (+9.7) | +0.021 (+4.3) / +0.002 (+0.6) |
| value search over the availability=sim optimizer | 200 | 0.718 ± 0.004 | 0.187 ± 0.002 | +0.029 ± 0.004 (+7.9) | +0.036 ± 0.003 (+13.0) | +0.031 (+7.5) / +0.002 (+0.6) |

**W_sym, graded under truth `market`** (slot 1)

| arm | n | P(playoffs) | P(title) | ΔP(playoffs) vs BPA (z) | ΔP(title) vs BPA (z) | ΔP(po) / ΔP(title) vs default optimizer (z) |
|---|---|---|---|---|---|---|
| BPA (value, lineup-feasible) | 200 | 0.594 ± 0.004 | 0.099 ± 0.002 | – | – | – |
| optimizer (current default: Normal-ADP availability, cat-wins) | 200 | 0.662 ± 0.004 | 0.167 ± 0.003 | +0.068 ± 0.005 (+14.9) | +0.067 ± 0.003 (+19.8) | – |
| GreedyCat (one-step cat-wins, no lookahead/availability) | 200 | 0.625 ± 0.005 | 0.121 ± 0.003 | +0.032 ± 0.005 (+6.8) | +0.022 ± 0.003 (+6.8) | -0.036 (-6.9) / -0.045 (-13.3) |
| optimizer, availability=sim (room = W), cat-wins | 200 | 0.620 ± 0.004 | 0.112 ± 0.002 | +0.027 ± 0.004 (+6.1) | +0.013 ± 0.003 (+4.6) | -0.042 (-8.8) / -0.055 (-16.7) |
| optimizer, availability=sim (room = W), matchup objective | 200 | 0.619 ± 0.005 | 0.117 ± 0.003 | +0.025 ± 0.005 (+5.4) | +0.018 ± 0.003 (+6.3) | -0.043 (-8.9) / -0.049 (-13.6) |
| value search (`--search value`) | 120 | 0.679 ± 0.005 | 0.162 ± 0.003 | +0.076 ± 0.005 (+14.5) | +0.059 ± 0.004 (+16.1) | +0.011 (+1.9) / -0.008 (-2.1) |
| value search over the availability=sim optimizer | 200 | 0.669 ± 0.005 | 0.153 ± 0.003 | +0.076 ± 0.005 (+16.1) | +0.054 ± 0.003 (+16.3) | +0.008 (+1.6) / -0.014 (-3.8) |

## 4. Ablation readings (the user's question: "the optimizer should be at least as good as BPA")
Under the consistent grade (truth `ours`):
- **Objective (GreedyCat vs BPA):** −0.2 pp P(playoffs) (z −1.1) / +0.6 pp P(title) (z 2.2) in W_mixed; −0.2 /
  +1.2 pp in W_adp; −1.4 pp (z −3.6) / −0.1 pp in W_sym. The one-step cat-wins objective is not what loses value in
  W_mixed/W_adp; in W_sym it costs ~2 pp P(playoffs) (it ignores that sharp opponents take the same balanced
  players).
- **Availability (opt_sim vs default optimizer):** +2.1 pp P(playoffs) (z 9.9) in W_mixed, +0.1 pp in W_adp,
  **+6.3 pp (z 13.6)** in W_sym; opt_sim is within 0.5 pp of BPA on P(playoffs) everywhere (+0.0 in W_sym). So "3 ≈ 1 while 4 < 1": the
  analytic ADP availability model (waiting on players it thinks will last, reaching for players it thinks
  won't) is the culprit. In W_adp the remaining gap is small because the Normal model is only mildly wrong there.
- **Matchup objective** (opt_sim_matchup) vs cat-wins (opt_sim): −0.2 pp P(playoffs) / −0.2 pp P(title) in
  W_mixed, ±0 in W_adp, +0.1 / +0.6 pp in W_sym — no consistent gain. A strength-aware variant (matchup prob averaged over the 11 actual opponents'
  rosters) does not track the simulator better either (§6), so it was not built into the optimizer.
- **Residual:** opt_sim still trails BPA by −0.5 pp P(playoffs) (z −2.5) in W_mixed. The category model's
  expected cat wins predicts the simulator's P(playoffs) less well than the plain value sum (§6), so a small
  objective mismatch remains; value search (trained on simulator outcomes) is what closes the P(title) gap.
- **Plain bugs checked:** no league ever contained a duplicated player (0 of all simulated leagues); the
  optimizer's planned next-pick target was never already taken at decision time (0 of 11,304 plans); the
  harness passes the true current pick (`max(taken) + 1`, every pick recorded) and the default `sd_mult`; BPA
  uses the same feasibility function in the harness and the CLI; all arms share one `DraftContext`
  (projections, valuation params); paired seeds reproduce bit-for-bit (468/468 policy-drafts re-run, and BPA
  grades identical across harness versions). The bug is the model mismatch above, not an implementation error.

## 5. Mechanism (default optimizer vs BPA, same boards, current code, 200 drafts per world)
- How often the optimizer's pick differs from BPA's on the same board (W_mixed): #24 76 %, #36 95 %, #37 89 %,
  #60 76 %, #108 80 %. It reaches: mean ADP of its 13 players 84 vs BPA 90 (W_mixed), 78 vs 84 (W_adp), 82 vs 96
  (W_sym), at a cost of 0.6 / 0.0 / 1.3 value points (z −10.7 / +0.2 / −20.5).
- When it passed the BPA player, that player was gone before my next pick 29 % (W_mixed) / 21 % (W_adp) / 38 %
  (W_sym) of the time — most often Kon Knueppel (158 of 1,302 passes in W_mixed), Julian Champagnie, Fred
  VanVleet, Miles Bridges, Kel'el Ware.
- Its planned next-pick target was still there 68 % (W_mixed), 81 % (W_adp), 50 % (W_sym); the targets most
  often gone: Mikal Bridges, Bam Adebayo, Reed Sheppard, Miles Bridges, Fred VanVleet, Derrick White.
- Category profile (season-sim per-category win rate, optimizer − BPA, W_mixed): FG% +0.10, blocks +0.08,
  3PM −0.13, FT% −0.05, assists −0.03 — a big-man tilt (same sign in every world).
- With sim availability the reach mostly disappears (opt_sim mean ADP 89 vs BPA 90 in W_mixed, 93 vs 96 in
  W_sym) and value is kept (40.4 vs 40.6). vs_sim sits in between (ADP 86, value 40.0): value search buys some of
  the market-favoured players back, which is where its P(title) and its market-truth robustness come from.

## 6. Which objective proxy tracks the simulator?
Within-room Spearman correlation (rosters of the arms in the same room, demeaned by room) between a proxy and
the season-sim outcome, truth `ours`, current code (930 rosters, 268 rooms):

| proxy | → P(title) all / mixed / sym | → P(playoffs) all / mixed / sym |
|---|---|---|
| value sum (G-score) | +0.10 / −0.03 / +0.38 | **+0.47 / +0.40 / +0.68** |
| expected cat wins vs league average (optimizer) | **+0.25** / +0.15 / +0.39 | +0.21 / +0.15 / +0.44 |
| P(matchup win) vs league average | +0.24 / +0.12 / **+0.43** | +0.32 / +0.26 / +0.50 |
| expected cat wins vs the 11 actual opponents | +0.23 / +0.13 / +0.37 | +0.21 / +0.15 / +0.45 |
| P(matchup win) vs the 11 actual opponents | +0.24 / +0.13 / +0.40 | +0.34 / +0.31 / +0.49 |

No category proxy dominates; the matchup variants are marginally better for P(playoffs), the plain value sum is
best for P(playoffs) and worst for P(title). Strength-aware proxies add nothing over league-average ones. Same
ranking on the d07f903 rows (942 rosters). Hence: keep cat-wins as the optimizer objective and let value search
(trained directly on simulator outcomes) do the final re-ranking.

## 7. Robustness across code versions and slots (d07f903: valuation-0.2.3, season-sim-0.4.0)
n ≈ 50 paired drafts per cell at slot 1, ≈ 33 at slots 6/12; Δ vs BPA in pp (z), P(playoffs)/P(title):

| slot, room | truth ours: optimizer / value search | truth mid | truth market |
|---|---|---|---|
| 1 adp | −0.8 (−4) / +2.0 (+3); +0.0 / +4.1 (+7) | opt +0.0 / +3.8; VS +0.8 / +5.0 | opt +2.0 / +3.5; VS +3.5 / +4.8 |
| 1 sym | −8.1 (−10) / −0.9 (−2); −5.3 (−6) / +0.4 | opt −2.1 / +3.8; VS +1.1 / +4.7 | opt +5.1 / +7.1; VS +7.9 / +7.5 |
| 1 symbpa | −5.7 (−8) / −1.1; −4.4 (−7) / −0.9 | opt −1.3 / +2.6; VS −0.1 / +2.8 | opt +2.9 / +5.6; VS +3.7 / +6.0 |
| 6 adp | −0.7 / +1.6; −0.3 / +3.4 (+4) | opt +0.6 / +2.3; VS +1.2 / +3.9 | opt +2.9 / +2.9; VS +3.5 / +3.9 |
| 6 sym | −15.2 (−15) / −0.9; −9.4 (−9) / +0.7 | opt −2.2 / +5.5; VS −0.4 / +4.6 | opt +11.5 / +11.2; VS +9.5 / +8.1 |
| 12 adp | −0.2 / +1.8; +1.0 / +3.6 (+4) | opt +1.0 / +2.5; VS +2.7 / +3.7 | opt +3.5 / +3.3; VS +5.2 / +4.1 |
| 12 sym | −10.0 (−8) / −0.3; −3.9 (−4) / +2.6 (+4) | opt −0.6 / +4.0; VS +2.4 / +5.3 | opt +9.0 / +6.7; VS +9.2 / +6.4 |

Same pattern as the current code at every slot: the default optimizer loses P(playoffs) to BPA on our
projections, most in sharp rooms; value search is ≥ the optimizer in 20 of 21 cells. The intermediate fixes
tried there — hybrid (optimizer only if it beats BPA by 0.02/0.05 cat wins), BPA + need tiebreak (ε = 0.2),
optimizer with availability from our value rank, ADP sd halved — all landed between BPA and the optimizer and
were never best; they are kept as `--policy` options but not recommended.

**Why the earlier comparison (docs/research/value_search.md) looked different under market truth** (there BPA ≈
optimizer; here the optimizer wins by 3–4 pp P(title)): it used the pre-blend projections and the uncalibrated
simulator; with the 50/50 ESPN blend our values moved towards ADP, and BPA-by-value is now further from the
market than the optimizer's reaches. Its main finding (BPA beats the optimizer on our projections, most in
symmetric rooms: then −6.1 pp P(playoffs)) reproduces (now −6.1 pp in W_sym) and is explained by §4.

## 8. Picks on the current projections (slot 1, deterministic room drafting in Yahoo-ADP order)
| rule | #1 | #24 | #36 | #37 | #60 |
|---|---|---|---|---|---|
| default optimizer | Nikola Jokić | Kawhi Leonard | Derrick White | Desmond Bane | Naz Reid |
| BPA by value | Nikola Jokić | Kon Knueppel | Derrick White | Desmond Bane | Kel'el Ware |
| GreedyCat | Nikola Jokić | Bam Adebayo | Derrick White | Desmond Bane | Kel'el Ware |
| opt_sim (mixed) | Nikola Jokić | Bam Adebayo | Derrick White | Desmond Bane | Kel'el Ware |
| value search (default optimizer) | Nikola Jokić | Bam Adebayo | Derrick White | Desmond Bane | Michael Porter Jr. |
| **vs_sim (recommended)** | Nikola Jokić | Bam Adebayo | Derrick White | Desmond Bane | Kel'el Ware |

CLI on a mock draft at the same boards (`--search value --availability sim --room mixed`): #24 Bam Adebayo,
compute 2.0 s, wall 5.1 s; #60 Kel'el Ware, 1.6 s / 4.7 s (load average ≈ 14 on 4 cores from other agents);
the default at #24 says Kawhi Leonard. Real boards will differ; the point is that the change is a different
player at roughly every second pick after #1, not a different strategy.

## 9. Limitations
- Truth `ours` is in-sample with respect to our projections (the simulator adds fitted projection error, but
  centred on our lines). Market/mid truths are stress tests, not evidence; nothing here says which is closer.
- The sharp-drafter proxy inside `availability="sim"` is imperfect (MAE 0.033 vs a 0.01 noise floor in W_sym);
  the evaluation's sharp opponents are the real optimizer on perturbed views.
- W_mixed's "3 sharp" is the user's qualitative description turned into a number (prior). W_adp and W_sym
  bracket it, and the recommended config wins or ties BPA on P(title) in both.
- Slots 6 and 12 were run only on the earlier code version (§7). The value model (`value-model-0.1.0`) was
  trained before the noise-model change; it was not retrained.
- 400 seasons per league; per-draft simulation noise is inside the paired SEs.

## 10. Reproduce
```bash
export PYTHONPATH=src OMP_NUM_THREADS=1
VM="--value-model data/models/value_search/value-model-0.1.0.npz --opt-strategy balanced"
python -m fantasy.draft.policy_eval run --seeds 0:200 --slots 1 --regimes mixed,adp,sym \
  --policies bpa,optimizer,greedycat,opt_sim,opt_sim_matchup,value_search,vs_sim $VM --out res.jsonl
python -m fantasy.draft.policy_eval rooms --seeds 10000:10100 --regimes adp,mixed,sym --opt-strategy balanced --out rooms.npz
python -m fantasy.draft.policy_eval summary --out res.jsonl
```
Scratch analysis scripts (tables, fidelity, proxies, mechanism) were kept in the session scratchpad; the paired
tables are `summary` output plus the vs-optimizer column.

## 11. Room-sim self-pick correction (2026-10-19)
_Research agent, 2026-10-19. Opt-in code only; **no default changed**. All numbers are **fitted by simulation**
(paired policy-eval harness, graded by the full season simulator, 400 seasons per league; method as §2)._

**The quirk (review finding).** In `room_sim.simulate_availability` the simulated me takes the best-value
player at *every* one of my picks, including the current one. When I am on the clock this removes the
top-value player in all 300 simulated rooms, so he gets P(available) = 0 at every later pick, whichever
candidate the optimizer is scoring (e.g. scoring "take Bam now" assumes Kon Knueppel cannot be there at #36).
At my intermediate picks the table also removes the best-value player while the rollout separately discounts
its own planned pick (`not_mine`), so one of my picks is counted twice. (The displayed `prob_available_next`
was fixed separately in 4940316 — display only; this section is about the *decision* table.)

**Opt-in corrections** (`OptimizerParams(room_self=...)`, `simulate_availability(self_pick=...)`; policy-eval
arms `opt_sim_skip|plan`, `vs_sim_skip|plan`):
- `best` — default, byte-identical (8 boards' full payloads compared with `origin/main`).
- `skip` — my picks remove nobody: the table is P(not taken by an *opponent*); the rollout owns all of my picks.
- `plan` — skip at the decision pick; at my later picks remove the rollout's planned player (plan of the top
  candidate from a first `skip` pass; falls back to best value in runs where he is already gone). ≈2× compute.

**Results, truth `ours`, slot 1** (Δ = corrected − uncorrected in pp, paired SE, z). Main runs: value model
`value-model-0.1.0` (frozen copy of `862cc74` + this change), seeds 0–199 (W_mixed) / 0–149 (W_adp, W_sym).

| world (n) | arm | baseline P(po) / P(title) | ΔP(playoffs) skip | ΔP(title) skip | ΔP(playoffs) plan | ΔP(title) plan |
|---|---|---|---|---|---|---|
| **W_mixed (200)** | **vs_sim (default)** | 0.896 / 0.310 | **−0.34 ± 0.17 (−2.0)** | −0.06 ± 0.27 (−0.2) | −0.02 ± 0.14 (−0.1) | −0.22 ± 0.22 (−1.0) |
| W_mixed (200) | opt_sim (fallback) | 0.899 / 0.294 | −0.98 ± 0.19 (−5.1) | +0.68 ± 0.29 (+2.3) | −0.51 ± 0.16 (−3.3) | +0.24 ± 0.25 (+1.0) |
| W_adp (150) | vs_sim | 0.939 / 0.412 | −0.18 ± 0.14 (−1.3) | +0.41 ± 0.30 (+1.4) | −0.02 ± 0.13 (−0.1) | −0.17 ± 0.27 (−0.6) |
| W_adp (150) | opt_sim | 0.936 / 0.399 | −0.12 ± 0.15 (−0.8) | +1.50 ± 0.35 (+4.4) | −0.06 ± 0.14 (−0.4) | +0.25 ± 0.29 (+0.9) |
| W_sym (150) | vs_sim | 0.759 / 0.225 | +0.13 ± 0.27 (+0.5) | −0.17 ± 0.25 (−0.7) | +0.29 ± 0.30 (+1.0) | −0.41 ± 0.23 (−1.8) |
| W_sym (150) | opt_sim | 0.775 / 0.219 | −0.38 ± 0.27 (−1.4) | −0.23 ± 0.23 (−1.0) | −0.34 ± 0.23 (−1.5) | −0.03 ± 0.23 (−0.1) |
| **W_mixed (150), value-model-0.2.0** (current default artifact, main at `7f4bd68`) | **vs_sim (default)** | 0.902 / 0.322 | +0.04 ± 0.19 (+0.2) | +0.35 ± 0.28 (+1.3) | −0.11 ± 0.15 (−0.8) | +0.05 ± 0.25 (+0.2) |

With value-model-0.2.0 (W_mixed, n = 150): truth mid skip +0.2 / +0.4 pp, plan −0.2 / +0.0; truth market skip
+0.4 / +0.5 pp (z 1.3 / 1.8), plan −0.1 / +0.0. Any different pick: skip 129/150, plan 114/150; pick #1 0/150.

Robustness truths (Δ pp, P(playoffs) / P(title); z in parentheses where |z| ≥ 2):

| world | vs_sim skip: mid | market | vs_sim plan: mid | market | opt_sim skip: mid | market | opt_sim plan: mid | market |
|---|---|---|---|---|---|---|---|---|
| W_mixed | −0.2 / +0.1 | +0.1 / +0.4 (2.0) | −0.1 / −0.4 | +0.1 / −0.4 (−2.0) | −0.4 / +1.3 (5.1) | +0.7 (2.4) / +1.7 (7.8) | −0.2 / +0.5 (2.5) | +0.2 / +0.8 (4.3) |
| W_adp | −0.0 / +0.8 (2.6) | +0.6 (2.4) / +1.3 (4.7) | +0.0 / +0.1 | +0.1 / +0.3 | +0.5 (2.4) / +1.9 (6.0) | +1.7 (5.6) / +2.5 (8.9) | +0.2 / +0.5 (2.1) | +1.0 (4.1) / +0.9 (4.0) |
| W_sym | +0.3 / +0.1 | +0.6 / +0.0 | +0.4 / +0.1 | +0.6 / −0.2 | +0.0 / +0.2 | +0.4 / +0.2 | +0.0 / +0.3 | +0.3 / +0.4 |

**How often the pick changes.** Pick #1 never changes (0 of 500 paired drafts for every arm pair: Jokić
dominates). The drafts are identical until my first differing pick, so the first divergence is the first board
on which the corrected optimizer changes my pick:

| pair | drafts with any different pick (mixed / adp / sym) | first divergence at #24 / #36–37 / later (W_mixed) |
|---|---|---|
| vs_sim skip vs vs_sim | 181/200 · 141/150 · 123/150 | 64 / 58 / 59 |
| vs_sim plan vs vs_sim | 163/200 · 129/150 · 125/150 | 16 / 62 / 85 |
| opt_sim skip vs opt_sim | 198/200 · 150/150 · 142/150 | 139 / 32 / 27 |
| opt_sim plan vs opt_sim | 185/200 · 148/150 · 138/150 | 46 / 65 / 74 |

On the deterministic market-order board (§8 style, opt_sim balanced): #24 Bam Adebayo → **Kawhi Leonard** under
`skip` (the old default's reach) and Bam Adebayo under `plan`; #36 Derrick White → Desmond Bane (`skip`) / Derrick
White (`plan`).

**Mechanism.** With the top-value player no longer pre-committed, the optimizer believes he will often last
(e.g. Kon Knueppel P(available at #36) 0 → 0.73 on the #24 board), so it takes a category-fit player now and
plans to collect the value player later — i.e. it reaches more. In W_mixed (value-model-0.1.0) that costs value (value sum −0.10 ±
0.05 for both `skip` arms; cat-model expected cat wins −0.011 ± 0.004 for opt_sim) and P(playoffs), and it buys
P(title) and market-truth robustness — the same trade-off as the old default's reaches (§3, §5). With value-model-0.2.0 re-ranking the candidates the cost disappears (value sum +0.05 ± 0.06) — and so does any gain. The quirk was
effectively a mild BPA-lean regulariser on an objective (expected cat wins) that tracks P(playoffs) less well
than the value sum (§6). `plan` removes the double count without the over-optimism of `skip`, and lands within
noise of the uncorrected table for vs_sim everywhere.

**Recommendation: do not adopt; keep `room_self="best"` (default).** The user's rule is "adopt only if not
worse, and better where it matters". In the cell that matters (W_mixed, truth `ours`, the recommended vs_sim):
with value-model-0.1.0 `skip` is worse on P(playoffs) (−0.34 pp, z −2.0) with no P(title) gain; with the current
default value-model-0.2.0 it is a tie (+0.04 / +0.35 pp, z 0.2 / 1.3). `plan` is a tie under both (|z| ≤ 1),
at 2× compute. Neither is *better* where it matters, and the sign of `skip` flips with the value model. For the opt_sim fallback, `skip` trades −1.0 pp
P(playoffs) (z −5.1) for +0.7 pp P(title) (z 2.3) — fails "not worse". The corrections' gains appear mainly
under market truth (a stress test, §9) and in the casual W_adp room. The user-visible symptom (P = 0 shown for the
top player) is already handled by the display-only fix. The options stay in the code for research.

**Limitations.** Slot 1 only; balanced strategy (as §2); the main vs_sim runs used `value-model-0.1.0` (0.2.0
became the default artifact during this work — the 0.2.0 recheck, vs_sim arms only, is the last row of the table);
`plan` uses the top candidate's plan for every candidate's table (a per-candidate table would cost ~15× more).

Reproduce (frozen copy of `src/` + `data/`):
```bash
export PYTHONPATH=src OMP_NUM_THREADS=1
P=vs_sim,vs_sim_skip,vs_sim_plan,opt_sim,opt_sim_skip,opt_sim_plan
VM="--value-model data/models/value_search/value-model-0.1.0.npz --opt-strategy balanced --workers 2"
python -m fantasy.draft.policy_eval run --seeds 0:200 --slots 1 --regimes mixed --policies $P $VM --out mixed.jsonl
python -m fantasy.draft.policy_eval run --seeds 0:150 --slots 1 --regimes adp,sym --policies $P $VM --out adpsym.jsonl
python -m fantasy.draft.policy_eval run --seeds 0:150 --slots 1 --regimes mixed --policies vs_sim,vs_sim_skip,vs_sim_plan \
  --value-model data/models/value_search/value-model-0.2.0.npz --opt-strategy balanced --workers 2 --out mixed_vm2.jsonl
```
