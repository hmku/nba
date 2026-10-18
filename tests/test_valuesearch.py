"""Value search (fantasy.valuesearch): featurization invariance, numpy tree export, determinism, CLI flag.
Synthetic projections, temp SQLite, no network; a hand-built one-tree model stands in for the artifact."""

import json

import numpy as np
import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState
from fantasy.league.settings import load_settings
from fantasy.model.lines import load_projections
from fantasy.valuesearch.features import Featurizer
from fantasy.valuesearch.model import TARGETS, TreeEnsemble, ValueModel, objective_value
from fantasy.valuesearch.policies import (
    ADPPolicy,
    Board,
    GreedyCatPolicy,
    run_draft,
    sample_room_order,
)
from fantasy.valuesearch.search import SearchParams, ValueSearch

runner = CliRunner()


@pytest.fixture(scope="module")
def setup(proj_path):
    s = load_settings()
    proj = load_projections(proj_path)
    ctx = DraftContext(proj, s)
    return ctx, Featurizer(ctx, proj.df), proj


def _league(ctx, seed=0):
    room = sample_room_order(ctx, np.random.default_rng(seed))
    pol = {t: ADPPolicy(room) for t in range(1, 13)}
    pol[1] = GreedyCatPolicy(ctx)
    return run_draft(ctx, pol).rosters


def stump_model(names: list[str], feature: str = "mwp") -> ValueModel:
    """One split on `feature` at 0.5 per target: left leaf 0.2, right leaf 0.6 (+ baseline 0)."""
    j = names.index(feature)

    def tree() -> TreeEnsemble:
        return TreeEnsemble(baseline=0.0, feature=np.array([[j, 0, 0]], np.int32), threshold=np.array([[0.5, 0, 0]]),
                            left=np.array([[1, 0, 0]], np.int32), right=np.array([[2, 0, 0]], np.int32),
                            missing_left=np.zeros((1, 3), bool), is_leaf=np.array([[False, True, True]]),
                            value=np.array([[0.0, 0.2, 0.6]]), depth=1)

    return ValueModel({t: tree() for t in TARGETS}, names, {"model_version": "test-stump"})


def test_features_permutation_invariant_over_opponents(setup):
    ctx, feat, _ = setup
    ros = _league(ctx)
    lg = [ros[t] for t in range(1, 13)]
    X = feat.transform(feat.pad([lg]))[0]
    assert X.shape == (12, len(feat.names)) and np.isfinite(X).all()
    perm = [0, 7, 3, 11, 1, 5, 2, 9, 4, 10, 6, 8]
    Xp = feat.transform(feat.pad([[lg[i] for i in perm]]))[0]
    for new, old in enumerate(perm):
        np.testing.assert_allclose(Xp[new], X[old], atol=1e-12)
    # a team's features change when its own roster changes
    lg2 = [list(r) for r in lg]
    lg2[0] = lg2[0][:-1]
    assert not np.allclose(feat.transform(feat.pad([lg2]))[0][0], X[0])


def test_tree_export_matches_sklearn(setup):
    sk = pytest.importorskip("sklearn")
    del sk
    from fantasy.valuesearch.model import fit_hgb

    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, 6))
    X[rng.random((400, 6)) < 0.05] = np.nan
    y = np.nan_to_num(X[:, 0]) * 2 - np.nan_to_num(X[:, 1]) ** 2 + rng.normal(0, 0.1, 400)
    m = fit_hgb(X, y, max_iter=30, min_samples_leaf=10)
    te = TreeEnsemble.from_sklearn(m)
    np.testing.assert_allclose(te.predict(X), m.predict(X), atol=1e-9)


def test_model_save_load_roundtrip(setup, tmp_path):
    _, feat, _ = setup
    vm = stump_model(feat.names)
    p = tmp_path / "m.npz"
    vm.save(p)
    vm2 = ValueModel.load(p)
    X = np.zeros((3, len(feat.names)))
    X[1, feat.names.index("mwp")] = 0.9
    a, b = vm.predict(X), vm2.predict(X)
    assert vm2.version == "test-stump" and vm2.feature_names == feat.names
    for t in TARGETS:
        np.testing.assert_allclose(a[t], b[t])
    np.testing.assert_allclose(objective_value(a, "blend"), [0.2, 0.6, 0.2])


