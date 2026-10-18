"""Draft pick order: standard snake and third-round reversal (3RR).

3RR: round 1 goes 1→N, round 2 N→1, round 3 N→1 again (reversed), then alternation resumes
(round 4 1→N, round 5 N→1, ...). For N=12, slot 1 picks 1, 24, 36, 37, 60, 61, 84, 85, ...
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pick:
    overall: int  # 1-indexed
    round: int  # 1-indexed
    slot: int  # 1-indexed draft slot (team) making the pick
    pick_in_round: int  # 1-indexed


def round_is_reversed(rnd: int, order_type: str) -> bool:
    """True if `rnd` runs N→1."""
    if order_type == "snake":
        return rnd % 2 == 0
    if order_type == "snake_3rr":
        if rnd == 1:
            return False
        if rnd == 2:
            return True
        return rnd % 2 == 1  # 3, 5, 7, ... reversed; 4, 6, ... forward
    if order_type == "linear":
        return False
    raise ValueError(f"unknown draft order type: {order_type}")


def draft_order(num_teams: int, num_rounds: int, order_type: str = "snake_3rr") -> list[Pick]:
    picks: list[Pick] = []
    for r in range(1, num_rounds + 1):
        slots = range(num_teams, 0, -1) if round_is_reversed(r, order_type) else range(1, num_teams + 1)
        for i, s in enumerate(slots, start=1):
            picks.append(Pick(overall=len(picks) + 1, round=r, slot=s, pick_in_round=i))
    return picks


def picks_for_slot(slot: int, num_teams: int, num_rounds: int, order_type: str = "snake_3rr") -> list[int]:
    return [p.overall for p in draft_order(num_teams, num_rounds, order_type) if p.slot == slot]


def pick_info(overall: int, num_teams: int, num_rounds: int, order_type: str = "snake_3rr") -> Pick:
    order = draft_order(num_teams, num_rounds, order_type)
    if not 1 <= overall <= len(order):
        raise ValueError(f"pick {overall} out of range 1..{len(order)}")
    return order[overall - 1]
