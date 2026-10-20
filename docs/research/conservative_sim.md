# Conservative season sim: grading our roster with third-party projections

_2026-10-20. Question (user): "is there a way to run a more conservative sim? Instead of our model projections, use a
third-party projection like ESPN." Code: `fantasy data alt-projections` (`src/fantasy/data/cli.py`, test
`tests/test_external_blend.py::test_alt_projections_cli_writes_only_out`), blend logic `src/fantasy/model/blend.py`.
Simulator `season-sim-0.5.0` defaults (fitted priors, `sim_calibration.md`). Every probability below is **simulated**
(12,000 seasons, seed 0); SEs are binomial and exclude projection/model error — that error is what this note is about._

## Why

We drafted with our own projections (50/50 ours/ESPN blend at draft time). Grading the same roster with the same
projections is optimistic: we picked players our model likes more than the market (winner's curse; see
`optimism_decomposition.md`, point 3). Re-running the sim with projections we did *not* draft with bounds that bias.

## Method

- **Rosters:** real post-draft rosters `data/manual/league_rosters_2026-27.json` (156 players). **Schedule:** Yahoo's real
  matchup schedule `data/manual/league_schedule.json` + bbref NBA schedule. Identical for all runs; same seed, so the
  runs are paired (common random numbers), and differences between rows are more precise than the per-row SEs suggest.
- **Projection sets** (all in the exact projections schema; player set and non-stat columns = our base file, written
  to the git-ignored `var/projections_alt/`; third-party numbers are not committed beyond the normalised raw CSVs
  already in `data/raw/`):

| set | per-game stats | games (gp, gp_sd) |
|---|---|---|
| `none` | our unblended model (`projections_2026_27.base.csv`) | ours (fitted availability model) |
| `blend` | current default: 50/50 ours/ESPN (`projections_2026_27.csv`) | ours |
| `espn-only` | ESPN 2026-27 projection (331 players; 155/156 rostered covered) | ours |
| `espn-only-gp` | ESPN | ESPN (known ~+11 games optimistic per player, `projection_quality.md`) |
| `fantasypros-only` | FantasyPros consensus (260 players; 155/156 rostered) | ours |
| `market` | 50/50 ESPN/FantasyPros (no input from our stat model) | ours |

  The one uncovered rostered player (Sandro Mamukelashvili) keeps our numbers in every set. Caveat: FantasyPros has no
  attempts, so its FGA/FTA are imputed from ours scaled to FantasyPros' points (`blend.impute_attempts`); its FG%/FT%
  are its own. The streaming/free-agent replacement line is drawn from the same file, so each run is self-consistent.

## Results (simulated, 12k sims, seed 0; my team = slot 1)

| set | P(playoffs) | P(bye) | P(title) | exp. rank | top 3 rivals by P(title) (P(playoffs) / P(title)) |
|---|---|---|---|---|---|
| `none` (ours) | 92.9% ±0.23 | 66.7% | 35.2% ±0.44 | 2.52 | Haagen Daz 79.0/13.1, Yeet 49.0/10.2, jesse's 51.1/8.2 |
| `blend` (current) | 91.9% ±0.25 | 64.7% | 33.7% ±0.43 | 2.63 | Haagen Daz 76.0/11.8, Yeet 48.2/10.5, jesse's 48.0/7.6 |
| `espn-only` | 90.8% ±0.26 | 61.8% | 31.9% ±0.43 | 2.77 | Yeet 48.4/11.4, Haagen Daz 72.6/10.6, Thomas's 47.7/7.7 |
| `market` (ESPN+FP) | 88.0% ±0.30 | 57.1% | 27.9% ±0.41 | 3.08 | Haagen Daz 73.5/11.7, Yeet 44.8/10.0, jesse's 48.0/8.0 |
| `espn-only-gp` | 85.7% ±0.32 | 54.7% | 26.3% ±0.40 | 3.28 | Yeet 55.5/15.5, Thomas's 51.5/10.6, Haagen Daz 72.7/10.3 |
| `fantasypros-only` | 84.8% ±0.33 | 52.1% | 25.1% ±0.40 | 3.39 | Haagen Daz 74.1/12.5, jesse's 51.3/10.2, Yeet 41.2/9.1 |

`blend` reproduces the published post-draft run (`season_simulator.md`, 91.9% / 33.7%) exactly.

My category ranks (1 = best of 12):

| set | FG% | FT% | 3PM | PTS | REB | AST | STL | BLK | TO |
|---|---|---|---|---|---|---|---|---|---|
| `none` | 9 | 5 | 2 | 1 | 1 | 2 | 1 | 2 | 11 |
| `blend` | 9 | 5 | 2 | 1 | 1 | 2 | 1 | 2 | 9 |
| `espn-only` | 9 | 5 | 1 | 1 | 1 | 2 | 1 | 2 | 7 |
| `market` | 10 | 4 | 2 | 2 | 1 | 4 | 1 | 2 | 7 |
| `espn-only-gp` | 9 | 5 | 2 | 3 | 1 | 5 | 1 | 2 | 6 |
| `fantasypros-only` | 10 | 4 | 2 | 3 | 1 | 4 | 2 | 2 | 6 |