def test_search_deterministic_and_paired(setup):
    ctx, feat, _ = setup
    vs = ValueSearch(ctx, feat, stump_model(feat.names))
    taken = {i + 1: ctx.market[i] for i in range(23)}
    rec = DraftOptimizer(ctx).recommend(DraftState(taken=taken, my_slot=1))
    cands = [r["player_id"] for r in rec["recommendations"]]
    p = SearchParams(n_candidates=4, rollouts=6, seed=3)
    a, b = vs.run(taken, 1, cands, p), vs.run(taken, 1, cands, p)
    a.pop("compute_seconds"), b.pop("compute_seconds")
    assert a == b
    assert a["for_pick"] == 24 and len(a["candidates"]) == 4
    first = next(c for c in a["candidates"] if c["player_id"] == cands[0])
    assert first["diff_vs_optimizer_top"] == 0.0
    assert a["top_pick"] in cands[:4]
    with pytest.raises(ValueError):
        vs.run(taken, 1, cands, SearchParams(opponents="nope"))


def test_board_from_taken_and_completion(setup):
    ctx, feat, _ = setup
    taken = {i + 1: ctx.market[i] for i in range(30)}
    b = Board.from_taken(ctx, taken)
    assert (~b.available).sum() == 30 and len(b.rosters[1]) == 2  # my picks 1 and 24
    vs = ValueSearch(ctx, feat, stump_model(feat.names))
    rem = [(pk.overall, pk.slot) for pk in ctx.order if pk.overall > 30]
    room = sample_room_order(ctx, np.random.default_rng(1))
    ros = vs._complete(b, rem, 1, 36, ctx.pos[ctx.market[40]], room, "adp")
    assert sum(len(r) for r in ros.values()) == len(ctx.order)
    assert ctx.pos[ctx.market[40]] in ros[1]
    allp = [r for rs in ros.values() for r in rs]
    assert len(allp) == len(set(allp))


def _cli(*args):
    res = runner.invoke(app, [str(a) for a in args])
    return res.exit_code, (json.loads(res.stdout) if "--json" in args and res.stdout.strip() else res.stdout)


def test_cli_search_flag(setup, proj_path, db_url, tmp_path, monkeypatch):
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)
    _, feat, _ = setup
    mp = tmp_path / "vm.npz"
    stump_model(feat.names).save(mp)
    P = ["--projections", proj_path, "--draft-id", "mock-vs", "--no-save", "--value-model", mp, "--rollouts", 4,
         "--search-candidates", 3]
    assert _cli("draft", "init", "--draft-id", "mock-vs", "--mock")[0] == 0
    code, out = _cli("draft", "recommend", *P, "--search", "value", "--objective", "title", "--json")
    assert code == 0 and out["ok"]
    v = out["value_search"]
    assert v["mode"] == "value" and not v["advisory"] and v["params"]["objective"] == "title"
    assert out["recommendations"][0]["player_id"] == v["top_pick"]
    code, adv = _cli("draft", "recommend", *P, "--search", "advise", "--json")
    assert code == 0 and adv["value_search"]["advisory"] and "value_search" in adv
    code, none = _cli("draft", "recommend", *P, "--search", "none", "--json")
    assert code == 0 and "value_search" not in none
    assert _cli("draft", "recommend", *P, "--search", "bogus", "--json")[0] == 2
    assert _cli("draft", "recommend", *P, "--search", "none", "--objective", "title", "--json")[0] == 2
    # without an explicit --search the valid objectives are the optimizer's, as before
    code, out = _cli("draft", "recommend", *P, "--objective", "bogus", "--json")
    assert code == 2 and out["valid"] == ["cat_wins", "matchup"]
    # a broken artifact never blocks a recommendation
    code, bad = _cli("draft", "recommend", *[x if x != mp else tmp_path / "missing.npz" for x in P],
                     "--search", "advise", "--json")
    assert code == 0 and "value_search" not in bad and any("value search failed" in w for w in bad["warnings"])
