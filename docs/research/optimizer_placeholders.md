# Optimizer rollout placeholders: taken players and complementarity (`filler-eval-0.1.0`)

_2026-10-19, optimizer-placeholder research agent. Every number here is **fitted by simulation** (method,
sample size and paired SE given with each table) unless it is labelled prior/guess. **No default was changed.**_

## TL;DR

- **Question.** While the rollout ranks candidates for one of my future picks p_j, my picks after p_j are
  stood in for by `_filler[p]`: the average line of the market's picks p-4..p+2, fixed before the draft. The
  user raised two concerns. (a) The placeholder ignores who has actually been drafted. (b) It is a generic,
  balanced player, so the ranking may assume a generic rest-of-roster and mis-value specialists.
- **(a) is real, but small and narrow.** In noisy-ADP rooms, about 1–7% of a later pick's placeholder window
  has already been drafted when I decide. The exception is the back-to-back turn pick (37 after 36, 61 after
  60, 85 after 84), where 32–39% of the window is gone, because it includes picks 33–35 that were already
  made. Re-centring the window on the actual board (`filler="board"`) moves the placeholder's value by
  −0.8 to +0.2 G-score.
- **(b): the market placeholder under-values specialists, but only slightly.** For 11–16 specialists at picks 24, 36 and 37, I compared
  each score with a balanced-player control group. A complement-aware placeholder (`refine_passes`) moves
  specialists by **+0.002 to +0.010** expected category wins (SE ≈ 0.002–0.003). The best-available
  placeholder moves them by −0.002 to +0.007. In other words, the market placeholder *under*-values
  specialists slightly. The size is **≤ ⅓ of the 0.03 near-tie scale**. Single players move by up to
  ±0.05 (e.g. Sabonis +0.059 under `available` at #24).
- **On outcomes, no variant helps.** Slot 1, 150 paired drafts per room type, graded by the full season
  simulator. Every variant's paired difference from the current optimizer is within ±0.5 pp P(playoffs) and
  ±0.8 pp P(title) in every room × truth cell. The one exception is `available`, which is *worse* in the
  two smart rooms (−1.3 ± 0.6 pp P(playoffs) sym, −0.8 ± 0.35 pp symbpa; |z| ≈ 2.2, 5 of 60 cells reach |z| ≥ 2 vs ≈ 3 by chance). This
  holds even though the variants change the #1 recommendation on 10–58% of boards at picks 24–108. The
  changes are near-ties: final rosters share 10–12 of 13 players in ADP rooms, and the category-model score
  moves by ≤ 0.01. The result is the same at slots 6 and 12 (75 paired drafts each; `board` loses 1.2–1.4 pp
  at slot 6, z ≈ −2.5) and with the room-simulated availability model (`availability="sim"`, 100 drafts per
  room: all differences within ±0.65 pp).
- **Recommendation: keep `filler="market", refine_passes=0`.** Neither concern changes outcomes measurably.
  The placeholder is not why the optimizer trailed BPA; that was the availability model
  (docs/research/draft_policy.md). The options stay opt-in for research
  (`fantasy draft recommend --filler board|available --refine k`).

## 1. What was implemented (opt-in; default outputs byte-identical)
`OptimizerParams.filler` and `OptimizerParams.refine_passes` (src/fantasy/draft/optimizer.py), used only
by expected rollouts:

| option | placeholder line for my later pick p, while ranking an earlier pick |
|---|---|
| `filler="market"` (default) | mean line of the market's picks p-4..p+2 (fixed at draft start) |
| `filler="board"` | E[line of the players the room takes at p-4..p+2 \| board]: weights w_i = P(avail at max(p-4, now)) − P(avail at p+3) over **available** players, normalised. This is the market window re-centred on the actual board. |
| `filler="available"` | E[line of the highest-value player still available at p \| board]: q_i = pa_i ∏_{j valued above i}(1 − pa_j). It uses the same independence approximation as the rollout. Leftover mass gets the market line. |
| `refine_passes=k` | after the rollout, re-run it k more times. In each new run, the placeholders for later picks are replaced by the per-pick expected lines (Σ q_i·line_i + residual) that the previous pass planned. Fixed-point iteration; complement-aware. |

Availability for `board`/`available` comes from the optimizer's own model (`_pa`). That means the analytic
`prob_available`, or the room simulation with `availability="sim"`, which landed concurrently. The
placeholders exclude every drafted player and the candidate being scored, and they are cached per
(board, candidate). The payload gains `filler` (mode, passes, convergence) and per-candidate `score_passes`
**only** when an option is non-default.

**Default unchanged (checked).** I ran `fantasy draft recommend --json` (full CLI path: strategy scan, policy
block, advisory value search) at picks 1, 24 and 36 of a mock on a fresh SQLite DB, with others drafting by
market order. Origin/main and this branch produced the same JSON apart from timestamps and `compute_seconds`.
The rollout's floating-point association order is kept exactly for that reason.

## 2. (a) Taken players in the placeholder window
Slot 1, 3RR, 200 noisy-ADP rooms (rng([s, 11]), others = noisy ADP, me = BPA). At each of my decisions,
for the next four of my later picks (script: §7):

| decision | later pick | share of the market window already drafted | market placeholder value (G) | board − market (G) | available − market (G) |
|---|---|---|---|---|---|
| 24 | 36 (next) | 0.056 | 2.79 | +0.08 ± 0.04 sd | +1.18 |
| 24 | 37, 60, 61 | 0.017 | 2.73 | −0.35 | +1.03 |
| 36 | **37 (next, back-to-back)** | **0.321** | 2.57 | +0.22 | +1.37 |
| 36 | 60, 61, 84 | 0.013 | 2.53 | −0.66 | +0.91 |
| 37 | 60 (next) | 0.019 | 2.77 | −0.65 | +0.77 |
| 60 | **61 (next)** | **0.329** | 2.84 | −0.76 | +0.68 |
| 84 | **85 (next)** | **0.386** | 1.63 | −0.33 | +1.43 |

(Values are means over rooms; "G" is the valuation's G-score sum, where ~1 G is roughly one strong
category.) The window does contain drafted players, but only at the turn, and there for a structural reason.
The board-conditioned window is mostly *lower* in value than the fixed one. Taken players are removed, and
the availability model spreads the window over players with more uncertain ADPs. The "best available" line
is a different object. It is +0.7 to +1.5 G better than the average market pick at p, because our values
disagree with ADP, so it also changes the placeholder's *level*.

## 3. (b) Specialists: direction and size of the placeholder bias
Mock: slot 1, 3RR, others draft by market order, and I take the balanced optimizer's #1 (Jokić, Kawhi
Leonard, Derrick White). The boards are those at picks 24, 36 and 37. Specialists are players with value
rank 15–90 whose largest positive category G-score is ≥ 45% of their positive G-score total. That gives 18
players: Boozer/Sabonis/Adebayo (reb), Sarr/Turner/Buzelis/Jackson Jr./Holmgren (blk), Knueppel/B. Miller/Curry
(3PM), Daniels (stl), Castle/Giddey/James/Young (ast), J. Brown/Siakam (pts). The controls are the 18 most
balanced players in the same value band. Every player available at the pick was scored by the same rollout
(balanced weights), even those outside the optimizer's usual 14+4 candidates.

