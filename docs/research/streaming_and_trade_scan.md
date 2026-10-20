# Streaming scan (T-006) and trade scan (T-008)

_2026-10-20, `inseason-0.3.0` (was 0.2.0; §0 lists the quant-review fixes and supersedes the 0.2.0 examples in §1-2).
Code: `src/fantasy/inseason/streamscan.py`, `tradescan.py`, `tradeeval.py`, `freeagents.py`, `possim.py` (mid-season
roster windows, replacement-level ids), CLI `fantasy inseason stream`, `trade`, `trade-scan`. Tests:
`tests/test_inseason_scan.py`, `tests/test_inseason_review_fixes.py` (synthetic, offline). Validation script:
`docs/research/trade_scan_validation_2026-10-20.py`. Analysis only: nothing here touches Yahoo, and nothing was planned
in the txn ledger or executed._

**Labels** (CLAUDE.md evidence standard). *Simulated* = season simulator output (`season-sim`, fitted parameters
from `sim_calibration.md`; ± is the paired Monte Carlo SE only, not model error). *Model* = the lineup planner's
analytic matchup model (`lineup_optimizer.md`: fitted game noise and projection error, availability priors).
*Schedule* = counted from the NBA schedule. *Prior* = an assumption, not fitted. *Assumption* = a rule of Yahoo
we have not verified on a processed transaction.

## 0. inseason-0.3.0: review fixes and decision evidence (2026-10-20)

### 0.1 What the independent quant review found, and what changed

| # | Finding (0.2.0) | Fix (0.3.0) |
|---|---|---|
| B1 | The stream season check kept the streamer **all season** (cost ≈ dropped − streamer). | `make_stream_season_eval`: streamer only for his **stint** (effective day → end of the matchup week, or `--stint-days`), then the (k+1)-th best realistic free agent F_k for the rest of the season (k = 0, `--chain-k` 5, replacement level). Also the *direct* move (drop → F_k, no stream) and `stream_vs_direct` (paired). Runs under every projection set. Cost ≈ (dropped − F_k) × games. |
| M1 | Trades compared with the untouched roster and refilled with the best free agent (Champagnie). | Base = league **after my best free move** (`--free-move auto`: best realistic FA for one of my 3 lowest-value players, if Δcat-wins > 2 SE and nothing < −2 SE). If a trade gives away the move's drop, the move drops my next-lowest player instead. Refill default = 6th-best realistic FA (`--refill-skip 5`) or `--refill-replacement`; the best remaining FA is reported only as `optimistic_me`. |
| M2 | Schedule-driven title gains not separated. | Schedule-neutral re-run: incoming players given the outgoing players' NBA schedule (by value rank), paired base re-run; `schedule_bet` when > half of a positive ΔP(title) is schedule. |
| M3 | "helps both" used blend + market, which share our availability model. | Third set `stress` = ESPN stats **and** ESPN games (`fantasy data alt-projections --blend espn-only --blend-gp espn=1`); output states the shared-availability caveat. |
| M4 | Shortlist judged on the screen's seed; P(title)-only gains called "helps". | Stage 2 re-simulates the top rows with fresh seeds (≥ 5), SE = max(paired, seed-to-seed)/√n, reports the empirical factor; class "helps" only if ΔP(title) > 2 SE and neither ΔP(playoffs) nor Δcat-wins < −1 SE; title-only = "speculative". |
| M5 | Partner acceptability by summed value only. | `partner_ok` = partner's simulated ΔP(playoffs) ≥ −1 pp under every set (prior threshold); `win_win`. |
| M6 | `fantasy inseason trade` refilled from all unrostered players (incl. O-tagged). | Same realistic capture pool as the scan (O/INJ skipped), same evaluator (`tradeeval.evaluate_trade`), seeds, sets. |
| minor | `date.today()` for FA loading; refill/drop order from blend values in every set; adds budget ignored the follow-up claim. | `--date`; each set orders drops/refills by its own values; `adds_needed` counts the stream claim and the chain claim (this week / next week). Also `--waiver-lag` (default 1, **assumption**: a claim processed on Oct 22 plays from Oct 23). |

