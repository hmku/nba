"""In-season tools: position-aware lineups, streaming ranker, trade evaluation (synthetic data, offline)."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from fantasy.inseason.lineup import (
    LineupSolver,
    PlayerSpec,
    bool_from_masks,
    masks_from_bool,
    simulate_lineups,
    team_days,
)
from fantasy.inseason.possim import PositionAwareSimulator
from fantasy.inseason.stream import window_value
from fantasy.inseason.trade import Trade, apply_trade, evaluate_trades
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import load_projections
from fantasy.sim.context import build_context
from fantasy.sim.engine import SimParams
from fantasy.sim.league import LeagueState
from fantasy.sim.schedule import uniform_schedule

SLOTS = LeagueSettings().starting_slots


def test_solver_respects_positions_and_priority():
    # slots PG + Util; players by priority: C, PG, PG -> C takes Util, best PG takes PG
    s = LineupSolver([["C"], ["PG"], ["PG"]], ["PG", "Util"])
    assert s.starters(0b111) == 0b011
    assert s.starters(0b101) == 0b101
    # two C-only players cannot both start in PG + C
    s2 = LineupSolver([["C"], ["C"]], ["PG", "C"])
    assert bin(s2.starters(0b11)).count("1") == 1
    # augmenting path: flexible high-priority player moves to make room
    s3 = LineupSolver([["PG", "SG"], ["PG"]], ["PG", "SG"])
    assert s3.starters(0b11) == 0b11
    assert set(s3.assignment(0b11).values()) == {0, 1}


def test_full_league_slots_fit_three_c_only_plus_two_pf_c():
    pos = [["C"], ["C"], ["C"], ["PF", "C"], ["PF", "C"], ["PF"], ["SG", "SF", "PF"], ["PG"], ["PG"], ["PG", "SG"]]
    s = LineupSolver(pos, SLOTS)
    assert s.starters((1 << 10) - 1) == (1 << 10) - 1  # all 10 fit
    # a 4th C-only player instead of the PF cannot start: only C, C, Util, Util take a C
    pos4 = [["C"]] * 4 + [["C"]] + [["PF"], ["SG", "SF", "PF"], ["PG"], ["PG"], ["PG", "SG"]]
    assert bin(LineupSolver(pos4, SLOTS).starters((1 << 10) - 1)).count("1") == 9


def test_mask_roundtrip():
    rng = np.random.default_rng(0)
    a = rng.random((5, 13, 7)) < 0.5
    m = masks_from_bool(a, axis=1)
    assert m.shape == (5, 7)
    assert (bool_from_masks(m, 13, axis=1) == a).all()


def test_simulate_lineups_counts_position_losses():
    d0 = date(2026, 10, 20)
    days = [d0, d0 + timedelta(1)]
    plays = team_days([(d0, "AAA", "BBB")], days)
    players = [PlayerSpec("c1", "C1", "AAA", ["C"], 3.0), PlayerSpec("c2", "C2", "BBB", ["C"], 2.0),
               PlayerSpec("c3", "C3", "AAA", ["C"], 1.0)]
    res = simulate_lineups(players, ["C", "PG"], days, plays)
    s = res.summary()
    assert s["player_games"] == 3 and s["starts"] == 1
    assert s["lost_to_positions"] == 1 and s["lost_to_slot_count"] == 1
    assert [p["exp_starts"] for p in res.per_player()] == [1.0, 0.0, 0.0]
    mc = simulate_lineups(players, ["C", "PG"], days, plays, n_sims=200, seed=1)
    assert mc.summary()["starts"] <= 1.0


def test_window_value_and_streamer_gain():
    d0 = date(2026, 10, 20)
    days = [d0]
    plays = {"AAA": {d0}, "BBB": {d0}}
    pos = {"a": ["C"], "b": ["C"], "fa": ["PG"]}
    team = {"a": "AAA", "b": "AAA", "fa": "BBB"}
    v = pd.Series({"a": 2.0, "b": 1.0, "fa": 0.5})
    base, starts = window_value(["a", "b"], pos, team, v, set(), ["C", "PG"], days, plays)
    assert base == 2.0 and starts == {"a": 1, "b": 0}
    with_fa, _ = window_value(["a", "b", "fa"], pos, team, v, set(), ["C", "PG"], days, plays)
    assert with_fa == 2.5
    out, _ = window_value(["a", "b"], pos, team, v, {"a"}, ["C", "PG"], days, plays)
    assert out == 1.0


def _values():
    return pd.DataFrame({"name": list("ABCDEFGH"), "value": [8, 7, 6, 5, 4, 3, 2, 1.0],
                         "adp": [1, 2, 3, 4, 5, 6, 7, 8.0]}, index=list("abcdefgh"))


def test_apply_trade_refills_and_trims():
    lg = LeagueState(rosters={1: ["a", "d", "e"], 2: ["b", "c", "f"]}, my_team=1, source="t")
    new, moves = apply_trade(lg, 1, Trade(2, ["d", "e"], ["b"]), _values(), roster_size=3)
    assert sorted(new.rosters[1]) == ["a", "b", "g"]  # best free agent g refills
    assert sorted(new.rosters[2]) == ["c", "d", "e"]  # partner drops its worst (f)
    assert moves[1]["add"] == ["g"] and moves[2]["drop"] == ["f"]
    new2, _ = apply_trade(lg, 1, Trade(2, ["d", "e"], ["b"]), _values(), roster_size=3, refill_skip=1)
    assert "h" in new2.rosters[1]
    assert lg.rosters[1] == ["a", "d", "e"]  # original untouched
    with pytest.raises(ValueError):
        apply_trade(lg, 1, Trade(2, ["b"], ["c"]), _values(), roster_size=3)


def test_apply_trade_keeps_yahoo_out_players_above_the_cap():
    """Out (IL) players don't count against the cap: a no-op trade leaves a 3 + 1 IL roster alone, and a trim never
    drops the Out player (the engine's roster cap handles his return, as in the base run)."""
    lg = LeagueState(rosters={1: ["a", "d", "e", "h"], 2: ["b", "c", "f"]}, my_team=1, source="t", out_now={"h"})
    new, moves = apply_trade(lg, 1, Trade(2, [], []), _values(), roster_size=3)
    assert new.rosters == lg.rosters and moves == {}
    new, moves = apply_trade(lg, 1, Trade(2, ["d"], ["b", "c"]), _values(), roster_size=3)
    assert "h" in new.rosters[1] and moves[1]["drop"] == ["e"]  # e (value 4) goes, not h (value 1, Out)


@pytest.fixture(scope="module")
def ctx(proj_path):
    return build_context(load_projections(proj_path), LeagueSettings(), [1, 2], nba=uniform_schedule(["TST"]))


def test_position_aware_sim_runs_and_pairs(ctx):
    pids = list(ctx.values.index[:26])
    lg = LeagueState(rosters={1: pids[0::2], 2: pids[1::2]}, my_team=1, source="t")
    p = SimParams(n_sims=60, seed=3, playoff_teams=2)
    sim = PositionAwareSimulator(ctx.proj.df, ctx.values, ctx.replacement, ctx.settings, ctx.nba, ctx.calendar,
                                 ctx.matchups, p)
    a, b = sim.run(lg), sim.run(lg)
    assert np.array_equal(a.totals, b.totals)  # deterministic -> paired comparisons are exact
    blind = ctx.simulator(p).run(lg)
    assert (a.games <= blind.games + 1e-6).all()  # slot rules can only remove games
    base, rows = evaluate_trades(sim, lg, 1, [Trade(2, [pids[2]], [pids[3]])], ctx.values, 13, base=a)
    assert rows[0]["me"]["p_title"]["delta_se"] >= 0 and "partner_delta" in rows[0]
