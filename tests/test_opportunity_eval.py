"""Opportunity detector 0.1.1: window cut, value calibration, verdicts, evaluation (offline, synthetic, stubs)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from fantasy.inseason.opportunity import calibrated_gain, primary_group, stint_end_day, week_mpg
from fantasy.inseason.opportunity_run import _verdict, evaluate, unprojected_risers


def test_primary_group_uses_first_position():
    assert primary_group("SG|SF") == {"G"}
    assert primary_group(["C", "PF"]) == {"B"}
    assert primary_group("") == frozenset()


def test_week_mpg_applies_opportunity_only_to_window_games():
    assert week_mpg(15.0, 25.0, 1.0, 4) == pytest.approx((25 + 3 * 15) / 4)
    assert week_mpg(15.0, 25.0, 10.0, 3) == pytest.approx(25.0)
    assert week_mpg(15.0, 25.0, 2.0, 0) == pytest.approx(15.0)


def test_stint_end_day_is_day_after_last_window_game():
    games = [(date(2026, 10, d), "AAA", "BBB") for d in (24, 26, 27, 30)] + [(date(2026, 10, 25), "CCC", "DDD")]
    assert stint_end_day("AAA", date(2026, 10, 24), 1.0, games) == date(2026, 10, 25)
    assert stint_end_day("AAA", date(2026, 10, 24), 2.4, games) == date(2026, 10, 28)  # ceil -> 3rd game (27th)
    assert stint_end_day("AAA", date(2026, 10, 25), 20.0, games) == date(2026, 10, 31)  # fewer games left
    assert stint_end_day("ZZZ", date(2026, 10, 24), 3.0, games) == date(2026, 10, 24)


def test_calibrated_gain():
    assert calibrated_gain(4.0, 1.0, 0.5, 1.5, 1.0, 0.2, 1.0) == pytest.approx(4 * (1.5 - 0.2))
    assert calibrated_gain(4.0, 1.0, 0.5, 1.5, 1.0, 0.2, 0.5) == pytest.approx(4 * (1.0 - 0.2))


def _d(delta, se):
    return {"delta": delta, "delta_se": se, "before": 0.5, "after": 0.5 + delta}


def _chain(po, ti, svd=(0.0, 0.01), sets=("blend", "market")):
    leg = {"stream": {"p_playoffs": _d(*po), "p_title": _d(*ti), "exp_cat_wins": _d(0, 1)},
           "direct": {"p_playoffs": _d(0, 0.01), "p_title": _d(0, 0.01), "exp_cat_wins": _d(0, 1)},
           "stream_vs_direct": {"p_playoffs": _d(*svd), "p_title": _d(*svd), "exp_cat_wins": _d(0, 1)}}
    return {"p_playoffs": _d(*po), "p_title": _d(*ti), "primary_chain": "fa5", "by_set": {s: {"fa5": leg} for s in sets}}


def test_verdict_rules():
    assert _verdict(_chain((0.05, 0.01), (0.0, 0.01), svd=(0.05, 0.01)), None, 2.0)[0] == "helps"
    assert _verdict(_chain((0.05, 0.01), (0.0, 0.01)), None, 2.0)[0] == "direct_helps"  # gain = the drop only
    assert _verdict(_chain((0.05, 0.01), (0.0, 0.01), svd=(-0.05, 0.01)), None, 2.0)[0] == "direct_better"
    assert _verdict(_chain((-0.05, 0.01), (0.0, 0.01)), None, 2.0)[0] == "hurts"
    assert _verdict(_chain((0.005, 0.01), (0.0, 0.01)), None, 2.0)[0] == "noise"
    assert _verdict(_chain((0.015, 0.01), (0.0, 0.01), svd=(0.015, 0.01)), None, 2.0)[0] == "speculative"  # > 1 SE
    assert _verdict(_chain((0.015, 0.01), (0.0, 0.01)), None, 2.0)[0] == "noise"  # positive only via the drop
    week = {"d_p_win": 0.05, "d_p_win_se": 0.01}
    assert _verdict(_chain((0.0, 0.01), (0.0, 0.01)), week, 2.0)[0] == "speculative"
    assert _verdict(None, week, 2.0)[0] == "unevaluated"  # never a season verdict without the chain


def test_evaluate_with_stubs():
    cands = [{"player_id": "a"}, {"player_id": "b"}]
    seen = []

    def week_fn(c):
        seen.append(("w", c["player_id"]))
        return {"mc": {"d_p_win": 0.0, "d_p_win_se": 0.01}}

    def season_fn(c):
        seen.append(("s", c["player_id"]))
        return _chain((0.05, 0.01), (0.01, 0.01), svd=(0.03, 0.01)) if c["player_id"] == "a" else _chain((-0.05, 0.01), (0.0, 0.01))

    ev = evaluate(cands, week_fn, season_fn)
    assert ev["a"]["verdict"] == "helps" and ev["b"]["verdict"] == "hurts"
    assert ev["a"]["week"]["mc"]["d_p_win"] == 0.0
    assert set(seen) == {("w", "a"), ("s", "a"), ("w", "b"), ("s", "b")}
    ev = evaluate(cands, None, None)
    assert {v["verdict"] for v in ev.values()} == {"unevaluated"}


def test_unprojected_risers():
    box = pd.DataFrame([
        {"player_id": "new-guy", "name": "New Guy", "box_team": "AAA", "games_played": 2, "last3_mpg": 22.0,
         "prev_mpg": np.nan},
        {"player_id": "steady", "name": "Steady", "box_team": "AAA", "games_played": 8, "last3_mpg": 20.0,
         "prev_mpg": 19.0},
        {"player_id": "riser", "name": "Riser", "box_team": "BBB", "games_played": 8, "last3_mpg": 24.0,
         "prev_mpg": 10.0},
        {"player_id": "projected", "name": "P", "box_team": "BBB", "games_played": 8, "last3_mpg": 30.0,
         "prev_mpg": 5.0},
        {"player_id": "owned", "name": "O", "box_team": "BBB", "games_played": 8, "last3_mpg": 30.0, "prev_mpg": 5.0},
    ]).set_index("player_id")
    out = unprojected_risers(box, {"projected"}, {"owned"})
    assert [r["player_id"] for r in out] == ["riser", "new-guy"]