Measured seed-to-seed factor (sd of the delta across seeds / mean paired SE): 0.5-1.6 for ΔP(title) in the runs below
(simulated); the paired SE is roughly right on average but can understate by ~1.6×, so the SEs below use the larger.

### 0.2 Free move: add a free agent (claim clears Oct 22, plays from Oct 23), drop Bailey / Fears / Sheppard

Season deltas (simulated, position-aware season sim, **8 seeds × 2000 paired sims**, effective Oct 23, pp ± seed-aware
SE). Adds: Julian Champagnie (best realistic FA under all three sets) and the next best by set: Filipowski (blend #2,
Q), Sexton (market #2, Q), Mitchell Robinson (stress #2), Ayton (blend #3). O-tagged players skipped. Both captures
(`free_agents_2026-10-20.json`, `..._T0445Z_top50.json`) list them all as W (Oct 22).

| Add | Drop | blend ΔP(po) / ΔP(title) / Δcat-wins | market | stress (ESPN stats + gp) |
|---|---|---|---|---|
| **Champagnie** | **Bailey** | **+0.68±0.15 / +1.76±0.27 / +0.45±0.03** | **+1.06±0.17 / +1.57±0.26 / +0.58±0.03** | **+1.09±0.18 / +2.36±0.26 / +0.55±0.04** |
| Champagnie | Fears | +0.26±0.15 / −0.88±0.30 / +0.36±0.03 | +1.03±0.17 / +0.11±0.30 / +0.66±0.03 | +1.31±0.23 / +0.72±0.30 / +0.78±0.03 |
| Champagnie | Sheppard | −0.41±0.18 / +0.78±0.28 / −0.53±0.04 | −0.62±0.22 / +0.34±0.27 / −0.48±0.04 | −2.21±0.20 / +0.06±0.28 / −1.30±0.04 |
| Sexton | Bailey | −0.76±0.16 / +0.67±0.29 / −0.40±0.03 | −0.46±0.17 / +0.81±0.31 / −0.02±0.03 | −0.44±0.20 / +0.57±0.28 / −0.18±0.03 |
| Filipowski | Bailey | −0.62±0.16 / −0.28±0.29 / −0.40±0.03 | −0.64±0.18 / −0.99±0.31 / −0.40±0.03 | −0.67±0.18 / −1.50±0.30 / −0.57±0.03 |
| Ayton | Bailey | −0.85±0.17 / −1.25±0.28 / −0.64±0.03 | −1.43±0.18 / −1.87±0.31 / −0.78±0.03 | −1.67±0.18 / −2.42±0.29 / −1.08±0.03 |
| M. Robinson | Bailey | −1.79±0.17 / −1.59±0.29 / −1.40±0.03 | −1.15±0.19 / −0.77±0.29 / −0.76±0.03 | −0.08±0.19 / +0.08±0.30 / −0.35±0.05 |
| any other add | Fears or Sheppard | all negative on ΔP(po) or ΔP(title) in ≥ 2 sets (e.g. Filipowski/Fears −0.8 / −3.0 / −0.50 blend) | | |

This week (model, lineup planner, week 1 vs Joshua's Excellent Team, Oct 20-25; paired MC 4000 sims): Champagnie for
Bailey: Champagnie starts 2 of his 2 games (Oct 23-25), Bailey loses 2; ΔE[cats] −0.009, **ΔP(win) −0.9 ± 0.45 pp**.
Champagnie for Fears: −0.35 ± 0.39 pp (noise).

**Verdict (simulated): add Julian Champagnie, drop Ace Bailey.** It is the only move that raises ΔP(playoffs),
ΔP(title) and category wins beyond 2 SE under all three projection sets (title +1.6 to +2.4 pp, 6-9 SE), at a
week-1 cost of about −1 pp P(win) (model, ~2 SE). It needs one add (6/week) and the waiver claim (we hold priority 1:
user-reported, not verified). Needs the user's approval.

### 0.3 Trades for Jamal Murray (Yeet), vs the base with Champagnie-for-Bailey

Simulated, **6 fresh seeds × 2000 paired sims**, three sets, realistic refill (6th-best realistic FA of each set:
Royce O'Neale / Aaron Nesmith / Scotty Pippen Jr.), partner Yeet drops Day'Ron Sharpe. T1 gives away Bailey, so
its world makes the free move with Fears instead (Champagnie for Fears); net vs base T1 = Brunson + Fears out,
Murray + O'Neale in. pp ± seed-aware SE.

| Trade | Set | me ΔP(po) | me ΔP(title) | me Δcat-wins | ΔP(title) schedule-neutral | partner ΔP(po) / ΔP(title) | class |
|---|---|---|---|---|---|---|---|
| T1 Brunson + Bailey → Murray | blend | +0.38±0.21 | +4.06±0.68 | +0.23±0.06 | −0.66±0.48 | −0.83 / −3.98 | helps |
| | market | +0.54±0.37 | +5.68±0.55 | +0.66±0.07 | +0.47±0.40 | −3.17 / −4.64 | helps |
| | stress | +1.39±0.26 | +8.18±0.43 | +0.90±0.06 | +3.31±0.47 | −5.84 / −6.94 | helps |
| T2 Ware + VanVleet → Murray | blend | −0.55±0.24 | +5.67±0.47 | −0.32±0.06 | +0.89±0.58 | +2.97 / −2.13 | speculative |
| | market | −1.48±0.44 | +5.20±0.61 | −0.69±0.06 | +0.84±0.40 | +1.91 / −2.30 | speculative |
| | stress | −1.05±0.28 | +7.53±0.43 | −0.85±0.08 | +2.60±0.43 | +0.77 / −4.13 | speculative |

Flags: T1 `class` helps (all sets), **`schedule_bet` yes** (schedule share of the title gain 116% / 92% / 60%),
**`partner_ok` no** (Yeet loses 0.8-5.8 pp P(playoffs)). T2 speculative (title up, P(playoffs) and cat wins down),
`schedule_bet` yes (84% / 84% / 65%), `partner_ok` yes (Yeet gains P(playoffs) in every set).
Sensitivity (simulated, same seeds): replacement-level refill T1 title +4.8 / +6.2 / +4.5, T2 +7.1 / +6.0 / +5.3 (same
classes); optimistic refill (best remaining FA, Filipowski/Sexton/Robinson) T1 +4.2 / +6.8 / +6.2. The 0.2.0
framing (untouched base, Champagnie refill) gave T1 +9.8 / +8.9 / +10.2 and T2 +8.3 / +6.6 / +8.4: about half of
the old headline was the free move that needs no trade.

**Verdict (simulated, decision rule above):**
- T1: the title gain is real in all three sets but is a **schedule bet** — Denver's 4-game playoff weeks 19-20
  (schedule) explain all of it under blend and market (schedule-neutral ΔP(title) ≈ 0); only the stress set
  (ESPN stats + games, which likes Murray more) keeps +3.3 pp schedule-neutral. It also fails the partner check, so a
  rational Yeet should refuse it. Not recommended as a priority; acceptable only if the user wants a schedule bet
  and Yeet accepts.
- T2: **speculative** — +5-8 pp title bought with −0.5 to −1.5 pp P(playoffs) and −0.3 to −0.9 cat-wins/season,
  mostly schedule (schedule-neutral +0.8 to +2.6 pp). Partner-acceptable. Not "helps" under the rule.
- Neither should precede the free move; both need the user's approval.

### 0.4 Stream question for this week, under the realistic chain

`fantasy inseason stream --date 2026-10-20 --free-agents data/raw/yahoo/free_agents_2026-10-20.json --season-sims
2000 --season-top 8` (three sets, chain k = 5, waiver lag 1 → streamers play Oct 23-25, chain claim Oct 26). The best
week gains (model + MC): Mikel Brown Jr. / Fears +2.5 ± 0.5 pp P(win), Noah Clowney / Fears +2.6 ± 0.4, Keon Ellis /
Bailey +2.5 ± 0.5, Keon Ellis / Fears +1.7 ± 0.4. Season (simulated, 2000 sims, one seed):
- `stream_vs_direct` (what the 3-day stream itself adds to the season) is within about ±0.5 pp on ΔP(playoffs) and
  ΔP(title) for every pair and set (SE 0.1-0.2 pp); the stream is roughly free for the season.
- The chain's cost is the **direct move**: e.g. Fears → Bobby Portis (F_5): ΔP(po) −1.85 ± 0.46, ΔP(title)
  −1.25 ± 0.83 (blend); Bailey → Portis: ΔP(po) −1.35 ± 0.45. With F_0 = Champagnie the chain is fine, but that
  is just the free move of §0.2.
- Recommendation: **none**; every shortlisted pair is "hurts" or "noise" under at least one set. No stream beats
  noise under the realistic chain. A stream would also use the add slot that the Champagnie claim needs on the
  same days (Oct 22-23) and a second add on Oct 26.

**Corrected verdict:** the 0.2.0 conclusion (streaming costs 2-7 pp of title) was wrong in size — it charged the
streamer's whole season. Under the realistic chain the stream itself costs ~0 for the season and gains 2-3 pp of
week-1 P(win) (model), but it is only worth doing if we would make the underlying drop → F_k move anyway, which we
would not (it hurts ΔP(playoffs) for Fears and Bailey alike unless F_k is Champagnie). Do the Champagnie-for-Bailey
claim instead.

## 1. Streaming: `fantasy inseason stream`

### What it answers
"Which free agent should I add this week, and whom should I drop?" For every (free agent c, drop candidate x)
pair, for the rest of the current matchup week against the real opponent, and for the rest of the season.

### Method
1. **Inputs.** Rosters `data/manual/league_rosters_2026-27.json`; injury tags and Yahoo positions from the newest
   roster capture; the free-agent capture (`data/raw/yahoo/free_agents_*.json`); league schedule
   `data/manual/league_schedule.json` (week dates and my opponent); bbref NBA schedule.
2. **Timing** (assumption, `freeagents.py`). A free agent (`ownership = "FA"`) plays from today
   (`weekly_deadline = daily_today`). A waiver player (`"W (Oct 22)"`) plays from the day the claim processes,
   and the dropped player keeps playing until then. The 2026-10-20 capture has no per-player ownership, so its
   note ("all listed players were on waivers W (Oct 22)") is applied to all players (`waiver_source = capture notes`).
   Waivers are a continual rolling list, so a claim can lose to a team ahead of us.
3. **Week value** (model). The baseline is the lineup planner (lineup-0.2.0): my matchup-aware daily lineups
   against the opponent's best-players lineup, with category weights w_c = dE[categories won]/d(my weekly total)
   (the gradient in `lineup/value.py`). For a pair, the roster holds both players and is cut by day: x's games
   are kept before the effective day and c's from it (one extra bench row, so the slot rules are unchanged).
   - `week_linear_gain`: per-game value = P(plays) × Σ_c w_c δ_c at the *baseline* weights. The daily lineups are
     re-solved position-aware, and the gain is Σ started values minus the baseline. Only games where c fills an
     open slot or displaces a lower-value starter count.
   - `week_d_exp_cats` / `week_d_p_win`: the planner re-optimised (fixed point), with full moments.
   - `week_mc` (shortlist only): paired Monte Carlo of the matchup. Draws are keyed by player id, so every common
     player has identical draws in both arms. Gives the SE.
4. **Season value** (simulated). This is a paired position-aware season sim of the swap from its effective day:
   `PositionAwareSimulator.run(league, windows)`, with x active until the effective day and c from it. Injury-spell
   intervals (used by the IL replacement) are clipped to the same windows. No random numbers are added, so the run
   stays paired with the unchanged league (test `test_windows_keep_runs_paired_and_cut_games`). Reports
   ΔP(playoffs), ΔP(title) and Δ category wins ± paired SE. **0.3.0 (B1):** c is active only for his stint, then
   the chain free agent F_k (k = 0, 5, replacement level); the direct move x → F_k and `stream_vs_direct` are
   reported too, under every projection set (blend, market, stress). 0.2.0 kept c all season (wrong).
5. **Shortlist** for the expensive steps: half by this week's gain, half by the cheap season proxy
   `season_proxy_d_value` = valuation value(c) − value(x) (season G-score totals above replacement).
6. **Decision rule.** Recommend a pair only if its season chain (F_5) ΔP(playoffs) or ΔP(title) is > 2 paired SE and
   neither is < −2 SE under every projection set, and the stream is not < −2 SE vs the direct move. Otherwise recommend nothing. Pairs whose only significant gain is this week's ΔP(win) go in
   `week_only`, with the season ΔP(title) lower bound, for the user to weigh. They are never recommended.
   Every add/drop needs the user's approval. The output also reports the adds budget (`--adds-used`; 6 per
   week) and blocks recommendations when it is exhausted.

Priors used: `rest_share` 0.25 and the injury-tag P(plays) (lineup planner); the waiver timing above; the
opponent sets a generic best-players lineup; other teams never add or drop in the season sim.

### Validation
- **Linear vs re-optimised week gain:** Pearson 0.999 over the 20 listed pairs (real league, week 1). The first-order
  gradient value is enough for ranking. The re-optimised number is reported because it is cheap.
- **Analytic vs Monte Carlo week Δ:** mean |Δ E[cats] analytic − MC| = 0.012, against a mean MC SE of 0.010
  (10 shortlisted pairs, 4000 paired sims).
- **Season proxy vs simulated season Δ** (10 shortlisted pairs, 4000 paired sims). Spearman ρ = 1.00 with Δ category
  wins, 0.98 with ΔP(playoffs), 0.65 with ΔP(title). The proxy is good enough to choose which pairs to simulate,
  but not to judge title odds; that is why the sim is the deciding number.

### Example: real league, Tue 2026-10-20, week 1 vs Joshua's Excellent Team (inseason-0.2.0; SUPERSEDED by §0.4: the season columns kept the streamer all season)
`fantasy inseason stream --date 2026-10-20 --week-sims 4000 --season-sims 4000 --season-top 10` (387 pairs: 129
free agents × 3 drop candidates Bailey / Fears / Sheppard; every free agent clears waivers Thu Oct 22).
Baseline (model): E[cats] 4.92, P(win) 0.621. This is the planner's figure with today's tags. The season sim's week-1
figure is 0.73 because it uses season-rate availability; see the main-session handoff.

Top 5 by this week's gain (the season columns are simulated, 4000 paired sims, percentage points):

| Add (W Oct 22) | Drop | starts/games | ΔE[cats] wk (model) | ΔP(win wk) MC | ΔP(playoffs) | ΔP(title) | verdict |
|---|---|---|---|---|---|---|---|
| Anfernee Simons | Jeremiah Fears | 3/3 | +0.208 | +0.059 ± 0.005 | −2.1 ± 0.3 | −4.2 ± 0.6 | hurts |
| Anfernee Simons | Reed Sheppard | 3/3 | +0.184 | +0.046 ± 0.005 | −2.6 ± 0.4 | −2.4 ± 0.6 | hurts |
| Kentavious Caldwell-Pope | Jeremiah Fears | 3/3 | +0.174 | +0.042 ± 0.004 | −3.8 ± 0.4 | −6.6 ± 0.6 | hurts |
| Kentavious Caldwell-Pope | Ace Bailey | 3/3 | +0.163 | +0.038 ± 0.005 | −2.8 ± 0.3 | −4.2 ± 0.5 | hurts |
| Christian Braun | Jeremiah Fears | 2/2 | +0.160 | +0.036 ± 0.004 | −1.8 ± 0.3 | −2.9 ± 0.6 | hurts |

The best season-proxy pair is Julian Champagnie for Ace Bailey. Its week effect is about 0. Season (simulated):
ΔP(playoffs) +0.3 ± 0.3, ΔP(title) +0.5 ± 0.5, Δ category wins +0.37 ± 0.06. It is "noise" on the decision
variables.

**Result: no recommendation.** Streaming a guard for the three Oct 22-25 games raises this week's P(win) by
4-6 pp. But every drop costs 2-7 pp of P(title) over the season, because our 13th man is better than any
streamer. This matches the hand analysis in `week1_and_trades_2026-10-20.md`. The category weights this week
(model) favour BLK, STL and the percentages; PTS and REB are nearly settled.

## 2. Trade scan: `fantasy inseason trade-scan`

### Method
1. **Enumerate** all trades with each of the 11 other teams: 1-for-1 (169), I give 2 and get 1 (1014), I give 1 and
   get 2 (1014). That is 24,167 trades in total. Rosters return to 13. A short roster adds the best *realistic* free
   agent of the latest capture: not tagged O/INJ/NA, best by blend value (assumption: that team wins the claim). A
   long roster drops its lowest-value player. `drops_incoming` flags 1-for-2 deals where I would drop a player I
   just received.
2. **Screen** (deterministic, about 50 s). The proxy is all-play expected categories won per week against the
   average other team, Σ_c Φ(sign_c (μ_me − μ_u)/√(sd_me² + sd_u²)) averaged over u:
   - Each team's weekly category means and sds come from the base season sim.
   - A trade shifts a team's means by the change in a linear weekly line: Σ_i start_share(rank_i) × games/week_i ×
     per-game line_i, with percentages from makes/attempts.
   - games/week = the NBA team's regular-season games per fantasy week × gp/82.
   - start_share(rank) is measured on the real league by the position-aware lineup Monte Carlo (iid gp/82
     availability, a prior): 1.00 for ranks 1-10, then 0.997, 0.990, 0.985.
   - A second proxy uses the same formula anchored on the playoff weeks (18-20) with playoff-week games.

   Keep a trade if both proxies say it might pass for the partner (priors): by rankings (Σ value given − received)
   the partner loses ≤ 0.5, and the partner's proxy falls by ≤ 0.05 cats/week. Then, per partner, keep the top 3
   by my regular proxy plus the top 1 by my playoff proxy (cap 44).
3. **Simulate** the shortlist: paired position-aware season sims (2000 per trade and set) under the default **blend**
   projections and the conservative **market** projections (`fantasy data alt-projections --blend market`; ESPN +
   FantasyPros, git-ignored, read from `var/projections_alt/market.csv`; see `conservative_sim.md`). Both teams'
   ΔP(playoffs), ΔP(title) and Δ category wins ± paired SE.
4. **Rule (`helps_both`)**: the trade's season verdict is "helps" under *both* projection sets. "Helps" means
   ΔP(playoffs) or ΔP(title) > 2 SE and neither < −2 SE. **0.3.0:** this is only the stage-1 screen
   (`helps_all`, three sets, base with the free move, realistic refill); the decision is the stage-2 class (§0.1 M4).
5. **Decomposition hooks**: playoff-week games (weeks 18-20, schedule) of every player in and out, the per-player
   difference, regular-season games/week, and `projection_disagreement` = blend Δ − market Δ.

Trades are simulated as if effective for the whole season. The 2-day review is ignored, so at most a few days of
week 1 are misattributed. Effective-dated trades need per-team player windows, which the engine does not have:
a player traded mid-season would sit on two rosters.

### Validation of the screen (`trade_scan_validation_2026-10-20.py`; 40 random enumerated trades, no filter, 1500 paired blend sims)

| Spearman ρ vs simulated | Δ cat wins | ΔP(playoffs) | ΔP(title) |
|---|---|---|---|
| regular-season screen | 0.975 | 0.964 | 0.768 |
| playoff-weeks screen | 0.879 | 0.881 | **0.920** |

The regular screen's sign agrees with the sign of the simulated Δ cat wins in 39 of 40 trades. Across the full
range the screen is reliable. Within the shortlist, where every trade is already good, it does not order title
gains (ρ ≈ −0.2 to −0.3 with ΔP(title), n = 44), so the sim decides.

Two things show why both screens are needed:
- **Blind spot:** the reviewed Murray trade (Ware + VanVleet for Jamal Murray) ranks 516th of 2197 Yeet trades on the
  regular screen and 199th on the playoff screen, yet simulates at ΔP(title) +10.2 ± 1.3 pp (blend) and +8.5 ± 1.2
  (market). The reviewer's +8.6 pp used a different seed and draw count. The scan therefore does not prove a trade
  absent. Check named trades with `fantasy inseason trade`.
- **What the playoff screen finds:** its addition put Brunson + Bailey for Murray at the top of the shortlist.

### Example: real league, 2026-10-20 (`--sims 2000`, market from `var/projections_alt/market.csv`) (inseason-0.2.0; SUPERSEDED by §0.3: untouched base, best-FA refill, one seed)
24,167 trades enumerated, 44 simulated. The shortlist is 42 2-for-1s and 2 1-for-2s. The refill is Julian Champagnie.
**All 44 help under both projection sets** with the best-free-agent refill. Top 5 (simulated, pp, ± paired SE):

| # | Trade (I give → I get) | Partner | Blend: me ΔP(po) / ΔP(title) | Market: me ΔP(po) / ΔP(title) | Partner ΔP(po), blend / market | Naive value give / get | Playoff games per player, get − give (wk 18/19/20) |
|---|---|---|---|---|---|---|---|
| 1 | Brunson + Bailey → Jamal Murray | Yeet | +1.8 ± 0.6 / **+12.5 ± 1.1** | +3.1 ± 0.7 / **+10.8 ± 1.0** | −1.8 / −4.0 | 4.96 / 4.98 | −0.5 / +1 / +1 |
| 2 | Sheppard + Bailey → Nickeil Alexander-Walker | Yeet | +2.1 ± 0.5 / +11.2 ± 1.0 | +4.5 ± 0.7 / +10.0 ± 1.0 | −0.8 / −4.8 | 2.97 / 3.18 | −1 / +1 / +1 |
| 3 | VanVleet + Bailey → Devin Booker | SCOTTIER BARNEY | +2.1 ± 0.6 / +9.6 ± 1.1 | +3.9 ± 0.7 / +11.0 ± 1.0 | +0.4 / −2.1 | 3.23 / 3.50 | −1 / +1 / +1 |
| 4 | Podziemski + Bailey → Kyrie Irving | Ed's Extraordinary | +1.1 ± 0.6 / +8.6 ± 1.0 | +3.1 ± 0.7 / +9.8 ± 1.0 | +0.6 / −1.8 | 3.10 / 3.41 | −2 / +0.5 / +1 |
| 5 | Podziemski + Bailey → Devin Booker | SCOTTIER BARNEY | +1.4 ± 0.6 / +9.3 ± 1.1 | +3.1 ± 0.7 / +8.3 ± 1.0 | +0.1 / −3.0 | 3.10 / 3.50 | −1 / +0.5 / +1 |

How to read it:
- **Consolidation drives every top trade.** We give two bench-level guards, at least one of them Ace Bailey (lowest
  value on our roster), take one better guard back, and refill with the best free agent. That raises the quality of
  the 10 starters. The title gain is 4-8× the playoff gain. Two things fit this, but neither is quantified here:
  - the schedule hook: each incoming player has 4 games in each of playoff weeks 19 and 20, against 3 for the
    players we give;
  - the ceiling compression noted in the main-session review.
- **Partner acceptability** (simulated partner Δ): most partners lose 1-5 pp of P(playoffs), more under market
  projections. By rankings the deals look about even to them (naive value give ≈ get), which is how a human partner
  is likely to judge (prior). The Booker deal with SCOTTIER BARNEY is the most even: partner ΔP(playoffs) +0.4 (blend).
- **Refill sensitivity** (`--refill-skip 11`: the short roster gets the 12th-best realistic free agent, Christian
  Braun, as if every other team claimed first): 33 of 44 trades still help under both sets. The top trade is still
  Brunson + Bailey for Murray, but its P(playoffs) gain disappears and only the title gain remains:
  - blend: ΔP(playoffs) −0.5 ± 0.6, ΔP(title) +9.4 ± 1.1;
  - market: ΔP(playoffs) +0.5 ± 0.7, ΔP(title) +8.8 ± 1.0.

  VanVleet + Bailey for Booker behaves the same way (title +8.8 blend / +9.3 market). 1-for-2 "throw-in" deals
  rise in the ranking: VanVleet for Reaves + Maluach, where we drop Maluach (`drops_incoming`), gives
  ΔP(title) +7.1 / +6.6. So the playoff-odds part of the consolidation gain depends on winning the waiver claim;
  the title part mostly does not.
- **Nothing is recommended for execution here.** Any trade needs the user's approval, and a proposal goes through the
  yahoo-browser skill and the txn ledger.

## Caveats
- The simulator is optimistic for us: other teams never trade, stream or adjust lineups. 0.3.0 refills with the
  6th-best realistic free agent by default (a prior); the best-FA refill is only the optimistic bound.
- ± values are Monte Carlo SEs only (0.3.0: seed-aware). Projection error is handled by comparing the projection sets,
  not by the SE. Blend and market share our fitted availability model; only the stress set (ESPN games) does not.
- The free move and trades are simulated whole-season (free move: from Oct 23); trades ignore the 2-day review.
- Waiver timing (`--waiver-lag 1`: a claim processed on the waiver date plays from the next day) is an assumption.
- The 2026-10-20 free-agent capture has no per-player waiver status. Every player is assumed `W (Oct 22)` from the
  capture note. Re-capture with `scripts/yahoo/capture_free_agents.js` for per-player ownership.
- The stream scan's week model uses today's tags (lineup planner priors). The season sim uses season-rate availability.
- The trade screen is a screen. It misses individual high-title trades (Murray example), so evaluate named ideas with
  `fantasy inseason trade --partner N --give .. --get ..`.
- Kyrie Irving's and other injured-return players' availability comes from our projections (gp, status). Read the
  partner's injury news before acting.

## Reproduce
```bash
# stress set (third-party numbers stay git-ignored):
fantasy data alt-projections --blend espn-only --blend-gp espn=1 --out var/projections_alt/espn-only-gp.csv
# §0.4 (inseason-0.3.0)
fantasy inseason stream --date 2026-10-20 --free-agents data/raw/yahoo/free_agents_2026-10-20.json \
    --season-sims 2000 --season-top 8 --week-sims 4000 --json
# §0.2 week effect: same with --drop "Ace Bailey,Jeremiah Fears,Reed Sheppard" --season-sims 0 --season-top 400
# §0.3 (seeds 101.. by default; the note used seeds 301-306 via tradeeval.evaluate_trade)
fantasy inseason trade --partner 6 --give "Jalen Brunson,Ace Bailey" --get "Jamal Murray" --free-move \
    "Julian Champagnie:Ace Bailey" --seeds 6 --date 2026-10-20 --json
fantasy inseason trade --partner 6 --give "Kel'el Ware,Fred VanVleet" --get "Jamal Murray" --free-move \
    "Julian Champagnie:Ace Bailey" --seeds 6 --date 2026-10-20 --json
fantasy inseason trade-scan --date 2026-10-20 --sims 2000 [--refill-skip 5 | --refill-replacement] --json
# §0.2 free move: 8 seeds (201-208) x 2000 sims of windows {add: (Oct 23, None), drop: (None, Oct 23)} per set,
# aggregated with tradeeval.agg_seeds (one-off script, not committed)
# 0.2.0 (superseded) examples:
fantasy inseason stream --date 2026-10-20 --week-sims 4000 --season-sims 4000 --season-top 10 [--adds-used N] --json
python docs/research/trade_scan_validation_2026-10-20.py --market var/projections_alt/market.csv
```
