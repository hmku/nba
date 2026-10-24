# Minutes-opportunity detector (opportunity-0.1.1)

**Answer:** beyond what the minutes model (model A) already says, only the vacated-minutes signal adds information
out of sample (2025-26, constants fitted on 2024-25):
- **Vacated flags:** +1.42 ± 0.36 mpg over model A in the next 5 games played. While the teammate stays out they get
  +4.1 extra mpg against +3.4 predicted.
- **Jump flags:** +0.32 ± 0.32 mpg over model A, i.e. nothing beyond it. A jump candidate is simply "model A says his
  minutes rose", with no extra adjustment. That is still useful, because nobody consumes `mpg_next5` otherwise.

The large lift over unflagged free-agent-like players (+7.7 ± 0.4 mpg vs preseason) mostly reflects model A's own
selection. On next-5 RMSE the calibrated vacated opportunity mpg (5.07) is no better than model A (5.02): the
window is short (about 3 games), and next-5 includes games after the star's return.

Code: `src/fantasy/inseason/opportunity.py` (pure logic), `src/fantasy/inseason/opportunity_run.py` (inputs,
valuation, evaluation). CLI: `fantasy inseason opportunity`. Fit and backtest scripts: `scripts/opportunity/fit.py`
(-> `report_fit.json`) and `scripts/opportunity/backtest.py` (-> `report_backtest.json`).

## Method

1. **Signals** for every projected player on no league roster:
   - (a) **jump**: `mpg_next5` (model A, minutes-0.2.0) − preseason mpg ≥ 4 (PRIOR). The opportunity minutes are
     model A's, unadjusted.
   - (b) **vacated**: a teammate projected ≥ 28 mpg is Out. Sources: the ESPN injury report (Out / OFS), a Yahoo
     O/INJ/OFS/IR tag, or a high/medium news item flagged indefinite / season-ending / surgery / exited the game.
     When news is the only source, the allocation is discounted by P(out) = 0.5 (PRIOR).
2. **Allocation** (FITTED, 2024-25 + 2025-26, `report_fit.json`; 317 absences of ≥ 3 games, 3,934 teammate rows).
   - **Gain** = minutes during the absence (≤ 10 games) − minutes over the previous 10 games.
   - **Excess** = gain − the placebo gain of his depth bucket. Placebo windows have no ≥ 28-mpg absence; their bucket
     means are within ±0.6 mpg.
   - **Weight** = mean excess / star mpg, by depth bucket × PRIMARY-position match (G / W / B). Positions come from
     bbref's prior-season position, never from later data. Floored at 0; no position uses the "other" weight.

   | bucket | same | other |
   |---|---|---|
   | 4-5 | 0.086 | 0.028 |
   | 6-8 | 0.099 | 0.078 |
   | 9-11 | 0.165 | 0.134 |
   | 12+ | 0.106 | 0.074 |

   Cell SEs are 0.008-0.020. The total share is 0.77 ± 0.034. The allocation is star mpg × w_i × min(1, share / Σw),
   so it never exceeds share × star mpg.
3. **Minutes calibration for free-agent-like players** (FITTED on the 2024-25 backtest): realised extra minutes
   during the absence = 0.95 ± 0.15 × allocated. Out of sample, 2025-26 with this calibration gives 1.27 ± 0.20.
   Production uses `vacated_calib = 0.95`. The 0.54 in opportunity-0.1.0 was an artefact of a leak: the backtest
   took a star as "out" whenever he sat game g, rests included.
4. **Window** = expected games still to miss, capped at 20 (PRIOR).
   - FITTED survival of ≥ 28-mpg absences (1,174 spells): P(≥5 | ≥3) = 0.58, P(≥10 | ≥3) = 0.25,
     P(≥20 | ≥3) = 0.085. E[games | just ruled out] = 2.9; E[games | 3 missed] = 6.4.
   - With an ESPN return date, both windows are reported and the **shorter** is used (PRIOR rule).
   - An absence that has lasted ≥ 2 team games is "observed" and counts only through signal (a).
5. **Opportunity line**: `mpg_next5` + the time-averaged unobserved extra, capped at 40 mpg (PRIOR). Per-minute
   rates are kept (FITTED ratio ~1.00-1.04). After the window, model B applies; there is no retention credit.
