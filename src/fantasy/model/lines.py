"""Player stat lines: load projections and convert to availability-adjusted per-game lines.

The atomic unit for all valuation is a per-game *line* of raw stats (makes AND attempts for
percentages, never averaged percentages). "Effective" lines blend a player's projection with
replacement-level production for the games he is expected to miss (you stream a waiver player
into an empty slot, but imperfectly — `stream_recovery` controls how much is recovered).

Availability (`availability()`) is gp / 82, optionally with a downside adjustment for gp
uncertainty (`gp_risk_lambda`: gp - lambda * gp_sd) and a tilt toward availability in the fantasy
playoff weeks (`playoff_weight`, using the projections' `playoff_mult`). See
docs/research/availability_model.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import warnings
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from fantasy.league.settings import REPO_ROOT

DEFAULT_PROJECTIONS = REPO_ROOT / "data" / "processed" / "projections_2026_27.csv"  # preseason (draft) projections
# Rest-of-season projections (`fantasy data update-projections`, fantasy.model.inseason): the default once written;
# FANTASY_PROJECTIONS=preseason forces the preseason file.
INSEASON_PROJECTIONS = REPO_ROOT / "data" / "processed" / "projections_2026_27.inseason.csv"
INSEASON_MAX_AGE_DAYS = 7  # older in-season projections still load, with a warning
STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
SEASON_GAMES = 82
GAMES_PER_WEEK = 3.5  # 82 games over ~23.5 matchup weeks
# Share of the fantasy season's matchup weeks that are playoff weeks (weeks 18-20 of 20). A
# playoff_weight equal to this leaves availability unchanged (season average).
PLAYOFF_CALENDAR_SHARE = 3 / 20

# Game-to-game noise lives in fantasy.model.noise (one variance model for draft and simulator).


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


def default_projections_path() -> Path:
    """In-season projections when they exist (unless FANTASY_PROJECTIONS=preseason), else the preseason file.

    Warns when the in-season file was built from a different preseason file than the current one (re-run
    `fantasy data update-projections`)."""
    if os.environ.get("FANTASY_PROJECTIONS", "").lower() == "preseason" or not INSEASON_PROJECTIONS.exists():
        return DEFAULT_PROJECTIONS
    meta = INSEASON_PROJECTIONS.with_name(INSEASON_PROJECTIONS.stem + ".meta.json")
    try:
        m = json.loads(meta.read_text())
    except (OSError, ValueError):
        m = {}
    built_from = m.get("prior_sha256")
    try:
        age = (date.today() - date.fromisoformat(str(m.get("as_of")))).days
    except ValueError:
        age = None
    if age is not None and age > INSEASON_MAX_AGE_DAYS:
        warnings.warn(f"{INSEASON_PROJECTIONS.name} is {age} days old (as of {m.get('as_of')}): "
                      "run `fantasy data update-projections --fetch`", stacklevel=2)
    if built_from and DEFAULT_PROJECTIONS.exists() and built_from != hashlib.sha256(
            DEFAULT_PROJECTIONS.read_bytes()).hexdigest():
        warnings.warn(f"{INSEASON_PROJECTIONS.name} was built from an older {DEFAULT_PROJECTIONS.name}: "
                      "re-run `fantasy data update-projections`", stacklevel=2)
    return INSEASON_PROJECTIONS


def load_projections(path: Path | str | None = None) -> ProjectionSet:
    p = Path(path) if path else default_projections_path()
    raw = p.read_bytes()
    df = pd.read_csv(p)
    missing = {"player_id", "name", *STATS, "gp"} - set(df.columns)
    if missing:
        raise ValueError(f"projections file {p} missing columns: {sorted(missing)}")
    df = df.drop_duplicates("player_id").set_index("player_id")
    df["positions"] = df.get("positions", pd.Series(index=df.index, dtype=object)).map(parse_positions)
    for col in STATS + ["gp"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    # optional availability columns (older projection files lack them -> neutral values)
    df["gp_sd"] = pd.to_numeric(df["gp_sd"], errors="coerce").fillna(0.0) if "gp_sd" in df else 0.0
    df["playoff_mult"] = (
        pd.to_numeric(df["playoff_mult"], errors="coerce").fillna(1.0) if "playoff_mult" in df else 1.0
    )
    if "adp" not in df:
        df["adp"] = np.nan
    df["adp"] = pd.to_numeric(df["adp"], errors="coerce")
    for col in ("team", "status", "injury_note"):
        if col not in df:
            df[col] = ""
    df["gp"] = df["gp"].clip(0, SEASON_GAMES)
    return ProjectionSet(df=df, path=str(p), sha256=hashlib.sha256(raw).hexdigest())


def availability(
    df: pd.DataFrame, gp_risk_lambda: float = 0.0, playoff_weight: float = PLAYOFF_CALENDAR_SHARE
) -> np.ndarray:
    """Share of scheduled games a player is valued as available for.

    a = clip((gp - gp_risk_lambda * gp_sd) / 82, 0, 1), then reweighted between regular-season and
    playoff weeks: a * ((1 - w) * r + w * m), where m = playoff_mult (playoff-week availability
    relative to the season average), r = (1 - s*m) / (1 - s) the matching regular-season
    availability (so the season average is preserved), s = PLAYOFF_CALENDAR_SHARE and
    w = playoff_weight. w = s reproduces the plain season average.
    """
    gp = df["gp"].to_numpy(dtype=float)
    if gp_risk_lambda:
        sd = df["gp_sd"].to_numpy(dtype=float) if "gp_sd" in df else np.zeros(len(df))
        gp = gp - gp_risk_lambda * sd
    a = np.clip(gp / SEASON_GAMES, 0.0, 1.0)
    if playoff_weight != PLAYOFF_CALENDAR_SHARE and "playoff_mult" in df:
        s, w = PLAYOFF_CALENDAR_SHARE, playoff_weight
        m = df["playoff_mult"].to_numpy(dtype=float)
        r = (1 - s * m) / (1 - s)
        a = np.clip(a * ((1 - w) * r + w * m), 0.0, 1.0)
    return a


def effective_lines(
    df: pd.DataFrame,
    replacement: pd.Series | None,
    stream_recovery: float = 0.5,
    avail: np.ndarray | None = None,
) -> pd.DataFrame:
    """Per-scheduled-game lines: avail * own + (1 - avail) * recovery * replacement.

    `avail` defaults to the plain season average gp / 82 (see `availability`)."""
    if avail is None:
        avail = availability(df)
    avail = np.asarray(avail, dtype=float)[:, None]
    own = df[STATS].to_numpy(dtype=float)
    if replacement is None:
        eff = avail * own
    else:
        rep = replacement[STATS].to_numpy(dtype=float)[None, :]
        eff = avail * own + (1 - avail) * stream_recovery * rep
    return pd.DataFrame(eff, index=df.index, columns=STATS)

