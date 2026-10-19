"""Draft policies (fantasy.draft.policy), `draft recommend|simulate --policy` and the policy-eval harness
(synthetic projections, no network, no season simulation)."""

import json

import numpy as np
import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.policy import (
    DEFAULT_POLICY,
    POLICIES,
    PolicyParams,
    apply_policy,
    availability_context,
    board_state,
    bpa_row,
    decide,
    feasible,
)
from fantasy.draft.simulate import simulate_drafts
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import load_projections

runner = CliRunner()
BAL = OptimizerParams(strategy="balanced", availability="adp")  # the harnesses' pre-0.4.0 optimizer


@pytest.fixture(scope="module")
def ctx(proj_path):
    return DraftContext(load_projections(proj_path), LeagueSettings())


def _state_at_24(ctx):
    """Board on the clock at #24 (slot 1): I took the best player, the room took the market's next 22."""
    first = ctx.ids[int(np.argmax(ctx.value))]
    rest = [p for p in ctx.market if p != first][:22]
    return DraftState(taken={1: first, **{i + 2: pid for i, pid in enumerate(rest)}}, my_slot=1)


def test_default_policy_unchanged():
    assert DEFAULT_POLICY == "optimizer"
    assert {"optimizer", "bpa", "hybrid", "bpa_need"} <= set(POLICIES)


def test_bpa_row_is_best_feasible_value(ctx):
    st = _state_at_24(ctx)
    available, roster, left = board_state(ctx, st.taken, 1)
    assert len(roster) == 1 and left == 11
    r = bpa_row(ctx, available, roster, left)
    av = np.flatnonzero(available)
    assert available[r] and ctx.value[r] == ctx.value[av].max()  # early: everyone is feasible
    # late in the draft feasibility binds: with no picks left the lineup must still be completable
    assert feasible(ctx, roster, r, left)


def test_decide_rules(ctx):
    st = _state_at_24(ctx)
    rec = DraftOptimizer(ctx, BAL).recommend(st)
    opt_pid = rec["recommendations"][0]["player_id"]
    d_bpa = decide(ctx, rec, st.taken, 1, PolicyParams("bpa"))
    d_opt = decide(ctx, rec, st.taken, 1, PolicyParams("optimizer"))
    assert d_opt["player_id"] == opt_pid and d_bpa["player_id"] == d_bpa["bpa_player_id"]
    # hybrid: an unreachable margin always falls back to BPA, a negative one always keeps the optimizer
    assert decide(ctx, rec, st.taken, 1, PolicyParams("hybrid", margin=99))["player_id"] == d_bpa["player_id"]
    assert decide(ctx, rec, st.taken, 1, PolicyParams("hybrid", margin=-99))["player_id"] == opt_pid
    # bpa_need with a zero window is BPA; with a wide window it's still an available, feasible player
    assert decide(ctx, rec, st.taken, 1, PolicyParams("bpa_need", eps=0.0))["player_id"] == d_bpa["player_id"]
    wide = decide(ctx, rec, st.taken, 1, PolicyParams("bpa_need", eps=5.0))["player_id"]
    assert wide not in st.taken.values()
    with pytest.raises(ValueError):
        decide(ctx, rec, st.taken, 1, PolicyParams("opt_valavail"))  # needs the alternative payload


def test_availability_context_only_changes_availability(ctx):
    c = availability_context(ctx, "valavail")
    assert c.values is ctx.values and c.model is ctx.model
    top = int(np.argmax(ctx.value))
    assert c.market_rank[top] == 1 and sorted(c.market_rank) == list(range(1, len(ctx.ids) + 1))
    s = availability_context(ctx, "sd50")
    assert np.allclose(s.sd_mult, ctx.sd_mult * 0.5) and s.market_rank is ctx.market_rank
    assert not np.allclose(ctx.sd_mult, s.sd_mult)  # original untouched


@pytest.mark.parametrize("name", ["optimizer", "bpa", "hybrid", "bpa_need", "opt_valavail"])
def test_apply_policy_payload(ctx, name):
    st = _state_at_24(ctx)
    rec = DraftOptimizer(ctx, BAL).recommend(st)
    opt_top = rec["recommendations"][0]["player_id"]
    apply_policy(ctx, rec, st, PolicyParams(name), BAL)
    pol = rec["policy"]
    assert pol["name"] == name and pol["version"].startswith("draft-policy-")
    assert rec["recommendations"][0]["player_id"] == pol["top_pick"]
    assert pol["optimizer_top"]["player_id"] == opt_top
    assert {"player_id", "name", "value", "optimizer_score", "optimizer_rank"} <= set(pol["bpa_top"])
    assert pol["agrees_with_optimizer"] == (pol["top_pick"] == opt_top)
    assert pol["agrees_with_bpa"] == (pol["top_pick"] == pol["bpa_top"]["player_id"])
    if name == "optimizer":
        assert pol["top_pick"] == opt_top
    if name == "bpa":
        assert pol["agrees_with_bpa"]
    assert (pol["alt_optimizer_top"] is not None) == (name == "opt_valavail")
    ids = [r["player_id"] for r in rec["recommendations"]]
    assert len(ids) == len(set(ids))


