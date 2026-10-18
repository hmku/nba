from collections import Counter

import pytest

from fantasy.draft.order import draft_order, pick_info, picks_for_slot, round_is_reversed


def test_3rr_slot1_matches_league_expectation():
    assert picks_for_slot(1, 12, 13, "snake_3rr")[:8] == [1, 24, 36, 37, 60, 61, 84, 85]


def test_3rr_slot12():
    # slot 12: R1 pick 12, R2 pick 13, R3 pick 25 (round 3 runs 12→1), R4 pick 48
    assert picks_for_slot(12, 12, 13, "snake_3rr")[:4] == [12, 13, 25, 48]


def test_plain_snake():
    assert picks_for_slot(1, 12, 4, "snake") == [1, 24, 25, 48]


def test_round_direction():
    assert [round_is_reversed(r, "snake_3rr") for r in range(1, 7)] == [False, True, True, False, True, False]


@pytest.mark.parametrize("order_type", ["snake", "snake_3rr"])
def test_every_team_picks_once_per_round(order_type):
    order = draft_order(12, 13, order_type)
    assert len(order) == 156
    assert [p.overall for p in order] == list(range(1, 157))
    for r in range(1, 14):
        assert sorted(p.slot for p in order if p.round == r) == list(range(1, 13))
    assert set(Counter(p.slot for p in order).values()) == {13}


def test_pick_info():
    p = pick_info(36, 12, 13)
    assert (p.round, p.slot, p.pick_in_round) == (3, 1, 12)
    with pytest.raises(ValueError):
        pick_info(157, 12, 13)


def test_custom_order_matches_formula_and_validates():
    formula = draft_order(12, 13, "snake_3rr")
    rounds = [[p.slot for p in formula if p.round == r] for r in range(1, 14)]
    custom = draft_order(12, 13, "custom", rounds)
    assert [p.slot for p in custom] == [p.slot for p in formula]
    # commissioner flipped only round 3: slot 1 then picks 1, 24, 36, 48, 49, ...
    only_r3 = [[p.slot for p in draft_order(12, 13, "snake") if p.round == r] for r in range(1, 14)]
    only_r3[2] = list(range(12, 0, -1))
    assert picks_for_slot(1, 12, 13, "custom", only_r3)[:5] == [1, 24, 36, 48, 49]
    with pytest.raises(ValueError):
        draft_order(12, 13, "custom", rounds[:-1])
