# Games-played model fit (gp-ols-*)

Reproduces the constants in `src/fantasy/model/availability.py` and the numbers in
`docs/research/availability_model.md`.

```bash
mkdir -p /tmp/gp_raw && cd /tmp/gp_raw
curl -sSfLO "https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/Player%20Totals.csv"
curl -sSfLO "https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/Team%20Summaries.csv"
cd - && python scripts/gp_model/fit_gp.py --raw /tmp/gp_raw --out scripts/gp_model/report_gp-ols-0.2.json
```

- `panel.py`: player-season panel (82-game scaling, lost seasons = 0 games, retirees dropped, lost-season rows).
- `fit_gp.py`: strict out-of-sample test (fit <= 2024, test 2025/2026 vs the gp-ols-0.1 spec), rolling-origin
  calibration, production fit, gp_sd knots; prints JSON (`report_gp-ols-0.2.json` = the run behind gp-ols-0.2,
  upstream files fetched 2026-10-21). Residual entries are `[mean actual - predicted, SE, n]`.
- The raw files are not committed (4.5 MB, upstream changes daily; the committed `data/raw/bbref_sumitrodatta`
  copy only keeps seasons >= 2022).