def test_simulate_with_policy(ctx):
    res = simulate_drafts(ctx, n_sims=2, seed=3, strategy="balanced", policy=PolicyParams("bpa"))
    sm = res["summary"]
    assert sm["policy"] == "bpa"
    # --policy bpa replays exactly the BPA baseline
    for s in res["sims"]:
        assert s["roster_ids"] == s["baseline_bpa_roster_ids"]
    assert sm["edge_category_wins"] == 0.0


def test_harness_policies_on_adp_room(ctx):
    from fantasy.draft import policy_eval as pe
    from fantasy.valuesearch.policies import ADPPolicy, run_draft, sample_room_order

    room = sample_room_order(ctx, np.random.default_rng([0, 11]), 1.0)
    opts = {"params": BAL, "opt": DraftOptimizer(ctx, BAL),
            "opt_valavail": DraftOptimizer(availability_context(ctx, "valavail"), BAL)}
    cache: dict = {}
    rosters = {}
    for spec in ("optimizer", "bpa", "hybrid02", "need20", "opt_valavail"):
        pol = pe.MyPolicy(ctx, spec, opts, cache)
        board = run_draft(ctx, {1: pol, **{t: ADPPolicy(room) for t in range(2, 13)}})
        rosters[spec] = [ctx.ids[r] for r in board.rosters[1]]
        assert len(rosters[spec]) == 13 and len(pol.log) == 13
        assert all(e["chosen"] == pid for e, pid in zip(pol.log, rosters[spec], strict=True))
    # the optimizer payloads are shared through the cache across specs (paired, no recomputation)
    assert any(k[0] == "opt" for k in cache)


def test_harness_summary_paired():
    from fantasy.draft.policy_eval import summarize

    def row(seed, a, b):
        g = lambda x: {"ours": {"p_playoffs": x, "p_title": x / 2, "e_rank": 3 - x},  # noqa: E731
                       "market": {"p_playoffs": x, "p_title": x / 2, "e_rank": 3 - x}}
        return {"seed": seed, "slot": 1, "regime": "adp",
                "policies": {"optimizer": {"grade": g(a)}, "bpa": {"grade": g(b)}}}

    rows = [row(s, 0.5 + 0.01 * s, 0.52 + 0.01 * s) for s in range(5)]
    sm = summarize(rows)["slot1/adp/ours"]
    d = sm["p_playoffs"]["optimizer"]["minus_bpa"]
    assert sm["n"] == 5 and d["mean"] == pytest.approx(-0.02) and d["se"] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------- CLI
@pytest.fixture()
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)


def _cli(*args):
    res = runner.invoke(app, [str(a) for a in args])
    return res.exit_code, (json.loads(res.stdout) if "--json" in args else res.stdout)


def test_cli_recommend_policy(_env, proj_path):
    P = ["--projections", str(proj_path)]
    _cli("draft", "init", "--draft-id", "mock-p", "--mock")
    D = ["--draft-id", "mock-p", "--strategy", "balanced", "--search", "none", "--no-save"]
    code, base = _cli("draft", "recommend", *D, "--json", *P)
    assert code == 0 and base["policy"]["name"] == "optimizer"  # default unchanged, block always present
    assert base["recommendations"][0]["player_id"] == base["policy"]["optimizer_top"]["player_id"]
    code, out = _cli("draft", "recommend", *D, "--policy", "bpa", "--json", *P)
    assert code == 0 and out["policy"]["name"] == "bpa"
    assert out["recommendations"][0]["player_id"] == out["policy"]["bpa_top"]["player_id"]
    assert out["policy"]["optimizer_top"]["player_id"] == base["recommendations"][0]["player_id"]
    code, out = _cli("draft", "recommend", *D, "--policy", "hybrid", "--policy-margin", 0.05, "--json", *P)
    assert code == 0 and out["policy"]["params"] == {"margin": 0.05}
    assert out["compute_seconds"] < 5
    code, out = _cli("draft", "recommend", *D, "--policy", "bogus", "--json", *P)
    assert code == 2 and "bpa" in out["valid"]
    code, out = _cli("draft", "recommend", "--draft-id", "mock-p", "--search", "value", "--policy", "bpa",
                     "--no-save", "--json", *P)
    assert code == 2
    code, out = _cli("draft", "recommend", *D, "--policy", "bpa", *P)  # text mode names both picks
    assert code == 0 and "policy bpa" in out and "optimizer #1" in out


