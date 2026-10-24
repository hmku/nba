# Minutes-opportunity detector (opportunity-0.1.0)

**Answer:** the detector's two signals are both real out of sample (2025-26, constants fitted on 2024-25 only).
Flagged free-agent-like players beat their preseason minutes by +3.7 mpg over their next 5 games. Unflagged players
in the same pool fall 3.2 mpg short. The lift is +6.9 ± 0.5 mpg, and per-game 9-cat value rises by +1.33 ± 0.15
G-score. The observed-jump signal (model A) is the strong, well-calibrated one. Vacated minutes add +1.1 ± 0.3 mpg
beyond the minutes model, but only after a fitted 0.54 calibration of the allocation.

Code: `src/fantasy/inseason/opportunity.py` (pure logic), `src/fantasy/inseason/opportunity_run.py` (inputs,
valuation, evaluation). CLI: `fantasy inseason opportunity`. Fit and backtest scripts: `scripts/opportunity/fit.py`
(-> `report_fit.json`) and `scripts/opportunity/backtest.py` (-> `report_backtest.json`).

## Method

1. **Signals** for every projected player on no league roster:
   - (a) **jump**: `mpg_next5` (model A, minutes-0.2.0) − preseason mpg ≥ 4 (PRIOR threshold). Also reported:
     his last-3 mpg and his starts in the last 3 games.
   - (b) **vacated**: a teammate projected ≥ 28 mpg is Out. Sources: the ESPN injury report (Out / OFS), a Yahoo
     O/INJ/OFS/IR tag in the league file, or a high/medium news item flagged indefinite / season-ending / surgery /
     exited the game. A "timeline" item only adds context. When news is the only source, the allocation is
     discounted by P(out) = 0.5 (PRIOR).
2. **Allocation** (FITTED, 2024-25 + 2025-26, `report_fit.json`). There are 317 absences of ≥ 3 games by ≥ 28-mpg
   players, with 3,934 teammate rows. For each teammate:
   - **Gain** = his mean minutes during the absence (≤ 10 games) − his mean over the previous 10 games.
   - **Excess** = the gain − the mean placebo gain for his depth bucket. Placebo windows are windows with no ≥ 28-mpg
     absence. This removes regression to the mean from ranking players by their baseline. Placebo bucket means
     were within ±0.6 mpg.
   - **Weight** = mean excess / star mpg, by depth bucket (rank 1-3, 4-5, 6-8, 9-11, 12+) and position match
     (G = PG/SG, W = SF, B = PF/C). The weights are floored at 0, because the top-3 bucket loses minutes.

   | bucket | same | other |
   |---|---|---|
   | 4-5 | 0.071 | 0.017 |
   | 6-8 | 0.100 | 0.062 |
   | 9-11 | 0.186 | 0.145 |
   | 12+ | 0.160 | 0.108 |

   Main-cell SEs are 0.013-0.019. The total share is 0.77 ± 0.034 (0.81 raw, without the placebo correction). The
   allocation is star mpg × w_i × min(1, share / Σw), so it never exceeds share × star mpg. Production ranks depth
   by projected mpg (model B) among available teammates.
3. **Calibration for the detector's universe** (FITTED). The share weights are averages over all teammates. For
   players outside the top 156 by preseason value, the realised extra minutes during the absence, over model A,
   come to 0.54 ± 0.057 × the allocated extra. This slope through the origin was fitted on the 2024-25 backtest,
   with a player-cluster bootstrap SE. Out of sample on 2025-26 the uncalibrated slope was 0.56 ± 0.051, and the
   calibrated one 1.07 ± 0.16. Production uses `vacated_calib = 0.54`.
4. **Window** = expected games still to miss, capped at 20 (PRIOR). Three sources:
   - FITTED survival of ≥ 28-mpg absences given the games already missed (1,174 spells): P(≥5 | ≥3) = 0.58,
     P(≥10 | ≥3) = 0.25, P(≥20 | ≥3) = 0.085. E[games | just ruled out] = 2.9 and E[games | 3 missed] = 6.4.
   - ESPN's estimated return date when given (team games before it; an estimate, labelled as such).
   - The horizon cap when he is out for the season.

   An absence that has already lasted ≥ 2 team games (PRIOR) is **observed**: model A has seen his minutes, or he
   sat while healthy. It then counts only through signal (a).
5. **Opportunity line**: `mpg_next5` plus the time-averaged unobserved extra, capped at 40 mpg (PRIOR).
   Per-minute rates come from the in-season file. FITTED: per-minute rates of big gainers during absences vs the
   rest of the season are ~1.00-1.04. After the window, model B's level applies with no retention credit, which is
   conservative (research: gainers keep ~half of their gain after the return).
