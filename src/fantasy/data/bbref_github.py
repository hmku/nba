"""Basketball-Reference season tables via the sumitrodatta/bball-reference-datasets GitHub mirror.

basketball-reference.com itself is not reachable from the agent container, but this public
repository (the source of the Kaggle "NBA Stats (1947-present)" dataset) republishes the
bbref player tables as CSV and is reachable on raw.githubusercontent.com.

We cache only recent seasons (>= MIN_SEASON) to keep the committed raw files small.
Season labels follow bbref: season 2026 == 2025-26.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from fantasy.data.provenance import RAW_DIR, write_provenance

BASE = "https://raw.githubusercontent.com/sumitrodatta/bball-reference-datasets/master/Data/"
SOURCE = "bbref_sumitrodatta"
MIN_SEASON = 2022

FILES = {
    "player_totals.csv": "Player Totals.csv",
    "player_play_by_play.csv": "Player Play By Play.csv",
    "player_career_info.csv": "Player Career Info.csv",
}


def _url(remote: str) -> str:
    return BASE + remote.replace(" ", "%20")


def fetch(min_season: int = MIN_SEASON, timeout: float = 60.0) -> list[dict]:
    out_dir = RAW_DIR / SOURCE
    out_dir.mkdir(parents=True, exist_ok=True)
    provs = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for local, remote in FILES.items():
            url = _url(remote)
            r = client.get(url)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text), keep_default_na=True)
            full_rows = len(df)
            if "season" in df.columns:
                df = df[(df["season"] >= min_season) & (df["lg"] == "NBA")]
                note = f"filtered to NBA seasons >= {min_season} (bbref season = end year)"
            else:  # career info: keep players active in the window
                df = df[df["to"] >= min_season]
                note = f"filtered to players with last season >= {min_season}"
            path = out_dir / local
            df.to_csv(path, index=False)
            provs.append(
                write_provenance(
                    path,
                    url=url,
                    method="httpx GET raw.githubusercontent.com, pandas filter",
                    rows=len(df),
                    notes=note,
                    extra={
                        "upstream_rows": full_rows,
                        "upstream": "https://github.com/sumitrodatta/bball-reference-datasets",
                    },
                )
            )
    return provs


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(RAW_DIR / SOURCE / name)
