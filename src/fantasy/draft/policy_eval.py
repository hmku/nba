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
  mixed   N_SHARP (3) "sharp" opponents as in `sym` + the other 8 noisy ADP (as `adp`, same room order); the
          sharp seats are drawn per room (rng([seed, 13])). The user's description of the league ("a few prepare
          seriously, most are casual") -> the primary world W for the draft-policy decision.
Ablation arms (lead/user request, 2026-10-19; which component of the optimizer loses value vs BPA):
  greedycat    one-step pick maximising expected category wins vs the league-average opponent (roster + player +
               market filler), lineup-feasible, no lookahead, no availability model  -> isolates the objective
  opt_oracle   the optimizer with its Normal(ADP) availability replaced by the room's empirical availability
               (per-player survival curves from independent simulated drafts of the same room type, seeds
               disjoint from the evaluation seeds; `rooms` subcommand)               -> isolates availability
  opt_matchup  the optimizer with objective P(win the weekly matchup) instead of expected category wins
  opt_sim      the optimizer with availability="sim": Monte Carlo of the SAME room model W it is graded in
               (fantasy.draft.room_sim; sym/symbpa -> room "sym", mixed -> "mixed", adp -> "adp")
  opt_sim_matchup  opt_sim with the matchup objective
  vs_sim       value search re-ranking the candidates of opt_sim (instead of the default optimizer's)
Each league is graded under truth `ours` (the drafting projections), `market`
(fantasy.sim.market_truth.market_implied_projections) and `mid` (their 50/50 blend_projections).

  python -m fantasy.draft.policy_eval run --seeds 0:200 --slots 1 --regimes adp,sym,symbpa --out x.jsonl
  python -m fantasy.draft.policy_eval rooms --seeds 10000:10150 --regimes adp,sym,symbpa --out rooms.npz
  python -m fantasy.draft.policy_eval run ... --policies greedycat,opt_oracle --oracle-positions rooms.npz
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
    board_state,
    bpa_row,
    decide,
    feasible,
    need_row,
)

EVAL_VERSION = "policy-eval-0.2.0"  # 0.2.0: ablation arms greedycat / opt_oracle / opt_matchup
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
    "greedycat": ("greedycat", 0.0, 0.0),
    "opt_oracle": ("opt_oracle", 0.0, 0.0),
    "opt_matchup": ("opt_matchup", 0.0, 0.0),
    "opt_sim": ("opt_sim", 0.0, 0.0),
    "opt_sim_matchup": ("opt_sim_matchup", 0.0, 0.0),
    "vs_sim": ("vs_sim", 0.0, 0.0),
}
N_SHARP = 3
TRUTHS = ("ours", "market", "mid")  # mid = 50/50 ours / market-implied stat lines
_E: dict = {}


