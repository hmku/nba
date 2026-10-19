"""Training data: simulated full leagues labelled by the season simulator.

Each league (seed s) draws a room regime, drafts all 12 rosters, then runs the season simulator
(`n_seasons` seasons, seed s) and records, for *every* team, P(playoffs), P(title), expected final
regular-season rank and expected regular-season category wins. Every team is a training example
(features from its own point of view), so one league gives 12 rows.

Regimes (probabilities in REGIMES):
  adp      opponents draft by noisy ADP (noise multiplier ~ U(0.6, 1.6)); slot 1 (+ with prob 0.3 one
           more random slot) drafts with the optimizer (balanced) plus diversity.
  mixed    slot 1 and k ~ U{2..10} random other slots use the optimizer with diversity; rest noisy ADP.
  sym      all 12 slots use the optimizer with diversity (symmetric room: "everyone uses our strategy").
  proxy    slot 1 optimizer; each other slot independently ADP (50%), GreedyCat proxy (30%) or BPA (20%).
Diversity for optimizer teams: softmax over the top-5 recommendation scores with temperature
~ U(0, 0.03) expected category wins, and with prob eps = 0.1 a uniform choice among the top 5.

Run: python -m fantasy.valuesearch.datagen --n 1500 --out <dir> --workers 3

Generator v2 (`--gen v2`, value-model-0.2.0; docs/research/value_search.md "Value model 0.2.0"): one *room*
per seed, regimes REGIMES_V2 = the policy-eval worlds (`fantasy.draft.policy_eval.build_room`): noisy ADP
(noise multiplier ~ U(0.6, 1.6)), "sharp" opponents = balanced optimizer on their own perturbed projection
view (`perturb_projections`); `mixed` = 3 sharp seats + 8 ADP, `sym` = 11 sharp. Slot 1 (me) drafts with the
optimizer with availability="sim" and the matching room, plus diversity. **Paired completions:** at a random
decision pick among my first 8, K candidates (the optimizer's #1 plus K-1 drawn from its #2-#6) are each
completed with the same room (same opponents, same room order) and my later picks by GreedyCat (as value-search
rollouts) or by the same optimizer (prob 1/2 per room); all K leagues are labelled with the same season seed
(common random numbers), so within-room differences carry little Monte Carlo noise. One record per league
with `room` = seed and `cand` = 0..K-1.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

REGIMES = {"adp": 0.40, "mixed": 0.25, "sym": 0.20, "proxy": 0.15}
LABELS = ["p_playoffs", "p_title", "e_rank", "e_cat_wins"]
REGIMES_V2 = {"mixed": 0.55, "adp": 0.25, "sym": 0.20}
N_SHARP = 3

_W: dict = {}


def _init_worker(projections: str | None = None) -> None:
    from fantasy.draft.optimizer import DraftContext
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context

    s = load_settings()
    proj = load_projections(projections)
    _W["ctx"] = DraftContext(proj, s)
    _W["sctx"] = build_context(proj, s, list(range(1, s.num_teams + 1)))
    _W["proj"] = proj


def season_labels(sctx, rosters: dict[int, list[str]], n_seasons: int, seed: int) -> np.ndarray:  # noqa: ANN001
    """(T, 4) labels in sorted team order, from `n_seasons` simulated seasons."""
    from fantasy.sim.engine import SimParams
    from fantasy.sim.league import LeagueState

    lg = LeagueState(rosters=rosters, my_team=1, source="valuesearch")
    res = sctx.simulator(SimParams(n_sims=n_seasons, seed=seed)).run(lg)
    T = len(res.team_ids)
    champ = np.stack([(res.champion == i) for i in range(T)], axis=1)
    return np.stack([res.playoff.mean(0), champ.mean(0), res.rank.mean(0), res.cat_wins.mean(0)], axis=1)


def draft_league(ctx, seed: int, regime: str | None = None) -> tuple[dict[int, list[int]], dict]:  # noqa: ANN001
    from fantasy.valuesearch.policies import (
        ADPPolicy,
        BPAPolicy,
        GreedyCatPolicy,
        OptimizerPolicy,
        run_draft,
        sample_room_order,
    )

    rng = np.random.default_rng([seed, 17])
    T = ctx.settings.num_teams
    if regime is None:
        names = list(REGIMES)
        regime = names[int(rng.choice(len(names), p=np.array(list(REGIMES.values()))))]
    noise = float(rng.uniform(0.6, 1.6))
    room = sample_room_order(ctx, rng, noise)
    adp = ADPPolicy(room)

    def opt() -> OptimizerPolicy:
        return OptimizerPolicy(ctx, strategy="balanced", temp=float(rng.uniform(0, 0.03)), eps=0.1, k=5,
                               rng=np.random.default_rng(int(rng.integers(2**31))))

    kinds: dict[int, str] = {t: "adp" for t in range(1, T + 1)}
    pol: dict = {t: adp for t in range(1, T + 1)}
    others = list(range(2, T + 1))
    if regime == "adp":
        chosen = [1] + ([int(rng.choice(others))] if rng.random() < 0.3 else [])
    elif regime == "mixed":
        chosen = [1] + [int(x) for x in rng.choice(others, size=int(rng.integers(2, 11)), replace=False)]
    elif regime == "sym":
        chosen = list(range(1, T + 1))
    else:
        chosen = [1]
        greedy, bpa = GreedyCatPolicy(ctx), BPAPolicy()
        for t in others:
            u = rng.random()
            if u < 0.3:
                pol[t], kinds[t] = greedy, "greedy"
            elif u < 0.5:
                pol[t], kinds[t] = bpa, "bpa"
    for t in chosen:
        pol[t], kinds[t] = opt(), "optimizer"
    board = run_draft(ctx, pol)
    return board.rosters, {"regime": regime, "noise": noise, "kinds": kinds}


def make_league(seed: int, n_seasons: int = 160) -> dict:
    ctx, sctx = _W["ctx"], _W["sctx"]
    t0 = time.perf_counter()
    rosters, meta = draft_league(ctx, seed)
    t1 = time.perf_counter()
    ids = {t: [ctx.ids[r] for r in rows] for t, rows in rosters.items()}
    y = season_labels(sctx, ids, n_seasons, seed)
    return {"seed": seed, "rosters": ids, "labels": y.tolist(), **meta, "n_seasons": n_seasons,
            "draft_s": round(t1 - t0, 3), "sim_s": round(time.perf_counter() - t1, 3)}


class _SimOptPolicy:
    """My slot: availability="sim" optimizer (room = the room drafted in) with diversity; draws keyed by
    (seed, slot, overall) so paired completions that reach the same board make the same choice."""

    def __init__(self, opt, seed: int, temp: float, eps: float, k: int = 5) -> None:  # noqa: ANN001
        self.opt, self.seed, self.temp, self.eps, self.k = opt, seed, temp, eps, k

    def recs(self, board, slot: int) -> list[dict]:  # noqa: ANN001
        from fantasy.draft.optimizer import DraftState

        rec = self.opt.recommend(DraftState(taken=dict(board.taken), my_slot=slot))["recommendations"]
        return [r for r in rec if board.available[board.ctx.pos[r["player_id"]]]]

    def pick(self, board, overall: int, slot: int) -> int:  # noqa: ANN001
        recs = self.recs(board, slot)[: self.k]
        rng = np.random.default_rng([self.seed, slot, overall])
        j = 0
        if len(recs) > 1:
            if rng.random() < self.eps:
                j = int(rng.integers(len(recs)))
            elif self.temp > 0:  # softmax over the scores (expected category wins), as OptimizerPolicy
                sc = np.array([r["score"] for r in recs])
                w = np.exp((sc - sc.max()) / self.temp)
                j = int(rng.choice(len(recs), p=w / w.sum()))
        return board.ctx.pos[recs[j]["player_id"]]


def make_room_v2(seed: int, n_seasons: int = 500, k: int = 4) -> list[dict]:
    """v2: one room, K paired completions at a random decision pick of slot 1 (see module doc)."""
    from fantasy.draft.optimizer import DraftContext, DraftOptimizer, OptimizerParams
    from fantasy.draft.policy_eval import ViewOptPolicy
    from fantasy.sim.symmetric import perturb_projections
    from fantasy.valuesearch.policies import (
        ADPPolicy,
        GreedyCatPolicy,
        run_draft,
        sample_room_order,
    )

    ctx, sctx, proj = _W["ctx"], _W["sctx"], _W["proj"]
    s = ctx.settings
    T = s.num_teams
    rng = np.random.default_rng([seed, 29])
    names = list(REGIMES_V2)
    regime = names[int(rng.choice(len(names), p=np.array(list(REGIMES_V2.values()))))]
    noise = float(rng.uniform(0.6, 1.6))
    room = sample_room_order(ctx, rng, noise)
    others = list(range(2, T + 1))
    n_sharp = {"adp": 0, "mixed": N_SHARP, "sym": T - 1}[regime]
    sharp = set(rng.choice(others, n_sharp, replace=False).tolist()) if n_sharp else set()
    cache: dict = {}
    pol: dict = {t: (ViewOptPolicy(ctx, DraftContext(perturb_projections(proj, seed, t), s), cache)
                     if t in sharp else ADPPolicy(room)) for t in others}
    key = f"opt_sim:{regime}"
    if key not in _W:
        _W[key] = DraftOptimizer(ctx, OptimizerParams(strategy="balanced", availability="sim", room=regime))
    me = _SimOptPolicy(_W[key], seed, temp=float(rng.uniform(0, 0.03)), eps=0.1)
    pol[1] = me
    mine = [p.overall for p in ctx.order if p.slot == 1]
    decision = mine[int(rng.integers(min(8, len(mine))))]
    completion = "greedy" if rng.random() < 0.5 else "optimizer"
    t0 = time.perf_counter()
    board = run_draft(ctx, pol, stop=decision - 1)
    recs = [r["player_id"] for r in me.recs(board, 1)]
    pool = recs[1:6]
    cands = recs[:1] + [pool[i] for i in sorted(rng.choice(len(pool), min(k - 1, len(pool)), replace=False))]
    later = GreedyCatPolicy(ctx) if completion == "greedy" else me
    out = []
    for ci, c in enumerate(cands):
        t1 = time.perf_counter()
        b = board.copy()
        b.take(decision, 1, ctx.pos[c])
        b = run_draft(ctx, {**pol, 1: later}, board=b, start=decision + 1)
        ids = {t: [ctx.ids[r] for r in rows] for t, rows in b.rosters.items()}
        t2 = time.perf_counter()
        y = season_labels(sctx, ids, n_seasons, seed)
        kinds = {t: ("sharp" if t in sharp else "adp") for t in others} | {1: "opt_sim"}
        out.append({"seed": seed * 10 + ci, "room": seed, "cand": ci, "cand_id": c, "decision_pick": decision,
                    "completion": completion, "rosters": ids, "labels": y.tolist(), "regime": regime,
                    "noise": noise, "kinds": kinds, "n_seasons": n_seasons, "gen": "v2",
                    "draft_s": round(t2 - t1 + (t1 - t0 if ci == 0 else 0), 3),
                    "sim_s": round(time.perf_counter() - t2, 3)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed0", type=int, default=100_000)
    ap.add_argument("--seasons", type=int, default=160)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--gen", choices=["v1", "v2"], default="v1")
    ap.add_argument("--k", type=int, default=4, help="v2: paired candidate completions per room")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if a.out.exists():
        done = {json.loads(line).get("room", json.loads(line)["seed"]) for line in a.out.read_text().splitlines()
                if line.strip()}
    seeds = [a.seed0 + i for i in range(a.n) if a.seed0 + i not in done]
    from multiprocessing import get_context

    t0 = time.time()
    with get_context("fork").Pool(a.workers, initializer=_init_worker) as pool, a.out.open("a") as fh:
        if a.gen == "v2":
            it = pool.imap_unordered(_job_v2, [(s, a.seasons, a.k) for s in seeds])
        else:
            it = pool.imap_unordered(_job, [(s, a.seasons) for s in seeds], chunksize=2)
        for k, rec in enumerate(it):
            for r in rec if isinstance(rec, list) else [rec]:
                fh.write(json.dumps(r) + "\n")
            fh.flush()
            if (k + 1) % 25 == 0:
                print(f"{k + 1}/{len(seeds)} leagues, {time.time() - t0:.0f}s", flush=True)
    print(f"done {len(seeds)} leagues in {time.time() - t0:.0f}s (pid {os.getpid()})")


def _job(args: tuple[int, int]) -> dict:
    return make_league(*args)


def _job_v2(args: tuple[int, int, int]) -> list[dict]:
    return make_room_v2(*args)


if __name__ == "__main__":
    main()