Bias statistic: for each specialist s, (score_s − mean control score) under the variant minus the same
quantity under `market`. Mean ± SE over specialists (expected category wins of 9):

| pick (n spec / ctrl) | board | available | refine 1 | refine 2 | available + refine 1 |
|---|---|---|---|---|---|
| 24 (16 / 14) | −0.0017 ± 0.0023 | +0.0072 ± 0.0042 | **+0.0095 ± 0.0026** | +0.0080 ± 0.0030 | +0.0044 ± 0.0043 |
| 36 (11 / 12) | +0.0010 ± 0.0033 | +0.0017 ± 0.0044 | +0.0057 ± 0.0022 | +0.0035 ± 0.0032 | +0.0012 ± 0.0051 |
| 37 (11 / 11) | −0.0008 ± 0.0015 | +0.0032 ± 0.0026 | +0.0017 ± 0.0013 | −0.0008 ± 0.0022 | +0.0060 ± 0.0026 |

- **Direction.** When the placeholder knows the plan (refine), specialists gain slightly relative to
  balanced players. So the generic placeholder biases specialists **down**, as the user suspected.
- **Size.** At most +0.01, i.e. ≤ ⅓ of the near-tie scale (0.03). Individual players move by up to ±0.05:
  Sabonis +0.059 under `available` at #24 (pool rank 30 → 21), Knueppel +0.031. Few specialists are close
  enough to the top for this to change a pick. Adebayo (#2 → #1 under `available` at #24) and Holmgren are
  the exceptions.
- Rank correlation of all candidates' scores between refinement passes: Spearman 0.97–0.997.

**Convergence of the fixed point** (default candidates, balanced, same boards; mean |Δ score| per pass):

| board | pass 1 | pass 2 | pass 3 | pass 4 | pass 5 | pass 6 | #1 by pass |
|---|---|---|---|---|---|---|---|
| #1 | 0.032 | 0.008 | 0.005 | 0.002 | 0.002 | 0.001 | Jokić throughout |
| #24 | 0.011 | 0.005 | 0.002 | 0.002 | 0.001 | 0.000 | Kawhi throughout |
| #36 | 0.011 | 0.006 | 0.002 | 0.001 | 0.000 | 0.000 | White → Clingan (pass 1 on) |
| #37 | 0.009 | 0.006 | 0.002 | 0.001 | 0.000 | 0.000 | Bane → Clingan (pass 1 on) |

The iteration converges, roughly geometrically (factor ≈ 0.4–0.6 per pass), but it is not monotone for
single candidates (max |Δ| up to 0.017 at pass 2). With the `available` filler it starts closer to the fixed
point (pass-1 change 0.003–0.013). One pass captures most of the movement; the #1 changes on 2 of these 4
boards, both between near-tied candidates.

## 4. Outcomes: paired policy evaluation, full season simulator
Harness `python -m fantasy.draft.filler_eval` (§7). It uses the same design as `fantasy.draft.policy_eval`,
whose opponent policies it imports. One job = (seed, my slot, room). The seed fixes the noisy-ADP room
order, the opponents' private projection views and the season seed, so all variants are paired (common
random numbers). Rooms: **adp** (11 noisy-ADP drafters), **symbpa** (11 value-BPA drafters on their own
perturbed views), **sym** (11 balanced optimizers on their own views). Each final league is graded with 400
seasons under our projections (**ours**) and market-implied truth (**market**). My optimizer runs with
`strategy="balanced"` (affordability; the draft-policy runs did the same) and the analytic ADP availability
(the current default). Code: main 4bab13f (valuation-0.3.0, optimizer-0.3.0, season-sim-0.5.0, unified game
noise); projections sha256 6acf9cce….

