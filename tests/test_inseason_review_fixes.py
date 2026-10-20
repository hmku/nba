"""inseason-0.3.0 quant-review fixes (B1, M1-M6, minor): synthetic data, offline."""

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from fantasy.inseason.cli import _fa_pool
from fantasy.inseason.possim import PositionAwareSimulator, make_sim, replacement_id
from fantasy.inseason.streamscan import (
    adds_needed,
    chain_specs,
    make_stream_season_eval,
    stint_end,
    stream_verdict,
)
from fantasy.inseason.trade import Trade
from fantasy.inseason.tradeeval import (
    EvalParams,
    Move,
    SetSpec,
    agg_seeds,
    apply_move,
    choose_free_move,
    classify,
    combine_classes,
    evaluate_trade,
    neutral_proj,
    pre_trade_league,
    trade_world,
)
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import load_projections
from fantasy.sim.context import build_context
from fantasy.sim.league import LeagueState
from fantasy.sim.schedule import uniform_schedule


@pytest.fixture(scope="module")
def ctx(proj_path):
    return build_context(load_projections(proj_path), LeagueSettings(), [1, 2], nba=uniform_schedule(["TST"]))


@pytest.fixture(scope="module")
def league(ctx):
    pids = list(ctx.values.index[:26])
    return LeagueState(rosters={1: pids[0::2], 2: pids[1::2]}, my_team=1, source="t")


def _d(x, se=0.01, before=0.5):
    return {"before": before, "after": before + x, "delta": x, "delta_se": se}


def _res(po, t, cw, se=(0.005, 0.005, 0.05)):
    return {"p_playoffs": _d(po, se[0]), "p_title": _d(t, se[1]), "exp_cat_wins": _d(cw, se[2])}


# ---------------------------------------------------------------- B1: stream chain
def test_chain_specs_and_stint():
    pool = ["s", "a", "b", "c", "d", "e", "f", "g"]
    ch = chain_specs(pool, {"s"}, (0, 5))
    assert [(c.label, c.player) for c in ch] == [("fa0", "a"), ("fa5", "f"), ("replacement", replacement_id("chain"))]
    assert stint_end(date(2026, 10, 22), date(2026, 10, 26)) == date(2026, 10, 27)
    assert stint_end(date(2026, 10, 22), date(2026, 10, 26), 2) == date(2026, 10, 24)


def test_adds_needed_counts_chain_claim():
    we = date(2026, 10, 26)
    assert adds_needed(date(2026, 10, 22), we) == {"this_week": 1, "next_week": 1, "chain_claim_day": "2026-10-27"}
    assert adds_needed(date(2026, 10, 22), we, 2)["this_week"] == 2  # chain claim also inside this week
    assert adds_needed(date(2026, 10, 28), we)["this_week"] == 0


def test_stream_chain_streamer_only_for_stint(ctx):
    """The streamer's season effect is limited to his stint: a great streamer kept for 0 days == the direct move,
    and kept for a week << kept all season."""
    sim = make_sim(ctx, 80, 5, playoff_teams=2)
    ids = list(ctx.values.index)
    league = LeagueState(rosters={1: ids[1:27:2], 2: ids[2:27:2]}, my_team=1, source="t")
    star, pool = ids[0], ids[27:]
    drop = min(league.rosters[1], key=lambda p: float(ctx.values["value"][p]))
    eff = ctx.calendar[1].start
    ev0 = make_stream_season_eval({"blend": sim}, league, 1, pool, eff, chain_k=1, stint_days=0)
    r0 = ev0(star, drop, eff)
    assert r0["stint_days"] == 0 and r0["primary_chain"] == "fa1"
    for lab in r0["chains"]:
        assert r0["by_set"]["blend"][lab]["stream"] == r0["by_set"]["blend"][lab]["direct"]
        assert r0["by_set"]["blend"][lab]["stream_vs_direct"]["exp_cat_wins"]["delta"] == 0
    ev = make_stream_season_eval({"blend": sim, "market": sim}, league, 1, pool, ctx.calendar[1].end, chain_k=1)
    r = ev(star, drop, eff)
    assert set(r["by_set"]) == {"blend", "market"} and set(r["chains"]) == {"fa0", "fa1", "replacement"}
    # permanent swap (the inseason-0.2.0 behaviour) is much larger than a one-week stint
    perm = league.copy()
    perm.rosters[1] = [*perm.rosters[1], star]
    from fantasy.sim.report import paired_delta

    base = sim.run(league)
    keep = paired_delta(base, sim.run(perm, {star: (eff, None), drop: (None, eff)}), 1)
    stint_gain = r["by_set"]["blend"]["fa1"]["stream_vs_direct"]["exp_cat_wins"]["delta"]
    assert keep["exp_cat_wins"]["delta"] > r["by_set"]["blend"]["fa1"]["stream"]["exp_cat_wins"]["delta"]
    assert abs(stint_gain) < abs(keep["exp_cat_wins"]["delta"])


