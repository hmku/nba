# Handoff 2026-10-19 - projection quality: backtest, external projections, blend (projections researcher, cloud session)

## What was accomplished
- **Backtest of the projection core** (`src/fantasy/model/proj_backtest.py`). It runs the production `projections.baseline()`
  on T-3..T-1 only, with seasons relabelled so T-1 = 2026, and does this for T = 2017-18 … 2025-26. It is compared with three naive
  baselines on per-game RMSE/MAE and on per-game G-score rank (production `valuation.compute_values`), with bootstrap SEs.
  Verdict: **ours beats every baseline on every stat and on value rank**. Top-150 Spearman is 0.724 (0.016), against 0.703 for
  last season, 0.697 for the 3-yr minutes-weighted average and 0.666 for the 3-yr average; paired gains are +0.021 (0.007),
  +0.027 (0.005) and +0.058 (0.008). Biases: ages 30-32 are over-projected (−0.54 G, mostly −1.9 mpg), ages 22-25
  under-projected, team changers over-projected, and minutes are under-regressed.
- **External projections** (`src/fantasy/data/external.py`, `fantasy data fetch-external --source espn|fantasypros|all [--season]`):
  ESPN fantasy API (2026-27 plus historical 2018-19…2025-26 season projections, with makes and attempts) and FantasyPros
  consensus (2026-27, no attempts). Both are cached under `data/raw/<source>/` with provenance. Raw payloads are git-ignored (ToS),
  and the normalised CSVs are committed.
- **Blend** (`src/fantasy/model/blend.py`, `fantasy data build-projections --blend default|"ours=..,espn=..,fantasypros=.."`):
  a per-stat linear blend of makes, attempts and counts, with GP kept from our fitted model. Its output
  `data/processed/projections_2026_27.blend.csv` (+ meta) was produced. **The default projections file is unchanged.**
- The ESPN-history backtest supports a 50/50 ours/ESPN blend: top-150 Spearman +0.020 (0.007), points RMSE −0.20 (0.03),
  better in all 6 usable seasons. ESPN's GP runs about +11 games optimistic, so GP stays ours.
- Comparison for 2026-27, the 25 biggest disagreements, the top-60 rank changes and the mock-draft picks are in
  `docs/research/projection_quality.md`. Pick 1 is Jokić either way. Pick 24 is Holmgren (ours) or Kawhi (blend), a near tie.
  Pick 36 is Derrick White either way.
- Small shared edit: `ids.ALIASES["walter-clayton-jr"] = "walter-clayton"` (the default projections are unchanged by it).
- Docs: `docs/research/projection_quality.md` (new) and `docs/research/data_sources.md` (2026-10-19 section). `docs/USER_TODO.md`
  has a new item about third-party numbers in the public repo.

## Decision pending (lead)
Whether to switch the draft default to the blend before the 2026-10-19 22:00 UTC freeze. My recommendation is yes for
`ours=0.5,espn=0.5` with our GP. The lower-risk alternative: keep ours and use the blend as a tiebreaker. To switch, either
pass `--projections data/processed/projections_2026_27.blend.csv` to `values build` / `draft recommend` / `draft pick`, or copy
the blend over the default file and rerun `fantasy values build`. Rerun `fantasy data build-projections --blend default`
after any manual override change, because the blend is built from the processed default file.

## Findings for others
- **Morez Johnson Jr.** (DAL rookie, Yahoo ADP 168, ESPN 24.7 mpg) appears in no projection input: he is in the ADP and
  positions files only, so our tools cannot draft him. Add a row to `rookies_2026_27.csv` if wanted.
- Our rookie priors are far below ESPN/FP: Caleb Wilson 11 vs about 18 pts, Dybantsa and Boozer similar. This is where the blend
  helps most, and nothing in history checks our priors.
- ESPN's stored 2022-23 projections are in-season updates. Don't use that season for any historical calibration.
- Possible model follow-ups after the draft (not done; draft freeze): refit `AGE_CURVE`/`MPG_GROWTH` on the minutes
  residuals by age, add a stronger minutes regression toward about 22 mpg, and add a team-change minutes penalty. Each is
  backtestable with `proj_backtest`.

## Tests performed
- `tests/test_external_blend.py` (11 tests: the parsers on inline fixtures, the blend's weights, coverage, consistency and imputation,
  backtest no-leakage, baselines, per-game values and bootstrap). No network.
- `python -m pytest -q` (pyproject pythonpath = src) → all passed. `ruff check src tests` → clean.

## Notes for next agent
- Reproduce the backtest: download the full `Player Totals.csv` / `Player Career Info.csv` from the sumitrodatta mirror,
  then call `proj_backtest.project_core(totals, career, T)`, `baselines`, `season_lines` and `per_game_values`. The scratchpad
  scripts are summarised in the research note.
- `fetch-external` is cached. Pass `--refresh` to re-download, and keep it polite (one request per payload). ESPN
  sometimes drops large responses; the fetch retries with backoff.
