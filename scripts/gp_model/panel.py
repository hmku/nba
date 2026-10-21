"""Player-season panel for the games-played model (bbref full history, sumitrodatta mirror).

Inputs (download once, not committed; see README.md in this folder):
  Player Totals.csv, Team Summaries.csv from
  https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/

One row per (player, target season T) with an NBA season in T-1 (any minutes), plus "lost season" rows:
no T-1 season but a T-2 season of >= LOST_MIN minutes (g1 = mp1 = 0, lost=1); the player must play in T
or later, as for every row.
  y      games in T on an 82-game scale; 0 if the player has no T row but plays again later
         (a lost season while still in the league); rows of players who never play again are dropped
  g1..g3 games in T-1..T-3 (82 scale, NaN when no season), mp1..mp3 minutes, mpg1 minutes per game in T-1, age = age in T-1 + 1.
Season labels follow bbref (2026 == 2025-26).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MULTI = {"TOT", "2TM", "3TM", "4TM", "5TM"}
LOST_MIN = 1000.0


def player_seasons(totals: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    """One row per NBA player-season: g82 (games scaled to 82, capped), mp, age."""
    t = totals[totals["lg"] == "NBA"].copy()
    tm = teams[teams["lg"] == "NBA"].copy()
    tm["sched"] = tm["w"] + tm["l"]
    sched = tm.set_index(["season", "abbreviation"])["sched"].to_dict()
    t["sched"] = [sched.get((s, a), np.nan) for s, a in zip(t["season"], t["team"], strict=True)]
    rows = []
    for (pid, season), g in t.groupby(["player_id", "season"], sort=False):
        multi = g[g["team"].isin(MULTI)]
        row = multi.iloc[0] if len(multi) else g.iloc[0]
        stints = g[~g["team"].isin(MULTI)]
        n_sched = stints["sched"].max() if stints["sched"].notna().any() else 82.0
        rows.append({
            "player_id": pid, "player": row["player"], "season": int(season),
            "age": float(row["age"]), "g": float(row["g"]), "mp": float(row["mp"]),
            "g82": float(min(82.0, row["g"] * 82.0 / n_sched)),
        })
    return pd.DataFrame(rows)


def build_panel(ps: pd.DataFrame, first_target: int = 2000) -> pd.DataFrame:
    """Targets T >= first_target (see module doc)."""
    by = ps.set_index(["player_id", "season"])
    last = ps.groupby("player_id")["season"].max()
    max_season = int(ps["season"].max())
    out = []

    def row(pid: str, name: str, T: int, age: float, lost: bool) -> None:
        if (pid, T) in by.index:
            y = by.loc[(pid, T), "g82"]
        elif last[pid] > T:
            y = 0.0
        else:
            return  # never played again: retired / out of the league
        rec = {"player_id": pid, "player": name, "T": T, "y": float(y), "age": age, "lost": int(lost)}
        for k in (1, 2, 3):
            key = (pid, T - k)
            rec[f"g{k}"] = by.loc[key, "g82"] if key in by.index else (0.0 if lost and k == 1 else np.nan)
            rec[f"mp{k}"] = by.loc[key, "mp"] if key in by.index else (0.0 if lost and k == 1 else np.nan)
        key = (pid, T - 1)
        rec["mpg1"] = by.loc[key, "mp"] / max(by.loc[key, "g"], 1.0) if key in by.index else 0.0
        out.append(rec)

    for (pid, s1), r in by.iterrows():
        if first_target <= s1 + 1 <= max_season:
            row(pid, r["player"], s1 + 1, r["age"] + 1, lost=False)
        T = s1 + 2  # lost season: no row in s1 + 1
        if (first_target <= T <= max_season and (pid, s1 + 1) not in by.index and r["mp"] >= LOST_MIN):
            row(pid, r["player"], T, r["age"] + 2, lost=True)
    return pd.DataFrame(out)
