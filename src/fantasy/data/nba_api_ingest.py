"""Optional ingestion from stats.nba.com via `nba_api` (extra: `pip install -e .[nba]`).

stats.nba.com is blocked from the agent container, so the default pipeline uses the
Basketball-Reference mirror (fantasy.data.bbref_github). This path exists so the user can refresh
season totals on their own machine. Output: data/raw/nba_api/league_player_totals_<season>.csv
with provenance, in nba.com column naming (PLAYER_NAME, TEAM_ABBREVIATION, GP, MIN, FGM, ...).

`to_bbref_like()` converts these files to the bbref-mirror schema used by the projection model,
so they can be dropped in as a replacement if the mirror ever goes stale.
"""

from __future__ import annotations

import time

import pandas as pd

from fantasy.data.provenance import RAW_DIR, write_provenance

SOURCE = "nba_api"


def fetch(seasons: list[str], sleep_s: float = 1.0) -> list[dict]:
    try:
        from nba_api.stats.endpoints import leaguedashplayerstats
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError("nba_api not installed: pip install -e '.[nba]'") from e

    out_dir = RAW_DIR / SOURCE
    out_dir.mkdir(parents=True, exist_ok=True)
    provs = []
    for season in seasons:
        resp = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season, per_mode_detailed="Totals", season_type_all_star="Regular Season", timeout=60
        )
        df = resp.get_data_frames()[0]
        path = out_dir / f"league_player_totals_{season}.csv"
        df.to_csv(path, index=False)
        provs.append(
            write_provenance(
                path,
                url="https://stats.nba.com/stats/leaguedashplayerstats",
                method=f"nba_api LeagueDashPlayerStats season={season} PerMode=Totals",
                rows=len(df),
            )
        )
        time.sleep(sleep_s)
    return provs


def to_bbref_like(df: pd.DataFrame, season_end_year: int) -> pd.DataFrame:
    """Map nba_api LeagueDashPlayerStats totals to the bbref-mirror totals schema."""
    return pd.DataFrame(
        {
            "season": season_end_year,
            "lg": "NBA",
            "player": df["PLAYER_NAME"],
            "player_id": "nba" + df["PLAYER_ID"].astype(str),
            "age": df["AGE"],
            "team": df["TEAM_ABBREVIATION"],
            "pos": "",
            "g": df["GP"],
            "mp": df["MIN"],
            "fg": df["FGM"],
            "fga": df["FGA"],
            "x3p": df["FG3M"],
            "x3pa": df["FG3A"],
            "ft": df["FTM"],
            "fta": df["FTA"],
            "trb": df["REB"],
            "ast": df["AST"],
            "stl": df["STL"],
            "blk": df["BLK"],
            "tov": df["TOV"],
            "pts": df["PTS"],
        }
    )