def test_stream_verdict_all_sets_and_direct():
    good = {"stream": _res(0.03, 0.03, 0.3), "direct": _res(0.03, 0.03, 0.3), "stream_vs_direct": _res(0, 0, 0)}
    bad = {"stream": _res(-0.03, -0.03, -0.3), "direct": _res(0, 0, 0), "stream_vs_direct": _res(-0.03, -0.03, -0.3)}
    mk = lambda **sets: {"primary_chain": "fa5", "by_set": {k: {"fa5": v} for k, v in sets.items()}}  # noqa: E731
    assert stream_verdict(mk(blend=good, market=good)) == "helps"
    assert stream_verdict(mk(blend=good, market=bad)) == "hurts"
    worse = dict(good, stream_vs_direct=_res(-0.03, 0.0, 0.0))
    assert stream_verdict(mk(blend=worse)) == "direct_better"
    assert stream_verdict(_res(0.03, 0.0, 0.1)) == "helps"  # plain (legacy) result


# ---------------------------------------------------------------- M1: base with the free move, realistic refill
def test_pre_trade_league_moves_free_move_when_drop_is_traded():
    vals = pd.DataFrame({"name": list("abcdAX"), "value": [9, 8, 2, 1, 5, 0.5]}, index=list("abcdAX"))
    lg = LeagueState(rosters={1: list("abcd"), 2: ["X"]}, my_team=1, source="t")
    mv = Move("A", "d")
    assert sorted(apply_move(lg, 1, mv).rosters[1]) == ["A", "a", "b", "c"]
    pre, note = pre_trade_league(lg, 1, Trade(2, ["a"], ["X"]), mv, vals)
    assert sorted(pre.rosters[1]) == ["A", "a", "b", "c"] and "free move" in note
    pre, note = pre_trade_league(lg, 1, Trade(2, ["a", "d"], ["X"]), mv, vals)
    assert sorted(pre.rosters[1]) == ["A", "a", "b", "d"] and "d traded" in note  # drops c (next lowest) instead
    assert pre_trade_league(lg, 1, Trade(2, ["a"], ["X"]), None, vals)[0].rosters == lg.rosters


def test_trade_world_realistic_refill_skips_rostered():
    vals = pd.DataFrame({"name": list("abcXA123456"), "value": [9, 8, 7, 6, 5, 4, 3, 2, 1, 0.5, 0.4]},
                        index=list("abcXA123456"))
    lg = LeagueState(rosters={1: ["a", "b", "A"], 2: ["X", "c", "c2"]}, my_team=1, source="t")
    pool = ["A", "1", "2", "3", "4", "5", "6"]  # A already rostered (my free move)
    new, moves = trade_world(lg, 1, Trade(2, ["a", "b"], ["X"]), vals, 3, pool, refill_skip=5)
    assert moves[1]["add"] == ["6"]  # 6th best of the free players, not counting A
    new0, m0 = trade_world(lg, 1, Trade(2, ["a", "b"], ["X"]), vals, 3, pool, refill_skip=0)
    assert m0[1]["add"] == ["1"]
    newr, mr = trade_world(lg, 1, Trade(2, ["a", "b"], ["X"]), vals, 3, pool, replacement=True)
    assert mr[1]["add"] == [replacement_id("refill0")]


def test_choose_free_move(ctx, league):
    sim = make_sim(ctx, 60, 1, playoff_teams=2)
    free = [p for p in ctx.values.index if p not in {q for ps in league.rosters.values() for q in ps}]
    worst = sorted(league.rosters[1], key=lambda p: float(ctx.values["value"][p]))[:2]
    mv, rows = choose_free_move(sim, league, 1, free[0], worst)
    assert len(rows) == 2
    if mv is not None:
        assert mv.add == free[0] and mv.drop in worst and any(r["qualifies"] for r in rows)
    mv2, _ = choose_free_move(sim, league, 1, free[-1], worst)  # the worst free agent never qualifies
    assert mv2 is None


# ---------------------------------------------------------------- M2: schedule-neutral
def test_neutral_proj_maps_by_value_rank():
    df = pd.DataFrame({"team": ["NYK", "SAS", "DEN", "UTA"]}, index=["g1", "g2", "r1", "r2"])
    vals = pd.DataFrame({"value": [5.0, 1.0, 6.0, 0.5], "name": list("abcd")}, index=["g1", "g2", "r1", "r2"])
    out, m = neutral_proj(df, Trade(2, ["g2", "g1"], ["r1"]), vals)
    assert m == {"r1": "NYK"} and out.loc["r1", "team"] == "NYK" and df.loc["r1", "team"] == "DEN"
    out, m = neutral_proj(df, Trade(2, ["g1"], ["r2", "r1"]), vals)
    assert m == {"r1": "NYK", "r2": "NYK"}


