"""Historical player timelines for the in-season (rest-of-season) update. See docs/research/inseason_updates.md.

Inputs (not committed, see README.md in this folder):
  box   player box scores, one row per (game, team, player listed): NocturneBear/NBA-Data-2010-2024 for
        2010-11..2023-24, the repo's ESPN ingester (`fantasy data fetch-boxscores`) for 2024-25 onward
  bbref Player Totals.csv / Team Summaries.csv (sumitrodatta mirror) for the preseason games prior (gp-ols panel)

Box scores list players who dressed (plus some inactive ones); long-injured players are mostly absent. So a player's
availability is read off his TEAM's schedule: his timeline is the ordered list of his team's games while he was on the
roster, and every timeline game without minutes is a missed game. Rules: fantasy.model.inseason.player_timeline
(start at game 1 of his first team if he was on it in T-1 or is listed in its first 5 games, else at his first
listing; a trade switches at his first listing for the new team; the last stint runs to season end, so a waived
player keeps missing games; no listing at all = a lost season on his last T-1 team).
Season labels follow bbref (2024 == 2023-24).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "gp_model"))

from panel import build_panel, player_seasons  # noqa: E402

from fantasy.data.ids import canonical_id  # noqa: E402
from fantasy.model.availability import (  # noqa: E402
    TEAM_ALIASES,
    gp_sd_from_model,
    is_low_minutes,
    predict_gp,
)
from fantasy.model.inseason import player_timeline  # noqa: E402

STATS = ["min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"]
TEAM_MAP = {**TEAM_ALIASES, "NJN": "BRK", "NOH": "NOP", "NOK": "NOP", "SEA": "OKC", "CHH": "CHO"}


def team_code(t: str) -> str:
    t = str(t).upper()
    return TEAM_MAP.get(t, t)


def _minutes(v: object) -> float:
    if isinstance(v, str) and ":" in v:
        m, s = v.split(":")[:2]
        return float(m) + float(s) / 60.0
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if np.isnan(x) else x


def load_nocturne(paths: list[Path]) -> pd.DataFrame:
    """NocturneBear regular-season box scores -> tidy schema (season = end year)."""
    df = pd.concat([pd.read_csv(p, low_memory=False) for p in paths], ignore_index=True)
    out = pd.DataFrame({
        "season": df["season_year"].str[:4].astype(int) + 1,
        "game_id": df["gameId"].astype(str),
        "game_date": df["game_date"].astype(str),
        "team": df["teamTricode"].map(team_code),
        "name": df["personName"],
        "min": df["minutes"].map(_minutes),
        "pts": df["points"], "fgm": df["fieldGoalsMade"], "fga": df["fieldGoalsAttempted"],
        "fg3m": df["threePointersMade"], "fg3a": df["threePointersAttempted"],
        "ftm": df["freeThrowsMade"], "fta": df["freeThrowsAttempted"], "reb": df["reboundsTotal"],
        "ast": df["assists"], "stl": df["steals"], "blk": df["blocks"], "tov": df["turnovers"],
    })
    out["player_id"] = out["name"].map(canonical_id)
    out["played"] = out["min"] > 0
    return out


def load_espn(paths: list[Path]) -> pd.DataFrame:
    """Repo ESPN ingester output (data/raw/espn_boxscores/player_games_<season>.csv.gz) -> tidy schema."""
    df = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    df = df[df["regular_season"].astype(bool)]  # drop the NBA Cup final (does not count in the standings)
    out = df.rename(columns={"player_name": "name"})[
        ["season", "game_id", "game_date", "team", "name", "player_id", *STATS]].copy()
    out["game_id"] = out["game_id"].astype(str)
    out["team"] = out["team"].map(team_code)
    out[STATS] = out[STATS].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    out["played"] = out["min"] > 0
    return out


def team_schedules(box: pd.DataFrame) -> dict[tuple[int, str], pd.DataFrame]:
    """(season, team) -> that team's games in date order (game_id, game_date, idx 1..n)."""
    g = box[["season", "team", "game_id", "game_date"]].drop_duplicates(["season", "team", "game_id"])
    out = {}
    for key, d in g.sort_values(["game_date", "game_id"]).groupby(["season", "team"], sort=False):
        d = d.reset_index(drop=True)
        d["idx"] = np.arange(1, len(d) + 1)
        out[key] = d
    return out


