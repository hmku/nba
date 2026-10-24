"""Team-game x player minutes panel and star-absence spells from ESPN box scores (research code for
docs/research/opportunity_detector.md; production code is src/fantasy/inseason/opportunity.py).

Season labels follow the box-score files: 2025 = 2024-25, 2026 = 2025-26.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BOX = ROOT / "data" / "raw" / "espn_boxscores"
STATS = ["pts", "fgm", "fga", "fg3m", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"]
STAR_MPG = 28.0


def load_season(season: int) -> pd.DataFrame:
    """Long panel: one row per (team game, player listed or inside his stint). min = 0 when he did not play;
    gaps inside a stint (not listed) count as missed. Columns: season, team, gi (team game index), game_date,
    player_id, min, played, starter, inj (non-coach DNP reason), STATS."""
    d = pd.read_csv(BOX / f"player_games_{season}.csv.gz")
    d = d[d["regular_season"].astype(bool)].drop_duplicates(["game_id", "team", "player_id"])
    d = d[d["player_id"].notna()]
    d["min"] = d["min"].fillna(0.0)
    d.loc[~d["played"].astype(bool), "min"] = 0.0
    tg = d[["team", "game_id", "game_date"]].drop_duplicates().sort_values(["team", "game_date", "game_id"])
    tg["gi"] = tg.groupby("team").cumcount()
    d = d.merge(tg[["team", "game_id", "gi"]], on=["team", "game_id"])
    reason = d["dnp_reason"].fillna("").astype(str).str.upper()
    d["inj"] = (reason != "") & ~reason.str.contains("COACH")
    d["starter"] = d["starter"].fillna(False).astype(bool) & (d["min"] > 0)
    out = []
    for (team, pid), g in d.groupby(["team", "player_id"], sort=False):
        lo, hi = int(g["gi"].min()), int(g["gi"].max())
        full = pd.DataFrame({"gi": np.arange(lo, hi + 1)})
        g = full.merge(g, on="gi", how="left")
        g["team"], g["player_id"] = team, pid
        g["min"] = g["min"].fillna(0.0)
        g["starter"] = g["starter"].fillna(False).astype(bool)
        g["inj"] = g["inj"].fillna(False).astype(bool)
        out.append(g)
    p = pd.concat(out, ignore_index=True)
    dates = tg.set_index(["team", "gi"])["game_date"]
    p["game_date"] = dates.reindex(list(zip(p["team"], p["gi"], strict=True))).to_numpy()
    p["played"] = p["min"] > 0
    p["season"] = season
    return p[["season", "team", "gi", "game_date", "player_id", "min", "played", "starter", "inj", *STATS]]


def absence_spells(p: pd.DataFrame, star_mpg: float = STAR_MPG, min_prior: int = 5) -> pd.DataFrame:
    """Spells of >= 1 consecutive missed team games by a player whose last <= 10 games PLAYED averaged >= star_mpg
    (>= min_prior games played before the spell). L = spell length; censored = the stint/season ended first."""
    rows = []
    for (season, team, pid), g in p.groupby(["season", "team", "player_id"], sort=False):
        m = g["min"].to_numpy()
        gi = g["gi"].to_numpy()
        n = len(m)
        k = 1
        while k < n:
            if m[k] == 0 and m[k - 1] > 0:
                prev = m[:k][m[:k] > 0][-10:]
                if len(prev) >= min_prior and prev.mean() >= star_mpg:
                    L = 0
                    while k + L < n and m[k + L] == 0:
                        L += 1
                    rows.append({"season": season, "team": team, "star": pid, "g0": int(gi[k]), "L": L,
                                 "censored": k + L >= n, "star_mpg": float(prev.mean())})
                    k += max(L, 1)
                    continue
            k += 1
    return pd.DataFrame(rows)


def survival(spells: pd.DataFrame, kmax: int = 40) -> list[float]:
    """S(k) = P(L >= k | L >= 1), k = 1..kmax (censored spells counted at their observed length: slightly short)."""
    L = spells["L"].to_numpy()
    return [float((L >= k).mean()) for k in range(1, kmax + 1)]


def groups_map(proj_path: Path = ROOT / "data" / "processed" / "projections_2026_27.csv") -> dict[str, str]:
    """Position group string ('G', 'W', 'B' letters) per player from the current projections (a proxy for past
    seasons: positions are fairly stable)."""
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from fantasy.inseason.opportunity import position_groups

    pos = pd.read_csv(proj_path)[["player_id", "positions"]]
    return {r.player_id: "".join(sorted(position_groups(r.positions))) for r in pos.itertuples()}
