"""Opportunity detector (opportunity-0.1.0): offline, synthetic."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from fantasy.inseason.opportunity import (
    Absence,
    OpportunityParams,
    absence_window,
    allocate_vacated,
    detect,
    expected_absence_games,
    override_frames,
    position_groups,
    season_mpg,
)
from fantasy.inseason.opportunity_run import find_absences


def _players(rows):
    df = pd.DataFrame(rows, columns=["player_id", "team", "positions", "mpg", "mpg_next5", "mpg_pre", "out"])
    return df.set_index("player_id")


def _team(n=14, team="AAA", star=True):
    rows = []
    if star:
        rows.append(("star", team, "PG|SG", 34.0, 34.0, 34.0, True))
    for i in range(n):
        mpg = 32.0 - 2.2 * i
        pos = ["PG", "SG", "SF", "PF", "C"][i % 5]
        rows.append((f"p{i}", team, pos, mpg, mpg, mpg, False))
    return _players(rows)


def test_position_groups():
    assert position_groups("PG|SG") == {"G"}
    assert position_groups(["SF", "PF"]) == {"W", "B"}
    assert position_groups(None) == frozenset()


@pytest.mark.parametrize("n", [3, 8, 14, 20])
def test_allocation_sums_to_at_most_the_share(n):
    p = OpportunityParams()
    mates = [(f"p{i}", 30.0 - i, position_groups(["PG", "SF", "C"][i % 3])) for i in range(n)]
    ex = allocate_vacated(34.0, frozenset({"G"}), mates, p)
    assert set(ex) == {m[0] for m in mates}
    assert all(v >= 0 for v in ex.values())
    assert sum(ex.values()) <= p.share * 34.0 + 1e-9
    # the top-3 depth bucket gets nothing (fitted weight floored at 0); deep bench gets the most
    order = sorted(mates, key=lambda m: -m[1])
    assert all(ex[m[0]] == 0.0 for m in order[:3])


def test_same_position_gets_more_than_other_at_same_depth():
    p = OpportunityParams()
    mates = [(f"p{i}", 30.0 - i, frozenset({"B"})) for i in range(14)]
    mates[9] = ("guard", mates[9][1], frozenset({"G"}))
    ex = allocate_vacated(34.0, frozenset({"G"}), mates, p)
    assert ex["guard"] > ex["p10"]  # same bucket (9-11), same group wins


def test_no_signal_no_candidate():
    df = _team(star=False)
    assert detect(df, [], OpportunityParams()) == []


def test_jump_candidate_uses_mpg_next5():
    df = _team(star=False)
    df.loc["p12", ["mpg_next5", "mpg_pre"]] = [18.0, 8.0]  # +10 over preseason
    out = detect(df, [], OpportunityParams())
    assert [c["player_id"] for c in out] == ["p12"]
    c = out[0]
    assert c["signal"] == "jump" and c["opp_mpg"] == pytest.approx(18.0)
    assert c["window_games"] == OpportunityParams().jump_window


def test_vacated_candidate_and_observed_absence():
    p = OpportunityParams()
    df = _team()
    a = Absence("star", "Star", "AAA", 34.0, missed=0)
    out = {c["player_id"]: c for c in detect(df, [a], p)}
    assert out, "a 34-mpg absence must create candidates"
    for c in out.values():
        assert c["signal"] == "vacated"
        assert c["opp_mpg"] == pytest.approx(min(p.mpg_cap, c["mpg_next5"] + c["vacated_unobserved_mpg"]), abs=0.02)
        assert c["window_games"] == pytest.approx(expected_absence_games(0, p), abs=0.01)
    assert sum(c["vacated_mpg"] for c in out.values()) <= p.share * 34.0 + 1e-6
    # after 3 missed games the absence is observed (model A saw the games he played; a healthy DNP means no minutes):
    # no vacated signal without a jump, whether they played or not
    a3 = Absence("star", "Star", "AAA", 34.0, missed=3)
    for flag in (True, False):
        played = {pid: [flag] * 4 for pid in df.index}
        assert detect(df, [a3], p, recent_played=played) == []
    # ... but an observed jump during it is a candidate ("jump (absence)"), on model A's minutes
    df.loc["p12", "mpg_next5"] = df.loc["p12", "mpg_pre"] + 6.0
    out = detect(df, [a3], p, recent_played={pid: [True] * 4 for pid in df.index})
    assert [c["signal"] for c in out] == ["jump (absence)"]
    assert out[0]["opp_mpg"] == pytest.approx(out[0]["mpg_next5"])
    assert out[0]["window_games"] == pytest.approx(expected_absence_games(3, p), abs=0.01)


def test_news_only_absence_is_discounted():
    p = OpportunityParams()
    df = _team()
    full = detect(df, [Absence("star", "Star", "AAA", 34.0)], p)
    half = detect(df, [Absence("star", "Star", "AAA", 34.0, p_out=p.p_out_news)], p)
    tot = lambda cs: sum(c["vacated_mpg"] for c in cs)  # noqa: E731
    assert tot(half) < tot(full)


def test_absence_window_sources():
    p = OpportunityParams()
    assert absence_window(Absence("s", "S", "AAA", 30.0, season_out=True), p)[0] == float(p.horizon)
    # ESPN estimate vs fitted survival: the shorter (more conservative) one wins, both are named
    fitted0 = expected_absence_games(0, p)
    g, src = absence_window(Absence("s", "S", "AAA", 30.0, return_date="2026-11-01", espn_games=12), p)
    assert g == pytest.approx(fitted0) and "ESPN" in src and "fitted" in src
    g, src = absence_window(Absence("s", "S", "AAA", 30.0, return_date="2026-10-25", espn_games=1), p)
    assert g == 1.0 and src.startswith("ESPN")
    e = [expected_absence_games(m, p) for m in range(6)]
    assert e[0] >= 1.0 and all(b >= a for a, b in zip(e, e[1:], strict=False))  # spells that last tend to last
    assert all(x <= p.horizon for x in e)


def _proj_rows():
    rows = []
    for i, pid in enumerate(["a", "b", "c"]):
        rows.append({"player_id": pid, "name": pid.upper(), "team": "AAA", "positions": "PG", "gp": 70.0, "mpg": 20.0,
                     "fgm": 4.0, "fga": 9.0, "ftm": 2.0, "fta": 2.5, "fg3m": 1.0, "pts": 11.0, "reb": 3.0, "ast": 2.0,
                     "stl": 0.6, "blk": 0.3, "tov": 1.1, "ros_games": 60.0, "mpg_next5": 20.0, "status": "healthy",
                     "x_other": i})
    return pd.DataFrame(rows).set_index("player_id")


def test_override_only_changes_intended_rows():
    df = _proj_rows()
    cand = {"player_id": "b", "opp_mpg": 30.0, "window_games": 10.0}
    win, seas = override_frames(df, [cand])
    for frame in (win, seas):
        pd.testing.assert_frame_equal(frame.drop(index="b"), df.drop(index="b"))
        assert list(frame.columns) == list(df.columns)
    assert win.at["b", "mpg"] == pytest.approx(30.0)
    assert win.at["b", "pts"] == pytest.approx(11.0 * 1.5)
    assert win.at["b", "fgm"] / win.at["b", "fga"] == pytest.approx(4.0 / 9.0)  # percentages unchanged
    m = season_mpg(df.loc["b"], 30.0, 10.0)
    assert 20.0 < m < 30.0
    assert m == pytest.approx((10 * 70 / 82 * 30 + (60 - 10 * 70 / 82) * 20) / 60)
    assert seas.at["b", "mpg"] == pytest.approx(m, abs=1e-3)
    assert seas.at["b", "mpg_next5"] == pytest.approx(30.0)
    assert win.at["b", "x_other"] == 1  # untouched columns stay


def test_find_absences_sources(tmp_path):
    inj = {"injuries": [{"displayName": "Team", "injuries": [
        {"status": "Out", "date": "2026-10-23T10:00Z", "shortComment": "knee",
         "details": {"fantasyStatus": {"abbreviation": "OUT"}, "type": "Knee", "returnDate": "2026-10-30"},
         "athlete": {"displayName": "Star One", "team": {"abbreviation": "AAA"}}},
        {"status": "Out", "date": "2026-10-20T10:00Z", "shortComment": "acl",
         "details": {"fantasyStatus": {"abbreviation": "OFS"}, "type": "Knee"},
         "athlete": {"displayName": "Star Two", "team": {"abbreviation": "BBB"}}},
        {"status": "Out", "date": "2026-10-20T10:00Z", "shortComment": "ankle", "details": {},
         "athlete": {"displayName": "Bench Guy", "team": {"abbreviation": "AAA"}}},
    ]}]}
    path = tmp_path / "injuries.json"
    path.write_text(json.dumps(inj))
    from fantasy.data.injuries import parse_espn

    df = pd.DataFrame([
        ("star-one", "Star One", "AAA", 33.0, 0), ("star-two", "Star Two", "BBB", 30.0, 2),
        ("star-three", "Star Three", "CCC", 31.0, 0), ("star-four", "Star Four", "DDD", 29.0, 0),
        ("star-five", "Star Five", "EEE", 35.0, 0), ("bench-guy", "Bench Guy", "AAA", 12.0, 0),
    ], columns=["player_id", "name", "team", "mpg", "missed_streak"]).set_index("player_id")
    league = {"synced_at": "2026-10-24T10:00Z", "teams": {"1": {"players": ["star-three"], "status": {"star-three": "O"}}}}
    news = {"players": [
        {"player_id": "star-four", "items": [{"severity": "medium", "flags": ["injury_exit"], "published": "2026-10-24",
                                              "headline": "exited Friday's game", "source": "Rotowire"}]},
        {"player_id": "star-five", "items": [{"severity": "medium", "flags": ["timeline"], "published": "2026-10-24",
                                              "headline": "back in two weeks", "source": "Rotowire"}]},
    ]}

    class NBA:
        games = [(date(2026, 10, d), "AAA", "ZZZ") for d in (24, 26, 28, 30, 31)]

    abs_, _ = find_absences(df, df["mpg"], date(2026, 10, 24), NBA(), parse_espn(path), league, news,
                            OpportunityParams())
    by = {a.player_id: a for a in abs_}
    assert set(by) == {"star-one", "star-two", "star-three", "star-four"}  # bench guy < 28 mpg; timeline-only skipped
    assert by["star-one"].return_date == "2026-10-30" and by["star-one"].espn_games == 3
    assert by["star-one"].sources[0]["source"] == "ESPN injury report"
    assert by["star-two"].season_out and by["star-two"].missed == 2
    assert by["star-three"].sources[0]["source"].startswith("Yahoo") and by["star-three"].p_out == 1.0
    assert by["star-four"].p_out == OpportunityParams().p_out_news
    assert np.isclose(by["star-one"].mpg, 33.0)
