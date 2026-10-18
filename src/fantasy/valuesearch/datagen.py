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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed0", type=int, default=100_000)
    ap.add_argument("--seasons", type=int, default=160)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if a.out.exists():
        done = {json.loads(line)["seed"] for line in a.out.read_text().splitlines() if line.strip()}
    seeds = [a.seed0 + i for i in range(a.n) if a.seed0 + i not in done]
    from multiprocessing import get_context

    t0 = time.time()
    with get_context("fork").Pool(a.workers, initializer=_init_worker) as pool, a.out.open("a") as fh:
        for k, rec in enumerate(pool.imap_unordered(_job, [(s, a.seasons) for s in seeds], chunksize=2)):
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if (k + 1) % 25 == 0:
                print(f"{k + 1}/{len(seeds)} leagues, {time.time() - t0:.0f}s", flush=True)
    print(f"done {len(seeds)} leagues in {time.time() - t0:.0f}s (pid {os.getpid()})")


def _job(args: tuple[int, int]) -> dict:
    return make_league(*args)


if __name__ == "__main__":
    main()
