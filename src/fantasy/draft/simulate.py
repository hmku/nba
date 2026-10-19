"""Monte Carlo draft simulation.

Opponents draft by a noisy version of the market order (draft position ~ Normal(adp, sd(adp))).
My team drafts with the optimizer; as a baseline the same simulated room is replayed with a
"best player available by value" policy so the optimizer's edge can be measured.

`simulate_drafts` returns a display-ready `summary` (schema `draft_sim/1`, documented field by field in
docs/research/dashboard_data.md) plus per-sim detail in `sims`; with `keep_leagues=True` each sim also
carries the full 12-team rosters of the optimizer room (used by `fantasy sim preseason`).
"""

from __future__ import annotations

import time
from collections import Counter

import numpy as np

from fantasy.draft.availability import prob_available, sample_market_positions
from fantasy.draft.optimizer import (
    OPTIMIZER_VERSION,
    DraftContext,
    DraftOptimizer,
    DraftState,
    OptimizerParams,
)
from fantasy.draft.policy import PolicyParams, apply_policy
from fantasy.draft.roster import max_starting_matching

SUMMARY_SCHEMA = "draft_sim/1"
N_AVAIL_PICKS = 6
N_AVAIL_PLAYERS = 40

EXPLANATIONS = {
    "overview": (
        "We replayed the whole 12-team draft many times on the computer. In each replay the other 11 teams "
        "pick and our optimizer makes my picks; the same replay is then re-run with a simple 'take the "
        "best-valued player left' rule (BPA) for my team, so the two strategies face identical opponents."),
    "opponents": (
        "Opponents draft by consensus ADP (average draft position on Yahoo) plus random noise: each player's "
        "draft slot is drawn from Normal(ADP, sd) with sd = 2.5 + 0.18 x ADP picks (wider for players with no "
        "Yahoo ADP; ADP order = market rank, with a model-based slot for players without Yahoo ADP), "
        "scaled by a noise multiplier of {noise}. Opponents never react to my picks or to category needs."),
    "expected_category_wins": (
        "For a finished 13-man roster: the expected number of the 9 categories it wins in a typical week "
        "against a league-average opponent (sum of the 9 per-category win probabilities). 4.5 = coin flip; "
        "each +0.1 is roughly one extra category win every 10 weeks."),
    "matchup_win_prob": (
        "Probability of winning a weekly head-to-head matchup (more categories won than lost) against a "
        "league-average opponent, from the same per-category model."),
    "edge": (
        "Optimizer minus BPA on the same simulated draft rooms (paired). SE = standard error of the mean "
        "difference over sims; an edge smaller than ~2 SE is not distinguishable from zero. "
        "p_optimizer_better = share of sims where the optimizer's roster scored higher, p_tie = share where "
        "both rules produced equally scored (usually identical) rosters."),
    "picks_distribution": (
        "Who I took at each of my picks across the simulations (top 5). Early picks are nearly deterministic; "
        "later picks spread out because the board differs from draft to draft."),
    "availability": (
        "Model probability that the other teams have not yet taken each key player by my first six picks, "
        "from the ADP availability model the optimizer itself uses (computed analytically from pick 1, not "
        "simulated; it ignores that I may have taken him myself earlier). sim_taken_by_me_freq is how often "
        "I actually drafted him in the simulations."),
    "category_win_probs": (
        "Distribution across simulations of my final roster's weekly win probability in each category vs a "
        "league-average team: mean and 10th/90th percentiles. Low values in a category mean the roster "
        "tends to concede it (a de-facto punt)."),
    "example_rosters": (
        "Three full rosters the optimizer produced: the best, median and worst simulation by expected "
        "category wins - a feel for the range of outcomes from slot "),
    "caveats": (
        "In-sample: the optimizer is scored by the same projections and win model it optimizes, so its edge "
        "over BPA is an upper bound. Absolute levels are optimistic: the opponent is a league-average team "
        "drafted by ADP that never manages its roster, and weekly variance ignores schedule and category "
        "correlation. Compare the two strategies relatively; don't read 5.4 cats/week as a forecast. "
        "Variance priors and ADP noise are uncalibrated."),
}


