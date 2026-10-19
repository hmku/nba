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
    # without an explicit --search the default (value) applies: value objectives are valid too
    code, out = _cli("draft", "recommend", *P, "--objective", "bogus", "--json")
    assert code == 2 and out["valid"][:2] == ["cat_wins", "matchup"] and "title" in out["valid"]
    # a broken artifact never blocks a recommendation
    code, bad = _cli("draft", "recommend", *[x if x != mp else tmp_path / "missing.npz" for x in P],
                     "--search", "advise", "--json")
    assert code == 0 and "value_search" not in bad and any("value search failed" in w for w in bad["warnings"])


def test_default_policy_is_value_search_with_sim_availability(setup, proj_path, db_url, tmp_path, monkeypatch):
    """2026-10-19 user decision: the default recommend = --search value --availability sim --room mixed; the old
    behaviour stays reachable with explicit flags; the saved payload says which policy produced the pick."""
    from fantasy.draft import store
    from fantasy.valuesearch.search import DEFAULT_SEARCH

    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)
    assert DEFAULT_SEARCH == "value"
    _, feat, _ = setup
    mp = tmp_path / "vm.npz"
    stump_model(feat.names).save(mp)
    P = ["--projections", proj_path, "--draft-id", "mock-def", "--value-model", mp, "--rollouts", 4,
         "--search-candidates", 3]
    assert _cli("draft", "init", "--draft-id", "mock-def", "--mock")[0] == 0
    code, out = _cli("draft", "recommend", *P, "--json")
    assert code == 0 and out["ok"]
    rp = out["recommend_policy"]
    assert (rp["search"], rp["availability"], rp["room"], rp["value_search_status"]) == ("value", "sim", "mixed", "ok")
    assert rp["is_default"] and rp["fallback"] is None and rp["version"].startswith("recommend-")
    assert out["availability"].startswith("sim:mixed") and out["value_search"]["mode"] == "value"
    assert out["recommendations"][0]["player_id"] == out["value_search"]["top_pick"]
    assert out["optimizer_version"] == "optimizer-0.4.0"
    # provenance is in the saved row (dashboard / prepare-pick read it)
    saved = store.latest_recommendation("mock-def")
    assert saved["recommend_policy"]["search"] == "value" and saved["recommend_policy"]["availability"] == "sim"
    # deterministic per board state
    code, again = _cli("draft", "recommend", *P, "--no-save", "--json")
    assert [r["player_id"] for r in again["recommendations"]] == [r["player_id"] for r in out["recommendations"]]
    assert again["value_search"]["candidates"] == out["value_search"]["candidates"]
    # old behaviour with explicit flags
    code, old = _cli("draft", "recommend", *P, "--no-save", "--search", "advise", "--availability", "adp", "--json")
    assert code == 0 and old["availability"] == "adp" and old["value_search"]["advisory"]
    assert old["recommend_policy"]["search"] == "advise" and not old["recommend_policy"]["is_default"]
    assert old["recommend_policy"]["room"] is None
    # a non-optimizer --policy decides the top itself: the default search drops to advise (no usage error)
    code, bpa = _cli("draft", "recommend", *P, "--no-save", "--policy", "bpa", "--json")
    assert code == 0 and bpa["recommend_policy"]["search"] == "advise" and bpa["value_search"]["advisory"]
    assert bpa["recommend_policy"]["top_pick_decided_by"] == "policy:bpa"
    assert _cli("draft", "recommend", *P, "--no-save", "--policy", "bpa", "--search", "value", "--json")[0] == 2


def test_default_policy_falls_back_to_optimizer(setup, proj_path, db_url, tmp_path, monkeypatch):
    """Value model missing, or the search throwing: the optimizer's (sim-availability) ranking with a warning."""
    import fantasy.valuesearch.search as vsearch

    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)
    _, feat, _ = setup
    mp = tmp_path / "vm.npz"
    stump_model(feat.names).save(mp)
    P = ["--projections", proj_path, "--draft-id", "mock-fb", "--no-save", "--rollouts", 4, "--search-candidates", 3]
    assert _cli("draft", "init", "--draft-id", "mock-fb", "--mock")[0] == 0
    code, ref = _cli("draft", "recommend", *P, "--search", "none", "--json")
    assert code == 0 and ref["recommend_policy"]["value_search_status"] == "off"
    ref_ids = [r["player_id"] for r in ref["recommendations"]]
    code, miss = _cli("draft", "recommend", *P, "--value-model", tmp_path / "missing.npz", "--json")
    assert code == 0 and miss["ok"] and "value_search" not in miss
    assert miss["recommend_policy"]["value_search_status"] == "failed" and miss["recommend_policy"]["fallback"]
    assert miss["recommend_policy"]["top_pick_decided_by"] == "optimizer"
    assert any("value search failed" in w for w in miss["warnings"])
    assert [r["player_id"] for r in miss["recommendations"]] == ref_ids

    def boom(self, *a, **k):
        raise RuntimeError("search exploded")

    monkeypatch.setattr(vsearch.ValueSearch, "run", boom)
    code, err = _cli("draft", "recommend", *P, "--value-model", mp, "--json")
    assert code == 0 and err["recommend_policy"]["value_search_status"] == "failed"
    assert any("search exploded" in w for w in err["warnings"])
    assert [r["player_id"] for r in err["recommendations"]] == ref_ids