# ---------------------------------------------------------------- M4: seeds and classes
def test_agg_seeds_inflates_se():
    ds = [_res(0.0, x, 0.0, (0.01, 0.01, 0.1)) for x in (0.0, 0.10, -0.10, 0.04)]
    a = agg_seeds(ds)["p_title"]
    assert a["n_seeds"] == 4 and a["delta"] == pytest.approx(0.01)
    assert a["se_paired"] == pytest.approx(0.005)
    assert a["delta_se"] == pytest.approx(a["sd_across_seeds"] / 2) and a["seed_se_factor"] > 5
    one = agg_seeds(ds[:1])["p_title"]
    assert one["delta_se"] == pytest.approx(0.01) and one["seed_se_factor"] is None


def test_classify_and_combine():
    assert classify(_res(0.0, 0.05, 0.0)) == "helps"
    assert classify(_res(-0.02, 0.05, 0.0)) == "speculative"  # title up, P(playoffs) down > 1 SE
    assert classify(_res(0.0, 0.05, -0.2)) == "speculative"  # cat wins down > 1 SE
    assert classify(_res(-0.03, 0.0, 0.0)) == "hurts"
    assert classify(_res(0.03, 0.005, 0.0)) == "floor_only"
    assert classify(_res(0.0, 0.005, 0.0)) == "noise"
    assert combine_classes(["helps", "helps"]) == "helps"
    assert combine_classes(["helps", "speculative"]) == "speculative"
    assert combine_classes(["noise", "hurts"]) == "hurts"
    assert combine_classes(["helps", "noise"]) == "mixed"


# ---------------------------------------------------------------- M2/M4/M5/M6: evaluate_trade end to end
def test_evaluate_trade(ctx, league):
    taken = {q for ps in league.rosters.values() for q in ps}
    pool = [p for p in ctx.values.index if p not in taken]
    spec = SetSpec(ctx, pool, sim_kw={"playoff_teams": 2})
    tr = Trade(2, [league.rosters[1][3], league.rosters[1][5]], [league.rosters[2][4]])
    res = evaluate_trade(tr, league, league, 1, {"blend": spec, "stress": spec}, 13,
                         EvalParams(n_sims=40, seeds=(7, 8), refill_skip=2))
    b = res["by_set"]["blend"]
    assert res["seeds"] == [7, 8] and b["me"]["p_title"]["n_seeds"] == 2
    assert b["refill_moves"]["1"]["add"] == [str(ctx.values["name"][pool[2]])]  # realistic refill (k = 2)
    # one NBA team in the synthetic schedule -> the schedule-neutral run equals the raw one
    assert b["neutral_me"]["p_title"]["delta"] == b["me"]["p_title"]["delta"] and not res["schedule_bet"]
    assert "optimistic_me" in b and isinstance(res["partner_ok"], bool)
    assert res["partner_ok"] == all(r["partner"]["p_playoffs"]["delta"] >= -0.01 for r in res["by_set"].values())
    assert res["class"] == combine_classes(list(res["class_by_set"].values()))


def test_fa_pool_skips_out_and_uses_date(tmp_path):
    cap = {"captured_at": "2026-10-20T03:25:00Z", "players": [
        {"player_id": "a", "name": "A", "team": "BKN", "positions": "PG", "status": None, "ownership": "FA"},
        {"player_id": "b", "name": "B", "team": "GS", "positions": "C", "status": "O", "ownership": "FA"},
        {"player_id": "c", "name": "C", "team": "NYK", "positions": "SF", "status": None, "ownership": "FA"},
        {"player_id": "d", "name": "D", "team": "NYK", "positions": "SF", "status": None, "ownership": "FA"}]}
    p = tmp_path / "fa.json"
    p.write_text(json.dumps(cap))
    vals = pd.DataFrame({"value": [1.0, 9.0, 3.0, 2.0]}, index=list("abcd"))
    w: list[str] = []
    assert _fa_pool(p, date(2026, 10, 20), vals, {"d"}, w) == ["c", "a"] and w == []
    _fa_pool(p, date(2026, 10, 20) + timedelta(days=5), vals, set(), w)
    assert any("re-capture" in x for x in w)  # --date drives the staleness check, not date.today()


def test_replacement_id_is_slot_eligible(ctx):
    sim = PositionAwareSimulator(ctx.proj.df, ctx.values, ctx.replacement, ctx.settings, ctx.nba, ctx.calendar,
                                 ctx.matchups)
    assert len(sim._positions(replacement_id(3))) == 5 and sim._positions("unknown-guy") == []
