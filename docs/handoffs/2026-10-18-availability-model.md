# Handoff: games-played (availability) model (2026-10-18)

## Summary
The hand-set gp rule (a recency mean shrunk toward 68) was **about 8 games optimistic** out of sample
(+12 for age ≥30). It is replaced by a fitted OLS on the last three seasons' games, age hinges and
"ironman" hinges. GP RMSE is **1.8-1.9 games lower** in both held-out seasons (SE about 0.3), and
the bias falls to +1.4..+1.8 overall and to about 0 for rotation players. `gp_sd` now comes from the
residuals (about 15-24 games, calibrated: z sd 0.95-0.99). The old value of about 9 was 2.2x too
tight. A fitted **playoff-week availability multiplier** (`playoff_mult`, based on team tank and injury
history) is applied with a prior weight `playoff_weight = 0.30`. `gp_risk_lambda` exists but defaults
to 0. Versions: projections `baseline-0.2.0`, valuation `valuation-0.2.0`. Details and all numbers
are in `docs/research/availability_model.md`.

## What changed
- `src/fantasy/model/availability.py` (new): fitted constants, `predict_gp`, `gp_sd_from_model`,
  `playoff_mult`, and the 2025-26 team records used as the tank proxy.
- `projections.py`: `Params.gp_model="fitted"` (`"shrink"` = old rule) and `manual_gp_scale=0.90`
  (manual `gp` overrides and rookie priors were set on the old, optimistic scale). New CSV column
  `playoff_mult` (last column); meta has `gp_model`.
- `lines.py`: `availability(df, gp_risk_lambda, playoff_weight)`. `load_projections` tolerates
  files without `gp_sd`/`playoff_mult`. `valuation.py`: `ValuationParams.gp_risk_lambda=0.0`,
  `playoff_weight=0.30`. `fantasy values build --gp-risk-lambda --playoff-weight`.
- `tests/test_availability.py` (12 tests). Full `pytest` and `ruff check src tests` are green.
- `data/processed/projections_2026_27.*` rebuilt (456 rows). Mean gp fell from 59.7 to 53.6.

## Before → after (value rank; `fantasy values build --no-save`)
| Player | ADP | gp old → new | playoff_mult | rank before → after |
|---|---|---|---|---|
| Kawhi Leonard | 34.1 | 60.8 → 50.8 | 0.96 | 21 → 27 |
| Joel Embiid | 50.5 | 44.3 → 39.6 | 0.89 | 63 → 63 |
| Anthony Davis | 42.9 | 49.7 → 40.4 | 0.80 (WAS + injuries) | 32 → 51 |
| Zion Williamson | 68.0 | 58.7 → 53.7 | 0.94 | 117 → 102 |
| Ja Morant | 88.9 | 40.5 → 39.7 | 0.82 | 180 → 161 |
| LaMelo Ball | 25.3 | 59.0 → 55.7 | 0.97 | 41 → 31 |
| Jimmy Butler | 149.8 | 21.9 → 17.8 | 0.88 | 257 → 263 |
| Tyrese Haliburton | 12.9 | 66 → 59.4 (manual ×0.9) | 0.94 | 6 → 6 |
| Kyrie Irving | 48.1 | 58 → 52.2 (manual ×0.9) | 0.91 | 47 → 44 |
| Paul George | 80.0 | 53.1 → 43.4 | 0.89 | 99 → 116 |
Other top-60 moves: Curry 54→74, Harden 34→46, Siakam 56→67, Giannis 48→57, Booker 25→33 and Durant
33→39 went down (older players). Brandon Miller 66→56, Sarr 51→42 and Pritchard 49→40 went up.
The top 12 is essentially unchanged (Jokić, Wemby, SGA, Luka, Maxey, Haliburton, ...). The rank-ADP
Spearman correlation (ADP ≤ 100) went from 0.722 to 0.683: the model is now more skeptical of older
stars than Yahoo's market is, and the out-of-sample evidence (old rule +9.5/+11.9 games optimistic
at age ≥30) supports that direction.

## Simulation (paired, 200 sims each, seeds 1000-1007 × 25, slot 1)
| | optimizer ECW | BPA ECW | edge ± SE | matchup-win edge ± SE | P(opt > BPA) |
|---|---|---|---|---|---|
| before (old projections, neutral availability) | 6.304 | 6.224 | +0.080 ± 0.006 | +0.0032 ± 0.0007 | 0.84 |
| after (defaults) | 6.242 | 6.137 | **+0.106 ± 0.008** | +0.0085 ± 0.0009 | 0.865 |
The edge is not hurt. The two rows are scored by different (in-sample) models, so compare the edges,
not the absolute levels. Round 2 (#24) is still Holmgren/Murray. Trey Murphy III replaced Kawhi as
the third most common choice. Latency: `recommend` at pick 1 takes 0.45 s compute and 1.6-2.0 s wall
(the limit is 5 s). A simulated draft takes 3.6 s.

## For the lead to decide
1. **Manual gp values** (`data/manual`, not my path): rookies 66-72, Haliburton 66, Tatum 66,
   Kessler 64, VanVleet 64, Lillard 58 and Kyrie 58 are scaled by 0.90 in code. If you would rather
   restate them on the new scale, edit the CSVs and set `manual_gp_scale=1.0`.
2. `playoff_weight` 0.30 is a prior (0.15 = neutral, 0.5 = title-focused). `gp_risk_lambda` is 0
   (expected value; playoff risk is priced separately). λ=0.25 is a reasonable risk-averse setting.
   `draft recommend/simulate` use the defaults: the draft CLI has no flags for these (outside my
   paths).
3. The tank proxy is the 2025-26 record of the player's current team. It penalises IND (19-63
   without Haliburton) and WAS/BRK/SAC/UTA. Replace it with 2026-27 win totals if you have them.
4. `docs/ARCHITECTURE.md` should list the new `playoff_mult` column (I did not edit it).
5. `stream_recovery = 0.5` remains unfitted. The new lower gp makes it matter more.
6. A model change on draft eve: the user's OK is required per the model-eval skill. Rollback:
   `Params(gp_model="shrink", manual_gp_scale=1.0)` together with
   `ValuationParams(playoff_weight=0.15)` reproduces the old values.

## Not mine (left untouched in the working tree)
`src/fantasy/cli.py`, `src/fantasy/sim/`, `data/raw/bbref_schedule/`.
