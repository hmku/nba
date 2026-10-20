# Streaming scan (T-006) and trade scan (T-008)

_2026-10-20, `inseason-0.2.0`. Code: `src/fantasy/inseason/streamscan.py`, `tradescan.py`, `freeagents.py`,
`possim.py` (mid-season roster windows), CLI `fantasy inseason stream` and `fantasy inseason trade-scan`. Tests:
`tests/test_inseason_scan.py` (synthetic, offline). Validation script: `docs/research/trade_scan_validation_2026-10-20.py`.
Analysis only: nothing here touches Yahoo, and nothing was planned in the txn ledger or executed._

**Labels** (CLAUDE.md evidence standard). *Simulated* = season simulator output (`season-sim`, fitted parameters
from `sim_calibration.md`; ± is the paired Monte Carlo SE only, not model error). *Model* = the lineup planner's
analytic matchup model (`lineup_optimizer.md`: fitted game noise and projection error, availability priors).
*Schedule* = counted from the NBA schedule. *Prior* = an assumption, not fitted. *Assumption* = a rule of Yahoo
we have not verified on a processed transaction.

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
   ΔP(playoffs), ΔP(title) and Δ category wins ± paired SE. This is the full cost of losing x for the season, and it
   includes this week at season-rate availability.
5. **Shortlist** for the expensive steps: half by this week's gain, half by the cheap season proxy
   `season_proxy_d_value` = valuation value(c) − value(x) (season G-score totals above replacement).
6. **Decision rule.** Recommend a pair only if its season ΔP(playoffs) or ΔP(title) is > 2 paired SE and neither
   is < −2 SE. Otherwise recommend nothing. Pairs whose only significant gain is this week's ΔP(win) go in
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

### Example: real league, Tue 2026-10-20, week 1 vs Joshua's Excellent Team
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
   ΔP(playoffs) or ΔP(title) > 2 SE and neither < −2 SE.
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

### Example: real league, 2026-10-20 (`--sims 2000`, market from `var/projections_alt/market.csv`)
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
- The simulator is optimistic for us: other teams never trade, stream or adjust lineups. Consolidation gains assume we
  win the waiver claim for the best free agent, so check the `--refill-skip 11` run.
- ± values are Monte Carlo SEs only. Projection error is handled by comparing the two projection sets, not by the SE.
- The 2026-10-20 free-agent capture has no per-player waiver status. Every player is assumed `W (Oct 22)` from the
  capture note. Re-capture with `scripts/yahoo/capture_free_agents.js` for per-player ownership.
- The stream scan's week model uses today's tags (lineup planner priors). The season sim uses season-rate availability.
- The trade screen is a screen. It misses individual high-title trades (Murray example), so evaluate named ideas with
  `fantasy inseason trade --partner N --give .. --get ..`.
- Kyrie Irving's and other injured-return players' availability comes from our projections (gp, status). Read the
  partner's injury news before acting.

## Reproduce
```bash
fantasy inseason stream --date 2026-10-20 --week-sims 4000 --season-sims 4000 --season-top 10 [--adds-used N] --json
fantasy inseason trade-scan --sims 2000 [--market-projections var/projections_alt/market.csv] [--refill-skip 11] --json
python docs/research/trade_scan_validation_2026-10-20.py --market var/projections_alt/market.csv
```
