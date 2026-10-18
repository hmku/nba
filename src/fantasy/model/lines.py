"""Player stat lines: load projections and convert to availability-adjusted per-game lines.

The atomic unit for all valuation is a per-game *line* of raw stats (makes AND attempts for
percentages, never averaged percentages). "Effective" lines blend a player's projection with
replacement-level production for the games he is expected to miss (you stream a waiver player
into an empty slot, but imperfectly — `stream_recovery` controls how much is recovered).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fantasy.league.settings import REPO_ROOT

DEFAULT_PROJECTIONS = REPO_ROOT / "data" / "processed" / "projections_2026_27.csv"
STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
SEASON_GAMES = 82
GAMES_PER_WEEK = 3.5  # 82 games over ~23.5 matchup weeks

# Per-game coefficient of variation for each raw stat (within-player game-to-game noise).
# Heuristic priors; calibrate against game logs (task: backtesting).
GAME_CV: dict[str, float] = {
    "fgm": 0.38, "fga": 0.30, "ftm": 0.60, "fta": 0.55, "fg3m": 0.75, "pts": 0.35,
    "reb": 0.42, "ast": 0.48, "stl": 0.85, "blk": 0.95, "tov": 0.58,
}


@dataclass(frozen=True)
class ProjectionSet:
    df: pd.DataFrame  # indexed by player_id
    path: str
    sha256: str


def parse_positions(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    return [p.strip() for p in str(value).replace(",", "|").replace("/", "|").split("|") if p.strip()]


def load_projections(path: Path | str | None = None) -> ProjectionSet:
    p = Path(path) if path else DEFAULT_PROJECTIONS
    raw = p.read_bytes()
    df = pd.read_csv(p)
    missing = {"player_id", "name", *STATS, "gp"} - set(df.columns)
    if missing:
        raise ValueError(f"projections file {p} missing columns: {sorted(missing)}")
    df = df.drop_duplicates("player_id").set_index("player_id")
    df["positions"] = df.get("positions", pd.Series(index=df.index, dtype=object)).map(parse_positions)
    for col in STATS + ["gp"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    if "adp" not in df:
        df["adp"] = np.nan
    df["adp"] = pd.to_numeric(df["adp"], errors="coerce")
    for col in ("team", "status", "injury_note"):
        if col not in df:
            df[col] = ""
    df["gp"] = df["gp"].clip(0, SEASON_GAMES)
    return ProjectionSet(df=df, path=str(p), sha256=hashlib.sha256(raw).hexdigest())


def effective_lines(
    df: pd.DataFrame, replacement: pd.Series | None, stream_recovery: float = 0.5
) -> pd.DataFrame:
    """Per-scheduled-game lines: avail * own + (1 - avail) * recovery * replacement."""
    avail = (df["gp"] / SEASON_GAMES).clip(0, 1).to_numpy()[:, None]
    own = df[STATS].to_numpy(dtype=float)
    if replacement is None:
        eff = avail * own
    else:
        rep = replacement[STATS].to_numpy(dtype=float)[None, :]
        eff = avail * own + (1 - avail) * stream_recovery * rep
    return pd.DataFrame(eff, index=df.index, columns=STATS)


def game_variance(lines: pd.DataFrame) -> pd.DataFrame:
    """Per-game variance of each raw stat for each player (CV heuristic)."""
    cv = pd.Series(GAME_CV)[STATS]
    return (lines[STATS] * cv) ** 2
