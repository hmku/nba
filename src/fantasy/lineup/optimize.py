"""Exact daily lineup assignment (position-aware).

Players are rows; columns are the starting slots (PG, SG, G, SF, PF, F, C, C, Util, Util), the bench slots and the
IL slots. Score(player, starting slot) = the player's value that day if he is eligible for the slot (and startable),
score(player, BN) = 0, score(player, IL) = 0 if IL-eligible. Every player must get a column; ineligible pairs are
forbidden. Maximising the total score is a rectangular linear assignment problem, solved exactly by
scipy.optimize.linear_sum_assignment (Hungarian / Jonker-Volgenant).

Tie-breaks (deterministic, far below any value difference that matters): +KEEP_BONUS for keeping a player's current
slot type (so equivalent lineups do not generate needless moves for the browser executor) and +IL_BONUS for an
IL-eligible player in an IL slot (frees a bench spot).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from fantasy.inseason.lineup import slot_eligibility

KEEP_BONUS = 1e-7
IL_BONUS = 1e-8
_FORBID = -1e9


@dataclass(frozen=True)
class DaySlots:
    starting: tuple[str, ...]  # e.g. ("PG","SG","G","SF","PF","F","C","C","Util","Util")
    n_bench: int = 3
    n_il: int = 3

    @property
    def columns(self) -> list[str]:
        return [*self.starting, *(["BN"] * self.n_bench), *(["IL"] * self.n_il)]


def eligible(positions: Sequence[str], slot: str) -> bool:
    """Shared rule with fantasy.inseason.lineup (a player without known positions may only fill Util)."""
    return bool(slot_eligibility(positions, [slot]))


def score_matrix(
    positions: Sequence[Sequence[str]], values: np.ndarray, startable: Sequence[bool], il_ok: Sequence[bool],
    slots: DaySlots, current: Sequence[str | None] | None = None, tie_breaks: bool = True,
) -> np.ndarray:
    cols = slots.columns
    n = len(positions)
    M = np.full((n, len(cols)), _FORBID)
    for i in range(n):
        for j, s in enumerate(cols):
            if s == "BN":
                M[i, j] = 0.0
            elif s == "IL":
                if il_ok[i]:
                    M[i, j] = IL_BONUS if tie_breaks else 0.0
            elif startable[i] and eligible(positions[i], s):
                M[i, j] = float(values[i])
            if tie_breaks and M[i, j] > _FORBID / 2 and current is not None and current[i] == s:
                M[i, j] += KEEP_BONUS
    return M


def solve_day(
    positions: Sequence[Sequence[str]], values: np.ndarray, startable: Sequence[bool], il_ok: Sequence[bool],
    slots: DaySlots, current: Sequence[str | None] | None = None,
) -> list[str]:
    """Slot label for every player (PG..Util, BN or IL) maximising the sum of values of started players."""
    n = len(positions)
    cols = slots.columns
    if n > len(cols):
        raise ValueError(f"{n} players but only {len(cols)} roster slots")
    M = score_matrix(positions, values, startable, il_ok, slots, current)
    rows, cidx = linear_sum_assignment(M, maximize=True)
    if (M[rows, cidx] <= _FORBID / 2).any():
        raise ValueError("no feasible lineup (too many players for bench + IL?)")
    out = [""] * n
    for r, c in zip(rows, cidx, strict=True):
        out[r] = cols[c]
    return out


def lineup_value(labels: Sequence[str], values: np.ndarray, slots: DaySlots) -> float:
    start = set(slots.starting)
    return float(sum(v for lab, v in zip(labels, values, strict=True) if lab in start))
