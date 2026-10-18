"""Paired evaluation of draft *policies* (fantasy.draft.policy) graded by the FULL season simulator.

One job = (draft seed s, my slot, regime). The seed fixes the noisy-ADP room order (rng([s, 11]), as
fantasy.valuesearch.validate / fantasy.sim.symmetric), the opponents' private projection views
(perturb_projections(proj, s, team)) and the season-simulation seed, so every policy of mine faces the same
room and the same season noise (common random numbers; differences are paired over seeds).

Regimes (opponents = every slot except mine):
  adp     noisy ADP (next available of the sampled room order, positions ignored; = `draft simulate`)
  sym     our balanced optimizer on their own perturbed view (= value-search validation "sym", the
          optimism-decomposition C11 proxy; "as smart as us, independent errors")
  symbpa  value-BPA (lineup-feasible) on their own perturbed view (A_bpa-style smart-but-simple room)
Each league is graded under truth `ours` (the drafting projections) and `market`
(fantasy.sim.market_truth.market_implied_projections).

  python -m fantasy.draft.policy_eval run --seeds 0:200 --slots 1 --regimes adp,sym,symbpa --out x.jsonl
  python -m fantasy.draft.policy_eval summary --out x.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.policy import (
    PolicyParams,
    availability_context,
    decide,
    feasible,
)

EVAL_VERSION = "policy-eval-0.1.0"
# policy spec name -> (policy name, margin, eps)
SPECS: dict[str, tuple[str, float, float]] = {
    "optimizer": ("optimizer", 0.0, 0.0),
    "bpa": ("bpa", 0.0, 0.0),
    "hybrid02": ("hybrid", 0.02, 0.0),
    "hybrid05": ("hybrid", 0.05, 0.0),
    "need20": ("bpa_need", 0.0, 0.2),
    "need40": ("bpa_need", 0.0, 0.4),
    "opt_valavail": ("opt_valavail", 0.0, 0.0),
    "opt_sd50": ("opt_sd50", 0.0, 0.0),
    "value_search": ("value_search", 0.0, 0.0),
}
TRUTHS = ("ours", "market")
_E: dict = {}


# ---------------------------------------------------------------- my policies on a Board
class MyPolicy:
    """A policy spec for my slot. Optimizer payloads are memoised per board (shared across specs of one
    job via `cache`), so specs that agree on a prefix of the draft don't recompute it."""

    def __init__(self, ctx: DraftContext, spec: str, opts: dict, cache: dict, vs=None, seed: int = 0) -> None:  # noqa: ANN001
        self.ctx, self.spec, self.opts, self.cache = ctx, spec, opts, cache
        name, margin, eps = SPECS[spec]
        self.params = PolicyParams(name=name, margin=margin, eps=eps)
        self.log: list[dict] = []
        self.vsp = None
        if spec == "value_search":
            from fantasy.valuesearch.search import SearchParams, ValueSearchPolicy

            self.vsp = ValueSearchPolicy(vs, SearchParams(seed=seed), opt_params=opts["params"])

    def _rec(self, which: str, taken: dict[int, str], slot: int) -> dict:
        key = (which, slot, tuple(taken[k] for k in sorted(taken)))
        if key not in self.cache:
            self.cache[key] = self.opts[which].recommend(DraftState(taken=dict(taken), my_slot=slot))
        return self.cache[key]

    def pick(self, board, overall: int, slot: int) -> int:  # noqa: ANN001
        ctx, taken = self.ctx, board.taken
        name = self.params.name
        if name == "value_search":
            row = self.vsp.pick(board, overall, slot)
            rec = self._rec("opt", taken, slot)
            d = decide(ctx, rec, taken, slot, PolicyParams("optimizer"))
            d["player_id"] = ctx.ids[row]
        elif name in ("bpa", "bpa_need"):
            rec = self._rec("opt", taken, slot)  # for the mechanism log only (cached, shared)
            d = decide(ctx, rec, taken, slot, self.params)
        else:
            rec = self._rec("opt", taken, slot)
            alt = None
            if name in ("opt_valavail", "opt_sd50"):
                alt = self._rec(name, taken, slot)
            d = decide(ctx, rec, taken, slot, self.params, alt)
            if alt is not None:
                rec = alt
        plan = rec.get("planned_picks") or []
        recs = rec["recommendations"]
        sc = {r["player_id"]: r["score"] for r in recs}
        self.log.append({"pick": overall, "chosen": d["player_id"], "bpa": d["bpa_player_id"],
                         "opt": recs[0]["player_id"], "planned_next": plan[0]["player_id"] if plan else None,
                         "planned_next_pick": plan[0]["pick"] if plan else None,
                         "score_gain_opt_vs_bpa": None if d["bpa_player_id"] not in sc else
                         round(recs[0]["score"] - sc[d["bpa_player_id"]], 4),
                         "strategy": rec["strategy"]["name"]})
        return ctx.pos[d["player_id"]]


