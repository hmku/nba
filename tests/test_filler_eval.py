"""Rollout-placeholder research options (OptimizerParams.filler / refine_passes), `draft recommend --filler
--refine` and the filler-eval harness (synthetic projections, no network, no season simulation)."""

import json

import numpy as np
import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.draft.optimizer import DraftContext
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import load_projections

runner = CliRunner()


@pytest.fixture(scope="module")
def ctx(proj_path):
    return DraftContext(load_projections(proj_path), LeagueSettings())


def test_harness_variants_on_adp_room(ctx, monkeypatch):
    from fantasy.draft import filler_eval as fe
    from fantasy.draft.optimizer import DraftOptimizer, OptimizerParams
    from fantasy.valuesearch.policies import ADPPolicy, run_draft, sample_room_order

    monkeypatch.setattr(fe, "_E", {"ctx": ctx, "opts": {v: DraftOptimizer(ctx, OptimizerParams(strategy="balanced", **kw))
                                                        for v, kw in fe.VARIANTS.items()}})
    room = sample_room_order(ctx, np.random.default_rng([0, 11]), 1.0)
    cache: dict = {}
    for spec in ("opt_market", "bpa", "opt_avail"):
        pol = fe.VariantPolicy(spec, spec == "opt_market", cache)
        board = run_draft(ctx, {1: pol, **{t: ADPPolicy(room) for t in range(2, 13)}})
        assert len(board.rosters[1]) == 13 and len(pol.log) == 13
        if spec == "opt_market":  # every variant probed on the same boards
            assert all(set(e["tops"]) == set(fe.VARIANTS) for e in pol.log)
            assert all(e["chosen"] == e["tops"]["opt_market"] for e in pol.log)
    # opt_avail reuses the probe payloads on its shared prefix (at least pick 1)
    assert sum(1 for k in cache if k[1] == "opt_avail") >= 13


def test_harness_summary():
    from fantasy.draft.filler_eval import VARIANTS, summarize

    def g(x):
        return {t: {"p_playoffs": x, "p_title": x / 2, "e_rank": 3 - x} for t in ("ours", "market")}

    def row(seed, a, b):
        log = [{"pick": 1, "bpa": "x", "chosen": "a", "tops": {v: ("a" if v != "opt_avail" else "b") for v in VARIANTS},
                "secs": {v: 0.1 for v in VARIANTS}}]
        return {"seed": seed, "slot": 1, "regime": "adp",
                "policies": {"opt_market": {"grade": g(a), "log": log}, "opt_avail": {"grade": g(b), "log": []}}}

    sm = summarize([row(s, 0.5 + 0.01 * s, 0.52 + 0.01 * s) for s in range(5)])["slot1/adp"]
    d = sm["ours/p_playoffs"]["opt_avail"]["minus_opt_market"]
    assert sm["n"] == 5 and d["mean"] == pytest.approx(0.02)
    ch = sm["top_changed_vs_market"]["1"]
    assert ch["opt_avail"] == 1.0 and ch["opt_board"] == 0.0 and ch["bpa"] == 1.0


@pytest.fixture()
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)


def _cli(*args):
    res = runner.invoke(app, [str(a) for a in args])
    return res.exit_code, (json.loads(res.stdout) if "--json" in args else res.stdout)


def test_cli_recommend_filler(_env, proj_path):
    P = ["--projections", str(proj_path)]
    _cli("draft", "init", "--draft-id", "mock-f", "--mock")
    D = ["--draft-id", "mock-f", "--strategy", "balanced", "--search", "none", "--no-save"]
    code, base = _cli("draft", "recommend", *D, "--json", *P)
    assert code == 0 and "filler" not in base
    code, out = _cli("draft", "recommend", *D, "--filler", "available", "--refine", 1, "--json", *P)
    assert code == 0 and out["filler"]["mode"] == "available" and out["filler"]["refine_passes"] == 1
    assert len(out["recommendations"][0]["score_passes"]) == 2
    code, out = _cli("draft", "recommend", *D, "--filler", "bogus", "--json", *P)
    assert code == 2 and "board" in out["valid"]
    code, out = _cli("draft", "recommend", *D, "--refine", 9, "--json", *P)
    assert code == 2
    code, out = _cli("draft", "recommend", *D, "--refine", 1, *P)
    assert code == 0 and "research filler=market refine=1" in out