def _run_one(
    ctx: DraftContext, opt: DraftOptimizer | None, sampled_pos: np.ndarray, policy: PolicyParams | None = None
) -> tuple[list[int], str | None, dict[int, str]]:
    """Run one draft; opt=None → BPA-by-value policy; `policy` (fantasy.draft.policy) decides the top pick
    from the optimizer's payload (None = optimizer #1). Returns my roster rows, first-pick strategy and
    the full board (overall pick -> player id)."""
    my_slot = ctx.settings.my_slot
    available = np.ones(len(ctx.ids), dtype=bool)
    taken: dict[int, str] = {}
    mine: list[int] = []
    strategy = None
    room_order = np.argsort(sampled_pos)
    slots = ctx.settings.starting_slots
    my_total = sum(1 for p in ctx.order if p.slot == my_slot)
    for pk in ctx.order:
        if pk.slot == my_slot:
            if opt is not None:
                state = DraftState(taken=dict(taken), my_slot=my_slot)
                rec = opt.recommend(state)
                if policy is not None and policy.name != "optimizer":
                    apply_policy(ctx, rec, state, policy, opt.params)
                row = ctx.pos[rec["recommendations"][0]["player_id"]]
                strategy = strategy or rec["strategy"]["name"]
            else:
                left_after = my_total - len(mine) - 1
                row = -1
                for r in np.flatnonzero(available)[np.argsort(-ctx.value[available])]:
                    f, _ = max_starting_matching([ctx.positions[i] for i in mine + [int(r)]], slots)
                    if len(slots) - f <= left_after:
                        row = int(r)
                        break
                if row < 0:  # no candidate keeps the lineup completable: take best available
                    av = np.flatnonzero(available)
                    row = int(av[np.argmax(ctx.value[av])])
            mine.append(row)
        else:
            row = int(next(r for r in room_order if available[r]))
        available[row] = False
        taken[pk.overall] = ctx.ids[row]
    return mine, strategy, taken


def _paired(a: np.ndarray, b: np.ndarray) -> dict:
    d = a - b
    n = len(d)
    return {"mean": float(d.mean()), "se": float(d.std(ddof=1) / np.sqrt(n)) if n > 1 else None,
            "p_optimizer_better": float((d > 1e-12).mean()), "p_tie": float((np.abs(d) <= 1e-12).mean())}


def _name(ctx: DraftContext, pid: str) -> str:
    return str(ctx.values.loc[pid, "name"])


def _cat_dist(ctx: DraftContext, probs: list[dict]) -> dict:
    out = {}
    for c in ctx.model.cats:
        x = np.array([p[c.key] for p in probs])
        out[c.key] = {"label": c.label, "mean": float(x.mean()), "p10": float(np.percentile(x, 10)),
                      "p90": float(np.percentile(x, 90))}
    return out


def availability_table(
    ctx: DraftContext, my_picks: list[int], taken_counts: Counter[str] | None = None, n_sims: int = 1,
    n_players: int = N_AVAIL_PLAYERS, n_picks: int = N_AVAIL_PICKS, early_counts: Counter[str] | None = None,
    noise: float = 1.0,
) -> dict:
    """P(available at each of my first `n_picks` picks | draft start) for ~`n_players` key targets: the
    first-pick candidates, the players I drafted in the sims, and players whose availability at one of my
    later picks is uncertain (5-95%)."""
    picks = my_picks[:n_picks]
    vrank = {pid: i + 1 for i, pid in enumerate(ctx.values["value"].sort_values(ascending=False).index)}
    counts = taken_counts or Counter()

    def pav(pids: list[str]) -> np.ndarray:
        rows = [ctx.pos[p] for p in pids]
        return np.stack([prob_available(p, 1, ctx.market_rank[rows], ctx.sd_mult[rows] * noise) for p in picks],
                        axis=1)

    # 1) the first-pick candidates, 2) players I drafted at one of these picks in the sims (most often first),
    # 3) players whose availability at one of my later picks is genuinely uncertain, best value first
    chosen = list(ctx.market[:3])
    for pid, _ in (early_counts if early_counts is not None else counts).most_common():
        if len(chosen) >= n_players:
            break
        if pid not in chosen:
            chosen.append(pid)
    pool = [p for p in ctx.market[: max(picks) + 30] if p not in chosen]
    if pool and len(picks) > 1:
        pa_pool = pav(pool)[:, 1:]
        uncertain = [p for p, row in zip(pool, pa_pool, strict=True) if ((row > 0.05) & (row < 0.95)).any()]
        for pid in sorted(uncertain, key=lambda q: vrank[q]):
            if len(chosen) >= n_players:
                break
            chosen.append(pid)
    rows = [ctx.pos[p] for p in chosen]
    pa = pav(chosen)
    players = []
    for k, pid in enumerate(chosen):
        v = ctx.values.loc[pid]
        adp = v.get("adp")
        players.append({
            "player_id": pid, "name": str(v["name"]), "positions": list(v["positions"]),
            "team": str(v["team"]) if isinstance(v.get("team"), str) else None,
            "adp": float(adp) if adp is not None and adp == adp else None,
            "market_rank": int(ctx.market_rank[rows[k]]), "value": float(v["value"]), "value_rank": vrank[pid],
            "p_available": [round(float(x), 4) for x in pa[k]],
            "sim_taken_by_me_freq": round(counts[pid] / max(n_sims, 1), 4),
        })
    players.sort(key=lambda r: r["market_rank"])
    return {"picks": picks, "players": players}