# ---------------------------------------------------------------- opponents
class ViewOptPolicy:
    """Balanced optimizer on a private projection view (memoised by board)."""

    def __init__(self, base: DraftContext, view: DraftContext, cache: dict) -> None:
        self.base, self.view, self.cache = base, view, cache
        self.opt = DraftOptimizer(view, OptimizerParams(strategy="balanced"))

    def pick(self, board, overall: int, slot: int) -> int:  # noqa: ANN001
        key = ("view", slot, tuple(board.taken[k] for k in sorted(board.taken)))
        if key not in self.cache:
            rec = self.opt.recommend(DraftState(taken=dict(board.taken), my_slot=slot))
            pid = next((r["player_id"] for r in rec["recommendations"]
                        if r["player_id"] in self.base.pos and board.available[self.base.pos[r["player_id"]]]), None)
            self.cache[key] = pid
        pid = self.cache[key]
        if pid is None:
            av = np.flatnonzero(board.available)
            return int(av[np.argmax(self.base.value[av])])
        return self.base.pos[pid]


class ViewBPAPolicy:
    """Lineup-feasible BPA by a private view's values, on the base board."""

    def __init__(self, base: DraftContext, view: DraftContext) -> None:
        self.base = base
        self.order = np.array([base.pos[p] for p in view.ids if p in base.pos])

    def pick(self, board, overall: int, slot: int) -> int:  # noqa: ANN001
        mine = board.rosters[slot]
        left_after = sum(1 for p in self.base.order if p.slot == slot) - len(mine) - 1
        cand = self.order[board.available[self.order]]
        for r in cand:
            if feasible(self.base, mine, int(r), left_after):
                return int(r)
        return int(cand[0])


# ---------------------------------------------------------------- worker
def init_worker(proj_path: str | None, n_eval: int, opt_strategy: str | None, value_model: str | None) -> None:
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
    params = OptimizerParams(strategy=opt_strategy)
    opts = {"params": params, "opt": DraftOptimizer(ctx, params),
            "opt_valavail": DraftOptimizer(availability_context(ctx, "valavail"), params),
            "opt_sd50": DraftOptimizer(availability_context(ctx, "sd50"), params)}
    vs = None
    if value_model:
        from fantasy.valuesearch.features import Featurizer
        from fantasy.valuesearch.model import ValueModel
        from fantasy.valuesearch.search import ValueSearch

        vs = ValueSearch(ctx, Featurizer(ctx, proj.df), ValueModel.load(value_model))
    _E.clear()
    _E.update(settings=s, proj=proj, ctx=ctx, n_eval=n_eval, opts=opts, vs=vs,
              truths={"ours": ours, "market": build_context(mt.proj, s, teams, nba=ours.nba)},
              meta={"projections": proj.path, "projections_sha256": proj.sha256, "opt_strategy": opt_strategy,
                    "n_eval": n_eval, "eval_version": EVAL_VERSION})


def _cat_rates(res, team: int) -> list[float]:  # noqa: ANN001
    """My regular-season per-category win rate (ties 0.5) vs my scheduled opponents."""
    from fantasy.league.settings import CATEGORIES

    sign = np.array([1.0 if CATEGORIES[c].higher_is_better else -1.0 for c in res.cats])
    i = res.tindex(team)
    acc, n = np.zeros(len(res.cats)), 0
    for w in res.regular_weeks:
        opp = res.matchups.opponent(team, w)
        if opp is None or opp not in res.team_ids:
            continue
        wi = res.week_index(w)
        d = (res.totals[:, wi, i] - res.totals[:, wi, res.tindex(opp)]) * sign
        acc += (d > 0).mean(0) + 0.5 * (d == 0).mean(0)
        n += 1
    return [round(float(x), 4) for x in acc / max(n, 1)]


def grade(rosters: dict[int, list[str]], me: int, seed: int) -> dict:
    from fantasy.sim.engine import SimParams
    from fantasy.sim.league import LeagueState

    out = {}
    for name in TRUTHS:
        res = _E["truths"][name].simulator(SimParams(n_sims=_E["n_eval"], seed=seed)).run(
            LeagueState(rosters=rosters, my_team=me, source="policy_eval"))
        i = res.tindex(me)
        out[name] = {"p_playoffs": float(res.playoff[:, i].mean()), "p_title": float((res.champion == i).mean()),
                     "e_rank": float(res.rank[:, i].mean()), "e_cat_wins": float(res.cat_wins[:, i].mean())}
        if name == "ours":
            out[name]["cat_rates"] = _cat_rates(res, me)
    return out