# ---------------------------------------------------------------- my policies on a Board
class MyPolicy:
    """A policy spec for my slot. Optimizer payloads are memoised per board (shared across specs of one
    job via `cache`), so specs that agree on a prefix of the draft don't recompute it."""

    def __init__(self, ctx: DraftContext, spec: str, opts: dict, cache: dict, vs=None, seed: int = 0,  # noqa: ANN001
                 regime: str = "adp") -> None:
        self.ctx, self.spec, self.opts, self.cache, self.regime = ctx, spec, opts, cache, regime
        name, margin, eps = SPECS[spec]
        self.params = PolicyParams(name=name, margin=margin, eps=eps)
        self.log: list[dict] = []
        self.vsp = None
        if spec in ("value_search", "vs_sim"):
            from fantasy.valuesearch.search import SearchParams, ValueSearchPolicy

            op = self._sim_opt("opt_sim").params if spec == "vs_sim" else opts["params"]
            self.vsp = ValueSearchPolicy(vs, SearchParams(seed=seed), opt_params=op)

    def _sim_opt(self, name: str) -> DraftOptimizer:
        """The availability="sim" optimizer for this room (room model W = the room graded in), memoised in opts."""
        which = f"{name}:{self.regime}"
        if which not in self.opts:
            room = {"symbpa": "sym"}.get(self.regime, self.regime)
            prm = self.opts["params"]
            self.opts[which] = DraftOptimizer(self.ctx, OptimizerParams(
                strategy=prm.strategy, availability="sim", room=room,
                objective="matchup" if name == "opt_sim_matchup" else prm.objective))
        return self.opts[which]

    def _rec(self, which: str, taken: dict[int, str], slot: int) -> dict:
        key = (which, slot, tuple(taken[k] for k in sorted(taken)))
        if key not in self.cache:
            self.cache[key] = self.opts[which].recommend(DraftState(taken=dict(taken), my_slot=slot))
        return self.cache[key]

    def pick(self, board, overall: int, slot: int) -> int:  # noqa: ANN001
        ctx, taken = self.ctx, board.taken
        name = self.params.name
        if name == "greedycat":
            available, roster, left_after = board_state(ctx, taken, slot)
            remaining = [p.overall for p in ctx.order if p.slot == slot and p.overall > overall]
            row = need_row(ctx, available, roster, left_after, remaining, eps=1e9)
            bpa = ctx.ids[bpa_row(ctx, available, roster, left_after)]
            self.log.append({"pick": overall, "chosen": ctx.ids[row], "bpa": bpa, "opt": None, "planned_next": None,
                             "planned_next_pick": None, "score_gain_opt_vs_bpa": None, "strategy": "greedycat"})
            return row
        if name in ("opt_oracle", "opt_matchup", "opt_sim", "opt_sim_matchup"):
            which = {"opt_oracle": f"opt_oracle:{self.regime}", "opt_matchup": name}.get(name, f"{name}:{self.regime}")
            if name in ("opt_sim", "opt_sim_matchup"):
                self._sim_opt(name)
            rec = self._rec(which, taken, slot)
            d = decide(ctx, rec, taken, slot, PolicyParams("optimizer"))
        elif name in ("value_search", "vs_sim"):
            row = self.vsp.pick(board, overall, slot)
            if name == "vs_sim":
                self._sim_opt("opt_sim")
            rec = self._rec("opt" if name == "value_search" else f"opt_sim:{self.regime}", taken, slot)
            d = decide(ctx, rec, taken, slot, PolicyParams("optimizer"))
            d["player_id"] = ctx.ids[row]
        elif name in ("bpa", "bpa_need"):  # noqa: SIM114
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


def build_room(seed: int, me: int, regime: str, cache: dict) -> dict:
    """The 11 opponents' policies for one paired room (deterministic in seed)."""
    from fantasy.sim.symmetric import perturb_projections
    from fantasy.valuesearch.policies import ADPPolicy, sample_room_order

    ctx, s = _E["ctx"], _E["settings"]
    room = sample_room_order(ctx, np.random.default_rng([seed, 11]), 1.0)
    others = [t for t in range(1, s.num_teams + 1) if t != me]
    if regime == "adp":
        return {t: ADPPolicy(room) for t in others}
    if regime == "mixed":
        sharp = set(np.random.default_rng([seed, 13]).choice(others, N_SHARP, replace=False).tolist())
        return {t: (ViewOptPolicy(ctx, DraftContext(perturb_projections(_E["proj"], seed, t), s), cache)
                    if t in sharp else ADPPolicy(room)) for t in others}
    if regime in ("sym", "symbpa"):
        views = {t: DraftContext(perturb_projections(_E["proj"], seed, t), s) for t in others}
        return ({t: ViewOptPolicy(ctx, views[t], cache) for t in others} if regime == "sym"
                else {t: ViewBPAPolicy(ctx, views[t]) for t in others})
    raise ValueError(regime)


# ---------------------------------------------------------------- oracle availability
def survival_table(positions: np.ndarray, n_total: int, prior: np.ndarray | None = None, a: float = 2.0) -> np.ndarray:
    """S[x, row] = P(row is drafted at an overall pick > x), x = 0..n_total, from simulated drafts
    (`positions[d, row]` = overall pick, n_total + 1 if undrafted), shrunk towards `prior` (same shape) with
    `a` pseudo-drafts."""
    xs = np.arange(n_total + 1)[:, None, None]
    after = (positions[None, :, :] > xs).sum(axis=1).astype(float)  # [x, row]
    n = positions.shape[0]
    if prior is None:
        return after / n
    return (after + a * prior) / (n + a)


