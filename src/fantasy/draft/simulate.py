"""Monte Carlo draft simulation.

Opponents draft by a noisy version of the market order (draft position ~ Normal(adp, sd(adp))).
My team drafts with the optimizer; as a baseline the same simulated room is replayed with a
"best player available by value" policy so the optimizer's edge can be measured.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from fantasy.draft.availability import adp_sd
from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.roster import max_starting_matching


def _run_one(ctx: DraftContext, opt: DraftOptimizer | None, sampled_pos: np.ndarray) -> tuple[list[int], str | None]:
    """Run one draft; opt=None → BPA-by-value policy. Returns my roster rows and first-pick strategy."""
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
                rec = opt.recommend(DraftState(taken=dict(taken), my_slot=my_slot))
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
            mine.append(row)
        else:
            row = int(next(r for r in room_order if available[r]))
        available[row] = False
        taken[pk.overall] = ctx.ids[row]
    return mine, strategy


def simulate_drafts(
    ctx: DraftContext, n_sims: int = 5, seed: int = 0, noise: float = 1.0, strategy: str | None = None
) -> dict:
    rng = np.random.default_rng(seed)
    opt = DraftOptimizer(ctx, OptimizerParams(strategy=strategy))
    sims = []
    first_picks: Counter[str] = Counter()
    strategies: Counter[str] = Counter()
    for _ in range(n_sims):
        mr = ctx.market_rank
        sampled = mr + rng.normal(0, 1, len(mr)) * adp_sd(mr) * noise
        mine, strat = _run_one(ctx, opt, sampled)
        base, _ = _run_one(ctx, None, sampled)
        ev, evb = ctx.model.evaluate_team(mine), ctx.model.evaluate_team(base)
        first_picks[ctx.ids[mine[0]]] += 1
        strategies[strat or "?"] += 1
        sims.append({
            "roster": [str(ctx.values.loc[ctx.ids[r], "name"]) for r in mine],
            "expected_category_wins": ev["expected_category_wins"],
            "matchup_win_prob": ev["matchup_win_prob"],
            "cat_win_probs": ev["cat_win_probs"],
            "baseline_bpa_expected_category_wins": evb["expected_category_wins"],
            "baseline_bpa_matchup_win_prob": evb["matchup_win_prob"],
            "strategy": strat,
        })
    ecw = np.array([s["expected_category_wins"] for s in sims])
    bcw = np.array([s["baseline_bpa_expected_category_wins"] for s in sims])
    mwp = np.array([s["matchup_win_prob"] for s in sims])
    bmw = np.array([s["baseline_bpa_matchup_win_prob"] for s in sims])
    summary = {
        "n_sims": n_sims,
        "my_slot": ctx.settings.my_slot,
        "optimizer": {"expected_category_wins_mean": float(ecw.mean()), "expected_category_wins_sd": float(ecw.std()),
                      "matchup_win_prob_mean": float(mwp.mean())},
        "baseline_bpa": {"expected_category_wins_mean": float(bcw.mean()), "matchup_win_prob_mean": float(bmw.mean())},
        "edge_category_wins": float((ecw - bcw).mean()),
        "first_pick_counts": {ctx.values.loc[k, "name"]: v for k, v in first_picks.most_common()},
        "strategy_counts": dict(strategies),
    }
    return {"summary": summary, "sims": sims}
