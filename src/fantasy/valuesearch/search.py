"""Draft-time value search (`fantasy draft recommend --search value`).

For each of the top-N candidates of the current optimizer and each of M rollouts:
  1. take the candidate at my decision pick (if I'm not on the clock and he's gone by then, my pick
     falls back to the GreedyCat policy - the plan simply failed in that rollout);
  2. complete the whole draft: opponents by the opponent model - `adp` (default: next available of a
     noisy-ADP room order, as in `draft simulate`) or `ours` (GreedyCat proxy of our strategy for every
     opponent; ~40x slower); my later picks by GreedyCat (one-step category-model objective with filler);
  3. featurize the 12 completed rosters and score my team with the value model.
Rollout m uses the same room order for every candidate (common random numbers), so candidate
differences are paired; SE = sd(paired difference) / sqrt(M).
Decision: the candidate with the best mean objective replaces the optimizer's #1 only if its paired
gain is >= z_min standard errors (default 1.0); otherwise the optimizer's #1 stands.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import numpy as np

from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.valuesearch import VALUE_SEARCH_VERSION
from fantasy.valuesearch.features import Featurizer
from fantasy.valuesearch.model import ValueModel, objective_value
from fantasy.valuesearch.policies import Board, GreedyCatPolicy, sample_room_order

OPPONENT_MODELS = ("adp", "ours")
SEARCH_MODES = ("none", "advise", "value")
# Adoption decision: "value" (user-approved 2026-10-19 with availability sim/mixed; docs/research/draft_policy.md)
DEFAULT_SEARCH = "value"


@dataclass
class SearchParams:
    n_candidates: int = 6
    rollouts: int = 48
    opponents: str = "adp"
    objective: str = "blend"
    noise: float = 1.0
    seed: int = 0
    z_min: float = 1.0


class ValueSearch:
    def __init__(self, ctx: DraftContext, featurizer: Featurizer, model: ValueModel) -> None:
        self.ctx = ctx
        self.feat = featurizer
        self.model = model
        self.greedy = GreedyCatPolicy(ctx)
        if model.feature_names and model.feature_names != featurizer.names:
            raise ValueError("value model features do not match the featurizer (retrain or update the artifact)")

    def _complete(self, board: Board, remaining: list[tuple[int, int]], my_slot: int, decision: int,
                  cand: int, room: np.ndarray, opponents: str) -> dict[int, list[int]]:
        b = board.copy()
        ptr = 0
        av = b.available
        for ov, slot in remaining:
            if slot == my_slot:
                row = cand if (ov == decision and av[cand]) else self.greedy.pick(b, ov, slot)
            elif opponents == "ours":
                row = self.greedy.pick(b, ov, slot)
            else:
                while not av[room[ptr]]:
                    ptr += 1
                row = int(room[ptr])
            b.take(ov, slot, row)
        return b.rosters

    def run(self, taken: dict[int, str], my_slot: int, candidates: list[str],
            params: SearchParams | None = None) -> dict:
        p = params or SearchParams()
        if p.opponents not in OPPONENT_MODELS:
            raise ValueError(f"unknown opponent model {p.opponents!r}; valid: {', '.join(OPPONENT_MODELS)}")
        t0 = time.perf_counter()
        ctx = self.ctx
        board = Board.from_taken(ctx, taken)
        current = max(taken, default=0) + 1
        remaining = [(pk.overall, pk.slot) for pk in ctx.order if pk.overall >= current and pk.overall not in taken]
        mine = [ov for ov, s in remaining if s == my_slot]
        if not mine:
            raise ValueError("no remaining picks for my slot")
        decision = mine[0]
        cands = [c for c in candidates if c in ctx.pos and board.available[ctx.pos[c]]][: p.n_candidates]
        if not cands:
            raise ValueError("no available candidates")
        rows = [ctx.pos[c] for c in cands]
        rng = np.random.default_rng([p.seed, current])
        leagues = []
        T = ctx.settings.num_teams
        for _ in range(p.rollouts):
            room = sample_room_order(ctx, rng, p.noise)
            for r in rows:
                ros = self._complete(board, remaining, my_slot, decision, r, room, p.opponents)
                leagues.append([ros[t] for t in range(1, T + 1)])
        X = self.feat.transform(self.feat.pad(leagues))[:, my_slot - 1, :]
        pred = self.model.predict(X)
        N, M = len(rows), p.rollouts
        obj = objective_value(pred, p.objective).reshape(M, N).T  # (N, M)
        preds = {k: v.reshape(M, N).T for k, v in pred.items()}
        means = obj.mean(axis=1)
        best = int(np.argmax(means))
        out_c = []
        for i, c in enumerate(cands):
            d = obj[i] - obj[0]
            se = float(d.std(ddof=1) / np.sqrt(M)) if M > 1 else None
            out_c.append({
                "player_id": c, "name": str(ctx.values.loc[c, "name"]), "optimizer_rank": candidates.index(c) + 1,
                "objective": round(float(means[i]), 4),
                "p_playoffs": round(float(preds["p_playoffs"][i].mean()), 4),
                "p_title": round(float(preds["p_title"][i].mean()), 4),
                "e_rank": round(float(preds["e_rank"][i].mean()), 3),
                "diff_vs_optimizer_top": round(float(d.mean()), 4),
                "diff_se": None if se is None else round(se, 4),
                "z": None if not se else round(float(d.mean()) / se, 2),
            })
        z_best = out_c[best]["z"] or 0.0
        chosen = best if best != 0 and z_best >= p.z_min else 0
        ranked = sorted(range(N), key=lambda i: -means[i])
        return {
            "version": VALUE_SEARCH_VERSION, "model_version": self.model.version, "for_pick": decision,
            "params": asdict(p), "candidates": [out_c[i] for i in ranked],
            "top_pick": cands[chosen], "top_pick_name": out_c[chosen]["name"],
            "best_by_mean": cands[best], "agrees_with_optimizer": chosen == 0,
            "decision_rule": f"override the optimizer's #1 only if paired gain >= {p.z_min} SE",
            "compute_seconds": round(time.perf_counter() - t0, 2),
            "basis": ("value model trained on simulated leagues (season simulator labels); opponents "
                      f"{p.opponents}; my later picks GreedyCat; in-sample w.r.t. our projections"),
        }


class ValueSearchPolicy:
    """Drafting policy for simulations: optimizer candidates (default params) re-ranked by value search."""

    def __init__(self, vs: ValueSearch, params: SearchParams | None = None,
                 opt_params: OptimizerParams | None = None) -> None:
        self.vs = vs
        self.params = params or SearchParams()
        self.opt = DraftOptimizer(vs.ctx, opt_params or OptimizerParams())
        self.log: list[dict] = []

    def pick(self, board: Board, overall: int, slot: int) -> int:
        rec = self.opt.recommend(DraftState(taken=dict(board.taken), my_slot=slot))
        cands = [r["player_id"] for r in rec["recommendations"]]
        res = self.vs.run(board.taken, slot, cands, self.params)
        self.log.append({"pick": overall, "optimizer_top": cands[0], "chosen": res["top_pick"],
                         "agrees": res["agrees_with_optimizer"], "seconds": res["compute_seconds"]})
        return self.vs.ctx.pos[res["top_pick"]]