class OracleOptimizer(DraftOptimizer):
    """The optimizer with `prob_available` answered by an empirical survival table of the actual room type
    instead of Normal(ADP). Market ranks are unique integers, so the rows behind any `adp` array the optimizer
    passes in are recovered exactly; everything else (candidates, filler, objective) is unchanged."""

    def __init__(self, ctx: DraftContext, params: OptimizerParams, S: np.ndarray) -> None:
        super().__init__(ctx, params)
        self.S = S
        mr = ctx.market_rank.astype(int)
        if len(set(mr.tolist())) != len(mr):
            raise ValueError("market ranks are not unique; cannot decode rows")
        self.row_of = np.full(int(mr.max()) + 1, -1)
        self.row_of[mr] = np.arange(len(mr))

    def prob_available(self, at_pick: float, current_pick: float, adp: np.ndarray, sd_mult=None) -> np.ndarray:  # noqa: ANN001
        adp = np.asarray(adp, dtype=float)
        if at_pick <= current_pick:
            return np.ones_like(adp)
        rows = self.row_of[adp.astype(int)]
        s_now = self.S[int(current_pick) - 1, rows]
        s_then = self.S[int(at_pick) - 1, rows]
        with np.errstate(divide="ignore", invalid="ignore"):
            p = np.where(s_now > 1e-12, s_then / s_now, 0.0)
        return np.clip(p, 0.0, 1.0)

    def recommend(self, state: DraftState, draft_id: str = "local", model_run_id: str | None = None) -> dict:
        import fantasy.draft.optimizer as om

        orig = om.prob_available
        om.prob_available = self.prob_available
        try:
            return super().recommend(state, draft_id, model_run_id)
        finally:
            om.prob_available = orig


def normal_survival(ctx: DraftContext) -> np.ndarray:
    from fantasy.draft.availability import survival

    n_total = len(ctx.order)
    return np.stack([survival(x, ctx.market_rank, ctx.sd_mult) for x in range(n_total + 1)])


