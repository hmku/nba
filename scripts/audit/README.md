# Weekly model audit (Mondays; read-only)

```bash
source .venv/bin/activate
python scripts/audit/weekly_audit.py --through 2026-11-08            # default: last Sunday; ~1.5 s per 1k sims x 7 runs
python scripts/audit/weekly_audit.py --through 2026-11-08 --sims 2000 # quick (~35 s); --skip-sim reuses out/preds.json
```
Inputs come from git history (no checkout): preseason projection = last commit touching `projections_<season>.csv`
before the season start; each week's projection/rosters = repo state at noon ET on the week's start day; as-of state =
noon ET the day after `--through`. Weeks/matchups from `data/manual/league_schedule.json`. Nothing is saved to the DB
(`fantasy review week --json --no-ledger`, sims in-process). Method: `docs/research/bias_audit_2026-11-09.md`.

Outputs (`--out`, default `data/processed/audit/<through>/`):
- `report.md` — headline, candidate fixes (|t| ≥ 3 SE), week-over-week vs the newest earlier `findings.json`, then sections.
- `findings.json` — list of `{id, area, stat, estimate, se, t, candidate_fix, suggestion, ...}`; `t = (estimate − null)/se`
  (null 1 for slopes / var z). Stable ids so weeks compare; `findings.csv` is the same flattened. `meta.json`: shas, runtime.
- `bias_preseason.csv`, `bias_weekstart.csv` — per-stat bias per game (rate vs minutes split), clustered SEs.
- `player_level.csv`, `minutes_changes.csv`, `team_minutes.csv` (TVD, unprojected share), `performers.csv`,
  `availability.csv`, `unprojected_players.csv`.
- `standings.csv`, `matchups.csv` (outcome, basis yahoo_final|reconstructed, week-start P(win)), `calibration.json`,
  `preds.json` (sim cache), `reviews/` (raw week reviews), `inputs/` (rosters/projections at each week start).
- Rival moves: if `scripts/audit/moves_review.py` exists it is run as `--start <last week start> --end <through> --out
  <out>/moves`; its `findings.json` (same schema) is merged and `report.md` linked.

Modules: `common.py` (git, estimators, findings), `projection_bias.py`, `league_calibration.py`; tests `tests/test_audit_scripts.py`.
