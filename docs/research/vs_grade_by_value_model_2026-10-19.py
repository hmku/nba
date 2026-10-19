"""Grade the value-search validation leagues by the VALUE MODEL and by the full season simulator, side by side.

Research script for docs/research/value_search.md, section "Grading by the value model (2026-10-19)".
Run it against the validation-era code (commit 12d358a: season-sim-0.2.0, valuation-0.2.1, our projections
before the ESPN blend), which is the world the value model (value-model-0.1.0) was trained in and in which
`fantasy.valuesearch.validate` produced the leagues:

    git worktree add --detach ../nba-vsold 12d358a && cd ../nba-vsold
    OMP_NUM_THREADS=1 PYTHONPATH=src python <this file> grade --val val.jsonl \
        --model data/models/value_search/value-model-0.1.0.npz --out regrade.jsonl --workers 3
    python <this file> summary --out regrade.jsonl

`grade`, per validation seed (room) and regime (adp, sym):
  * re-drafts my slot with GreedyCat in the same room (opponents' room order / private views are seeded by the
    room seed; the stored BPA league is re-drafted for seeds 0-1 as a reproduction check);
  * value-model grade: features of all 12 rosters from our projections -> value model -> my team;
    for adp leagues also with market-implied projections as features (out of the training distribution);
  * simulator grade: the stored 400-season grade + 1,600 new seasons (seed + 7,000,000), combined to 2,000;
    GreedyCat leagues get 400 (same seed as validate.py) + 1,600 the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from multiprocessing import get_context

import numpy as np

EXTRA, SEED_OFF = 1600, 7_000_000
G: dict = {}
LAB = {"optimizer": "opt", "value_search": "VS", "bpa": "BPA", "greedycat": "GreedyCat"}
PAIRS = [("value_search", "optimizer"), ("bpa", "optimizer"), ("value_search", "bpa"), ("greedycat", "optimizer")]
SETS = [("adp", "ours", "(i) ADP"), ("sym", "ours", "(ii) symmetric"), ("adp", "market", "(iii) market truth")]


def _init(model: str) -> None:
    from fantasy.draft.optimizer import DraftContext
    from fantasy.sim.market_truth import market_implied_projections
    from fantasy.valuesearch import validate as V
    from fantasy.valuesearch.features import Featurizer
    from fantasy.valuesearch.model import ValueModel

    V._init(model, 400)
    E = V._E
    mt = market_implied_projections(E["proj"], E["settings"])
    mctx = DraftContext(mt.proj, E["settings"])
    G.update(V=V, E=E, vm=ValueModel.load(model),
             feat={"ours": Featurizer(E["ctx"], E["proj"].df), "market": Featurizer(mctx, mt.proj.df)},
             fctx={"ours": E["ctx"], "market": mctx})


def _sim(rosters: dict, truth: str, n: int, seed: int) -> dict:
    from fantasy.sim.engine import SimParams
    from fantasy.sim.league import LeagueState

    res = G["E"]["truths"][truth].simulator(SimParams(n_sims=n, seed=seed)).run(
        LeagueState(rosters=rosters, my_team=1, source="vsgrade"))
    i = res.tindex(1)
    return {"p_playoffs": float(res.playoff[:, i].mean()), "p_title": float((res.champion == i).mean()),
            "e_rank": float(res.rank[:, i].mean()), "e_cat_wins": float(res.cat_wins[:, i].mean())}


def _vm(rosters: dict, feat: str) -> dict:
    f, ctx = G["feat"][feat], G["fctx"][feat]
    rows = {t: [ctx.pos[p] for p in rosters[t]] for t in rosters}
    X = f.league_rows(rows)
    p = G["vm"].predict(X[[sorted(rows).index(1)]])
    return {k: float(v[0]) for k, v in p.items()}


def _job(row: dict) -> dict:
    from fantasy.draft.optimizer import DraftContext
    from fantasy.sim.symmetric import perturb_projections
    from fantasy.valuesearch.policies import (
        ADPPolicy,
        BPAPolicy,
        GreedyCatPolicy,
        run_draft,
        sample_room_order,
    )

    V, E = G["V"], G["E"]
    ctx, s = E["ctx"], E["settings"]
    T = s.num_teams
    seed = row["seed"]
    t0 = time.perf_counter()
    out: dict = {"seed": seed, "regimes": {}}
    room = sample_room_order(ctx, np.random.default_rng([seed, 11]), 1.0)
    for regime, rr in row["regimes"].items():
        truths = ["ours", "market"] if regime == "adp" else ["ours"]
        if regime == "adp":
            opp: dict = {t: ADPPolicy(room) for t in range(2, T + 1)}
        else:
            opp = {t: V.ViewPolicy(ctx, DraftContext(perturb_projections(E["proj"], seed, t), s)) for t in range(2, T + 1)}
        leagues = {p: {int(k): v for k, v in d["league"].items()} for p, d in rr.items()}
        b = run_draft(ctx, {1: GreedyCatPolicy(ctx), **opp})
        leagues["greedycat"] = {t: [ctx.ids[r] for r in rows] for t, rows in b.rosters.items()}
        if seed < 2:
            b2 = run_draft(ctx, {1: BPAPolicy(), **opp})
            out.setdefault("repro", {})[regime] = {t: [ctx.ids[r] for r in rows] for t, rows in b2.rosters.items()} == leagues["bpa"]
        res: dict = {}
        for p, lg in leagues.items():
            g = {"vm": _vm(lg, "ours")}
            if regime == "adp":
                g["vm_marketfeat"] = _vm(lg, "market")
            for tr in truths:
                s400 = rr[p]["grade"][tr] if p in rr else _sim(lg, tr, 400, seed)
                s1600 = _sim(lg, tr, EXTRA, seed + SEED_OFF)
                g[f"sim400_{tr}"] = s400
                g[f"sim2000_{tr}"] = {k: (400 * s400[k] + EXTRA * s1600[k]) / (400 + EXTRA) for k in s400}
            res[p] = g
        res["greedycat_roster"] = leagues["greedycat"][1]
        out["regimes"][regime] = res
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


def grade(a: argparse.Namespace) -> None:
    rows = [json.loads(x) for x in open(a.val)]
    try:
        done = {json.loads(x)["seed"] for x in open(a.out)}
    except FileNotFoundError:
        done = set()
    rows = sorted((r for r in rows if r["seed"] not in done), key=lambda r: r["seed"])
    for r in rows:  # drop the bulky value-search logs
        r["regimes"] = {g: {p: {"grade": d["grade"], "league": d["league"]} for p, d in rr.items()}
                        for g, rr in r["regimes"].items()}
    t0 = time.time()
    with get_context("fork").Pool(a.workers, initializer=_init, initargs=(a.model,)) as pool, open(a.out, "a") as fh:
        for k, rec in enumerate(pool.imap_unordered(_job, rows)):
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{k + 1}/{len(rows)} seed {rec['seed']} {time.time() - t0:.0f}s {rec.get('repro', '')}", flush=True)


def _fmt(x: np.ndarray) -> str:
    se = x.std(ddof=1) / np.sqrt(len(x))
    return f"{100 * x.mean():+.2f} ± {100 * se:.2f} ({x.mean() / se:+.1f})"


def summary(a: argparse.Namespace) -> None:
    rows = [json.loads(x) for x in open(a.out)]

    def d(rr: list, p: str, q: str, g: str, m: str) -> np.ndarray:
        return np.array([x[p][g][m] - x[q][g][m] for x in rr])

    print(f"n = {len(rows)} paired rooms; percentage points, mean ± paired SE (z)\n")
    print("| room | pair | P(playoffs): value model | P(playoffs): simulator | corr | "
          "P(title): value model | P(title): simulator | corr |")
    print("|---|---|---|---|---|---|---|---|")
    for regime, truth, t in SETS:
        rr = [r["regimes"][regime] for r in rows]
        for p, q in PAIRS:
            cells = []
            for m in ("p_playoffs", "p_title"):
                v, s = d(rr, p, q, "vm", m), d(rr, p, q, f"sim2000_{truth}", m)
                cells += [_fmt(v), _fmt(s), f"{np.corrcoef(v, s)[0, 1]:.2f}"]
            print(f"| {t} | {LAB[p]} − {LAB[q]} | " + " | ".join(cells) + " |")
    print("\nvalue model − simulator, per policy (level error) and per pair (gap), pp ± SE")
    for regime, truth, t in SETS:
        rr = [r["regimes"][regime] for r in rows]
        for m in ("p_playoffs", "p_title"):
            lv = [f"{LAB[p]} {_fmt(np.array([x[p]['vm'][m] - x[p][f'sim2000_{truth}'][m] for x in rr]))}" for p in LAB]
            gp = [f"{LAB[p]}−{LAB[q]} {_fmt(d(rr, p, q, 'vm', m) - d(rr, p, q, f'sim2000_{truth}', m))}" for p, q in PAIRS]
            print(t, m, "| levels:", "; ".join(lv), "| gaps:", "; ".join(gp))
    print("repro checks:", [r["repro"] for r in rows if "repro" in r])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["grade", "summary"])
    ap.add_argument("--val", help="val.jsonl written by fantasy.valuesearch.validate")
    ap.add_argument("--model", default="data/models/value_search/value-model-0.1.0.npz")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    if a.mode == "grade" and not a.val:
        sys.exit("--val is required for grade")
    (grade if a.mode == "grade" else summary)(a)


if __name__ == "__main__":
    main()
