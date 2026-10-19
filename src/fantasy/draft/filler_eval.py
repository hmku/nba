"""Paired evaluation of the optimizer's rollout *placeholders* (research; docs/research/optimizer_placeholders.md).

Variants of `OptimizerParams` (all opt-in; the default is `market`, refine 0):
  opt_market   filler="market"     (the default optimizer)
  opt_board    filler="board"      (market window re-centred on the actual board)
  opt_avail    filler="available"  (expected best-available-by-value at each later pick)
  opt_ref1/2   filler="market", refine_passes=1/2 (complement-aware fixed point)
  opt_avref1   filler="available", refine_passes=1
plus `bpa` (lineup-feasible best value) as the reference.

Same design as `fantasy.draft.policy_eval` (whose opponent policies it imports): one job = (draft seed, my
slot, regime); the seed fixes the noisy-ADP room order (rng([s, 11])), the opponents' private views
(perturb_projections(proj, s, team)) and the season seed, so variants are paired (common random numbers).
Every final league is graded by the full season simulator under truth `ours` and `market`.

On the default variant's draft path, every variant's #1 is also computed at each of my picks (same board), so
"how often does the top recommendation change" is measured on identical boards; those payloads are cached and
reused when a variant's own draft shares that prefix.

  python -m fantasy.draft.filler_eval run --seeds 0:100 --slots 1 --regimes adp,symbpa,sym --out x.jsonl
  python -m fantasy.draft.filler_eval summary --out x.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.policy import board_state, bpa_row

EVAL_VERSION = "filler-eval-0.1.0"
VARIANTS: dict[str, dict] = {
    "opt_market": {},
    "opt_board": {"filler": "board"},
    "opt_avail": {"filler": "available"},
    "opt_ref1": {"refine_passes": 1},
    "opt_ref2": {"refine_passes": 2},
    "opt_avref1": {"filler": "available", "refine_passes": 1},
}
SPECS = ("bpa", *VARIANTS)
TRUTHS = ("ours", "market")
_E: dict = {}


class VariantPolicy:
    """My slot: an optimizer variant's #1 (or BPA). Payloads are memoised per (variant, board) in `cache`."""

    def __init__(self, spec: str, probe: bool, cache: dict) -> None:
        self.spec, self.probe, self.cache = spec, probe, cache
        self.log: list[dict] = []

    def _top(self, variant: str, taken: dict[int, str], slot: int) -> tuple[str, float]:
        key = ("var", variant, slot, tuple(taken[k] for k in sorted(taken)))
        if key not in self.cache:
            t0 = time.perf_counter()
            rec = _E["opts"][variant].recommend(DraftState(taken=dict(taken), my_slot=slot))
            self.cache[key] = (rec["recommendations"][0]["player_id"], time.perf_counter() - t0)
        return self.cache[key]

    def pick(self, board, overall: int, slot: int) -> int:  # noqa: ANN001
        ctx = _E["ctx"]
        taken = board.taken
        available, roster, left_after = board_state(ctx, taken, slot)
        bpa = ctx.ids[bpa_row(ctx, available, roster, left_after)]
        entry: dict = {"pick": overall, "bpa": bpa}
        if self.probe:  # every variant's #1 on this same board
            entry["tops"] = {}
            entry["secs"] = {}
            for v in VARIANTS:
                entry["tops"][v], entry["secs"][v] = self._top(v, taken, slot)
            entry["secs"] = {k: round(x, 3) for k, x in entry["secs"].items()}
        pid = bpa if self.spec == "bpa" else self._top(self.spec, taken, slot)[0]
        entry["chosen"] = pid
        self.log.append(entry)
        return ctx.pos[pid]


def init_worker(proj_path: str | None, n_eval: int, opt_strategy: str | None, availability: str = "adp") -> None:
    from threadpoolctl import threadpool_limits

    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.market_truth import market_implied_projections

    threadpool_limits(1)
    s = load_settings()
    proj = load_projections(proj_path)
    ctx = DraftContext(proj, s)
    teams = list(range(1, s.num_teams + 1))
    ours = build_context(proj, s, teams)
    mt = market_implied_projections(proj, s)
    opts = {v: DraftOptimizer(ctx, OptimizerParams(strategy=opt_strategy, availability=availability, **kw))
            for v, kw in VARIANTS.items()}
    _E.clear()
    _E.update(settings=s, proj=proj, ctx=ctx, n_eval=n_eval, opts=opts,
              truths={"ours": ours, "market": build_context(mt.proj, s, teams, nba=ours.nba)},
              meta={"projections": proj.path, "projections_sha256": proj.sha256, "opt_strategy": opt_strategy,
                    "availability": availability, "n_eval": n_eval, "eval_version": EVAL_VERSION})


def grade(rosters: dict[int, list[str]], me: int, seed: int) -> dict:
    from fantasy.sim.engine import SimParams
    from fantasy.sim.league import LeagueState

    out = {}
    for name in TRUTHS:
        res = _E["truths"][name].simulator(SimParams(n_sims=_E["n_eval"], seed=seed)).run(
            LeagueState(rosters=rosters, my_team=me, source="filler_eval"))
        i = res.tindex(me)
        out[name] = {"p_playoffs": float(res.playoff[:, i].mean()), "p_title": float((res.champion == i).mean()),
                     "e_rank": float(res.rank[:, i].mean())}
    return out


