"""Population prior for players with minutes but no projection row (T-023), from 2024-25 / 2025-26 ESPN box scores.
Population = played, not in the production-like prior (priors_<T>.csv) and not in ESPN's preseason projections
(production: such players are neither ours nor in the rookie file). Pooled per-minute rates, FG%/FT%, mpg in games
played, and the share of timeline games played after his first appearance."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from fantasy.model.inseason import player_timeline  # noqa: E402

R = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[3] / "var" / "team_budget_bt"
OUT.mkdir(parents=True, exist_ok=True)
C = ["min", "fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov", "pts"]
res = {}
for T, tag in ((2025, "2024_25"), (2026, "2025_26"), (2027, None)):
    pri = pd.read_csv(OUT / f"priors_{T}.csv") if T < 2027 else pd.read_csv(R / "data/processed/projections_2026_27.csv")
    espn = set(pd.read_csv(R / f"data/raw/espn/projections_{tag}.csv")["player_id"]) if tag else set()
    pg = pd.read_csv(R / f"data/raw/espn_boxscores/player_games_{T}.csv.gz")
    pg = pg[pg.regular_season].copy()
    pg["game_id"] = pg.game_id.astype(str)
    pg["played"] = pg["played"].astype(bool) & (pg["min"] > 0)
    tg = pd.read_csv(R / f"data/raw/espn_boxscores/team_games_{T}.csv.gz")
    tg = tg[tg.regular_season].assign(game_id=lambda d: d.game_id.astype(str))
    sched = {t: d.sort_values(["game_date", "game_id"])[["game_id", "game_date"]].reset_index(drop=True)
             for t, d in tg.groupby("team")}
    known = set(pri.player_id)
    played = pg[pg.played]
    unk = sorted(set(played.player_id) - known - espn)
    unk_all = sorted(set(played.player_id) - known)
    P = played[played.player_id.isin(unk)]
    tot = P[C].sum()
    shares, mpgs = [], []
    for _pid, d in pg[pg.player_id.isin(unk)].groupby("player_id"):
        tl = player_timeline(d[["game_id", "game_date", "team", "played"]], sched, None)
        if tl.empty:
            continue
        p = tl["played"].to_numpy(bool)
        first = int(np.argmax(p))
        shares.append((p[first + 1:].sum(), len(p) - first - 1))
    sh = np.array(shares, float)
    r = {"n_players": len(unk), "n_players_incl_espn": len(unk_all),
         "minutes_share": float(P["min"].sum() / played["min"].sum()),
         "minutes_share_incl_espn": float(played[played.player_id.isin(unk_all)]["min"].sum() / played["min"].sum()),
         "mpg": float(tot["min"] / len(P)),
         "games_per_player": float(len(P) / max(len(unk), 1)),
         "per_min": {s: float(tot[s] / tot["min"]) for s in ["fga", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"]},
         "fg_pct": float(tot.fgm / tot.fga), "ft_pct": float(tot.ftm / tot.fta),
         "pts_ratio": float(tot.pts / (2 * tot.fgm + tot.fg3m + tot.ftm)),
         "share_after_first": float(sh[:, 0].sum() / max(sh[:, 1].sum(), 1)),
         "share_after_first_player_sd": float(np.nanstd(sh[sh[:, 1] >= 5, 0] / sh[sh[:, 1] >= 5, 1])),
         }
    res[T] = r
    print(T, json.dumps(r, indent=1))
json.dump(res, open(OUT / "pop.json", "w"), indent=1)