def room_positions(job: tuple[int, int, str]) -> tuple[str, np.ndarray]:
    """One simulated draft of a room type with me = lineup-feasible BPA: overall pick of every row."""
    from fantasy.valuesearch.policies import run_draft

    seed, me, regime = job
    ctx = _E["ctx"]
    opp = build_room(seed, me, regime, {})
    board = run_draft(ctx, {me: ViewBPAPolicy(ctx, ctx), **opp})
    pos = np.full(len(ctx.ids), len(ctx.order) + 1, dtype=np.int16)
    for ov, pid in board.taken.items():
        pos[ctx.pos[pid]] = ov
    return regime, pos


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
def init_worker(proj_path: str | None, n_eval: int, opt_strategy: str | None, value_model: str | None,
                oracle_positions: str | None = None) -> None:
    from threadpoolctl import threadpool_limits

    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.market_truth import blend_projections, market_implied_projections

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
            "opt_sd50": DraftOptimizer(availability_context(ctx, "sd50"), params),
            "opt_matchup": DraftOptimizer(ctx, OptimizerParams(strategy=opt_strategy, objective="matchup"))}
    if oracle_positions:
        z = np.load(oracle_positions, allow_pickle=False)
        ids = [str(x) for x in z["ids"]]
        prior = normal_survival(ctx)
        for regime in [k for k in z.files if k != "ids"]:
            pos = np.full((z[regime].shape[0], len(ctx.ids)), len(ctx.order) + 1, dtype=np.int16)
            for j, pid in enumerate(ids):
                if pid in ctx.pos:
                    pos[:, ctx.pos[pid]] = z[regime][:, j]
            opts[f"opt_oracle:{regime}"] = OracleOptimizer(ctx, params, survival_table(pos, len(ctx.order), prior))
    vs = None
    if value_model:
        from fantasy.valuesearch.features import Featurizer
        from fantasy.valuesearch.model import ValueModel
        from fantasy.valuesearch.search import ValueSearch

        vs = ValueSearch(ctx, Featurizer(ctx, proj.df), ValueModel.load(value_model))
    _E.clear()
    _E.update(settings=s, proj=proj, ctx=ctx, n_eval=n_eval, opts=opts, vs=vs,
              truths={"ours": ours, "market": build_context(mt.proj, s, teams, nba=ours.nba),
                      "mid": build_context(blend_projections(proj, mt, 0.5), s, teams, nba=ours.nba)},
              meta={"projections": proj.path, "projections_sha256": proj.sha256, "opt_strategy": opt_strategy,
                    "n_eval": n_eval, "eval_version": EVAL_VERSION, "oracle_positions": oracle_positions})


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
    from fantasy.valuesearch.policies import run_draft

    seed, me, regime, specs = job
    ctx = _E["ctx"]
    t0 = time.perf_counter()
    cache: dict = {}
    opp = build_room(seed, me, regime, cache)
    out: dict = {"seed": seed, "slot": me, "regime": regime, "policies": {}, **_E["meta"]}
    for spec in specs:
        t1 = time.perf_counter()
        pol = MyPolicy(ctx, spec, _E["opts"], cache, _E["vs"], seed, regime)
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
            "roster": rosters[me], "league": {str(t): r for t, r in rosters.items()}, "value_sum": round(float(ctx.value[rows].sum()), 4),
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
            if any(truth not in r["policies"][specs[0]]["grade"] for r in rr):
                continue
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
    ap.add_argument("cmd", choices=["run", "summary", "rooms"])
    ap.add_argument("--seeds", default="0:200")
    ap.add_argument("--slots", default="1")
    ap.add_argument("--regimes", default="adp,sym,symbpa")
    ap.add_argument("--policies", default="optimizer,bpa,hybrid02,hybrid05,need20,need40,opt_valavail,opt_sd50")
    ap.add_argument("--projections", default=None)
    ap.add_argument("--opt-strategy", default=None, help="None = full strategy scan (as recommend)")
    ap.add_argument("--value-model", default=None)
    ap.add_argument("--n-eval", type=int, default=400)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--oracle-positions", default=None, help="npz from `rooms` (needed for opt_oracle)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "summary":
        print(json.dumps(summarize(load(a.out)), indent=1))
        return
    lo, hi = (int(x) for x in a.seeds.split(":"))
    from multiprocessing import get_context

    if a.cmd == "rooms":  # independent room samples for the oracle availability model
        slot = int(a.slots.split(",")[0])
        rjobs = [(sd, slot, rg) for rg in a.regimes.split(",") for sd in range(lo, hi)]
        got: dict[str, list[np.ndarray]] = {}
        with get_context("fork").Pool(a.workers, initializer=init_worker,
                                      initargs=(a.projections, a.n_eval, a.opt_strategy, None)) as pool:
            for k, (rg, pos) in enumerate(pool.imap(room_positions, rjobs)):
                got.setdefault(rg, []).append(pos)
                print(f"{k + 1}/{len(rjobs)} room drafts", flush=True)
        init_worker(a.projections, a.n_eval, a.opt_strategy, None)
        np.savez_compressed(a.out, ids=np.array(_E["ctx"].ids), **{k: np.stack(v) for k, v in got.items()})
        return
    specs = a.policies.split(",")
    done = {(r["seed"], r["slot"], r["regime"]) for r in load(a.out)} if a.out.exists() else set()
    slots = [int(x) for x in a.slots.split(",")]
    jobs = [(sd, sl, rg, specs) for sd in range(lo, hi) for sl in slots for rg in a.regimes.split(",")
            if (sd, sl, rg) not in done]  # seed-major, so a partial file covers every regime/slot

    os.environ.setdefault("OMP_NUM_THREADS", "1")
    t0 = time.time()
    args = (a.projections, a.n_eval, a.opt_strategy, a.value_model, a.oracle_positions)
    with get_context("fork").Pool(a.workers, initializer=init_worker, initargs=args) as pool, a.out.open("a") as fh:
        for k, rec in enumerate(pool.imap_unordered(run_job, jobs)):
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{k + 1}/{len(jobs)} jobs, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