def run_job(job: tuple[int, int, str, list[str]]) -> dict:
    from fantasy.draft.policy_eval import ViewBPAPolicy, ViewOptPolicy
    from fantasy.sim.symmetric import perturb_projections
    from fantasy.valuesearch.policies import ADPPolicy, run_draft, sample_room_order

    seed, me, regime, specs = job
    ctx, s = _E["ctx"], _E["settings"]
    t0 = time.perf_counter()
    cache: dict = {}
    room = sample_room_order(ctx, np.random.default_rng([seed, 11]), 1.0)
    others = [t for t in range(1, s.num_teams + 1) if t != me]
    if regime == "adp":
        opp: dict = {t: ADPPolicy(room) for t in others}
    elif regime in ("sym", "symbpa"):
        views = {t: DraftContext(perturb_projections(_E["proj"], seed, t), s) for t in others}
        opp = ({t: ViewOptPolicy(ctx, views[t], cache) for t in others} if regime == "sym"
               else {t: ViewBPAPolicy(ctx, views[t]) for t in others})
    else:
        raise ValueError(regime)
    out: dict = {"seed": seed, "slot": me, "regime": regime, "policies": {}, **_E["meta"]}
    # the default variant first, probing every variant on its path (fills the cache for shared prefixes)
    for spec in sorted(specs, key=lambda x: x != "opt_market"):
        t1 = time.perf_counter()
        pol = VariantPolicy(spec, spec == "opt_market", cache)
        board = run_draft(ctx, {me: pol, **opp})
        rosters = {t: [ctx.ids[r] for r in rows] for t, rows in board.rosters.items()}
        rows = board.rosters[me]
        t2 = time.perf_counter()
        out["policies"][spec] = {
            "roster": rosters[me], "value_sum": round(float(ctx.value[rows].sum()), 4),
            "catmodel_ecw": round(float(ctx.model.evaluate_team(rows)["expected_category_wins"]), 4),
            "grade": grade(rosters, me, seed), "log": pol.log,
            "draft_s": round(t2 - t1, 2), "grade_s": round(time.perf_counter() - t2, 2)}
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


# ---------------------------------------------------------------- summary
def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def paired(a: np.ndarray, b: np.ndarray) -> dict:
    d = a - b
    se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else float("nan")
    return {"mean": round(float(d.mean()), 4), "se": round(se, 4),
            "z": round(float(d.mean()) / se, 2) if se > 0 else None}


def summarize(rows: list[dict], base: str = "opt_market") -> dict:
    out: dict = {}
    for slot, regime in sorted({(r["slot"], r["regime"]) for r in rows}):
        rr = [r for r in rows if r["slot"] == slot and r["regime"] == regime]
        specs = [p for p in SPECS if all(p in r["policies"] for r in rr)]
        blk: dict = {"n": len(rr)}
        for truth in TRUTHS:
            for metric in ("p_playoffs", "p_title", "e_rank"):
                x = {p: np.array([r["policies"][p]["grade"][truth][metric] for r in rr]) for p in specs}
                m = {p: {"mean": round(float(v.mean()), 4), "se": round(float(v.std(ddof=1) / np.sqrt(len(v))), 4)}
                     for p, v in x.items()}
                for p in specs:
                    if p != base and base in x:
                        m[p]["minus_" + base] = paired(x[p], x[base])
                    if p != "bpa" and "bpa" in x:
                        m[p]["minus_bpa"] = paired(x[p], x["bpa"])
                blk[f"{truth}/{metric}"] = m
        # top-pick agreement with the default on identical boards (the default's own path), per my pick
        probes = [e for r in rr if "opt_market" in r["policies"] for e in r["policies"]["opt_market"]["log"]]
        by_pick: dict = {}
        for e in probes:
            if "tops" not in e:
                continue
            d = by_pick.setdefault(e["pick"], {v: 0 for v in VARIANTS} | {"bpa": 0, "n": 0})
            d["n"] += 1
            for v, pid in e["tops"].items():
                d[v] += pid != e["tops"]["opt_market"]
            d["bpa"] += e["bpa"] != e["tops"]["opt_market"]
        blk["top_changed_vs_market"] = {
            str(p): {"n": d["n"], **{k: round(d[k] / d["n"], 3) for k in d if k not in ("n", "opt_market")}}
            for p, d in sorted(by_pick.items())}
        secs = {v: [e["secs"][v] for e in probes if "secs" in e] for v in VARIANTS}
        blk["recommend_seconds_median"] = {v: round(float(np.median(x)), 3) for v, x in secs.items() if x}
        out[f"slot{slot}/{regime}"] = blk
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--seeds", default="0:100")
    ap.add_argument("--slots", default="1")
    ap.add_argument("--regimes", default="adp,symbpa,sym")
    ap.add_argument("--policies", default=",".join(SPECS))
    ap.add_argument("--projections", default=None)
    ap.add_argument("--opt-strategy", default="balanced", help="'none' = full strategy scan (as recommend)")
    ap.add_argument("--availability", default="adp", help="optimizer availability model: adp | sim (room mixed)")
    ap.add_argument("--n-eval", type=int, default=400)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "summary":
        print(json.dumps(summarize(load(a.out)), indent=1))
        return
    lo, hi = (int(x) for x in a.seeds.split(":"))
    specs = a.policies.split(",")
    done = {(r["seed"], r["slot"], r["regime"]) for r in load(a.out)} if a.out.exists() else set()
    jobs = [(sd, sl, rg, specs) for sd in range(lo, hi) for sl in (int(x) for x in a.slots.split(","))
            for rg in a.regimes.split(",") if (sd, sl, rg) not in done]
    from multiprocessing import get_context

    os.environ.setdefault("OMP_NUM_THREADS", "1")
    t0 = time.time()
    strat = None if a.opt_strategy == "none" else a.opt_strategy
    with get_context("fork").Pool(a.workers, initializer=init_worker, initargs=(a.projections, a.n_eval, strat, a.availability)) as pool, \
            a.out.open("a") as fh:
        for k, rec in enumerate(pool.imap_unordered(run_job, jobs)):
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{k + 1}/{len(jobs)} jobs, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
