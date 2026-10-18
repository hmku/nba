"""Out-of-sample backtest of the projection core (docs/research/projection_quality.md).

For a target season T the *real* `fantasy.model.projections.baseline()` is run on Basketball-
Reference totals for T-3..T-1 only (no leakage). To reuse the production code unchanged, the
seasons are relabelled so that T-1 becomes 2026 (the production "latest season"): ages, season
weights, the minutes logic and the fitted games model then see exactly what they would have seen
before T. Manual offseason overlays (trades, injuries, rookies, roles) do not exist historically,
so this tests the statistical core only; like production, players without a T-1 season are not
projected (production re-activates them by hand).

Baselines (same inputs, same players):
- `last`: T-1 per-game line;
- `avg3`: three-season per-game average (sum of stats / sum of games);
- `mw3`: three-season minutes-weighted per-minute rates (5/3/2) x recency-weighted MPG
  (0.6/0.3/0.1), i.e. our method without regression to the mean, age curve or minutes growth.

Fantasy value = per-game 9-cat G-score from `fantasy.model.valuation.compute_values` with every
player available every game (gp = 82), so only the per-game line matters.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from fantasy.league.settings import LeagueSettings
from fantasy.model import projections as P
from fantasy.model.lines import ProjectionSet
from fantasy.model.valuation import ValuationParams, compute_values

STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
LATEST = 2026  # production "latest completed season" the core is written for
METHODS = ["ours", "last", "avg3", "mw3"]


def shifted_totals(totals: pd.DataFrame, target: int) -> pd.DataFrame:
    """Raw bbref totals rows for target-3..target-1, relabelled so target-1 -> LATEST."""
    t = totals[(totals["season"] >= target - 3) & (totals["season"] <= target - 1)].copy()
    t["season"] = t["season"] + (LATEST + 1 - target)
    return t


def project_core(totals: pd.DataFrame, career: pd.DataFrame, target: int) -> pd.DataFrame:
    """Production `baseline()` for `target` using only seasons before it. Indexed by bbref id."""
    with tempfile.TemporaryDirectory() as d:
        shifted_totals(totals, target).to_csv(Path(d) / "player_totals.csv", index=False)
        career.to_csv(Path(d) / "player_career_info.csv", index=False)
        with mock.patch.object(P, "_raw", lambda name: Path(d) / name):
            df = P.baseline(P.Params())
    df = df[df["last_season"] == LATEST]  # production: stale players need a manual override
    df = df.set_index("bbref_id")
    df["pts"] = 2 * df["fgm"] + df["fg3m"] + df["ftm"]
    return df


def season_lines(totals: pd.DataFrame, season: int) -> pd.DataFrame:
    """Per-game line of each player in `season` (combined row for traded players), by bbref id."""
    with tempfile.TemporaryDirectory() as d:
        totals[totals["season"] == season].to_csv(Path(d) / "player_totals.csv", index=False)
        with mock.patch.object(P, "_raw", lambda name: Path(d) / name):
            t = P.load_totals()
    t = t.set_index("player_id")
    g = t["g"].clip(lower=1)
    out = pd.DataFrame({s: t[s] / g for s in STATS if s != "pts"})
    out["pts"] = 2 * out["fgm"] + out["fg3m"] + out["ftm"]
    out["g"] = t["g"]
    out["mpg"] = t["mp"] / g
    out["team"] = t["last_team"].where(t["last_team"].notna(), t["team"])
    out["first_team"] = _first_team(totals, season)
    out["age"] = t["age"]
    out["name"] = t["player"]
    out["pos"] = t["pos"]
    return out


def _first_team(totals: pd.DataFrame, season: int) -> pd.Series:
    t = totals[(totals["season"] == season) & ~totals["team"].astype(str).str.fullmatch(r"\dTM")]
    return t.groupby("player_id")["team"].first()


def baselines(totals: pd.DataFrame, target: int) -> dict[str, pd.DataFrame]:
    """The three naive baselines for `target`, each indexed by bbref id."""
    with tempfile.TemporaryDirectory() as d:
        totals[(totals["season"] >= target - 3) & (totals["season"] <= target - 1)].to_csv(
            Path(d) / "player_totals.csv", index=False
        )
        with mock.patch.object(P, "_raw", lambda name: Path(d) / name):
            t = P.load_totals()
    raw = [s for s in STATS if s != "pts"]
    last = t[t["season"] == target - 1].set_index("player_id")
    g1 = last["g"].clip(lower=1)
    out_last = pd.DataFrame({s: last[s] / g1 for s in raw})
    out_last["mpg"] = last["mp"] / g1

    s = t.groupby("player_id")[[*raw, "g", "mp"]].sum()
    gs = s["g"].clip(lower=1)
    out_avg = pd.DataFrame({c: s[c] / gs for c in raw})
    out_avg["mpg"] = s["mp"] / gs

    w = {target - 1: 5.0, target - 2: 3.0, target - 3: 2.0}
    rw = {target - 1: 0.6, target - 2: 0.3, target - 3: 0.1}
    tt = t.copy()
    tt["w"] = tt["season"].map(w)
    num = tt[raw].mul(tt["w"], axis=0).groupby(tt["player_id"]).sum()
    den = (tt["mp"] * tt["w"]).groupby(tt["player_id"]).sum().clip(lower=1)
    rate = num.div(den, axis=0)
    gg = tt[tt["g"] >= 10].copy()
    gg["mpg"] = gg["mp"] / gg["g"]
    gg["rw"] = gg["season"].map(rw)
    mpg = (gg["mpg"] * gg["rw"]).groupby(gg["player_id"]).sum() / gg.groupby("player_id")[
        "rw"
    ].sum()
    mpg = mpg.reindex(rate.index)
    fallback = s["mp"] / gs
    mpg = mpg.fillna(fallback.reindex(rate.index))
    out_mw = rate.mul(mpg, axis=0)
    out_mw["mpg"] = mpg
    res = {"last": out_last, "avg3": out_avg, "mw3": out_mw}
    for df in res.values():
        df["pts"] = 2 * df["fgm"] + df["fg3m"] + df["ftm"]
    return res


def per_game_values(
    lines: pd.DataFrame, settings: LeagueSettings, kappa: float = 1.0
) -> pd.Series:
    """Per-game 9-cat G-score (value over replacement) with everyone available every game."""
    df = lines[STATS].astype(float).copy()
    df["name"] = lines.index.astype(str)
    df["team"] = ""
    df["positions"] = [[] for _ in range(len(df))]
    df["gp"] = 82.0
    df["gp_sd"] = 0.0
    df["playoff_mult"] = 1.0
    df["adp"] = np.nan
    df["status"] = "healthy"
    vals, _, _ = compute_values(
        ProjectionSet(df=df, path="backtest", sha256=""),
        settings,
        ValuationParams(kappa=kappa, playoff_weight=0.15, stream_recovery=0.0),
    )
    return vals["value"].reindex(lines.index)


def spearman(a: pd.Series, b: pd.Series) -> float:
    return float(a.rank().corr(b.rank()))


def bootstrap(
    groups: dict[int, pd.DataFrame],
    stat: Callable[[pd.DataFrame], dict[str, float]],
    n_boot: int = 500,
    seed: int = 0,
) -> tuple[dict[str, float], dict[str, float]]:
    """Mean over seasons of `stat` (dict of metrics) and its bootstrap SE (players resampled
    within season, seasons kept). Returns (point estimates, standard errors)."""
    rng = np.random.default_rng(seed)
    point: dict[str, list[float]] = {}
    for df in groups.values():
        for k, v in stat(df).items():
            point.setdefault(k, []).append(v)
    est = {k: float(np.mean(v)) for k, v in point.items()}
    draws: dict[str, list[float]] = {k: [] for k in est}
    for _ in range(n_boot):
        acc: dict[str, list[float]] = {k: [] for k in est}
        for df in groups.values():
            idx = rng.integers(0, len(df), len(df))
            for k, v in stat(df.iloc[idx]).items():
                acc[k].append(v)
        for k in est:
            draws[k].append(float(np.mean(acc[k])))
    se = {k: float(np.std(v, ddof=1)) for k, v in draws.items()}
    return est, se
