"""T-006 stream scan and T-008 trade scan (synthetic data, offline)."""

import json
from datetime import date, timedelta

import numpy as np
import pytest

from fantasy.inseason.freeagents import FreeAgent, load_free_agents, realistic_refills
from fantasy.inseason.possim import PositionAwareSimulator
from fantasy.inseason.streamscan import StreamParams, scan_streams, season_verdict
from fantasy.inseason.trade import Trade, apply_trade
from fantasy.inseason.tradescan import (
    ScanParams,
    Screen,
    enumerate_trades,
    games_per_week,
    screen_trades,
    simulate_shortlist,
    start_shares,
    trade_rows,
)
from fantasy.league.settings import LeagueSettings
from fantasy.lineup.plan import generic_per_game_values
from fantasy.lineup.players import RosterPlayer, parse_positions
from fantasy.model.lines import load_projections
from fantasy.sim.context import build_context
from fantasy.sim.engine import SimParams
from fantasy.sim.league import LeagueState
from fantasy.sim.schedule import NBASchedule, uniform_schedule


# ---------------------------------------------------------------- free agents
def test_load_free_agents_waiver_timing(tmp_path):
    cap = {"captured_at": "2026-10-20T03:25:00Z", "players": [
        {"player_id": "a", "name": "A", "team": "BKN", "positions": "PG,SG", "status": None, "ownership": "FA"},
        {"player_id": "b", "name": "B", "team": "GS", "positions": "C", "status": "Q", "ownership": "W (Oct 22)",
         "waiver_until": "Oct 22"},
        {"player_id": "c", "name": "C", "team": "NYK", "positions": "SF", "status": "O", "ownership": "FA"},
        {"player_id": None, "name": "Unknown Guy", "team": "NYK", "positions": "SF", "status": None}]}
    p = tmp_path / "fa.json"
    p.write_text(json.dumps(cap))
    fas, warnings = load_free_agents(p, date(2026, 10, 20))
    by = {f.player_id: f for f in fas}
    assert set(by) == {"a", "b", "c"} and "Unknown Guy" in warnings[0]
    assert by["a"].waiver_until is None and by["a"].available_from(date(2026, 10, 20)) == date(2026, 10, 20)
    assert by["b"].waiver_until == date(2026, 10, 22) and by["b"].ownership == "W (Oct 22)"
    assert by["a"].team == "BRK" and by["b"].team == "GSW" and by["b"].positions == ("C",)
    assert realistic_refills(fas, {"a": 1.0, "b": 2.0, "c": 9.0}) == ["b", "a"]  # O-tagged c excluded


def test_load_free_agents_blanket_note_and_year_rollover(tmp_path):
    cap = {"source": "Yahoo league pages read 2026-12-30 ~03:25 UTC",
           "notes": "All listed players were on waivers 'W (Jan 1)' at read time.",
           "players": [{"player_id": "a", "name": "A", "team": "NYK", "positions": "PG"}]}
    p = tmp_path / "fa.json"
    p.write_text(json.dumps(cap))
    fas, w = load_free_agents(p, date(2027, 1, 5))
    assert fas[0].waiver_until == date(2027, 1, 1) and fas[0].waiver_source == "capture notes"
    assert any("re-capture" in x for x in w)


def test_season_verdict():
    d = lambda x, se: {"delta": x, "delta_se": se}  # noqa: E731
    assert season_verdict({"p_playoffs": d(0.02, 0.005), "p_title": d(0.0, 0.01)}) == "helps"
    assert season_verdict({"p_playoffs": d(0.005, 0.005), "p_title": d(0.01, 0.01)}) == "noise"
    assert season_verdict({"p_playoffs": d(-0.02, 0.005), "p_title": d(0.05, 0.01)}) == "tradeoff"
    assert season_verdict({"p_playoffs": d(-0.02, 0.005), "p_title": d(0.0, 0.01)}) == "hurts"


# ---------------------------------------------------------------- season sim with windows
@pytest.fixture(scope="module")
def ctx(proj_path):
    return build_context(load_projections(proj_path), LeagueSettings(), [1, 2], nba=uniform_schedule(["TST"]))


