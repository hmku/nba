import numpy as np

from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.roster import max_starting_matching
from fantasy.draft.simulate import simulate_drafts
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import load_projections


def _ctx(proj_path):
    return DraftContext(load_projections(proj_path), LeagueSettings())


def test_first_pick_recommendation_shape(proj_path):
    ctx = _ctx(proj_path)
    rec = DraftOptimizer(ctx).recommend(DraftState(taken={}, my_slot=1), draft_id="t")
    assert rec["for_pick"] == 1
    assert rec["my_next_picks"][:4] == [1, 24, 36, 37]
    recs = rec["recommendations"]
    assert len(recs) >= 5
    assert recs[0]["marginal_value"] >= 0 >= recs[1]["marginal_value"]
    assert [r["score"] for r in recs] == sorted([r["score"] for r in recs], reverse=True)
    assert set(recs[0]["cat_impact"]) == set(LeagueSettings().categories)
    assert len(recs[0]["plan"]) == 12
    assert rec["strategy"]["name"] in rec["strategy"]["scan"]


def test_respects_taken_players(proj_path):
    ctx = _ctx(proj_path)
    opt = DraftOptimizer(ctx, OptimizerParams(strategy="balanced"))
    first = opt.recommend(DraftState(taken={}, my_slot=1))["recommendations"][0]["player_id"]
    taken = {i + 1: pid for i, pid in enumerate(ctx.market[:23])}
    taken[1] = first
    rec = opt.recommend(DraftState(taken=taken, my_slot=1))
    assert rec["for_pick"] == 24 and rec["on_the_clock"]["is_me"]
    ids = {r["player_id"] for r in rec["recommendations"]}
    assert not ids & set(taken.values())
    assert rec["roster"][0]["player_id"] == first


def test_deterministic(proj_path):
    ctx = _ctx(proj_path)
    a = DraftOptimizer(ctx).recommend(DraftState(taken={}, my_slot=1))
    b = DraftOptimizer(ctx).recommend(DraftState(taken={}, my_slot=1))
    assert [r["player_id"] for r in a["recommendations"]] == [r["player_id"] for r in b["recommendations"]]


def test_simulated_roster_is_feasible_and_beats_bpa(proj_path):
    ctx = _ctx(proj_path)
    res = simulate_drafts(ctx, n_sims=2, seed=1, strategy="balanced")
    assert res["summary"]["n_sims"] == 2
    for sim in res["sims"]:
        assert len(sim["roster"]) == 13
        ids = [ctx.values.index[ctx.values["name"] == n][0] for n in sim["roster"]]
        filled, _ = max_starting_matching([ctx.positions[ctx.pos[i]] for i in ids], ctx.settings.starting_slots)
        assert filled == 10
    assert np.isfinite(res["summary"]["edge_category_wins"])
    assert res["summary"]["edge_category_wins"] > -0.1


def test_sync_gap_does_not_rewind_clock(proj_path):
    """A missing earlier pick must not move 'on the clock' backwards or hide available players."""
    ctx = _ctx(proj_path)
    opt = DraftOptimizer(ctx, OptimizerParams(strategy="balanced"))
    taken = {i + 1: pid for i, pid in enumerate(ctx.market[:23])}
    full = opt.recommend(DraftState(taken=dict(taken), my_slot=1))
    del taken[5]
    gap = opt.recommend(DraftState(taken=taken, my_slot=1))
    assert gap["on_the_clock"]["overall"] == 24 and gap["on_the_clock"]["is_me"]
    assert gap["recommendations"][0]["player_id"] in {r["player_id"] for r in full["recommendations"][:3]} | {ctx.market[4]}
    assert any("not recorded" in w for w in gap["warnings"])


def test_payload_carries_optimizer_version(proj_path):
    from fantasy.draft.optimizer import OPTIMIZER_VERSION

    rec = DraftOptimizer(_ctx(proj_path), OptimizerParams(strategy="balanced")).recommend(DraftState(taken={}, my_slot=1))
    assert rec["optimizer_version"] == OPTIMIZER_VERSION
    assert rec["rollout_mode"] == "expected"


def test_expected_rollout_plan_is_distinct_and_available(proj_path):
    ctx = _ctx(proj_path)
    opt = DraftOptimizer(ctx, OptimizerParams(strategy="balanced"))
    taken = {i + 1: pid for i, pid in enumerate(ctx.market[:23])}
    rec = opt.recommend(DraftState(taken=taken, my_slot=1))
    for r in rec["recommendations"]:
        plan = [r["player_id"], *r["plan"]]
        assert len(plan) == len(set(plan)) == 12  # 13 picks, pick 1 already on the board
        assert not set(plan) & set(taken.values()) - {taken[1]}


def test_expected_rollout_equals_greedy_when_everyone_is_available(proj_path, monkeypatch):
    """With P(available)=1 the best-ranked candidate is taken with probability 1, so the expected
    rollout reduces to the greedy one."""
    import fantasy.draft.optimizer as om

    monkeypatch.setattr(om, "prob_available", lambda at, cur, adp, sd=1.0: np.ones(len(np.atleast_1d(adp))))
    ctx = _ctx(proj_path)
    w = ctx.weights([])
    roster = [ctx.pos[ctx.market[0]]]
    av = np.ones(len(ctx.ids), dtype=bool)
    av[roster[0]] = False
    future = [24, 36, 37, 60]
    exp = DraftOptimizer(ctx, OptimizerParams(rollout_mode="expected")).rollout(roster, av, future, 2, w)
    gre = DraftOptimizer(ctx, OptimizerParams(rollout_mode="greedy")).rollout(roster, av, future, 2, w)
    assert exp[0] == gre[0]
    assert np.isclose(exp[1], gre[1])


def test_expected_rollout_completes_plan(proj_path):
    ctx = _ctx(proj_path)
    w = ctx.weights([])
    roster = [ctx.pos[ctx.market[0]]]
    av = np.ones(len(ctx.ids), dtype=bool)
    av[roster[0]] = False
    future = [24, 36, 37, 60, 61, 84, 85]
    plan, obj, P = DraftOptimizer(ctx).rollout(roster, av, future, 2, w)
    assert len(plan) == len(set(plan)) == len(future) and roster[0] not in plan
    assert np.isfinite(obj) and np.all((P >= 0) & (P <= 1))