Slot 1, n = 150 paired drafts per room. The opt_market column is the level (± SE); every other column is the
paired difference vs opt_market (± paired SE):

**P(playoffs)**

| room / truth | opt_market | BPA − opt | board − opt | available − opt | refine1 − opt | refine2 − opt | avail+ref1 − opt |
|---|---|---|---|---|---|---|---|
| adp / ours | 0.935 ± 0.002 | +0.0066 ± 0.0016 | +0.0005 ± 0.0011 | +0.0004 ± 0.0012 | −0.0000 ± 0.0011 | −0.0003 ± 0.0011 | −0.0008 ± 0.0013 |
| adp / market | 0.781 ± 0.004 | −0.0289 ± 0.0033 | −0.0002 ± 0.0019 | +0.0045 ± 0.0023 | −0.0009 ± 0.0020 | +0.0008 ± 0.0020 | +0.0006 ± 0.0022 |
| symbpa / ours | 0.656 ± 0.005 | +0.0371 ± 0.0045 | −0.0014 ± 0.0034 | −0.0080 ± 0.0035 | +0.0002 ± 0.0027 | −0.0003 ± 0.0029 | −0.0026 ± 0.0030 |
| symbpa / market | 0.745 ± 0.004 | −0.0435 ± 0.0042 | −0.0003 ± 0.0032 | −0.0033 ± 0.0033 | +0.0034 ± 0.0029 | +0.0029 ± 0.0028 | −0.0001 ± 0.0031 |
| sym / ours | 0.717 ± 0.005 | +0.0586 ± 0.0055 | −0.0017 ± 0.0046 | −0.0126 ± 0.0059 | −0.0043 ± 0.0050 | −0.0032 ± 0.0050 | −0.0025 ± 0.0047 |
| sym / market | 0.666 ± 0.005 | −0.0663 ± 0.0051 | +0.0015 ± 0.0045 | −0.0035 ± 0.0053 | +0.0029 ± 0.0047 | +0.0023 ± 0.0046 | −0.0007 ± 0.0048 |