def test_cli_simulate_policy(_env, proj_path):
    code, out = _cli("draft", "simulate", "--sims", 1, "--strategy", "balanced", "--policy", "hybrid",
                     "--no-save", "--json", "--projections", str(proj_path))
    assert code == 0 and out["summary"]["policy"] == "hybrid"


# ---------------------------------------------------------------- availability="sim" (fantasy.draft.room_sim)
def _sim(ctx, room, available=None, current=1, targets=(1, 24, 36), n=400, seed=0):
    from fantasy.draft.room_sim import simulate_availability

    av = np.ones(len(ctx.ids), dtype=bool) if available is None else available
    return simulate_availability(ctx.market_rank, ctx.sd_mult, ctx.value, av, [pk.slot for pk in ctx.order],
                                 current, list(targets), 1, room=room, n_sims=n, seed=seed)


def test_room_sim_basic_properties(ctx):
    for room in ("adp", "mixed", "sym"):
        t = _sim(ctx, room)
        assert set(t) == {1, 24, 36}
        assert np.all(t[1] == 1.0)  # on the clock: everyone on the board is available
        assert np.all((t[36] >= 0) & (t[36] <= t[24] + 1e-12) & (t[24] <= 1))  # monotone in the pick
        # 23 players are gone by #24 (11 opponents x 2 + my #1): the expected count matches
        assert (1 - t[24]).sum() == pytest.approx(23, abs=1e-6)
        assert (1 - t[36]).sum() == pytest.approx(35, abs=1e-6)
    assert np.all(_sim(ctx, "adp", seed=3)[24] == _sim(ctx, "adp", seed=3)[24])  # deterministic per seed
    with pytest.raises(ValueError):
        _sim(ctx, "bogus")


def test_room_sim_taken_players_unavailable(ctx):
    av = np.ones(len(ctx.ids), dtype=bool)
    gone = [ctx.pos[p] for p in ctx.market[:5]]
    av[gone] = False
    t = _sim(ctx, "mixed", available=av, current=6, targets=(24,))
    assert np.all(t[24][gone] == 0.0)


def test_room_sim_adp_ranks_not_positions(ctx):
    """The ADP room drafts by the RANK of the sampled positions: the market's #23 lasts to #24 more often than
    the analytic Normal(position = pick) model says (the reviewer's 0.57 vs 0.47 on the real context)."""
    from fantasy.draft.availability import prob_available

    t = _sim(ctx, "adp", n=3000)
    top = np.argsort(ctx.market_rank)[:40]
    analytic = prob_available(24, 1, ctx.market_rank[top], ctx.sd_mult[top])
    assert abs(float((t[24][top] - analytic).mean())) < 0.1  # same room, small systematic difference
    assert float(t[24][top].sum()) == pytest.approx(40 - 23 + float((1 - t[24])[np.argsort(ctx.market_rank)[40:]].sum()), abs=1e-6)


def test_room_sim_self_pick_modes(ctx):
    """room_self research option: "skip" removes nobody at my picks, "plan" removes the planned row at my later
    picks; the default ("best") is the same table as before."""
    from fantasy.draft.room_sim import simulate_availability

    av = np.ones(len(ctx.ids), dtype=bool)
    slots = [pk.slot for pk in ctx.order]

    def sim(**kw):
        return simulate_availability(ctx.market_rank, ctx.sd_mult, ctx.value, av, slots, 1, [1, 24, 36], 1,
                                     room="mixed", n_sims=200, **kw)

    base, best, skip = sim(), sim(self_pick="best"), sim(self_pick="skip")
    assert all(np.array_equal(base[p], best[p]) for p in base)
    # a sleeper: our best value, market #100 -> the default's simulated me takes him at my own #1 in every run
    sleeper = int(np.argsort(ctx.market_rank)[99])
    val = ctx.value.copy()
    val[sleeper] = val.max() + 1.0
    adp = {m: simulate_availability(ctx.market_rank, ctx.sd_mult, val, av, slots, 1, [1, 24], 1, room="adp",
                                    n_sims=200, self_pick=m) for m in ("best", "skip")}
    assert adp["best"][24][sleeper] == 0.0 and adp["skip"][24][sleeper] > 0.9
    assert (1 - skip[24]).sum() == pytest.approx(22, abs=1e-6)  # only the 22 opponent picks before #24
    assert (1 - skip[36]).sum() == pytest.approx(33, abs=1e-6)
    planned = int(np.argsort(ctx.market_rank)[60])  # a player the room rarely takes before #36
    plan = sim(self_pick="plan", plan={24: planned})
    assert np.array_equal(plan[24], skip[24])  # nothing of mine removed before #24
    assert plan[36][planned] == 0.0 and (1 - plan[36]).sum() == pytest.approx(34, abs=1e-6)
    with pytest.raises(ValueError):
        sim(self_pick="bogus")