6. **Value and evaluation**:
   - **Ranking**: window games × (p_play × per-game G-score of the opportunity line − the drop's), where the drop
     is my lowest-season-value healthy player (`stream.per_game_values`).
   - **Week check** (top 5): the stream tool's matchup model (`streamscan.scan_streams`, lineup-0.2.0) with a
     paired Monte Carlo, run on the window lines.
   - **Season check** (top 5): a paired season sim (`sim compare` semantics, season-sim) on the season override,
     for the blend set and the market set (the same mpg factors applied to `market.inseason.csv`).
   - **Verdicts**: *helps* follows the stream tool's rule (ΔP(playoffs) or ΔP(title) > 2 SE under every set,
     neither < −2 SE). *speculative* means not significant, nothing negative, and either week ΔP(win) > 2 SE or
     blend ΔP(playoffs) > 1 SE (PRIOR). Otherwise the verdict is noise, hurts or tradeoff.
7. **Override projections** (same schema as `projections_2026_27.inseason.csv`; only the listed available
   candidates' rows change; written to `var/opportunity/opportunity_<date>.csv`, `.window.csv` and
   `.meta.json`):
   - **window** file: counting stats × opp/mpg; `mpg` = `mpg_next5` = opp. Use it for lineup or week tools over
     the window.
   - **season** file: mpg = (W·opp + (R − W)·mpg)/R, with W = window games × gp/82 and R = `ros_games`; counting
     stats are scaled by the same ratio and `mpg_next5` = opp. Use it with
     `fantasy sim compare --projections <file>`.

   Percentages are unchanged, because makes and attempts scale together.

## Backtest (2025-26 out of sample; `report_backtest.json`)

**Protocol.** The production `detect()` runs before every team game g. It sees:
- box scores of games before g only;
- the game-g inactive list, as a proxy for the pregame injury report. A ≥ 28-mpg player who sat game g counts as
  Out. This is a small optimism: surprise scratches are known only near tip-off;
- ESPN's 2025-26 preseason projections as the prior (mpg, per-game line);
- model A and model B on his games played so far.

**Universe.** ESPN-projected players outside the top 156 by preseason value: 207 players, 11,423 player-days.
Unprojected deep-bench players are excluded, which is a limitation. Flags are deduplicated to the first day of each
run. Outcomes are mean minutes in the next 5 and 10 games played, and the per-game G-score of the next-10 line minus
the preseason line's (preseason-pool scaling). SEs come from a player-cluster bootstrap (300 draws).

| (first flags, calibrated) | n | next-5 mpg − preseason | lift vs unflagged | next-10 value − preseason | lift |
|---|---|---|---|---|---|
| all flags | 571 | +3.74 ± 0.34 | **+6.95 ± 0.47** | +0.91 ± 0.12 | **+1.33 ± 0.15** |
| jump | 299 | +5.58 ± 0.46 | +8.79 ± 0.57 | +1.32 ± 0.16 | +1.74 ± 0.19 |
| vacated | 272 | +1.73 ± 0.39 | +4.93 ± 0.51 | +0.46 ± 0.14 | +0.88 ± 0.15 |
| unflagged | 9,234 | −3.21 ± 0.36 | | −0.42 ± 0.12 | |

- **Precision.** P(next-10 mpg ≥ preseason + 5) = 0.38 ± 0.03 for flags vs 0.08 ± 0.01 unflagged (lift +0.30).
- **Beyond the minutes model** (next-5 minus model A). Jump flags: +0.28 ± 0.33, so model A is calibrated: mean
  26.1 predicted vs 26.4 actual. Vacated flags: +1.07 ± 0.35, so the allocation adds information. During the
  absence the realised extra is 3.5 mpg vs 3.2 predicted (slope 1.07 ± 0.16).
- **Uncalibrated allocation** (×1.0): 1,001 flags, lift +5.6 ± 0.4 mpg. The vacated opportunity mpg overshoots:
  22.9 predicted vs 19.4 actual, with a higher RMSE than model A alone (6.2 vs 4.8). Hence the 0.54 calibration.
- **Value is still optimistic for vacated flags.** Predicted value gain is +0.85 vs +0.46 realised. Part of that is
  because the next-10 window includes games after the star's return.
- The unflagged pool's −3.2 mpg shows that preseason projections are optimistic for fringe players. That is why the
  lift, not the raw excess, is the right comparison.

## Open questions / next steps
- **Retention after the return.** The fitted retention is ~0.5 of the gain (17.5 vs 11.2 before, 23.1 during). The
  detector gives no credit, to stay conservative. A fitted retention term could raise the season value of real
  role changes.
- **Short windows.** A 1-3 game window is a stream, not a hold. The season check is "hold all season" (`sim
  compare`). For short windows the stream chain (`inseason stream --projections <window csv>`) is the better season
  number. It is not wired yet.
- **News-only P(out) = 0.5 and the thresholds (4 / 2 mpg, 2 games observed, 20-game cap)** are priors. They can be
  calibrated from the backtest by sweeping them.
- **Deep-bench players without a preseason projection** are outside the backtest and outside production (no
  projection row). The biggest single gainers often come from there (median depth rank 8, baseline 11 mpg).
- **Mid-season role changes with no injury driver** (trade, coach decision) reach the detector only through model A.
