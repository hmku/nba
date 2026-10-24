# In-season update fits (ros-avail-*, stat-shrink-*)

Reproduces the constants in `src/fantasy/model/inseason.py` and the numbers in `docs/research/inseason_updates.md`.

```bash
S=/tmp/inseason_raw && mkdir -p $S && cd $S
for p in 1 2 3; do curl -sSfLO "https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024/main/regular_season_box_scores_2010_2024_part_$p.csv"; done
curl -sSfLO "https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/Player%20Totals.csv"
curl -sSfLO "https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/Team%20Summaries.csv"
cd -   # ESPN 2024-25 / 2025-26: `fantasy data fetch-boxscores --season 2025` (and 2026), committed in data/raw/espn_boxscores
python - <<'EOF'
import sys; sys.path.insert(0, "scripts/inseason_model")
from pathlib import Path
import pandas as pd, timelines as P
S = Path("/tmp/inseason_raw")
pri = P.priors(pd.read_csv(S / "Player%20Totals.csv"), pd.read_csv(S / "Team%20Summaries.csv"), list(range(2011, 2027)))
box = P.load_nocturne(sorted(S.glob("regular_season_box_scores_*.csv")))
esp = P.load_espn(sorted(Path("data/raw/espn_boxscores").glob("player_games_202[56].csv.gz")))
tl = pd.concat([P.timelines(box, pri[pri.season <= 2024]), P.timelines(esp, pri[pri.season.isin([2025, 2026])])])
pri.to_pickle(S / "priors.pkl"); tl.to_pickle(S / "tl_all.pkl")
EOF
python scripts/inseason_model/fit_availability.py --tl /tmp/inseason_raw/tl_all.pkl --priors /tmp/inseason_raw/priors.pkl --out scripts/inseason_model/report_avail.json
python scripts/inseason_model/fit_stats.py --tl /tmp/inseason_raw/tl_all.pkl --priors /tmp/inseason_raw/priors.pkl --totals "/tmp/inseason_raw/Player%20Totals.csv" --n-boot 200 --out scripts/inseason_model/report_stats.json
```

- `timelines.py`: box scores -> player team-game timelines (rules shared with production via `inseason.player_timeline`).
- `fit_availability.py`: per-checkpoint WLS of the rest-of-season play share through the fantasy-season end (NBA last
  day - 21 days; `--to-nba-end` = the ros-avail-0.3 target); OOS vs static prior, iid Beta-binomial and the deployed
  early damping (`update_damped`).
- `fit_stats.py`: stat-specific stabilisation constants (n0) shrinking observed rates toward the preseason projection.
- `fit_minutes.py`: recency-weighted minutes models (minutes-*): fit 2010-11..2024-25 (NocturneBear parts above +
  committed ESPN 2024-25), test 2025-26; prior = `project_core` (needs the full sumitrodatta Player Totals):
  `PYTHONPATH=src python scripts/inseason_model/fit_minutes.py --nocturne $S --totals "$S/Player%20Totals.csv" --n-boot 200`
  -> `report_minutes.json` (`deployed` -> `minutes.FITTED`). Files used for minutes-0.2.0 (sha256):
  part_1 ca9636d08c577e3d752f69e32f90b173f4c3374c15f4dc8494b655049fdfc557,
  part_2 5643af8a9f15ce3540dced759375ca79e77c71644e23270136f23b7a57fc182d,
  part_3 65a01fee3df865a272e578cf4b365dce7dbc62eef2ce6f15f0844cf6694ef375; Player Totals (all seasons to 2026)
  1c081cd7f9c694755ea2210c3426e709540afedc31c1bf781456f3df4b7582b2.
- Production constants are pasted from `report_avail.json` (`production_model`: all seasons) and `report_stats.json` (`n0`).
