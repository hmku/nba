"""Persisted, display-ready simulation summaries: `draft simulate` (kind draft_sim) and `sim preseason`
(kind season_sim, scenario "pre-draft projection"). Schemas: docs/research/dashboard_data.md."""

import json

import numpy as np
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db.engine import get_engine
from fantasy.db.schema import model_runs
from fantasy.draft.optimizer import DraftContext
from fantasy.draft.simulate import simulate_drafts
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import load_projections
from fantasy.sim.context import build_context
from fantasy.sim.engine import SimParams
from fantasy.sim.preseason import preseason
from fantasy.sim.schedule import uniform_schedule

CATS = ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]


@pytest.fixture(scope="module")
def dctx(proj_path):
    return DraftContext(load_projections(proj_path), LeagueSettings())


@pytest.fixture(scope="module")
def sim_res(dctx):
    return simulate_drafts(dctx, n_sims=4, seed=3, strategy="balanced", keep_leagues=True)


def test_draft_sim_summary_schema(dctx, sim_res):
    s = sim_res["summary"]
    assert s["schema"] == "draft_sim/1" and s["n_sims"] == 4
    assert s["my_picks"] == [1, 24, 36, 37, 60, 61, 84, 85, 108, 109, 132, 133, 156]
    for k in ("expected_category_wins", "matchup_win_prob"):
        e = s["edge"][k]
        assert np.isfinite(e["mean"]) and e["se"] >= 0 and 0 <= e["p_optimizer_better"] <= 1
    assert s["edge_category_wins"] == s["edge"]["expected_category_wins"]["mean"]
    assert sum(s["strategy_counts"].values()) == 4
    pd_ = s["picks_distribution"]
    assert [p["pick"] for p in pd_] == s["my_picks"] and pd_[1]["round"] == 2
    for p in pd_:
        assert 1 <= len(p["top"]) <= 5 and sum(t["count"] for t in p["top"]) <= 4
        assert all(t["name"] and t["player_id"] for t in p["top"])
    av = s["availability"]
    assert av["picks"] == s["my_picks"][:6] and 10 <= len(av["players"]) <= 40
    for r in av["players"]:
        pa = r["p_available"]
        assert len(pa) == 6 and pa[0] == 1.0 and all(0 <= x <= 1 for x in pa)
        assert all(a >= b - 1e-9 for a, b in zip(pa, pa[1:], strict=False))  # availability only shrinks
    ranks = [r["market_rank"] for r in av["players"]]
    assert ranks == sorted(ranks)
    for side in ("optimizer", "baseline_bpa"):
        cw = s["category_win_probs"][side]
        assert list(cw) == CATS
        assert all(c["p10"] <= c["mean"] + 1e-9 <= c["p90"] + 2e-9 for c in cw.values())
    ex = s["example_rosters"]
    assert [e["label"] for e in ex] == ["best", "median", "worst"]
    assert ex[0]["expected_category_wins"] >= ex[1]["expected_category_wins"] >= ex[2]["expected_category_wins"]
    assert [p["pick"] for p in ex[0]["picks"]] == s["my_picks"]
    for k in ("overview", "opponents", "expected_category_wins", "edge", "availability", "caveats"):
        assert len(s["explanations"][k]) > 40
    json.dumps(s)  # display payload must be plain JSON


def test_keep_leagues_full_rooms(dctx, sim_res):
    for sim in sim_res["sims"]:
        lg = sim["league"]
        assert sorted(lg) == list(range(1, 13)) and all(len(v) == 13 for v in lg.values())
        assert lg[1] == sim["roster_ids"]
        allp = [p for v in lg.values() for p in v]
        assert len(allp) == len(set(allp)) == 156


