"""Streaming ranker: which free agent adds the most value to MY lineup over a date window.

Per-game value = sum of the player's per-game G-scores over the 9 categories (same formula and pool
as `fantasy.model.valuation`, but on per-game lines without availability), minus the per-game value
of a replacement-level player. Window value of a roster = sum over days of the per-game values of the
players who actually start (position-aware lineup, `lineup.LineupSolver`). A candidate's gain = window
value(roster - drop + candidate) - window value(roster). It is schedule- and position-aware but
opponent-agnostic (no category weights for a specific matchup) and ignores the dropped player's
rest-of-season value: check that with `fantasy sim compare` before proposing a drop.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd

from fantasy.inseason.lineup import LineupSolver
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import STATS, ProjectionSet, parse_positions
from fantasy.model.valuation import ValuationParams, _cat_scores, compute_values


def per_game_values(proj: ProjectionSet, settings: LeagueSettings, values: pd.DataFrame | None = None,
                    params: ValuationParams | None = None) -> pd.Series:
    """Per-game G-score total above replacement (higher = better) for every projected player."""
    params = params or ValuationParams()
    if values is None:
        values, _, _ = compute_values(proj, settings, params)
    n_pool = settings.num_teams * settings.num_rounds
    pool = values.index[:n_pool]
    raw = proj.df[STATS].astype(float)
    g = _cat_scores(raw, pool, settings, params.kappa).sum(axis=1)
    band = values.index[n_pool: n_pool + params.replacement_band]
    return g - float(g.loc[band].mean())


def window_value(pids: Sequence[str], positions: dict[str, list[str]], team: dict[str, str],
                 pg_value: pd.Series, out: set[str], slots: Sequence[str], days: Sequence[date],
                 plays: dict[str, set[date]]) -> tuple[float, dict[str, int]]:
    """(sum of started per-game values, starts per player) over `days` with everyone not in `out` healthy."""
    ps = sorted(pids, key=lambda p: -float(pg_value.get(p, -99.0)))
    solver = LineupSolver([positions.get(p, []) for p in ps], slots)
    total, starts = 0.0, dict.fromkeys(ps, 0)
    for d in days:
        mask = 0
        for i, p in enumerate(ps):
            if p not in out and d in plays.get(team.get(p, ""), set()):
                mask |= 1 << i
        st = solver.starters(mask)
        for i, p in enumerate(ps):
            if st >> i & 1:
                starts[p] += 1
                total += float(pg_value.get(p, 0.0))
    return total, starts


def rank_streamers(
    proj: ProjectionSet,
    settings: LeagueSettings,
    my_roster: Sequence[str],
    rostered: set[str],
    days: Sequence[date],
    plays: dict[str, set[date]],
    drop: str | None = None,
    out: set[str] | None = None,
    flags: dict[str, str] | None = None,
    top: int = 15,
    pg_value: pd.Series | None = None,
    candidates: Sequence[str] | None = None,
) -> dict:
    """Rank free agents by window gain for my lineup: `candidates` (e.g. Yahoo's available list) or,
    if None, every projected player not in `rostered`."""
    df = proj.df
    out = set(out or ())
    flags = flags or {}
    pgv = pg_value if pg_value is not None else per_game_values(proj, settings)
    positions = {p: parse_positions(df.loc[p, "positions"]) for p in df.index}
    team = {p: str(df.loc[p, "team"]) for p in df.index}
    slots = settings.starting_slots
    base, base_starts = window_value(my_roster, positions, team, pgv, out, slots, days, plays)
    if drop is None:  # lowest per-game value on my roster
        drop = min(my_roster, key=lambda p: float(pgv.get(p, -99.0)))
    kept = [p for p in my_roster if p != drop]
    kept_val, _ = window_value(kept, positions, team, pgv, out, slots, days, plays)
    rows = []
    pool = [c for c in candidates if c in df.index] if candidates is not None else list(df.index)
    for c in pool:
        if c in rostered or c in out or str(df.loc[c, "status"]) in ("out", "out_for_season"):
            continue
        g = sum(1 for d in days if d in plays.get(team[c], set()))
        if g == 0:
            continue
        v, starts = window_value([*kept, c], positions, team, pgv, out, slots, days, plays)
        rows.append({
            "player_id": c, "name": str(df.loc[c, "name"]), "team": team[c], "positions": "|".join(positions[c]),
            "games": g, "starts": starts.get(c, 0), "per_game_value": round(float(pgv.get(c, np.nan)), 3),
            "gain": round(v - base, 3), "flag": flags.get(c, ""),
        })
    rows.sort(key=lambda r: -float(r["gain"]))
    return {
        "window": [days[0].isoformat(), days[-1].isoformat()] if days else [],
        "drop": {"player_id": drop, "name": str(df.loc[drop, "name"]) if drop in df.index else drop,
                 "per_game_value": round(float(pgv.get(drop, np.nan)), 3), "window_starts": base_starts.get(drop, 0),
                 "window_cost": round(base - kept_val, 3)},
        "my_window_value": round(base, 3),
        "candidates": rows[:top],
    }