6. **Value (FITTED calibration)**: gain = window × (p × (v_base + κ (v_opp − v_base)) − p_drop × v_drop).
   - κ = realised / predicted window value gain over the model-B line for vacated first flags, bias-corrected by the
     unflagged pool's per-game residual: 1.14 ± 0.39 on 2024-25, 1.06 ± 0.41 out of sample on 2025-26.
   - Neither differs from 1, so κ = 1.0 (capped at 1, conservative). `*_raw` is the uncalibrated gain.
   - For jump flags the ratio is ill-conditioned (SE > 6), so they get no adjustment.
7. **Evaluation** of the top 5:
   - **Week**: the stream tool's matchup Monte Carlo. The candidate is on a week line where the opportunity minutes
     cover only his first `window` games this week (`week_mpg`).
   - **Season**: the stream tool's position-aware **stream chain** (`make_stream_season_eval`): drop → candidate on
     his window line from his effective day to the day after his ceil(window)-th game (`stint_end_day`) → the
     6th-best realistic free agent. Blend and market sets; verdict by `streamscan.stream_verdict`.
   - *helps* also needs the stint to beat the direct move (drop → chain FA) beyond 2 SE; else *direct_helps* (the gain
     is the drop, not the opportunity). *speculative* = noise, but week ΔP(win) > 2 SE or stint-vs-direct ΔP(playoffs)
     > 1 SE (PRIOR). Without a chain run the
     result is *unevaluated*: never a season verdict.
8. **Override projections** (`var/opportunity/opportunity_<date>.csv`, `.window.csv`, `.meta.json`; only candidate
   rows change):
   - **window** file: the line during the window.
   - **season** file: the rest-of-season blend, (W·opp + (R−W)·mpg)/R.
   - The meta records the input SHA-256s (projections, injuries, news, box scores, free agents, league, market) and
     `sim_version`.
9. **Unprojected risers**: box-score players with no projection row and on no roster, with last-3 ≥ 15 mpg and a
   rise of ≥ 6 mpg (PRIORS). They are listed as a warning for manual review.

## Backtest (`report_backtest.json`; leak fixes in 0.1.1)

**Information before game g:**
- box scores of games before g;
- game g's inactive list with a non-coach reason (published before tip-off), or "still out" from game g−1 with a
  reason. Game-g minutes are never used;
- ESPN's preseason projection of that season;
- unprojected players' prior = 8 mpg (PRIOR, no future games);
- bbref prior-season positions.

**Universe:** ESPN-projected players outside the top 156 (209 players, 13,583 player-days in 2025-26). Outcomes are
next-5/10 minutes played and next-10 per-game G-score. SEs: player-cluster bootstrap (300).

**2025-26, 2024-25 constants + calibration** (production constants in-sample give nearly the same results):

| first flags | n | next-5 − model A (incremental) | next-5 − preseason | lift vs unflagged | value lift (G/game) |
|---|---|---|---|---|---|
| jump | 266 | **+0.32 ± 0.32** | +5.25 ± 0.35 | +8.98 ± 0.50 | +1.74 ± 0.16 |
| vacated | 193 | **+1.42 ± 0.36** | +2.23 ± 0.47 | +5.96 ± 0.53 | +1.09 ± 0.17 |
| all | 459 | +0.77 ± 0.26 | +4.02 ± 0.33 | +7.74 ± 0.45 | +1.47 ± 0.15 |
| unflagged | 11,301 | −0.58 ± 0.14 | −3.73 ± 0.39 | | |

- **Precision**: P(next-10 ≥ preseason + 5 mpg) = 0.37 ± 0.03 for flags vs 0.07 ± 0.01 unflagged.
- **Calibration**: model A on jump flags predicts 25.7 mpg vs 26.0 actual. Vacated next-5 RMSE is 5.07 (opportunity
  mpg) vs 5.02 (model A). During the absence the slope is 1.27 ± 0.20.

## Open questions
- **Retention** after the return (~0.5 of the gain in the research) is not credited.
- **News-only P(out)** and the thresholds are priors: sweep them in the backtest.
- **Unprojected deep-bench players** are only listed, never valued.
- **Who's-out timing**: the backtest's inactive-list proxy is a little later than ESPN's report, and earlier than a
  surprise scratch.