def test_watch_saves_what_recommend_returns(setup, proj_path, db_url, tmp_path, monkeypatch):
    """`draft watch` uses the same default policy, so its precomputed recommendation equals `draft recommend`."""
    from fantasy.draft import store

    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)
    ctx, feat, _ = setup
    mp = tmp_path / "vm.npz"
    stump_model(feat.names).save(mp)
    assert _cli("draft", "init", "--draft-id", "mock-w", "--mock")[0] == 0
    for ov in range(1, 4):  # three picks recorded: my next pick is #24 (slot 1)
        assert _cli("draft", "pick", ctx.market[ov - 1], "--overall", ov, "--draft-id", "mock-w",
                    "--projections", proj_path)[0] == 0
    code, _ = _cli("draft", "watch", "--draft-id", "mock-w", "--projections", proj_path, "--value-model", mp,
                   "--interval", 0, "--max-iterations", 1)
    assert code == 0
    w = store.latest_recommendation("mock-w")
    code, r = _cli("draft", "recommend", "--draft-id", "mock-w", "--projections", proj_path, "--value-model", mp,
                   "--no-save", "--json")
    assert code == 0
    assert w["recommend_policy"]["search"] == "value" and w["recommend_policy"]["value_search_status"] == "ok"
    assert w["availability"] == r["availability"] and w["for_pick"] == r["for_pick"] == 24
    assert [x["player_id"] for x in w["recommendations"]] == [x["player_id"] for x in r["recommendations"]]
    assert w["value_search"]["top_pick"] == r["value_search"]["top_pick"]


def test_ensemble_combine_and_fe_variant():
    pytest.importorskip("sklearn")
    from fantasy.valuesearch.model import fit_hgb
    from fantasy.valuesearch.train import fit_variant, within_room

    rng = np.random.default_rng(1)
    X = rng.normal(size=(300, 4))
    a = TreeEnsemble.from_sklearn(fit_hgb(X, X[:, 0], max_iter=10, min_samples_leaf=10))
    b = TreeEnsemble.from_sklearn(fit_hgb(X, X[:, 1] ** 2, max_iter=25, max_leaf_nodes=7, min_samples_leaf=10))
    np.testing.assert_allclose(TreeEnsemble.combine(a, b, 0.7).predict(X), a.predict(X) + 0.7 * b.predict(X),
                               atol=1e-12)
    # paired toy data: 60 rooms x 3 candidates x 2 teams; label = room effect + 0.3 * x0
    n_room, K, T = 60, 3, 2
    g = np.repeat(np.arange(n_room), K * T)
    cand = np.tile(np.repeat(np.arange(K), T), n_room)
    team = np.tile(np.arange(T), n_room * K)
    X = rng.normal(size=(len(g), 4))
    y = rng.normal(size=n_room)[g] + 0.3 * X[:, 0]
    Y = np.stack([y, y, y, y], axis=1)
    paired = np.ones(len(g), dtype=bool)
    kw = {"max_iter": 20, "min_samples_leaf": 5}
    plain = fit_variant(X, Y, g, team, paired, "plain", hgb_kw=kw)
    fe0 = fit_variant(X, Y, g, team, paired, "fe", lam=0.0, folds=2, hgb_kw=kw)
    np.testing.assert_allclose(fe0["p_title"].predict(X), plain["p_title"].predict(X), atol=1e-12)
    fe = fit_variant(X, Y, g, team, paired, "fe", lam=1.0, folds=2, hgb_kw=kw)
    assert fe["e_rank"].feature.shape == plain["e_rank"].feature.shape  # only FE_TARGETS get the correction
    wr = within_room(fe["p_title"].predict(X), y, g, team, cand)
    assert wr["n"] == n_room * (K - 1) * T and wr["corr"] > 0.5
    # identical differences -> perfect agreement
    assert within_room(y, y, g, team, cand)["sign_agree"] == 1.0


def test_default_model_path_env_override(monkeypatch, tmp_path):
    from fantasy.valuesearch.model import DEFAULT_MODEL, MODEL_DIR, MODEL_ENV, default_model_path

    monkeypatch.delenv(MODEL_ENV, raising=False)
    assert default_model_path() == DEFAULT_MODEL
    monkeypatch.setenv(MODEL_ENV, "value-model-0.1.0")
    assert default_model_path() == MODEL_DIR / "value-model-0.1.0.npz"
    monkeypatch.setenv(MODEL_ENV, str(tmp_path / "x.npz"))
    assert default_model_path() == tmp_path / "x.npz"