**P(title)**

| room / truth | opt_market | BPA − opt | board − opt | available − opt | refine1 − opt | refine2 − opt | avail+ref1 − opt |
|---|---|---|---|---|---|---|---|
| adp / ours | 0.407 ± 0.004 | −0.0208 ± 0.0036 | −0.0017 ± 0.0027 | −0.0044 ± 0.0028 | −0.0004 ± 0.0026 | +0.0021 ± 0.0028 | +0.0016 ± 0.0030 |
| adp / market | 0.177 ± 0.003 | −0.0365 ± 0.0030 | −0.0032 ± 0.0021 | +0.0026 ± 0.0022 | +0.0021 ± 0.0022 | +0.0041 ± 0.0023 | +0.0047 ± 0.0023 |
| symbpa / ours | 0.186 ± 0.003 | +0.0145 ± 0.0025 | +0.0021 ± 0.0022 | −0.0002 ± 0.0024 | −0.0001 ± 0.0019 | −0.0014 ± 0.0023 | −0.0006 ± 0.0023 |
| symbpa / market | 0.255 ± 0.004 | −0.0523 ± 0.0043 | +0.0069 ± 0.0032 | +0.0020 ± 0.0034 | +0.0032 ± 0.0029 | −0.0004 ± 0.0030 | +0.0026 ± 0.0030 |
| sym / ours | 0.207 ± 0.003 | +0.0092 ± 0.0036 | −0.0045 ± 0.0029 | −0.0078 ± 0.0036 | −0.0023 ± 0.0033 | −0.0008 ± 0.0034 | −0.0054 ± 0.0035 |
| sym / market | 0.167 ± 0.004 | −0.0656 ± 0.0041 | −0.0034 ± 0.0029 | −0.0002 ± 0.0041 | +0.0027 ± 0.0035 | +0.0006 ± 0.0036 | −0.0043 ± 0.0036 |

The SE target (≲ 1 pp on P(playoffs)) is met: paired SEs are 0.1–0.6 pp. In 60 placeholder cells (5 variants ×
3 rooms × 2 truths × 2 metrics), 5 have |z| ≥ 2. Pure chance would give about 3. Three of the five are
`available` and *negative*, all under our-projection truth in the smart rooms (sym P(po) z −2.15, symbpa P(po)
z −2.25, sym P(title) z −2.18). The other two are positive P(title) cells under market truth: board in symbpa,
+0.7 ± 0.3 pp (z 2.15), and avail+refine1 in adp, +0.5 ± 0.2 pp (z 2.0). No variant is better than the
default in a way that is consistent across rooms and truths. Max |z| per variant: board 2.15, available 2.25,
refine1 1.18, refine2 1.80, avail+refine1 2.0.

**How often the #1 changes** (same board: every variant evaluated on the default's own draft path; share of
150 boards per pick; adp / symbpa / sym):