The profile is stable: strong REB/STL/BLK/3PM, weak FG%; counting-stat leads (PTS, AST) shrink under third-party
numbers because they partly come from extra projected games and from our model's view of young players.

## Where the gap comes from (winner's curse decomposition)

Team value = sum over the 13 rostered players of each set's own valuation (`compute_values`: G-score sum above
replacement, valuation defaults; each set valued on its own scale, so compare *within* a column). Source:
`var/conservative_sim/team_values.csv` (regenerate with the recipe below).

| set | my team value | league mean | my excess over mean | over 2nd-best team |
|---|---|---|---|---|
| `none` | 39.3 | 24.6 | +14.7 | +9.4 |
| `blend` | 39.9 | 26.2 | +13.7 | +9.6 |
| `espn-only` | 39.6 | 27.0 | +12.6 | +9.7 |
| `market` | 38.3 | 27.8 | +10.5 | +6.8 |
| `espn-only-gp` | 38.8 | 28.9 | +9.9 | +6.6 |
| `fantasypros-only` | 37.1 | 28.9 | +8.2 | +3.9 |

1. **Per-game opinion (ours vs ESPN): small.** My roster's value is about the same under ours and ESPN (-0.4); the
   other 11 rosters gain +2.7 on average under ESPN (team-level SD of the difference 2.6). So relative to the league, our model
   flatters us by ≈ 2 value units out of a ≈ 15 lead. Per player: mean (ours − ESPN) value is +0.18 higher for my 13
   players than for the other 143, SE 0.20 — not distinguishable from zero. Players our model likes more than ESPN:
   Jeremiah Fears (rank 84 vs ESPN 126), Ace Bailey (87 vs 122), Derik Queen (62 vs 88), Donovan Clingan (20 vs 38);
   ESPN likes more: Naz Reid (49 vs ESPN 20), Kawhi Leonard (26 vs 17), Reed Sheppard, Kel'el Ware. They roughly cancel.
2. **Games played: material.** Our availability model gives my roster 822 games vs a league mean of 732 (+90, the most of
   any team); ESPN's GP gives 947 vs ~895 (+52). About 40 of my ≈ 90-game edge is "our availability model likes my
   players" (ESPN GP is optimistic by ~+11/player for everyone, so only the relative edge matters). Switching to ESPN
   GP costs 5.1 pp playoffs / 5.6 pp title (`espn-only` → `espn-only-gp`). Our GP model is the one backtested as
   unbiased (`availability_model.md`), so this is a stress test, not the better estimate.
3. **FantasyPros disagrees most** (excess +8.2, title 25.1%), mostly because it rates the rest of the league higher
   (other teams +4.4 vs ours, my team +2.1 lower); it has no backtest in this repo and its percentages use imputed
   attempts, so treat it as one more market opinion, not truth.

Spread across reasonable projection sets is **85-93% playoffs, 25-35% title**: that range (projection uncertainty) is
~20-40x the Monte Carlo SE and is the honest uncertainty band to show the user. In every set my team remains the
clear #1 (rank 1 in team value, best P(playoffs)); Haagen Daz Center and Yeet are the consistent rivals.

## Recommendation

- Keep `blend` as the headline (it is the backtest-validated projection, `projection_quality.md`), but always report
  the **`market` run (ESPN+FantasyPros per-game, our fitted GP) as the conservative number**: it uses none of our stat
  model, so it removes the winner's curse on per-game stats while keeping the only backtested GP model. Today: **88.0%
  playoffs / 27.9% title** (vs 91.9% / 33.7%).
- Report the range over {`none`, `blend`, `espn-only`, `market`, `fantasypros-only`, `espn-only-gp`} as the
  projection-uncertainty band (today 85-93% / 25-35%). (Prior/judgement: this band is not a calibrated interval —
  it is the spread of reasonable experts, not a posterior.)
- For decisions (adds/drops, trades) compare deltas under both `blend` and `market`; act when both agree in sign.

## Recipe

```bash
source .venv/bin/activate
mkdir -p var/projections_alt
fantasy data alt-projections --blend market    --out var/projections_alt/market.csv     # ESPN+FP, our GP
fantasy data alt-projections --blend espn-only --out var/projections_alt/espn-only.csv
fantasy data alt-projections --blend espn-only --blend-gp espn=1 --out var/projections_alt/espn-only-gp.csv
fantasy data alt-projections --blend fantasypros-only --out var/projections_alt/fantasypros-only.csv
fantasy data alt-projections --blend none      --out var/projections_alt/none.csv       # our unblended model
fantasy sim season --league-file data/manual/league_rosters_2026-27.json \
  --projections var/projections_alt/market.csv --sims 12000 --seed 0 --no-save --json
```

`alt-projections` never touches `data/processed/projections_2026_27.csv` (unlike `build-projections --blend-output`,
which resets the default file to the unblended baseline). `--blend` also takes explicit weights, e.g.
`'ours=0.25,espn=0.75'`. Use `--no-save` (or `--scenario "conservative: market projections"` with save) so a
conservative run is not mistaken for the dashboard's headline run. Refresh the third-party inputs first with
`fantasy data fetch-external` when they are stale (the cached ESPN/FP files are pre-season, 2026-10-19).