def test_optimizer_room_self_option(ctx):
    st = _state_at_24(ctx)
    assert OptimizerParams().room_self == "best"
    for mode in ("skip", "plan"):
        rec = DraftOptimizer(ctx, OptimizerParams(strategy="balanced", room_self=mode, room_sims=100)).recommend(st)
        assert rec["availability"] == f"sim:mixedx100:self={mode}"
        assert rec["recommendations"][0]["player_id"] not in set(st.taken.values())
    with pytest.raises(ValueError):
        DraftOptimizer(ctx, OptimizerParams(room_self="bogus"))


def test_optimizer_sim_availability_default_and_adp_reachable(ctx):
    st = _state_at_24(ctx)
    assert OptimizerParams().availability == "sim" and OptimizerParams().room == "mixed"  # default since 0.4.0
    base = DraftOptimizer(ctx, BAL).recommend(st)
    assert base["availability"] == "adp"  # old behaviour reachable explicitly
    sim = DraftOptimizer(ctx, OptimizerParams(strategy="balanced", availability="sim", room="mixed",
                                              room_sims=200)).recommend(st)
    assert sim["availability"] == "sim:mixedx200"
    taken = set(st.taken.values())
    for rec in (base, sim):
        assert rec["recommendations"][0]["player_id"] not in taken
        assert all(p["player_id"] not in taken for p in rec["planned_picks"])
        assert all(0 <= r["prob_available_next"] <= 1 for r in rec["recommendations"])
    with pytest.raises(ValueError):
        DraftOptimizer(ctx, OptimizerParams(availability="bogus"))


def test_harness_ablation_arms(ctx):
    from fantasy.draft import policy_eval as pe
    from fantasy.valuesearch.policies import ADPPolicy, run_draft, sample_room_order

    room = sample_room_order(ctx, np.random.default_rng([1, 11]), 1.0)
    opts = {"params": BAL, "opt": DraftOptimizer(ctx, BAL)}
    for spec in ("greedycat", "opt_sim", "opt_sim_matchup", "opt_sim_skip"):
        pol = pe.MyPolicy(ctx, spec, opts, {}, regime="adp")
        board = run_draft(ctx, {1: pol, **{t: ADPPolicy(room) for t in range(2, 13)}})
        assert len(board.rosters[1]) == 13 and len(pol.log) == 13
    assert opts["opt_sim:adp"].params.availability == "sim" and opts["opt_sim:adp"].params.room == "adp"
    assert opts["opt_sim_matchup:adp"].params.objective == "matchup"
    assert opts["opt_sim_skip:adp"].params.room_self == "skip" and opts["opt_sim:adp"].params.room_self == "best"


def test_survival_table_and_oracle(ctx):
    from fantasy.draft.policy_eval import OracleOptimizer, survival_table

    n_total = len(ctx.order)
    pos = np.full((4, len(ctx.ids)), n_total + 1, dtype=np.int16)
    for d in range(4):  # the room drafts in market order every time
        for k, pid in enumerate(ctx.market[:n_total]):
            pos[d, ctx.pos[pid]] = k + 1
    S = survival_table(pos, n_total)
    r = ctx.pos[ctx.market[10]]  # drafted at #11
    assert S[10, r] == 1.0 and S[11, r] == 0.0
    o = OracleOptimizer(ctx, BAL, S)
    assert o.prob_available(24, 1, ctx.market_rank[[r]])[0] == 0.0
    assert o.prob_available(11, 1, ctx.market_rank[[r]])[0] == 1.0
    assert o.recommend(DraftState(taken={}, my_slot=1))["recommendations"]


def test_cli_recommend_availability_sim(_env, proj_path):
    P = ["--projections", str(proj_path)]
    _cli("draft", "init", "--draft-id", "mock-s", "--mock")
    D = ["--draft-id", "mock-s", "--strategy", "balanced", "--search", "none", "--no-save"]
    code, out = _cli("draft", "recommend", *D, "--availability", "sim", "--room", "sym", "--json", *P)
    assert code == 0 and out["availability"] == "sim:symx300" and out["compute_seconds"] < 10
    code, out = _cli("draft", "recommend", *D, "--availability", "sim", "--room", "bogus", "--json", *P)
    assert code == 2 and "mixed" in out["valid"]
