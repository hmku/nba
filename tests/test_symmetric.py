"""Optimism decomposition: market-implied truth, private projection views, multi-strategy draft rooms."""

import json

import numpy as np
import pytest
from scipy.stats import spearmanr
from typer.testing import CliRunner

from fantasy.draft.optimizer import DraftContext
from fantasy.draft.roster import max_starting_matching
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import STATS, load_projections
from fantasy.model.valuation import _cat_scores, compute_values
from fantasy.sim.market_truth import (
    blend_projections,
    fixed_pool_scores,
    market_implied_projections,
)
from fantasy.sim.schedule import uniform_schedule
from fantasy.sim.symmetric import (
    ROOMS,
    Experiment,
    build_experiment,
    decomposition_table,
    noisy_market_order,
    perturb_projections,
    run_experiment,
    smart_order,
    summarize,
)


@pytest.fixture(scope="module")
def proj(proj_path):
    return load_projections(proj_path)


@pytest.fixture(scope="module")
def small_settings():
    return LeagueSettings().model_copy(update={"num_teams": 4, "playoff_teams": 2})


def test_fixed_pool_scores_reproduce_valuation(proj):
    s = LeagueSettings()
    values, eff, _ = compute_values(proj, s)
    pool = values.index[:156]
    a = fixed_pool_scores(eff, eff, pool, s, 1.0)
    b = _cat_scores(eff, pool, s, 1.0)
    assert np.allclose(a.to_numpy(), b.to_numpy())


def test_market_truth_matches_adp_order_and_keeps_mix(proj):
    s = LeagueSettings()
    mt = market_implied_projections(proj, s)
    values0, _, _ = compute_values(proj, s)
    values1, _, _ = compute_values(mt.proj, s)
    adp = proj.df["adp"].dropna().sort_values()
    top = adp.index[:156]
    rho0 = spearmanr(values0.loc[top, "value"], -adp.loc[top]).statistic
    rho1 = spearmanr(values1.loc[top, "value"], -adp.loc[top]).statistic
    assert rho1 > max(rho0, 0.95)
    assert mt.diagnostics["spearman_value_vs_adp_top_pool"] == pytest.approx(rho1, abs=1e-3)
    m = mt.multiplier
    assert ((m >= 0.5 - 1e-9) & (m <= 2.0 + 1e-9)).all()
    no_adp = proj.df.index[proj.df["adp"].isna()]
    assert (m.loc[no_adp] == 1.0).all()
    # category mix and percentages unchanged: every stat scaled by the same factor
    d0, d1 = proj.df, mt.proj.df
    assert np.allclose(d1.loc[top, STATS].to_numpy(), d0.loc[top, STATS].to_numpy() * m.loc[top].to_numpy()[:, None])
    assert np.allclose(d1["fgm"] / d1["fga"], d0["fgm"] / d0["fga"])


def test_blend_is_linear(proj):
    s = LeagueSettings()
    mt = market_implied_projections(proj, s, iterations=1)
    assert np.allclose(blend_projections(proj, mt, 0.0).df[STATS], proj.df[STATS])
    assert np.allclose(blend_projections(proj, mt, 1.0).df[STATS], mt.proj.df[STATS])
    half = blend_projections(proj, mt, 0.5).df[STATS]
    assert np.allclose(half, 0.5 * (proj.df[STATS] + mt.proj.df[STATS]))


def test_perturbed_views_are_keyed_and_consistent(proj):
    a1, a2 = perturb_projections(proj, 3, 5), perturb_projections(proj, 3, 5)
    b = perturb_projections(proj, 3, 6)
    assert np.allclose(a1.df[STATS], a2.df[STATS])
    assert not np.allclose(a1.df["reb"], b.df["reb"])
    d, d0 = a1.df, proj.df
    assert (d["fgm"] <= d["fga"] + 1e-9).all() and (d["fg3m"] <= d["fgm"] + 1e-9).all()
    assert np.abs(np.log(d["reb"] / d0["reb"])).mean() == pytest.approx(0.08 * np.sqrt(2) * 0.8, rel=0.35)
    assert np.abs(d["fgm"] / d["fga"] - d0["fgm"] / d0["fga"]).max() < 0.1
    assert np.allclose(d["pts"] / (2 * d["fgm"] + d["fg3m"] + d["ftm"]),
                       d0["pts"] / (2 * d0["fgm"] + d0["fg3m"] + d0["ftm"]))