| my pick | board | available | refine1 | refine2 | avail+ref1 | (BPA) |
|---|---|---|---|---|---|---|
| 1 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 24 | .02 / .13 / .21 | .51 / .25 / .56 | .12 / .17 / .41 | .11 / .24 / .41 | .45 / .19 / .45 | .56 / .39 / .81 |
| 36 | .34 / .51 / .40 | .53 / .51 / .58 | .48 / .41 / .33 | .48 / .39 / .33 | .50 / .56 / .51 | .99 / .59 / .81 |
| 37 | .19 / .16 / .11 | .37 / .23 / .23 | .33 / .16 / .14 | .36 / .15 / .17 | .37 / .17 / .21 | .95 / .39 / .43 |
| 60 | .53 / .44 / .16 | .39 / .52 / .39 | .20 / .34 / .26 | .24 / .37 / .26 | .40 / .48 / .43 | .56 / .77 / .90 |
| 61 | .15 / .11 / .20 | .18 / .16 / .35 | .13 / .13 / .18 | .13 / .17 / .20 | .19 / .18 / .31 | .17 / .38 / .54 |
| 84 | .24 / .36 / .29 | .37 / .51 / .49 | .22 / .25 / .21 | .21 / .27 / .23 | .41 / .38 / .35 | .41 / .65 / .95 |
| 85 | .03 / .11 / .10 | .10 / .11 / .19 | .09 / .08 / .09 | .07 / .08 / .09 | .07 / .10 / .16 | .17 / .50 / .65 |
| 108 | .19 / .29 / .23 | .31 / .43 / .37 | .09 / .23 / .09 | .10 / .23 / .09 | .31 / .37 / .31 | .58 / .58 / .71 |
| 109–156 | ≤ .15 | ≤ .15 | ≤ .11 | ≤ .10 | ≤ .14 | .27–.70 |

From pick 109 on, the only later picks are 132/133/156, so the placeholder hardly matters. At pick 156 there
is none, and every variant is identical by construction.

Roster-level effect, slot 1 (mean over 150 drafts):

| room | same 13 as default (board / avail / ref1 / ref2 / avref1) | players shared with default (of 13) | Δ category-model ECW vs default |
|---|---|---|---|
| adp | .12 / .02 / .19 / .15 / .04 | 11.8 / 11.1 / 11.6 / 11.6 / 11.1 | −0.006 … +0.000 (SE 0.003) |
| symbpa | .05 / .03 / .13 / .11 / .05 | 10.5 / 10.0 / 10.6 / 10.6 / 10.1 | −0.009 … +0.001 |
| sym | .15 / .00 / .13 / .13 / .05 | 9.1 / 6.7 / 8.2 / 8.0 / 7.7 | −0.021 … −0.007 |

Even the optimizer's own objective (expected category wins, as the category model scores the final roster)
does not improve under any variant. Refinement improves the *assumption* about the rest of the roster, but
the realised roster is not better.

**Slots 6 and 12** (n = 75 paired drafts per slot × room; adp and symbpa rooms only, for compute reasons;
variants board, available and refine1). Paired difference vs the default optimizer, ± paired SE:

| slot / room / truth | opt_market P(po) | board | available | refine1 | opt_market P(title) | board | available | refine1 |
|---|---|---|---|---|---|---|---|---|
| 6 / adp / ours | 0.933 | −0.0015 ± 0.0019 | −0.0008 ± 0.0020 | −0.0010 ± 0.0019 | 0.360 | **−0.0137 ± 0.0049** | +0.0046 ± 0.0042 | −0.0012 ± 0.0038 |
| 6 / adp / market | 0.754 | +0.0012 ± 0.0032 | +0.0032 ± 0.0032 | +0.0059 ± 0.0027 | 0.132 | −0.0002 ± 0.0032 | +0.0033 ± 0.0030 | +0.0012 ± 0.0029 |
| 6 / symbpa / ours | 0.578 | **−0.0124 ± 0.0054** | −0.0007 ± 0.0055 | +0.0038 ± 0.0055 | 0.129 | −0.0065 ± 0.0030 | +0.0032 ± 0.0035 | +0.0017 ± 0.0026 |
| 6 / symbpa / market | 0.712 | +0.0062 ± 0.0051 | −0.0105 ± 0.0058 | +0.0023 ± 0.0055 | 0.220 | +0.0036 ± 0.0045 | −0.0011 ± 0.0055 | −0.0030 ± 0.0043 |
| 12 / adp / ours | 0.924 | −0.0001 ± 0.0017 | −0.0037 ± 0.0024 | −0.0002 ± 0.0018 | 0.327 | −0.0055 ± 0.0043 | −0.0080 ± 0.0044 | +0.0019 ± 0.0038 |
| 12 / adp / market | 0.724 | +0.0045 ± 0.0033 | +0.0008 ± 0.0029 | +0.0059 ± 0.0029 | 0.108 | −0.0015 ± 0.0026 | −0.0006 ± 0.0027 | +0.0027 ± 0.0017 |
| 12 / symbpa / ours | 0.573 | −0.0037 ± 0.0049 | +0.0002 ± 0.0056 | +0.0023 ± 0.0047 | 0.127 | −0.0044 ± 0.0027 | −0.0044 ± 0.0025 | +0.0028 ± 0.0028 |
| 12 / symbpa / market | 0.698 | +0.0027 ± 0.0047 | −0.0035 ± 0.0049 | +0.0020 ± 0.0040 | 0.200 | −0.0025 ± 0.0044 | −0.0033 ± 0.0046 | +0.0047 ± 0.0036 |

