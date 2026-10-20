"""Season simulator with position-aware daily lineups (and mid-season roster changes).

`fantasy.sim.engine.SeasonSimulator` counts each day's games for the first `active` (=10) healthy
players by projected value, ignoring slot eligibility. This subclass keeps everything else identical
(same seeds, same player draws -> paired comparisons stay valid) and, after the health draws, removes
the games of players who could not be slotted that day (priority-greedy matching, `lineup.LineupSolver`).
The engine's count cap then never binds, and its streaming term fills the remaining empty slots
(streamers are assumed to have a fitting position: optimistic for a slot that only a C could fill).

Mid-season changes (`windows`): {player_id: (first_day | None, end_day | None)} restricts a rostered player to
the days first_day <= d < end_day. An add/drop effective on day e is simulated as one roster holding both players,
the dropped one with window (None, e) and the added one with (e, None); only one of them is ever available, so
the lineup is the 13-man lineup before and after e. Their injury-spell intervals (IL replacement) are clipped to
the same window. No random numbers are added or removed, so runs stay paired with the unchanged league.

Replacement level (`replacement_id(tag)`): an id that is not in the projections is simulated by the engine at the
replacement line (`ctx.replacement`, gp 60, an every-other-day schedule). Ids made by `replacement_id` are also
eligible for every position here (a replacement-level free agent at whatever position is needed); other unknown ids
stay Util-only.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from fantasy.inseason.lineup import LineupSolver, bool_from_masks, masks_from_bool
from fantasy.model.lines import parse_positions
from fantasy.sim.engine import SeasonSimulator, SimParams, SimResult
from fantasy.sim.league import LeagueState

if TYPE_CHECKING:
    from fantasy.sim.context import SimContext

Window = tuple[date | None, date | None]
REPLACEMENT_PREFIX = "__replacement_level__"
ALL_POSITIONS = ["PG", "SG", "SF", "PF", "C"]


def replacement_id(tag: str | int = 0) -> str:
    """A replacement-level free agent (not in the projections; distinct tags give distinct players)."""
    return f"{REPLACEMENT_PREFIX}{tag}"


def is_replacement(pid: str) -> bool:
    return pid.startswith(REPLACEMENT_PREFIX)


def make_sim(ctx: SimContext, n_sims: int, seed: int, proj_df: pd.DataFrame | None = None,
             **kw: Any) -> PositionAwareSimulator:
    """Position-aware simulator from a sim context (optionally with a modified projections frame)."""
    return PositionAwareSimulator(ctx.proj.df if proj_df is None else proj_df, ctx.values, ctx.replacement,
                                  ctx.settings, ctx.nba, ctx.calendar, ctx.matchups,
                                  SimParams(n_sims=n_sims, seed=seed, **kw))


class PositionAwareSimulator(SeasonSimulator):
    _league: LeagueState | None = None
    _windows: dict[str, Window] | None = None

    def run(self, league: LeagueState, windows: dict[str, Window] | None = None) -> SimResult:
        self._league, self._windows = league, windows or None
        try:
            return super().run(league)
        finally:
            self._league, self._windows = None, None

    def _positions(self, pid: str) -> list[str]:
        if pid in self.proj.index and "positions" in self.proj.columns:
            return parse_positions(self.proj.loc[pid, "positions"])
        return list(ALL_POSITIONS) if is_replacement(pid) else []

    def _apply_windows(self, arr: dict[str, np.ndarray], pids: list[str]) -> None:
        pidx = {p: i for i, p in enumerate(pids)}
        days = np.array([(d - self.season_start).days for d in self.fdays])
        for pid, (a, b) in (self._windows or {}).items():
            j = pidx.get(pid)
            if j is None:
                continue
            lo = (a - self.season_start).days if a else -10**6
            hi = (b - self.season_start).days if b else 10**6
            arr["health"][:, j, :] &= ((days >= lo) & (days < hi))[None, :]
            if "il_start" in arr:
                s = np.clip(arr["il_start"][:, j], lo, hi)
                e = np.clip(arr["il_end"][:, j], lo, hi)
                arr["il_start"][:, j], arr["il_end"][:, j] = s, np.maximum(e, s)

    def _player_arrays(self, pids: list[str]) -> dict[str, np.ndarray]:
        arr = super()._player_arrays(pids)
        if self._windows:
            self._apply_windows(arr, pids)
        league = self._league
        if league is None:
            return arr
        health = arr["health"]  # (N, P+1, D)
        pidx = {p: i for i, p in enumerate(pids)}
        val = self.values["value"] if "value" in self.values else None
        slots = self.settings.starting_slots
        for t in league.team_ids:
            ps = [p for p in dict.fromkeys(league.rosters[t]) if p in pidx]
            if val is not None:  # same priority order as the engine's lineup
                ps.sort(key=lambda p: -float(val.get(p, -99.0)))
            if not ps:
                continue
            js = [pidx[p] for p in ps]
            solver = LineupSolver([self._positions(p) for p in ps], slots)
            H = health[:, js, :]  # (N, R, D)
            st = bool_from_masks(solver.starters_array(masks_from_bool(H, axis=1)), len(js), axis=1)
            health[:, js, :] = H & st
        return arr
