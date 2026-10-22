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
- `fit_availability.py`: per-checkpoint WLS of the rest-of-season play share; OOS vs static prior and iid Beta-binomial.
- `fit_stats.py`: stat-specific stabilisation constants (n0) shrinking observed rates toward the preseason projection.
- Production constants are pasted from `report_avail.json` (`production_model`: all seasons) and `report_stats.json` (`n0`).