The pattern is the same as at slot 1. `board` has two significant *losses* at slot 6: −1.4 ± 0.5 pp P(title)
(z −2.8) and −1.2 ± 0.5 pp P(playoffs) (z −2.3). `refine1` is never significantly worse; its largest
effects are +0.6 ± 0.3 pp P(playoffs) under market truth in the adp rooms (z 2.2 and 2.0). Paired SEs here are
0.2–0.6 pp. (For reference, BPA vs the default at slots 6/12 shows the same sign pattern as at slot 1:
better P(playoffs) on our truth in symbpa rooms, worse under market truth.)

**With the room-simulated availability model** (`availability="sim"`, room mixed, the draft-policy agent's
opt-in fix for the availability bias). Slot 1, n = 100 paired drafts per room, same variants. I added this check
because the placeholders inherit whatever availability model the optimizer uses. Here the default column is
the sim-availability optimizer with the market filler:

| room / truth | opt(sim) P(po) | board | available | refine1 | refine2 | avail+ref1 |
|---|---|---|---|---|---|---|
| adp / ours | 0.939 ± 0.002 | −0.0014 ± 0.0012 | −0.0003 ± 0.0009 | +0.0003 ± 0.0010 | −0.0003 ± 0.0009 | −0.0011 ± 0.0010 |
| adp / market | 0.765 ± 0.004 | −0.0002 ± 0.0025 | +0.0021 ± 0.0017 | +0.0019 ± 0.0021 | +0.0011 ± 0.0021 | +0.0016 ± 0.0021 |
| symbpa / ours | 0.688 ± 0.006 | +0.0036 ± 0.0031 | +0.0033 ± 0.0028 | **+0.0064 ± 0.0028** | +0.0039 ± 0.0028 | +0.0052 ± 0.0032 |
| symbpa / market | 0.716 ± 0.007 | +0.0002 ± 0.0029 | +0.0025 ± 0.0032 | +0.0048 ± 0.0028 | +0.0037 ± 0.0032 | +0.0036 ± 0.0034 |
| sym / ours | 0.775 ± 0.005 | −0.0039 ± 0.0049 | −0.0008 ± 0.0046 | −0.0039 ± 0.0034 | −0.0014 ± 0.0042 | +0.0006 ± 0.0041 |
| sym / market | 0.626 ± 0.006 | −0.0042 ± 0.0051 | +0.0041 ± 0.0040 | +0.0010 ± 0.0033 | +0.0047 ± 0.0039 | +0.0027 ± 0.0041 |

P(title) differences are all within ±0.35 pp (max |z| 1.4). With sim availability the variants change the #1
less often: 4–45% of boards at picks 24–37 and ≤ 23% later, against 10–58% under the analytic model. The one
|z| ≥ 2 cell is refine1 in symbpa/ours: +0.6 ± 0.3 pp P(playoffs), z 2.3, one of 30 P(po)/P(title) cells. So the
conclusion does not depend on the availability model. (For reference, the sim-availability optimizer is
within ±1 pp of BPA on our truth in all three rooms and better than BPA under market truth, consistent with
draft_policy.md.)