def priors(bbref_totals: pd.DataFrame, bbref_teams: pd.DataFrame, seasons: list[int]) -> pd.DataFrame:
    """Preseason games prior per player-season from the production gp model (gp-ols, availability.predict_gp).

    Coefficients are the production fit (all targets through 2026): the prior for a test season is in-sample for
    the gp model itself (~15 parameters on ~10k rows), which flatters the static-prior baseline, not the update.
    """
    ps = player_seasons(bbref_totals, bbref_teams)
    panel = build_panel(ps, first_target=min(seasons))
    panel = panel[panel["T"].isin(seasons)].copy()
    gp, sd = [], []
    for r in panel.itertuples():
        lags = [r.g1, r.g2, r.g3]
        mins = [r.mp1, r.mp2, r.mp3]
        lags = [None if pd.isna(v) else float(v) for v in lags]
        mins = [None if pd.isna(v) else float(v) for v in mins]
        g = predict_gp(lags, float(r.age), mins)
        gp.append(g)
        sd.append(gp_sd_from_model(g, low_minutes=is_low_minutes(mins)))
    panel["gp_prior"], panel["gp_sd_prior"] = gp, sd
    t = bbref_totals[(bbref_totals["lg"] == "NBA") & ~bbref_totals["team"].isin(["TOT", "2TM", "3TM", "4TM", "5TM"])]
    prev = t.groupby(["player_id", "season"])["team"].agg(lambda s: {team_code(x) for x in s})
    last = t.groupby(["player_id", "season"])["team"].last().map(team_code)
    panel["prev_teams"] = [prev.get((p, T - 1), set()) for p, T in zip(panel["player_id"], panel["T"], strict=True)]
    panel["prev_last_team"] = [last.get((p, T - 1)) or last.get((p, T - 2))
                               for p, T in zip(panel["player_id"], panel["T"], strict=True)]
    panel["slug"] = panel["player"].map(canonical_id)
    return panel.rename(columns={"T": "season"})


def timelines(box: pd.DataFrame, pri: pd.DataFrame) -> pd.DataFrame:
    """One row per (player-season, timeline game): season, slug, t (1..n), team, game_id, played, stats.

    The timeline rules are production's (fantasy.model.inseason.player_timeline) with start_team = his first listed
    team when he was on it in T-1, else his last T-1 team. Only player-seasons with a prior (an NBA season in T-1,
    or a lost-season row) are built. Slug collisions within a season (two players with the same name) are dropped.
    """
    sched = {k: d[["game_id", "game_date"]] for k, d in team_schedules(box).items()}
    rows = box.drop_duplicates(["season", "game_id", "player_id"])
    by_ps = {k: d for k, d in rows.groupby(["season", "player_id"], sort=False)}
    dup = pri.groupby(["season", "slug"]).size()
    out = []
    for r in pri.itertuples():
        if dup[(r.season, r.slug)] > 1:
            continue
        mine = by_ps.get((r.season, r.slug))
        first = None if mine is None else mine.sort_values("game_date")["team"].iloc[0]
        start = first if first in r.prev_teams else r.prev_last_team
        teams = {start} | (set() if mine is None else set(mine["team"]))
        season_sched = {t: sched[(r.season, t)] for t in teams if (r.season, t) in sched}
        tl = player_timeline(None if mine is None else mine[["game_id", "game_date", "team", "played"]],
                             season_sched, start)
        if tl.empty:
            continue
        tl["t"] = np.arange(1, len(tl) + 1)
        if mine is not None:
            tl = tl.merge(mine[["game_id", *STATS]], on="game_id", how="left")
        else:
            for c in STATS:
                tl[c] = 0.0
        tl[STATS] = tl[STATS].fillna(0.0).mul(tl["played"].astype(float), axis=0)
        tl.insert(0, "slug", r.slug)
        tl.insert(0, "season", r.season)
        out.append(tl)
    return pd.concat(out, ignore_index=True)
