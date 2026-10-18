"""Roster-slot feasibility via bipartite matching of players to starting slots."""

from __future__ import annotations

from fantasy.league.settings import SLOT_ELIGIBILITY


def max_starting_matching(player_positions: list[list[str]], slots: list[str]) -> tuple[int, list[int]]:
    """Maximum matching of players to starting slots.

    Returns (number of slots filled, assignment) where assignment[slot_index] is the player
    index filling it or -1. Players with no known positions can only fill Util.
    """
    elig: list[list[int]] = []
    for pos in player_positions:
        ps = set(pos)
        row = [j for j, s in enumerate(slots) if (ps & SLOT_ELIGIBILITY[s]) or (not ps and s == "Util")]
        elig.append(row)
    match_slot = [-1] * len(slots)

    def try_assign(i: int, seen: set[int]) -> bool:
        for j in elig[i]:
            if j in seen:
                continue
            seen.add(j)
            if match_slot[j] == -1 or try_assign(match_slot[j], seen):
                match_slot[j] = i
                return True
        return False

    # assign least-flexible players first for a stable, readable assignment
    for i in sorted(range(len(player_positions)), key=lambda k: len(elig[k])):
        try_assign(i, set())
    return sum(1 for m in match_slot if m >= 0), match_slot


def unfilled_slots(player_positions: list[list[str]], slots: list[str]) -> list[str]:
    _, assign = max_starting_matching(player_positions, slots)
    return [s for s, m in zip(slots, assign, strict=True) if m < 0]
