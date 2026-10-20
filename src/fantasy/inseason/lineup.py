"""Position-aware daily lineups.

Each day the manager starts at most one player per starting slot (PG, SG, G, SF, PF, F, C, C, Util,
Util), and only players whose NBA team plays that day. Which players start is a bipartite matching
of players to slots. We take the *priority-greedy* maximum matching: players in priority order
(projected value, best first) are added if the matching can still be extended (augmenting path).
Augmenting never unmatches a player, and the sets of players that can be matched simultaneously form
a transversal matroid, so this greedy gives the maximum-cardinality matching that also maximises
total priority (lexicographically). It is the lineup an attentive manager sets.

"Lost to positions" = min(players with a game, slots) - starters: games a position-blind count
(what `fantasy sim` assumes) would credit but the slot rules forbid.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from fantasy.league.settings import SLOT_ELIGIBILITY


def slot_eligibility(positions: Sequence[str], slots: Sequence[str]) -> list[int]:
    """Indices of `slots` a player with `positions` may fill (no positions -> Util only)."""
    ps = set(positions)
    return [j for j, s in enumerate(slots) if (ps & SLOT_ELIGIBILITY[s]) or (not ps and s == "Util")]


class LineupSolver:
    """Priority-greedy max matching of available players (bit mask) to starting slots, memoised.

    `positions[i]` belongs to the player with priority i (0 = start first)."""

    def __init__(self, positions: Sequence[Sequence[str]], slots: Sequence[str]) -> None:
        if len(positions) > 62:
            raise ValueError("at most 62 players per roster")
        self.slots = list(slots)
        self.elig = [slot_eligibility(p, self.slots) for p in positions]
        self._cache: dict[int, int] = {}

    def starters(self, available: int) -> int:
        """Bit mask of starters given the bit mask of players with a game (and healthy)."""
        hit = self._cache.get(available)
        if hit is not None:
            return hit
        match_slot = [-1] * len(self.slots)

        def augment(i: int, seen: set[int]) -> bool:
            for j in self.elig[i]:
                if j in seen:
                    continue
                seen.add(j)
                if match_slot[j] == -1 or augment(match_slot[j], seen):
                    match_slot[j] = i
                    return True
            return False

        chosen = 0
        for i in range(len(self.elig)):
            if available >> i & 1 and augment(i, set()):
                chosen |= 1 << i
        self._cache[available] = chosen
        return chosen

    def assignment(self, available: int) -> dict[str, int]:
        """'SLOT#k' -> player index for the starters chosen by `starters` (a readable lineup)."""
        chosen = self.starters(available)
        match_slot = [-1] * len(self.slots)

        def augment(i: int, seen: set[int]) -> bool:
            for j in self.elig[i]:
                if j in seen:
                    continue
                seen.add(j)
                if match_slot[j] == -1 or augment(match_slot[j], seen):
                    match_slot[j] = i
                    return True
            return False

        for i in range(len(self.elig)):
            if chosen >> i & 1:
                augment(i, set())
        return {f"{self.slots[j]}#{j}": i for j, i in enumerate(match_slot) if i >= 0}

    def starters_array(self, masks: np.ndarray) -> np.ndarray:
        """Vectorised `starters` over an integer array of masks (any shape)."""
        flat = np.asarray(masks, dtype=np.int64).ravel()
        uniq, inv = np.unique(flat, return_inverse=True)
        st = np.array([self.starters(int(m)) for m in uniq], dtype=np.int64)
        return st[inv].reshape(np.shape(masks))


def masks_from_bool(avail: np.ndarray, axis: int) -> np.ndarray:
    """Collapse a boolean player axis (<= 62 players) into an int64 bit mask (bit i = player i)."""
    a = np.moveaxis(np.asarray(avail, dtype=bool), axis, -1)
    w = np.left_shift(np.int64(1), np.arange(a.shape[-1], dtype=np.int64))
    return (a.astype(np.int64) * w).sum(axis=-1)


def bool_from_masks(masks: np.ndarray, n: int, axis: int) -> np.ndarray:
    """Inverse of `masks_from_bool`: int masks -> boolean array with the player axis at `axis`."""
    bits = (np.asarray(masks, dtype=np.int64)[..., None] >> np.arange(n, dtype=np.int64)) & 1
    return np.moveaxis(bits.astype(bool), -1, axis)


@dataclass
class PlayerSpec:
    player_id: str
    name: str
    team: str  # NBA team code as in the schedule
    positions: list[str]
    priority: float  # higher starts first (projected value)
    p_play: float = 1.0  # probability of playing a scheduled game (iid; 0 = out)


@dataclass
class LineupResult:
    days: list[date]
    players: list[PlayerSpec]  # in priority order (best first)
    has_game: np.ndarray  # (P, D) bool: team plays that day
    exp_available: np.ndarray  # (P, D) expected availability (has_game * p_play, or MC mean)
    exp_starts: np.ndarray  # (P, D) expected starts
    exp_blind: np.ndarray  # (D,) expected min(available players, slots)
    n_slots: int
    n_sims: int
    notes: list[str] = field(default_factory=list)

    def summary(self, start: date | None = None, end: date | None = None) -> dict:
        sel = [i for i, d in enumerate(self.days) if (start is None or d >= start) and (end is None or d <= end)]
        avail = float(self.exp_available[:, sel].sum())
        starts = float(self.exp_starts[:, sel].sum())
        blind = float(self.exp_blind[sel].sum())
        return {
            "start": self.days[sel[0]].isoformat() if sel else None,
            "end": self.days[sel[-1]].isoformat() if sel else None,
            "days": len(sel),
            "player_games": round(avail, 2),
            "starts": round(starts, 2),
            "starts_position_blind": round(blind, 2),
            "lost_to_positions": round(blind - starts, 2),
            "lost_to_slot_count": round(avail - blind, 2),
            "benched_total": round(avail - starts, 2),
        }

    def per_player(self, start: date | None = None, end: date | None = None) -> list[dict]:
        sel = [i for i, d in enumerate(self.days) if (start is None or d >= start) and (end is None or d <= end)]
        out = []
        for i, p in enumerate(self.players):
            out.append({
                "player_id": p.player_id, "name": p.name, "team": p.team, "positions": "|".join(p.positions),
                "games": int(self.has_game[i, sel].sum()),
                "exp_available": round(float(self.exp_available[i, sel].sum()), 2),
                "exp_starts": round(float(self.exp_starts[i, sel].sum()), 2),
            })
        return out

    def per_day(self) -> list[dict]:
        rows = []
        for k, d in enumerate(self.days):
            rows.append({
                "date": d.isoformat(),
                "with_game": int(self.has_game[:, k].sum()),
                "exp_available": round(float(self.exp_available[:, k].sum()), 2),
                "exp_starts": round(float(self.exp_starts[:, k].sum()), 2),
                "exp_lost_to_positions": round(float(self.exp_blind[k] - self.exp_starts[:, k].sum()), 2),
            })
        return rows


def team_days(games: Iterable[tuple[date, str, str]], days: Sequence[date]) -> dict[str, set[date]]:
    """NBA team -> the days in `days` it plays."""
    want = set(days)
    out: dict[str, set[date]] = {}
    for d, v, h in games:
        if d in want:
            out.setdefault(v, set()).add(d)
            out.setdefault(h, set()).add(d)
    return out


def simulate_lineups(
    players: Sequence[PlayerSpec],
    slots: Sequence[str],
    days: Sequence[date],
    plays: dict[str, set[date]],
    n_sims: int = 0,
    seed: int = 0,
) -> LineupResult:
    """Daily lineups over `days`. n_sims=0: deterministic, a player is available on each game day with
    weight p_play treated as 0/1 (p_play < 0.5 = out). n_sims>0: Monte Carlo, each scheduled game played
    independently with probability p_play (a prior; real absences come in spells)."""
    ps = sorted(players, key=lambda p: -p.priority)
    P, D = len(ps), len(days)
    solver = LineupSolver([p.positions for p in ps], slots)
    S = len(slots)
    has = np.array([[d in plays.get(p.team, set()) for d in days] for p in ps], dtype=bool).reshape(P, D)
    pp = np.array([p.p_play for p in ps], dtype=float)
    notes: list[str] = []
    if n_sims <= 0:
        av = has & (pp >= 0.5)[:, None]
        st = bool_from_masks(solver.starters_array(masks_from_bool(av, axis=0)), P, axis=0)
        blind = np.minimum(av.sum(axis=0), S).astype(float)
        return LineupResult(list(days), ps, has, av.astype(float), st.astype(float), blind, S, 0, notes)
    rng = np.random.default_rng(seed)
    av = has[None] & (rng.random((n_sims, P, D)) < pp[None, :, None])  # (N, P, D)
    st = bool_from_masks(solver.starters_array(masks_from_bool(av, axis=1)), P, axis=1)
    blind = np.minimum(av.sum(axis=1), S).mean(axis=0)
    notes.append("Monte Carlo availability: each game played iid with p_play (prior; ignores injury spells).")
    return LineupResult(list(days), ps, has, av.mean(axis=0), st.mean(axis=0), blind, S, n_sims, notes)
