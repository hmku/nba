"""Historical production-like priors for 2024-25 (2025) and 2025-26 (2026): project_core(T) on bbref T-3..T-1,
450-row volume cut, 50/50 blend with ESPN's preseason projections (production blend code), team = ESPN's preseason
team when given (proxy for production's trade overrides), else last T-1 team. Writes priors_<T>.csv."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from fantasy.model.blend import BlendSpec, blend  # noqa: E402
from fantasy.model.proj_backtest import project_core  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[3] / "var" / "team_budget_bt"
OUT.mkdir(parents=True, exist_ok=True)
tot = pd.read_csv(REPO / "data/raw/bbref_sumitrodatta/player_totals.csv")
car = pd.read_csv(REPO / "data/raw/bbref_sumitrodatta/player_career_info.csv")
for T, tag in ((2025, "2024_25"), (2026, "2025_26")):
    core = project_core(tot, car, T).reset_index()
    core["_rel"] = core["mpg"] * core["gp"].clip(lower=20)
    core = core.sort_values(["_rel", "player_id"], ascending=[False, True]).head(450)
    core = core.drop_duplicates("player_id")
    core["positions"] = core["bbref_pos"].astype(str)
    core["source"] = "core"
    core["status"] = "healthy"
    core["playoff_mult"] = 1.0
    cols = ["player_id", "name", "team", "positions", "age", "gp", "gp_sd", "mpg", "fgm", "fga", "ftm", "fta",
            "fg3m", "pts", "reb", "ast", "stl", "blk", "tov", "status", "source", "playoff_mult"]
    core = core[cols]
    espn = pd.read_csv(REPO / f"data/raw/espn/projections_{tag}.csv")
    b, rep = blend(core, {"espn": espn}, BlendSpec({"ours": 0.5, "espn": 0.5}))
    et = espn.drop_duplicates("player_id").set_index("player_id")["team"].astype(str)
    t_espn = b["player_id"].map(et)
    ok = t_espn.notna() & (t_espn != "FA") & (t_espn != "nan")
    b["team_core"] = b["team"]
    b.loc[ok, "team"] = t_espn[ok]
    b.to_csv(OUT / f"priors_{T}.csv", index=False)
    print(T, len(b), "blended", rep["blended_players"], "team changed", int((b.team != b.team_core).sum()),
          "mpg sum/team", round(float((b.mpg * b.gp / 82).sum() / 30), 1))