def _sim(ctx, n=60, seed=3):
    return PositionAwareSimulator(ctx.proj.df, ctx.values, ctx.replacement, ctx.settings, ctx.nba, ctx.calendar,
                                  ctx.matchups, SimParams(n_sims=n, seed=seed, playoff_teams=2))


def test_windows_keep_runs_paired_and_cut_games(ctx):
    pids = list(ctx.values.index[:27])
    lg = LeagueState(rosters={1: pids[0:26:2], 2: pids[1:26:2]}, my_team=1, source="t")
    sim = _sim(ctx)
    base = sim.run(lg)
    late = ctx.calendar[-1].end + timedelta(days=30)
    alt = lg.copy()
    alt.rosters[1] = [*alt.rosters[1], pids[26]]
    same = sim.run(alt, {pids[26]: (late, None)})  # never active -> identical to base
    assert np.allclose(same.totals, base.totals) and np.array_equal(same.champion, base.champion)
    # swap from the first day: the dropped player never plays, so it equals the plain roster change
    first = ctx.calendar[0].start
    swap = sim.run(alt, {pids[26]: (first, None), pids[24]: (None, first)})
    plain = lg.copy()
    plain.rosters[1] = [p for p in plain.rosters[1] if p != pids[24]] + [pids[26]]
    assert np.allclose(swap.totals, sim.run(plain).totals)
    # a mid-season swap lies between: early weeks equal base, late weeks equal the plain swap
    mid = ctx.calendar[5].start
    m = sim.run(alt, {pids[26]: (mid, None), pids[24]: (None, mid)})
    assert np.allclose(m.totals[:, :5], base.totals[:, :5])


# ---------------------------------------------------------------- stream scan
def _players(ids, df, team):
    return [RosterPlayer(p, str(df.loc[p, "name"]), parse_positions(df.loc[p, "positions"]), team, None, None,
                         float(df.loc[p, "gp"])) for p in ids]


def test_scan_streams_week_and_rule(ctx, proj_path):
    df = ctx.proj.df
    vals = ctx.values
    ids = list(vals.index)
    me = _players(ids[1:27:2], df, "AAA")
    opp = _players(ids[2:27:2], df, "BBB")
    d0 = date(2026, 11, 2)
    days = [d0 + timedelta(i) for i in range(7)]
    nba = NBASchedule(games=[(d, "AAA", "BBB") for d in days] + [(d, "CCC", "DDD") for d in days[::2]], source="t")
    good, waiver, out = ids[0], ids[30], ids[31]
    fas = [FreeAgent(good, "Good", "CCC", parse_positions(df.loc[good, "positions"]), None, None, "player"),
           FreeAgent(waiver, "Waiver", "AAA", parse_positions(df.loc[waiver, "positions"]), None, days[5], "player"),
           FreeAgent(out, "Out", "AAA", ("PG",), "O", None, "player"),
           FreeAgent(ids[1], "Rostered", "AAA", ("PG",), None, None, "player")]
    st = ctx.settings
    generic = generic_per_game_values(load_projections(proj_path), st)

    def stub(add, drop, eff):
        up = add == good
        d = {"delta": 0.03 if up else 0.0, "delta_se": 0.005}
        return {"p_playoffs": d, "p_title": d, "exp_cat_wins": d}

    params = StreamParams(week_sims=300, season_top=4)
    rep = scan_streams(df, st, nba, me, opp, fas, days, generic, vals["value"], params, season_eval=stub)
    assert rep["n_pairs_scanned"] == 2 * 3  # 2 eligible free agents x 3 default drops
    rows = {(r["add"], r["drop"]): r for r in rep["pairs"]}
    g = next(r for r in rep["pairs"] if r["add"] == good)
    assert g["add_games"] == 4 and g["add_starts"] <= 4 and g["week_d_exp_cats"] > 0
    w = next(r for r in rows.values() if r["add"] == waiver)
    assert w["effective"] == days[5].isoformat() and w["add_games"] == 2 and w["ownership"].startswith("W")
    assert rep["recommendation"]["add"] == good and all(x["add"] == good for x in rep["recommend"])
    assert all("week_mc" in r and "season" in r for r in rep["pairs"] if r["add"] == good)
    blocked = scan_streams(df, st, nba, me, opp, fas, days, generic, vals["value"],
                           StreamParams(week_sims=0, season_top=2), season_eval=stub, adds_used=6)
    assert blocked["recommend"] == [] and blocked["blocked"] and blocked["adds"]["remaining"] == 0
    none = scan_streams(df, st, nba, me, opp, fas, days, generic, vals["value"], StreamParams(week_sims=0, season_top=2),
                        season_eval=lambda a, d, e: {k: {"delta": 0.0, "delta_se": 0.01}
                                                     for k in ("p_playoffs", "p_title", "exp_cat_wins")})
    assert none["recommendation"] is None  # nothing beats noise -> recommend nothing


