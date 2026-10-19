"""Mid-draft advisory: season P(playoffs)/P(title) if I take each candidate at my next pick.

For each candidate c:
  1. my plan = c + the optimizer's expected-value rollout for my later picks (balanced strategy,
     fantasy.draft.optimizer.DraftOptimizer.rollout), i.e. the same planned picks `recommend` shows;
  2. every other remaining pick is filled from a noisy market (ADP) order (same seed for every
     candidate, c reserved for me), respecting positions loosely (fantasy.sim.league.complete_rosters);
  3. steps 2-3 are repeated for K completions (seeds seed..seed+K-1, sims split evenly), so the SEs
     include uncertainty about the other rosters; every candidate uses the same seeds (common random
     numbers), so differences between candidates are paired; SEs of the differences vs the first
     candidate are reported.
Evaluation only: it does not feed `fantasy draft recommend`.
"""

from __future__ import annotations

import time

import numpy as np

from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.sim.context import SimContext
from fantasy.sim.engine import SimParams, SimResult
from fantasy.sim.league import complete_rosters, from_draft, remaining_order


def draft_check(
    draft: dict, picks: list[dict], sctx: SimContext, candidates: list[str], params: SimParams,
    dctx: DraftContext | None = None, completion_noise: float = 1.0, completions: int = 4,
) -> dict:
    t0 = time.perf_counter()
    settings = sctx.settings
    custom = (draft.get("config") or {}).get("custom_rounds")
    if dctx is None:
        ds = settings.model_copy(update={"num_teams": int(draft["num_teams"]), "my_slot": int(draft["my_slot"]),
                                         "draft_order_type": draft["order_type"]})
        dctx = DraftContext(sctx.proj, ds, values=sctx.values, custom_rounds=custom)
    opt = DraftOptimizer(dctx, OptimizerParams(strategy="balanced", availability="adp"))
    taken = {int(p["overall_pick"]): p["player_id"] for p in picks if p.get("player_id")}
    my_slot = int(draft["my_slot"])
    state = DraftState(taken=taken, my_slot=my_slot)
    current = state.current_pick(len(dctx.order))
    available = np.ones(len(dctx.ids), dtype=bool)
    for pid in taken.values():
        if pid in dctx.pos:
            available[dctx.pos[pid]] = False
    my_all = opt.my_picks(my_slot)
    roster_rows = [dctx.pos[taken[p]] for p in my_all if p in taken and taken[p] in dctx.pos]
    future = [p for p in my_all if p >= current and p not in taken]
    if not future:
        raise ValueError("no remaining picks for my slot")
    later = future[1:]
    w = dctx.weights([])
    base_state = from_draft(draft, picks, settings)
    remaining = remaining_order(draft, picks)
    K = max(1, min(completions, params.n_sims))
    per = max(1, params.n_sims // K)
    sims = [sctx.simulator(SimParams(**{**params.to_dict(), "n_sims": per, "seed": params.seed + k})) for k in range(K)]
    rows = []
    po_all: list[np.ndarray] = []
    ti_all: list[np.ndarray] = []
    for c in candidates:
        if c in taken.values():
            raise ValueError(f"{c} is already drafted")
        plan_rows: list[int] = []
        if c in dctx.pos:
            av = available.copy()
            av[dctx.pos[c]] = False
            plan_rows, _, _ = opt.rollout(roster_rows + [dctx.pos[c]], av, later, current, w)
        plan = [c] + [dctx.ids[r] for r in plan_rows]
        po, ti, cw = [], [], []
        mine: list[str] = []
        for k, sim in enumerate(sims):  # K independent completions of the other rosters
            league = complete_rosters(base_state, remaining, sctx.values, sctx.market, settings,
                                      seed=params.seed + k, noise=completion_noise, my_plan=plan, reserved={c})
            res: SimResult = sim.run(league)
            i = res.tindex(my_slot)
            po.append(res.playoff[:, i].astype(float))
            ti.append((res.champion == i).astype(float))
            cw.append(res.cat_wins[:, i])
            mine = mine or league.rosters[my_slot]
        po_c, ti_c = np.concatenate(po), np.concatenate(ti)
        po_all.append(po_c)
        ti_all.append(ti_c)
        n = len(po_c)
        rows.append({
            "player_id": c, "name": str(sctx.values.loc[c, "name"]) if c in sctx.values.index else c,
            "p_playoffs": round(float(po_c.mean()), 4), "p_playoffs_se": round(float(po_c.std() / np.sqrt(n)), 4),
            "p_title": round(float(ti_c.mean()), 4), "p_title_se": round(float(ti_c.std() / np.sqrt(n)), 4),
            "exp_cat_wins": round(float(np.concatenate(cw).mean()), 2),
            "my_completed_roster": mine,
            "planned_picks": plan[1:],
        })
    # paired differences vs the first candidate (same completions seeds and season seeds)
    for r, po_c, ti_c in zip(rows, po_all, ti_all, strict=True):
        for key, x, y in (("p_playoffs", po_all[0], po_c), ("p_title", ti_all[0], ti_c)):
            d = y - x
            r[f"{key}_diff_vs_first"] = round(float(d.mean()), 4)
            r[f"{key}_diff_se"] = round(float(d.std(ddof=1) / np.sqrt(len(d))), 4) if len(d) > 1 else None
    return {
        "as_of_pick": current - 1, "for_pick": future[0], "my_later_picks": later,
        "n_sims": per * K, "completions": K, "seed": params.seed, "candidates": rows,
        "compute_seconds": round(time.perf_counter() - t0, 2),
        "method": ("my plan = optimizer rollout (balanced); others = noisy market order, K independent completions "
                   "(sims split across them); same seeds for every candidate, so differences are paired"),
    }
