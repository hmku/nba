"""Season simulator with position-aware daily lineups.

`fantasy.sim.engine.SeasonSimulator` counts each day's games for the first `active` (=10) healthy
players by projected value, ignoring slot eligibility. This subclass keeps everything else identical
(same seeds, same player draws -> paired comparisons stay valid) and, after the health draws, removes
the games of players who could not be slotted that day (priority-greedy matching, `lineup.LineupSolver`).
The engine's count cap then never binds, and its streaming term fills the remaining empty slots
(streamers are assumed to have a fitting position: optimistic for a slot that only a C could fill).
"""

from __future__ import annotations

import numpy as np

from fantasy.inseason.lineup import LineupSolver, bool_from_masks, masks_from_bool
from fantasy.model.lines import parse_positions
from fantasy.sim.engine import SeasonSimulator, SimResult
from fantasy.sim.league import LeagueState


class PositionAwareSimulator(SeasonSimulator):
    _league: LeagueState | None = None

    def run(self, league: LeagueState) -> SimResult:
        self._league = league
        try:
            return super().run(league)
        finally:
            self._league = None

    def _positions(self, pid: str) -> list[str]:
        if pid in self.proj.index and "positions" in self.proj.columns:
            return parse_positions(self.proj.loc[pid, "positions"])
        return []

    def _player_arrays(self, pids: list[str]) -> dict[str, np.ndarray]:
        arr = super()._player_arrays(pids)
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