# ---------------------------------------------------------------- trade scan
def test_enumerate_and_fa_refill():
    lg = LeagueState(rosters={1: list("abc"), 2: list("def"), 3: list("ghi")}, my_team=1, source="t")
    assert len(enumerate_trades(lg, 1, ("1-1",))) == 2 * 9
    assert len(enumerate_trades(lg, 1, ("1-1", "2-1", "1-2"), partners=[2])) == 9 + 3 * 3 + 3 * 3
    import pandas as pd

    vals = pd.DataFrame({"name": list("abcdefghxy"), "value": [9, 8, 7, 6, 5, 4, 3, 2, 1, 0.5]},
                        index=list("abcdefghxy"))
    new, moves = apply_trade(lg, 1, Trade(2, ["b", "c"], ["d"]), vals, 3, fa_pool=["y", "x"])
    assert moves[1]["add"] == ["y"] and sorted(new.rosters[1]) == ["a", "d", "y"]


def test_trade_scan_pipeline(ctx):
    pids = list(ctx.values.index[:26])
    lg = LeagueState(rosters={1: pids[0::2], 2: pids[1::2]}, my_team=1, source="t")
    st = ctx.settings
    sim = _sim(ctx, n=60)
    base = sim.run(lg)
    regular = [w.week for w in ctx.calendar if w.week < min(st.playoff_weeks)]
    days = [d for w in ctx.calendar if w.week in regular[:3] for d in w.days()]
    gpw = games_per_week(ctx.nba, ctx.calendar, regular)
    assert 3.0 < gpw["TST"] < 4.0  # uniform schedule: 3.5 games / 7 days
    shares = start_shares(lg, ctx.proj.df, ctx.values, st, ctx.nba, days, n_sims=5)
    assert shares[0] == pytest.approx(1.0) and (np.diff(shares[:13]) <= 1e-9).all()
    screen = Screen(base, lg, ctx.proj.df, ctx.values, st, gpw, shares)
    same = screen.score({1: lg.rosters[1], 2: lg.rosters[2]})
    assert 0 < same[1] < 9 and 0 < same[2] < 9
    pool = [p for p in ctx.values.index if p not in set(pids)]
    params = ScanParams(top_per_team=2, kinds=("1-1", "2-1"), partner_tol=5.0, naive_tol=99.0)
    rows, n = screen_trades(screen, lg, 1, ctx.values, 13, pool, params)
    assert n == 13 * 13 + 78 * 13 and 1 <= len(rows) <= 2
    assert all(r["proxy_me"] > 0 for r in rows)
    simulate_shortlist(rows, lg, 1, {"blend": sim, "market": sim})
    table = trade_rows(rows, ctx.values, ctx.proj.df, ctx.nba, ctx.calendar, st.playoff_weeks, gpw, lg,
                       ["blend", "market"])
    for r in table:
        a, b = r["sim"]["blend"], r["sim"]["market"]
        assert a["me"] == b["me"]  # same simulator twice -> identical paired deltas
        assert r["helps_both"] == (a["verdict"] == "helps")
        sched = r["decomposition"]["schedule"]
        assert len(sched["d_playoff_games_by_week"]) == len(st.playoff_weeks)
        assert r["decomposition"]["projection_disagreement"] == {"p_playoffs": 0.0, "p_title": 0.0}
