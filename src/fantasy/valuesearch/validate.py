"""Paired validation of my draft policy (slot 1): current optimizer vs value search vs BPA, graded by the
FULL season simulator (not the value model).

One draft seed s fixes the noisy-ADP room order, the opponents' private projection views and the season
seed, so the three policies face the same room and the same season noise (paired; SEs are over seeds).
Regimes:
  adp   opponents draft by noisy ADP (as `fantasy draft simulate`)                -> graded with `ours`
  sym   all 11 opponents use our strategy: balanced optimizer on their own perturbed projection view
        (fantasy.sim.symmetric.perturb_projections - "as smart as us with independent errors"; the
        same proxy as the optimism-decomposition C11 room)                         -> graded with `ours`
  market  = the `adp` drafts graded with market-implied truth projections
        (fantasy.sim.market_truth.market_implied_projections)
Policies for me: `optimizer` (DraftOptimizer, default params = `fantasy draft recommend`),
`value_search` (optimizer candidates re-ranked by ValueSearch with default SearchParams), `bpa`.

  python -m fantasy.valuesearch.validate --seeds 0:200 --regimes adp,sym --model <npz> --out val.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

POLICIES = ["optimizer", "value_search", "bpa"]
_E: dict = {}


def _init(model_path: str, n_eval: int) -> None:
    from threadpoolctl import threadpool_limits

    from fantasy.draft.optimizer import DraftContext
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.market_truth import market_implied_projections
    from fantasy.valuesearch.features import Featurizer
    from fantasy.valuesearch.model import ValueModel
    from fantasy.valuesearch.search import ValueSearch

    threadpool_limits(1)
    s = load_settings()
    proj = load_projections()
    ctx = DraftContext(proj, s)
    teams = list(range(1, s.num_teams + 1))
    sctx = build_context(proj, s, teams)
    mt = market_implied_projections(proj, s)
    _E.update(settings=s, proj=proj, ctx=ctx, n_eval=n_eval,
              truths={"ours": sctx, "market": build_context(mt.proj, s, teams, nba=sctx.nba)},
              vs=ValueSearch(ctx, Featurizer(ctx, proj.df), ValueModel.load(model_path)))


class ViewPolicy:
    """Balanced optimizer on a private projection view; returns rows of the base context."""

    def __init__(self, base, view) -> None:  # noqa: ANN001
        from fantasy.draft.optimizer import DraftOptimizer, OptimizerParams

        self.base, self.opt = base, DraftOptimizer(view, OptimizerParams(strategy="balanced", availability="adp"))

    def pick(self, board, overall: int, slot: int) -> int:  # noqa: ANN001
        from fantasy.draft.optimizer import DraftState

        rec = self.opt.recommend(DraftState(taken=dict(board.taken), my_slot=slot))
        for r in rec["recommendations"]:
            pid = r["player_id"]
            if pid in self.base.pos and board.available[self.base.pos[pid]]:
                return self.base.pos[pid]
        av = np.flatnonzero(board.available)
        return int(av[np.argmax(self.base.value[av])])


def _grade(rosters: dict[int, list[str]], truths: list[str], seed: int) -> dict:
    from fantasy.sim.engine import SimParams
    from fantasy.sim.league import LeagueState

    out = {}
    for name in truths:
        res = _E["truths"][name].simulator(SimParams(n_sims=_E["n_eval"], seed=seed)).run(
            LeagueState(rosters=rosters, my_team=1, source="validate"))
        i = res.tindex(1)
        out[name] = {"p_playoffs": float(res.playoff[:, i].mean()), "p_title": float((res.champion == i).mean()),
                     "e_rank": float(res.rank[:, i].mean()), "e_cat_wins": float(res.cat_wins[:, i].mean())}
    return out


def run_seed(job: tuple[int, list[str]]) -> dict:
    from fantasy.draft.optimizer import DraftContext, OptimizerParams
    from fantasy.sim.symmetric import perturb_projections
    from fantasy.valuesearch.policies import (
        ADPPolicy,
        BPAPolicy,
        OptimizerPolicy,
        run_draft,
        sample_room_order,
    )
    from fantasy.valuesearch.search import SearchParams, ValueSearchPolicy

    seed, regimes = job
    ctx, s = _E["ctx"], _E["settings"]
    T = s.num_teams
    t0 = time.perf_counter()
    room = sample_room_order(ctx, np.random.default_rng([seed, 11]), 1.0)
    out: dict = {"seed": seed, "regimes": {}}
    for regime in regimes:
        if regime == "adp":
            opp: dict = {t: ADPPolicy(room) for t in range(2, T + 1)}
            truths = ["ours", "market"]
        elif regime == "sym":
            opp = {t: ViewPolicy(ctx, DraftContext(perturb_projections(_E["proj"], seed, t), s)) for t in range(2, T + 1)}
            truths = ["ours"]
        else:
            raise ValueError(regime)
        res = {}
        for name in POLICIES:
            t1 = time.perf_counter()
            me: OptimizerPolicy | BPAPolicy | ValueSearchPolicy
            if name == "optimizer":
                me = OptimizerPolicy(ctx)
            elif name == "bpa":
                me = BPAPolicy()
            else:
                me = ValueSearchPolicy(_E["vs"], SearchParams(seed=seed), opt_params=OptimizerParams(availability="adp"))
            board = run_draft(ctx, {1: me, **opp})
            rosters = {t: [ctx.ids[r] for r in rows] for t, rows in board.rosters.items()}
            t2 = time.perf_counter()
            res[name] = {"grade": _grade(rosters, truths, seed), "roster": rosters[1], "league": rosters,
                         "draft_s": round(t2 - t1, 2), "grade_s": round(time.perf_counter() - t2, 2)}
            if isinstance(me, ValueSearchPolicy):
                res[name]["log"] = me.log
        out["regimes"][regime] = res
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


def summarize(rows: list[dict]) -> dict:
    """Means and paired differences (± SE over seeds) per regime/truth/metric."""
    out: dict = {}
    for regime, truths in (("adp", ["ours", "market"]), ("sym", ["ours"])):
        rr = [r for r in rows if regime in r["regimes"]]
        if not rr:
            continue
        for truth in truths:
            key = f"{regime}/{truth}"
            out[key] = {"n": len(rr)}
            for metric in ("p_playoffs", "p_title", "e_rank"):
                x = {p: np.array([r["regimes"][regime][p]["grade"][truth][metric] for r in rr]) for p in POLICIES}
                m: dict[str, dict] = {
                    p: {"mean": round(float(v.mean()), 4), "se": round(float(v.std(ddof=1) / np.sqrt(len(v))), 4)}
                    for p, v in x.items()}
                for a, b in (("value_search", "optimizer"), ("optimizer", "bpa"), ("value_search", "bpa")):
                    d = x[a] - x[b]
                    se = float(d.std(ddof=1) / np.sqrt(len(d)))
                    m[f"{a}-{b}"] = {"mean": round(float(d.mean()), 4), "se": round(se, 4),
                                     "z": round(float(d.mean()) / se, 2) if se > 0 else None}
                out[key][metric] = m
        logs = [e for r in rr for e in r["regimes"][regime]["value_search"].get("log", [])]
        if logs:
            out[f"{regime}/search"] = {"picks": len(logs), "agree_rate": round(float(np.mean([e["agrees"] for e in logs])), 3),
                                       "mean_seconds": round(float(np.mean([e["seconds"] for e in logs])), 2),
                                       "max_seconds": round(float(np.max([e["seconds"] for e in logs])), 2),
                                       "same_roster_rate": round(float(np.mean(
                                           [r["regimes"][regime]["value_search"]["roster"] == r["regimes"][regime]["optimizer"]["roster"]
                                            for r in rr])), 3)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0:200")
    ap.add_argument("--regimes", default="adp,sym")
    ap.add_argument("--model", required=True)
    ap.add_argument("--n-eval", type=int, default=400)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.summary:
        rows = [json.loads(x) for x in a.out.read_text().splitlines() if x.strip()]
        print(json.dumps(summarize(rows), indent=1))
        return
    lo, hi = (int(x) for x in a.seeds.split(":"))
    regimes = a.regimes.split(",")
    done = set()
    if a.out.exists():
        done = {json.loads(x)["seed"] for x in a.out.read_text().splitlines() if x.strip()}
    jobs = [(sd, regimes) for sd in range(lo, hi) if sd not in done]
    from multiprocessing import get_context

    t0 = time.time()
    with get_context("fork").Pool(a.workers, initializer=_init, initargs=(a.model, a.n_eval)) as pool, a.out.open("a") as fh:
        for k, rec in enumerate(pool.imap_unordered(run_seed, jobs)):
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{k + 1}/{len(jobs)} seeds, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