def run_job(job: tuple[int, int, str, list[str]]) -> dict:
    from fantasy.sim.symmetric import perturb_projections
    from fantasy.valuesearch.policies import ADPPolicy, run_draft, sample_room_order

    seed, me, regime, specs = job
    ctx, s = _E["ctx"], _E["settings"]
    T = s.num_teams
    t0 = time.perf_counter()
    cache: dict = {}
    room = sample_room_order(ctx, np.random.default_rng([seed, 11]), 1.0)
    others = [t for t in range(1, T + 1) if t != me]
    if regime == "adp":
        opp: dict = {t: ADPPolicy(room) for t in others}
    elif regime in ("sym", "symbpa"):
        views = {t: DraftContext(perturb_projections(_E["proj"], seed, t), s) for t in others}
        opp = ({t: ViewOptPolicy(ctx, views[t], cache) for t in others} if regime == "sym"
               else {t: ViewBPAPolicy(ctx, views[t]) for t in others})
    else:
        raise ValueError(regime)
    out: dict = {"seed": seed, "slot": me, "regime": regime, "policies": {}, **_E["meta"]}
    for spec in specs:
        t1 = time.perf_counter()
        pol = MyPolicy(ctx, spec, _E["opts"], cache, _E["vs"], seed)
        board = run_draft(ctx, {me: pol, **opp})
        rosters = {t: [ctx.ids[r] for r in rows] for t, rows in board.rosters.items()}
        taken_at = {pid: ov for ov, pid in board.taken.items()}
        for e in pol.log:  # when did the passed BPA player / the planned target actually go?
            e["bpa_taken_at"] = taken_at.get(e["bpa"])
            e["planned_taken_at"] = taken_at.get(e["planned_next"]) if e["planned_next"] else None
        rows = board.rosters[me]
        ev = ctx.model.evaluate_team(rows)
        t2 = time.perf_counter()
        out["policies"][spec] = {
            "roster": rosters[me], "value_sum": round(float(ctx.value[rows].sum()), 4),
            "catmodel_ecw": round(float(ev["expected_category_wins"]), 4),
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


def summarize(rows: list[dict], base: str = "bpa") -> dict:
    out: dict = {}
    keys = sorted({(r["slot"], r["regime"]) for r in rows})
    for slot, regime in keys:
        rr = [r for r in rows if r["slot"] == slot and r["regime"] == regime]
        specs = [p for p in SPECS if all(p in r["policies"] for r in rr)]
        for truth in TRUTHS:
            k = f"slot{slot}/{regime}/{truth}"
            out[k] = {"n": len(rr)}
            for metric in ("p_playoffs", "p_title", "e_rank"):
                x = {p: np.array([r["policies"][p]["grade"][truth][metric] for r in rr]) for p in specs}
                m = {p: {"mean": round(float(v.mean()), 4), "se": round(float(v.std(ddof=1) / np.sqrt(len(v))), 4)}
                     for p, v in x.items()}
                if base in x:
                    for p in specs:
                        if p != base:
                            m[p]["minus_" + base] = paired(x[p], x[base])
                out[k][metric] = m
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--seeds", default="0:200")
    ap.add_argument("--slots", default="1")
    ap.add_argument("--regimes", default="adp,sym,symbpa")
    ap.add_argument("--policies", default="optimizer,bpa,hybrid02,hybrid05,need20,need40,opt_valavail,opt_sd50")
    ap.add_argument("--projections", default=None)
    ap.add_argument("--opt-strategy", default=None, help="None = full strategy scan (as recommend)")
    ap.add_argument("--value-model", default=None)
    ap.add_argument("--n-eval", type=int, default=400)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "summary":
        print(json.dumps(summarize(load(a.out)), indent=1))
        return
    lo, hi = (int(x) for x in a.seeds.split(":"))
    specs = a.policies.split(",")
    done = {(r["seed"], r["slot"], r["regime"]) for r in load(a.out)} if a.out.exists() else set()
    slots = [int(x) for x in a.slots.split(",")]
    jobs = [(sd, sl, rg, specs) for sd in range(lo, hi) for sl in slots for rg in a.regimes.split(",")
            if (sd, sl, rg) not in done]  # seed-major, so a partial file covers every regime/slot
    from multiprocessing import get_context

    os.environ.setdefault("OMP_NUM_THREADS", "1")
    t0 = time.time()
    args = (a.projections, a.n_eval, a.opt_strategy, a.value_model)
    with get_context("fork").Pool(a.workers, initializer=init_worker, initargs=args) as pool, a.out.open("a") as fh:
        for k, rec in enumerate(pool.imap_unordered(run_job, jobs)):
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{k + 1}/{len(jobs)} jobs, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