def test_smart_order_nested_and_excludes_me():
    o = smart_order(7, 12, 1)
    assert sorted(o) == list(range(2, 13)) and o == smart_order(7, 12, 1)
    assert smart_order(8, 12, 1) != o


@pytest.fixture(scope="module")
def exp(proj, small_settings):
    e, diag = build_experiment(proj, small_settings, n_season_sims=40, nba=uniform_schedule(["TST"]))
    assert diag["n_adp_players"] > 0
    return e


def test_rooms_produce_complete_disjoint_feasible_rosters(exp):
    r = exp.run_seed(2, ROOMS)
    assert set(r["rooms"]) == set(ROOMS)
    slots = exp.settings.starting_slots
    for room, v in r["rooms"].items():
        rosters = v["rosters"]
        allp = [p for ps in rosters.values() for p in ps]
        assert len(rosters) == 4 and all(len(ps) == 13 for ps in rosters.values()), room
        assert len(set(allp)) == len(allp) == 52, room
        if room in ("A_bpa", "A_sym"):
            for ps in rosters.values():
                f, _ = max_starting_matching([exp.base.positions[exp.base.pos[p]] for p in ps], slots)
                assert f == len(slots), room
        for truth in ("ours", "market", "blend"):
            g = v["grade"][truth]
            assert len(g["p_title"]) == 4 and sum(g["p_title"]) == pytest.approx(1.0)
            assert sum(g["p_playoffs"]) == pytest.approx(2.0)
    # the ADP-only room is the market order itself
    order = noisy_market_order(exp.base, 2)
    adp = r["rooms"]["adp_all"]["rosters"]
    assert adp["1"][0] == order[0] and adp["2"][0] == order[1]
    # me (slot 1) uses the exact-projection optimizer in B and C rooms: same first pick
    assert r["rooms"]["B"]["rosters"]["1"][0] == r["rooms"]["C11"]["rosters"]["1"][0]


def test_run_experiment_resumes_and_summarizes(exp, tmp_path):
    out = tmp_path / "res.jsonl"
    res = run_experiment(exp, [0, 1], out, rooms=["adp_all", "B", "A_bpa"])
    assert [r["seed"] for r in res] == [0, 1]
    n_lines = len(out.read_text().splitlines())
    res2 = run_experiment(exp, [0, 1], out, rooms=["adp_all", "B", "A_bpa"])
    assert len(out.read_text().splitlines()) == n_lines  # nothing recomputed
    assert res2 == res
    s = summarize(res)
    assert s["n_seeds"] == 2 and set(s["rooms"]) == {"adp_all", "B", "A_bpa"}
    d = s["paired_diffs"]["B_minus_adp_all"]["p_title"]
    b = [r["rooms"]["B"]["grade"]["ours"]["p_title"][0] - r["rooms"]["adp_all"]["grade"]["ours"]["p_title"][0] for r in res]
    assert d["mean"] == pytest.approx(np.mean(b), abs=1e-4) and d["n"] == 2
    t = decomposition_table(s)
    assert {"room", "truth", "p_playoffs", "se_po", "p_title", "se_title"} <= set(t.columns)
    assert len(s["by_slot"]["adp_all"]["ours"]) == 4


def test_cli_symmetric_summary_only(exp, tmp_path):
    from fantasy.cli import app

    out = tmp_path / "res.jsonl"
    run_experiment(exp, [0, 1], out, rooms=["adp_all", "B"])
    r = CliRunner().invoke(app, ["sim", "symmetric", "--summary-only", "--out", str(out), "--json"])
    assert r.exit_code == 0, r.output
    body = json.loads(r.output)
    assert body["ok"] and body["n_seeds"] == 2 and body["table"]


def test_experiment_rejects_unknown_room(exp):
    with pytest.raises(ValueError):
        exp.drafters("Z", 0, {})
    assert isinstance(exp, Experiment) and isinstance(exp.base, DraftContext)