def test_preseason_pools_leagues(proj_path, dctx):
    sctx = build_context(load_projections(proj_path), LeagueSettings(), list(range(1, 13)), nba=uniform_schedule(["TST"]))
    s = preseason(sctx, dctx, n_leagues=2, params=SimParams(n_sims=80, seed=1), strategy="balanced")
    assert s["scenario"] == "pre-draft projection" and s["schema"] == "season_sim_preseason/1"
    assert s["n_sims"] == 80 and s["n_leagues"] == 2 and len(s["leagues"]) == 2
    t = s["league_table"]
    assert len(t) == 12 and sum(r["is_me"] for r in t) == 1
    assert sum(r["p_title"] for r in t) == pytest.approx(1.0, abs=1e-3)
    assert sum(r["p_playoffs"] for r in t) == pytest.approx(s["playoff_teams"], abs=1e-2)
    m = s["my_outlook"]
    assert 0 <= m["p_title"] <= m["p_final"] <= m["p_playoffs"] <= 1
    assert sum(m["final_standing_dist"].values()) == pytest.approx(1.0, abs=1e-3)
    assert m["across_leagues"]["p_title_min"] <= m["p_title"] + 1e-9 <= m["across_leagues"]["p_title_max"] + 2e-9
    assert list(m["category_profile"]) == CATS
    assert all(len(lg["my_roster"]) == 13 for lg in s["leagues"])
    assert set(s["explanations"]) >= {"what_is_simulated", "priors", "caveats"}
    json.dumps(s)


def _row(run_id):
    with get_engine().connect() as c:
        return c.execute(select(model_runs).where(model_runs.c.run_id == run_id)).mappings().one_or_none()


def test_cli_save_and_no_save(proj_path, db_url, tmp_path):
    r = CliRunner()
    P = ["--projections", str(proj_path), "--json"]
    out = r.invoke(app, ["draft", "simulate", "--sims", "2", "--strategy", "balanced", *P])
    assert out.exit_code == 0, out.output
    js = json.loads(out.output)
    row = _row(js["run_id"])
    assert row["kind"] == "draft_sim" and row["model_version"].startswith("optimizer-")
    assert row["inputs"]["projections_sha256"] and row["params"]["n_sims"] == 2
    assert row["summary"]["schema"] == "draft_sim/1" and len(row["inputs"]["per_sim"]) == 2
    out = r.invoke(app, ["draft", "simulate", "--sims", "1", "--strategy", "balanced", "--no-save", *P])
    assert out.exit_code == 0 and json.loads(out.output)["run_id"] is None

    nba = tmp_path / "missing.csv"
    out = r.invoke(app, ["sim", "preseason", "--leagues", "2", "--sims", "40", "--strategy", "balanced",
                         "--nba-schedule", str(nba), *P])
    assert out.exit_code == 0, out.output
    js = json.loads(out.output)
    row = _row(js["run_id"])
    assert row["kind"] == "season_sim" and row["params"]["command"] == "preseason"
    assert row["summary"]["scenario"] == "pre-draft projection"
    out = r.invoke(app, ["sim", "preseason", "--strategy", "bogus", *P])
    assert out.exit_code == 2


def test_cluster_se_exceeds_binomial_when_leagues_differ():
    from fantasy.sim.preseason import _cluster_se

    x = np.concatenate([np.ones(100), np.zeros(100)])  # league 1 always wins, league 2 never
    naive = np.sqrt(0.25 / 200)
    assert _cluster_se(x, 2) > 5 * naive
    rng = np.random.default_rng(0)
    y = (rng.random(4000) < 0.3).astype(float)  # no league effect -> close to binomial SE
    assert _cluster_se(y, 20) == pytest.approx(np.sqrt(0.21 / 4000), rel=0.35)


def test_edge_reports_ties(sim_res):
    e = sim_res["summary"]["edge"]["expected_category_wins"]
    assert 0 <= e["p_optimizer_better"] + e["p_tie"] <= 1


def test_preseason_refuses_yahoo_schedule(proj_path, db_url, tmp_path):
    out = CliRunner().invoke(app, ["sim", "preseason", "--schedule", str(tmp_path / "s.json"),
                                   "--projections", str(proj_path), "--json"])
    assert out.exit_code == 2
