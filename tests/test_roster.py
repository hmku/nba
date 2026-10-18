from fantasy.draft.roster import max_starting_matching, unfilled_slots
from fantasy.league.settings import LeagueSettings

SLOTS = LeagueSettings().starting_slots


def test_full_lineup():
    team = [["PG"], ["SG"], ["PG", "SG"], ["SF"], ["PF"], ["SF", "PF"], ["C"], ["C"], ["PG"], ["C"]]
    filled, _ = max_starting_matching(team, SLOTS)
    assert filled == 10


def test_missing_center():
    team = [["PG"], ["SG"], ["SF"], ["PF"]]
    assert sorted(unfilled_slots(team, SLOTS)) == ["C", "C", "F", "G", "Util", "Util"]


def test_unknown_positions_util_only():
    filled, assign = max_starting_matching([[], []], SLOTS)
    assert filled == 2
    assert all(SLOTS[j] == "Util" for j, m in enumerate(assign) if m >= 0)
