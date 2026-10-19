"""Draft *policies*: how the top pick is chosen from the optimizer's output and the board.

The rollout optimizer (`fantasy.draft.optimizer`) ranks candidates by the expected category wins of the
completed roster vs a league-average opponent. Graded by the full season simulator it lost to plain
best-player-available (docs/research/value_search.md, docs/research/draft_policy.md), so the decision rule
on top of it is a separate, selectable layer:

* `optimizer` - the optimizer's #1 (current default).
* `bpa`       - best available by value (G-score) that keeps the starting lineup completable (the BPA
                baseline of `fantasy draft simulate`).
* `hybrid`    - the optimizer's #1 only if its rollout score beats BPA's by at least `margin` expected
                category wins; otherwise BPA.
* `bpa_need`  - BPA with a light roster-need tiebreak: among feasible players within `eps` value of the
                best, take the one with the best one-step category objective (roster + player + market
                filler for my remaining picks, no lookahead).
* `opt_valavail` - the optimizer with availability predicted from *our value rank* instead of ADP (a
                room that drafts like us); `opt_sd50` - ADP availability with half the spread.

All functions are deterministic for a given board. `choose()` is what `fantasy draft recommend --policy`
uses; the simulation harness (`fantasy.draft.policy_eval`) wraps the same functions.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace

import numpy as np

from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.roster import max_starting_matching

# 0.2.0: opt_valavail / opt_sd50 pin availability="adp" (the optimizer default became "sim" in optimizer-0.4.0)
POLICY_VERSION = "draft-policy-0.2.0"
POLICIES = ("optimizer", "bpa", "hybrid", "bpa_need", "opt_valavail", "opt_sd50")
DEFAULT_POLICY = "optimizer"  # unchanged default; see docs/research/draft_policy.md before switching
DEFAULT_MARGIN = 0.02  # hybrid: expected category wins (of 9) the optimizer must gain over BPA (prior)
DEFAULT_EPS = 0.25  # bpa_need: value (G-score) window for the roster-need tiebreak (prior)


@dataclass
class PolicyParams:
    name: str = DEFAULT_POLICY
    margin: float = DEFAULT_MARGIN
    eps: float = DEFAULT_EPS


# ---------------------------------------------------------------- board helpers
def board_state(ctx: DraftContext, taken: dict[int, str], my_slot: int) -> tuple[np.ndarray, list[int], int]:
    """(available mask, my roster rows, my picks left after the next one)."""
    available = np.ones(len(ctx.ids), dtype=bool)
    for pid in taken.values():
        if pid in ctx.pos:
            available[ctx.pos[pid]] = False
    my_all = [p.overall for p in ctx.order if p.slot == my_slot]
    roster = [ctx.pos[taken[p]] for p in my_all if p in taken and taken[p] in ctx.pos]
    left_after = len(my_all) - len(roster) - 1
    return available, roster, left_after


def feasible(ctx: DraftContext, roster: list[int], row: int, left_after: int) -> bool:
    slots = ctx.settings.starting_slots
    f, _ = max_starting_matching([ctx.positions[i] for i in roster + [int(row)]], slots)
    return len(slots) - f <= left_after


def bpa_row(ctx: DraftContext, available: np.ndarray, roster: list[int], left_after: int) -> int:
    """Highest-value available player keeping the starting lineup completable (else highest value)."""
    av = np.flatnonzero(available)
    order = av[np.argsort(-ctx.value[av], kind="stable")]
    for r in order:
        if feasible(ctx, roster, int(r), left_after):
            return int(r)
    return int(order[0])


def need_row(ctx: DraftContext, available: np.ndarray, roster: list[int], left_after: int,
             remaining_picks: list[int], eps: float) -> int:
    """`bpa_need`: among feasible players within `eps` value of the best feasible one, the best one-step
    category objective (expected category wins of roster + player + market filler for `remaining_picks`)."""
    best = bpa_row(ctx, available, roster, left_after)
    av = np.flatnonzero(available & (ctx.value >= ctx.value[best] - eps))
    cand = np.array([r for r in av if feasible(ctx, roster, int(r), left_after)] or [best])
    if len(cand) == 1:
        return int(cand[0])
    a = ctx.arrays
    fs, fv, fr = ctx.filler_sums(remaining_picks)
    if roster:
        idx = np.array(roster)
        fs, fv, fr = fs + a.S[idx].sum(0), fv + a.V[idx].sum(0), fr + a.R[idx].sum(0)
    P = ctx.model.win_probs(fs[None] + a.S[cand], fv[None] + a.V[cand], fr[None] + a.R[cand])
    obj = P.sum(axis=1)
    return int(cand[int(np.argmax(obj))])


def availability_context(ctx: DraftContext, mode: str) -> DraftContext:
    """A shallow copy of `ctx` whose availability model assumes a sharper room. Only `market_rank` /
    `sd_mult` change (used by prob_available and the market candidates); values, the category model and
    the filler (market order) are shared."""
    c = copy.copy(ctx)
    if mode == "valavail":  # the room drafts by our value rank
        rank = np.empty(len(ctx.ids))
        rank[np.argsort(-ctx.value, kind="stable")] = np.arange(1, len(ctx.ids) + 1)
        c.market_rank = rank
        c.sd_mult = np.ones(len(ctx.ids))
    elif mode == "sd50":
        c.sd_mult = ctx.sd_mult * 0.5
    else:
        raise ValueError(f"unknown availability mode {mode!r}")
    return c


# ---------------------------------------------------------------- decision
def _rec_index(recs: list[dict], pid: str) -> int | None:
    return next((i for i, r in enumerate(recs) if r["player_id"] == pid), None)


def decide(ctx: DraftContext, rec: dict, taken: dict[int, str], my_slot: int, params: PolicyParams,
           alt_rec: dict | None = None) -> dict:
    """Apply a policy to an optimizer recommendation payload for the same board.

    Returns {"player_id", "bpa_player_id", "optimizer_player_id", "rule"}; `alt_rec` is the payload of the
    alternative-availability optimizer for the opt_* policies."""
    available, roster, left_after = board_state(ctx, taken, my_slot)
    recs = rec["recommendations"]
    opt_pid = recs[0]["player_id"]
    bpa_pid = ctx.ids[bpa_row(ctx, available, roster, left_after)]
    name = params.name
    if name == "optimizer":
        pid, rule = opt_pid, "optimizer #1 (rollout score)"
    elif name == "bpa":
        pid, rule = bpa_pid, "best available by value keeping the lineup completable"
    elif name == "hybrid":
        j = _rec_index(recs, bpa_pid)
        gain = None if j is None else recs[0]["score"] - recs[j]["score"]
        if gain is not None and gain < params.margin:
            pid = bpa_pid
            rule = f"BPA: optimizer #1 gains {gain:+.3f} < margin {params.margin} expected category wins"
        else:
            pid = opt_pid
            rule = ("optimizer #1: BPA not among scored candidates" if gain is None else
                    f"optimizer #1: gains {gain:+.3f} >= margin {params.margin} expected category wins over BPA")
    elif name == "bpa_need":
        my_all = [p.overall for p in ctx.order if p.slot == my_slot]
        current = max(taken, default=0) + 1
        future = [p for p in my_all if p >= current and p not in taken]
        pid = ctx.ids[need_row(ctx, available, roster, left_after, future[1:], params.eps)]
        rule = f"BPA with roster-need tiebreak within {params.eps} value"
    elif name in ("opt_valavail", "opt_sd50"):
        if alt_rec is None:
            raise ValueError(f"policy {name} needs alt_rec")
        pid, rule = alt_rec["recommendations"][0]["player_id"], f"optimizer #1 with availability mode {name[4:]}"
    else:
        raise ValueError(f"unknown policy {name!r}")
    return {"player_id": pid, "bpa_player_id": bpa_pid, "optimizer_player_id": opt_pid, "rule": rule}


def alt_optimizer(ctx: DraftContext, policy: str, params: OptimizerParams | None = None) -> DraftOptimizer | None:
    # these policies are variants of the analytic Normal(ADP) availability model: pin availability="adp"
    # (the optimizer default is "sim" since optimizer-0.4.0)
    params = replace(params or OptimizerParams(), availability="adp")
    if policy == "opt_valavail":
        return DraftOptimizer(availability_context(ctx, "valavail"), params)
    if policy == "opt_sd50":
        return DraftOptimizer(availability_context(ctx, "sd50"), params)
    return None


def _brief(ctx: DraftContext, pid: str, recs: list[dict]) -> dict:
    v = ctx.values.loc[pid]
    j = _rec_index(recs, pid)
    return {"player_id": pid, "name": str(v["name"]), "positions": list(v["positions"]),
            "value": round(float(v["value"]), 3), "rank": int(v["rank"]),
            "market_rank": int(ctx.market_rank[ctx.pos[pid]]),
            "optimizer_score": None if j is None else recs[j]["score"],
            "optimizer_rank": None if j is None else j + 1}


def apply_policy(ctx: DraftContext, rec: dict, state: DraftState, params: PolicyParams,
                 opt_params: OptimizerParams | None = None) -> dict:
    """Decide with `params`, move the chosen player to the top of `rec["recommendations"]` (in place) and
    add a `policy` block with the BPA and optimizer top picks side by side. Returns `rec`."""
    recs = rec.get("recommendations") or []
    if not recs:
        rec["policy"] = {"name": params.name, "version": POLICY_VERSION, "top_pick": None}
        return rec
    alt = alt_optimizer(ctx, params.name, opt_params)
    alt_rec = alt.recommend(state) if alt is not None else None
    d = decide(ctx, rec, state.taken, state.my_slot, params, alt_rec)
    pid = d["player_id"]
    j = _rec_index(recs, pid)
    if j is None:  # e.g. BPA outside the optimizer's scored candidates: minimal entry
        v = ctx.values.loc[pid]
        recs.insert(0, {"player_id": pid, "name": str(v["name"]), "team": "", "positions": list(v["positions"]),
                        "score": None, "value": round(float(v["value"]), 3), "rank": int(v["rank"]),
                        "market_rank": int(ctx.market_rank[ctx.pos[pid]]), "marginal_value": None,
                        "adp": None if v["adp"] != v["adp"] else float(v["adp"]), "plan": [],
                        "reason": "", "not_scored_by_optimizer": True})
    elif j > 0:
        recs.insert(0, recs.pop(j))
    if params.name != "optimizer":
        recs[0]["reason"] = f"policy {params.name}: {d['rule']}; " + (recs[0].get("reason") or "")
    rec["policy"] = {
        "name": params.name, "version": POLICY_VERSION, "rule": d["rule"],
        "params": {"margin": params.margin} if params.name == "hybrid" else
                  {"eps": params.eps} if params.name == "bpa_need" else {},
        "top_pick": pid, "top_pick_name": str(ctx.values.loc[pid, "name"]),
        "agrees_with_optimizer": pid == d["optimizer_player_id"],
        "agrees_with_bpa": pid == d["bpa_player_id"],
        "optimizer_top": _brief(ctx, d["optimizer_player_id"], recs),
        "bpa_top": _brief(ctx, d["bpa_player_id"], recs),
        "alt_optimizer_top": None if alt_rec is None else _brief(ctx, alt_rec["recommendations"][0]["player_id"], recs),
        "evidence": "docs/research/draft_policy.md",
    }
    return rec
