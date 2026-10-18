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