## 5. Timings
`DraftOptimizer.recommend` at the default settings (full 8-strategy scan), in-process, at picks 1/24/36 of the
mock. Median of 3. The box was shared (load average ≈ 12 on 4 cores), so ratios matter more than seconds:

| variant | seconds at #1 / #24 / #36 | × default | within the ≤ 2× target? |
|---|---|---|---|
| market (default) | 1.06 / 0.96 / 1.07 | 1.00 | — |
| board | 1.07 / 1.09 / 1.23 | 1.10 | yes |
| available | 1.10 / 1.10 / 1.37 | 1.15 | yes |
| refine 1 | 2.06 / 1.87 / 1.87 | 1.88 | yes |
| available + refine 1 | 1.68 / 1.75 / 2.50 | 1.92 | yes |
| refine 2 | 2.62 / 2.53 / 3.06 | 2.66 | **no** |

The CLI adds the policy block and the advisory value search on top (≈ 3–4 s wall in total, mostly not the
optimizer). In the balanced-strategy evaluation runs, the median per-recommend time was 0.11 s (market), 0.15
(available), 0.17 (board), 0.21 (refine 1), 0.24 (avail + refine 1) and 0.30 (refine 2).

## 6. Conclusion and recommendation
In plain language: the user's two concerns are logically right but quantitatively unimportant. The
placeholder for my later picks does include already-drafted players, but almost only at the back-to-back turn
picks. A placeholder that "knows" the rest of my plan does favour specialists, but by about a third of what
counts as a tie. Fixing either one changes the recommended player fairly often, because many candidates are
near-tied. It does not change how good the final team is under the season simulator (all paired differences
≤ 0.5 pp, SE 0.1–0.6 pp), except that the best-available placeholder is slightly *worse* in smart rooms.

**Recommendation for the lead/user: keep the default (`filler="market"`, `refine_passes=0`).** Placeholder
work is not where the remaining gap to BPA/value search comes from. The availability model is
(docs/research/draft_policy.md: `availability="sim"`). If the lead wants a complement-aware rollout anyway,
`--refine 1` is the variant closest to the default (all |z| ≤ 1.2). It costs about 1.9×
compute, and it is not justified by these results.

**Caveats.** All grading is in-sample in the sense of value_search.md: our projections, the same simulator,
and uncalibrated simulator priors. The market truth is a stress test. My optimizer used the balanced strategy
(the strategy scan rarely switches, and the draft-policy evaluation did the same). The specialist diagnosis
uses one deterministic mock board per pick, so its SEs are over players, not boards. The independence
approximation in `available` (q_i) is the same one the rollout uses. The analytic `prob_available` under-states
how long players last (draft_policy.md), which shifts both `board` and `available` placeholders toward earlier
(more valuable) players. The sim-availability check in §4 shows the conclusion holds under the corrected model.

## 7. Reproduce
```bash
# outcome evaluation (slot 1, ~3.7 h with 3 workers on a shared 4-core box)
python -m fantasy.draft.filler_eval run --seeds 0:150 --slots 1 --regimes adp,symbpa,sym --workers 3 --out slot1.jsonl
python -m fantasy.draft.filler_eval run --seeds 0:75 --slots 6,12 --regimes adp,symbpa \
    --policies bpa,opt_market,opt_board,opt_avail,opt_ref1 --workers 3 --out slot612.jsonl
python -m fantasy.draft.filler_eval run --seeds 0:100 --slots 1 --regimes adp,symbpa,sym --availability sim --out slot1_sim.jsonl
python -m fantasy.draft.filler_eval summary --out slot1.jsonl
# opt-in at draft time
fantasy draft recommend --filler available --refine 1
```
The specialist, convergence, taken-window and timing scripts were one-off research scripts. They are
summarised in §2, §3 and §5; their method is fully described there and they used only `OptimizerParams`,
`DraftContext.board_filler` and `DraftOptimizer.candidate_rollout`.
