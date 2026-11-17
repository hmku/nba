# Draft adverse selection: the market knew minutes we didn't (2026-11-16)

**Answer:** players our model valued above their ADP lost minutes against projection, and the effect is significant.
The slope is **−0.19 ± 0.05 mpg per 10 draft spots of model-vs-ADP disagreement**, from 270 players with ADP < 200
(fitted). The market (ADP) was pricing role news that our projections don't see. We bought the most of that
disagreement: our picks averaged +40 spots against roughly −23..+15 for the other teams. The effect on total value is
smaller and only borderline (−0.06 ± 0.03 z/g per 10 spots). Our team's value shortfall is mostly not explained by
it (team effect −0.3 ± 0.8 z/g after controlling). The in-season minutes model absorbs most of the bias within a few weeks.

## Data and method
- **Projection:** the draft-time file (`d295766`, the 50/50 blend of ours and ESPN).
- **Market:** Yahoo ADP. `div` = ADP rank − our value rank, where value = per-game 9-cat z × projected gp.
  Positive `div` means we liked the player more than the market did.
- **Outcomes:** ESPN box scores 2026-10-20..11-15.
- **Regressions:** WLS weighted by games played, SEs clustered by player. Scripts are not committed; the method is
  re-runnable from this description.

## Results
| test | slope ± SE | n |
|---|---|---|
| (1) preseason: actual − projected mpg ~ div/10 | **−0.190 ± 0.054** | 270 players |
| same, + ESPN−ours mpg gap | div −0.151 ± 0.052; ESPN gap +0.118 ± 0.103 | 251 |
| + age | div −0.160 ± 0.053; age −0.097 ± 0.061 | 251 |
| (2) in-season: next-7-day mpg − ros `mpg` that morning ~ div/10 (checkpoints 10-27, 11-02, 11-09) | −0.109 ± 0.043 | 679 player-weeks / 255 players |
| same vs `mpg_next5` | −0.071 ± 0.038 | 679 / 255 |
| by checkpoint (vs `mpg_next5`) | 10-27 −0.06 ± 0.06; 11-02 −0.12 ± 0.05; 11-09 −0.03 ± 0.06 | ~220 each |
| (3) per-game 9-cat value residual ~ div/10 | −0.059 ± 0.031 | 270 |

**Out-of-sample check (1):** with a random 50/50 player split, fitting on one half and scoring on the other cuts mpg RMSE
4.22 → 3.88 and 5.18 → 4.94. The ESPN gap alone is weaker (4.24 → 3.96, 4.99 → 4.80).

**Among drafted players only** (147 with games, adverse-selection view): the minutes slope is −0.27 ± 0.09.
- Our picks averaged div +40, which predicts about −1.1 mpg per player.
- Our value shortfall vs the league is −0.55 ± 0.76 z/g. Controlling for div leaves −0.3 ± 0.8, so it is mostly noise.
- Team-level spread of value residuals is SD 0.92 vs about 0.42 from game noise alone, so real role shocks dominate.

## News check of our roster (web research 2026-11-16; Reddit was blocked)
Most of our biggest misses had public pre-draft causes:
- **Ware:** traded to MIL in July; preseason pieces had him backing up Turner (Roundtable 2026-09-10). Projected
  25.4 mpg, plays 18.
- **Sheppard:** bench role behind VanVleet expected (Bleacher Report via RotoWire, 2026-09-19).
- **Queen:** bench behind Missi and Zion under a new coach (SI 2026-07-15, FantasyAlarm 2026-10-09).
- **Brunson:** July surgery on his shooting wrist and a slow preseason ramp (NBA.com 2026-09-28).
- **Leonard:** no live play all summer and a reconditioning plan. This was partly priced in (gp 50.8).
- **Reid:** his new starting role in CHA was known; the efficiency drop and a 10-28 benching came after.
- **Jokić:** no clear cause; likely noise.
- **Podziemski:** a known positive (Butler and Moody out).

The common thread is that **depth-chart and role news** (trades into crowded frontcourts, a starter ahead, a new coach)
is absent from our projections. They roll forward last season's role (`bbref_baseline`). ESPN's half of the blend only
partly corrects this.

## Recommendation
1. **Draft-time (next season; T-030):** add a market-minutes prior. Shift projected mpg by −0.19 × div/10 (refit
   before use). Alternatively, shrink value toward ADP rank in proportion to the disagreement. Add a preseason
   role/depth-chart source (projected starters, beat-writer minutes) as an override input. Re-run the draft optimizer
   backtest before adopting.
2. **In-season now: no production change.** The residual signal is fading (11-09: −0.03 ± 0.06), because the minutes
   model is catching up. T-027 (react faster to the last-5 minutes gap) is the better in-season lever.
3. **Process:** in `draft-prep`, treat a large model-vs-ADP gap as a question to research, not a bargain.
   Check role news for any pick where we are more than 30 spots ahead of ADP.

Caveats: one season of data and four weeks of games; `div` mixes valuation and minutes; ADP also embeds injury news.