def simulate_drafts(
    ctx: DraftContext, n_sims: int = 5, seed: int = 0, noise: float = 1.0, strategy: str | None = None,
    keep_leagues: bool = False, policy: PolicyParams | None = None,
) -> dict:
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    opt = DraftOptimizer(ctx, OptimizerParams(strategy=strategy))
    my_slot = ctx.settings.my_slot
    my_picks = [p.overall for p in ctx.order if p.slot == my_slot]
    round_of = {p.overall: p.round for p in ctx.order}
    sims = []
    first_picks: Counter[str] = Counter()
    strategies: Counter[str] = Counter()
    by_pick: list[Counter[str]] = [Counter() for _ in my_picks]
    taken_by_me: Counter[str] = Counter()
    for k in range(n_sims):
        sampled = sample_market_positions(ctx.market_rank, rng, ctx.sd_mult, noise)
        mine, strat, board = _run_one(ctx, opt, sampled, policy)
        base, _, _ = _run_one(ctx, None, sampled)
        ev, evb = ctx.model.evaluate_team(mine), ctx.model.evaluate_team(base)
        first_picks[ctx.ids[mine[0]]] += 1
        strategies[strat or "?"] += 1
        for j, r in enumerate(mine):
            by_pick[j][ctx.ids[r]] += 1
            taken_by_me[ctx.ids[r]] += 1
        sim = {
            "sim": k,
            "roster": [_name(ctx, ctx.ids[r]) for r in mine],
            "roster_ids": [ctx.ids[r] for r in mine],
            "expected_category_wins": ev["expected_category_wins"],
            "matchup_win_prob": ev["matchup_win_prob"],
            "cat_win_probs": ev["cat_win_probs"],
            "baseline_bpa_roster_ids": [ctx.ids[r] for r in base],
            "baseline_bpa_expected_category_wins": evb["expected_category_wins"],
            "baseline_bpa_matchup_win_prob": evb["matchup_win_prob"],
            "baseline_bpa_cat_win_probs": evb["cat_win_probs"],
            "strategy": strat,
        }
        if keep_leagues:
            slot_of = {p.overall: p.slot for p in ctx.order}
            teams: dict[int, list[str]] = {t: [] for t in range(1, ctx.settings.num_teams + 1)}
            for ov in sorted(board):
                teams[slot_of[ov]].append(board[ov])
            sim["league"] = teams
        sims.append(sim)
    ecw = np.array([s["expected_category_wins"] for s in sims])
    bcw = np.array([s["baseline_bpa_expected_category_wins"] for s in sims])
    mwp = np.array([s["matchup_win_prob"] for s in sims])
    bmw = np.array([s["baseline_bpa_matchup_win_prob"] for s in sims])
    edge_cw, edge_mw = _paired(ecw, bcw), _paired(mwp, bmw)

    picks_distribution = []
    for j, p in enumerate(my_picks):
        top = [{"player_id": pid, "name": _name(ctx, pid), "count": c, "freq": round(c / n_sims, 4)}
               for pid, c in by_pick[j].most_common(5)]
        picks_distribution.append({"pick": p, "round": round_of[p], "top": top, "n_distinct": len(by_pick[j])})

    order = np.argsort(ecw, kind="stable")
    examples = []
    for label, k in (("best", int(order[-1])), ("median", int(order[len(order) // 2])), ("worst", int(order[0]))):
        s = sims[k]
        examples.append({
            "label": label, "sim": k, "expected_category_wins": s["expected_category_wins"],
            "matchup_win_prob": s["matchup_win_prob"], "strategy": s["strategy"], "cat_win_probs": s["cat_win_probs"],
            "picks": [{"pick": p, "round": round_of[p], "player_id": pid, "name": _name(ctx, pid),
                       "positions": list(ctx.values.loc[pid, "positions"])}
                      for p, pid in zip(my_picks, s["roster_ids"], strict=True)],
        })

    expl = dict(EXPLANATIONS)
    expl["example_rosters"] += f"{my_slot}."
    expl["opponents"] = expl["opponents"].format(noise=noise)
    warnings = []
    if n_sims < 30:
        warnings.append(f"Only {n_sims} simulations: frequencies and percentiles are noisy.")
    summary = {
        "schema": SUMMARY_SCHEMA,
        "scenario": "pre-draft simulation",
        "n_sims": n_sims,
        "my_slot": my_slot,
        "num_teams": ctx.settings.num_teams,
        "num_rounds": len(my_picks),
        "my_picks": my_picks,
        "optimizer_version": OPTIMIZER_VERSION,
        "policy": (policy or PolicyParams()).name,
        "noise": noise,
        "seed": seed,
        "strategy_forced": strategy,
        "optimizer": {"expected_category_wins_mean": float(ecw.mean()), "expected_category_wins_sd": float(ecw.std()),
                      "matchup_win_prob_mean": float(mwp.mean()), "matchup_win_prob_sd": float(mwp.std())},
        "baseline_bpa": {"expected_category_wins_mean": float(bcw.mean()), "expected_category_wins_sd": float(bcw.std()),
                         "matchup_win_prob_mean": float(bmw.mean()), "matchup_win_prob_sd": float(bmw.std())},
        "edge": {"expected_category_wins": edge_cw, "matchup_win_prob": edge_mw},
        # legacy flat fields (CLI / model-eval skill); paired SE: |edge| < 2 SE is noise
        "edge_category_wins": edge_cw["mean"],
        "edge_category_wins_se": edge_cw["se"],
        "first_pick_counts": {_name(ctx, k): v for k, v in first_picks.most_common()},
        "strategy_counts": dict(strategies),
        "picks_distribution": picks_distribution,
        "availability": availability_table(ctx, my_picks, taken_by_me, n_sims,
                                           early_counts=sum(by_pick[:N_AVAIL_PICKS], Counter()), noise=noise),
        "category_win_probs": {"optimizer": _cat_dist(ctx, [s["cat_win_probs"] for s in sims]),
                               "baseline_bpa": _cat_dist(ctx, [s["baseline_bpa_cat_win_probs"] for s in sims])},
        "example_rosters": examples,
        "explanations": expl,
        "warnings": warnings,
        "compute_seconds": round(time.perf_counter() - t0, 1),
    }
    return {"summary": summary, "sims": sims}


def save_draft_sim(res: dict, params: dict, inputs: dict, engine=None) -> str:  # noqa: ANN001
    """Record a simulation as a `model_runs` row (kind `draft_sim`; display payload in `summary`)."""
    import uuid
    from dataclasses import asdict
    from datetime import UTC, datetime

    from sqlalchemy import insert

    from fantasy.db.engine import get_engine
    from fantasy.db.schema import model_runs
    from fantasy.model.noise import NOISE_MODEL_VERSION
    from fantasy.model.valuation import git_sha
    from fantasy.ops.common import json_safe as _clean

    eng = engine or get_engine()
    now = datetime.now(UTC)
    run_id = f"dsim-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    opt_params = {k: v for k, v in asdict(OptimizerParams()).items() if k != "strategies"}
    compact = [{"sim": s["sim"], "ecw": s["expected_category_wins"], "bpa_ecw": s["baseline_bpa_expected_category_wins"],
                "mwp": s["matchup_win_prob"], "bpa_mwp": s["baseline_bpa_matchup_win_prob"], "roster": s["roster_ids"]}
               for s in res["sims"]]
    with eng.begin() as conn:
        conn.execute(insert(model_runs).values(
            run_id=run_id, kind="draft_sim", model_version=OPTIMIZER_VERSION, git_sha=git_sha(),
            params=_clean({**params, "optimizer_params": opt_params, "noise_model": NOISE_MODEL_VERSION}),
            inputs=_clean({**inputs, "per_sim": compact}), summary=_clean(res["summary"]), created_at=now))
    return run_id
