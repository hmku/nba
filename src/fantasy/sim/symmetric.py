"""Optimism decomposition: how much of the pre-draft P(playoffs)/P(title) comes from the draft slot, from
opponents drafting by ADP, and from grading ourselves with our own projections.

One *draft seed* s fixes (common random numbers across all rooms):
  * the noisy market order the ADP drafters follow (position ~ N(market rank, adp_sd), as
    fantasy.draft.simulate);
  * each team's private projection view for "smart" opponents: our projections with independent
    season-level errors (`perturb_projections`, same priors as the season simulator's projection error),
    keyed by (s, team), so a smart opponent's view is identical in every room;
  * the order in which opponents become smart in the mixed rooms (C3 subset of C6 subset of C11);
  * the season-simulation seed (every room and every truth is played with the same season noise).

Rooms (slot 1 = me; `opt` = DraftOptimizer.recommend top pick; `opt_bal` = the same optimizer restricted
to the balanced strategy, ~8x faster, used for the 11 opponents; `bpa` = best available by own value
keeping the starting lineup completable, as the BPA baseline of fantasy.draft.simulate):
  adp_all  every team noisy ADP (pure slot value in a passive room)
  B        me opt (exact projections), others noisy ADP (status quo, = `fantasy sim preseason`)
  C3 / C6  me opt, 3 / 6 random opponents opt_bal on their own views, rest ADP
  C11      me opt (exact), all 11 opponents opt_bal on their own views
  A_sym    every team (me too) opt_bal on its own perturbed view - fully symmetric, isolates slot value
  A_bpa    every team bpa on its own perturbed view (fast proxy for A_sym)
  A_full   (subset of seeds) every team full-scan opt on its own view (checks the opt_bal proxy)
  C3m/C11m me opt (exact), 3 / 11 opponents opt_bal on perturbed *market-implied* views (smart opponents
           who disagree with us the way the market does), rest ADP

Every room is graded by the season simulator under one or more "truths": `ours` (our projections, as
today), `market` (market-implied projections, fantasy.sim.market_truth) and `blend` (50/50).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from fantasy.draft.availability import adp_sd
from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.roster import max_starting_matching
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import STATS, ProjectionSet
from fantasy.sim.context import SimContext
from fantasy.sim.engine import SimParams
from fantasy.sim.league import LeagueState

ROOMS = ["adp_all", "B", "C3", "C6", "C11", "A_sym", "A_bpa"]
SUBSET_ROOMS = ["A_full"]
# opponents who are as smart as us but believe the MARKET: optimizer on perturbed market-implied projections
MARKET_ROOMS = ["C3m", "C11m"]
PERTURB_DEFAULT = {"vol_sd": 0.08, "stat_sd": 0.08, "fg_pct_sd": 0.015, "ft_pct_sd": 0.03}


# ---------------------------------------------------------------- private views
def perturb_projections(proj: ProjectionSet, seed: int, team: int, sd: dict | None = None) -> ProjectionSet:
    """A team's private projection view: our lines with a season-level error per player (volume factor on
    all counting stats/attempts, per-stat factors on FGA, FTA, 3PM, REB, AST, STL, BLK, TO, additive FG%/FT%
    shifts; PTS recomputed from makes). Same functional form and default sds as the season simulator's
    projection error (SimParams.vol_sd etc.), i.e. 'as smart as us, but with independent errors'."""
    sd = {**PERTURB_DEFAULT, **(sd or {})}
    df = proj.df.copy()
    n = len(df)
    rng = np.random.default_rng([seed, 31_337, team])
    vol = np.exp(rng.normal(0, sd["vol_sd"], n))
    f = {s: np.exp(rng.normal(0, sd["stat_sd"], n)) for s in ("fga", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov")}
    fg_shift, ft_shift = rng.normal(0, sd["fg_pct_sd"], n), rng.normal(0, sd["ft_pct_sd"], n)
    b = {s: df[s].to_numpy(dtype=float) for s in STATS}
    with np.errstate(divide="ignore", invalid="ignore"):
        fgp = np.where(b["fga"] > 0, b["fgm"] / b["fga"], 0.0)
        ftp = np.where(b["fta"] > 0, b["ftm"] / b["fta"], 0.0)
        raw = 2 * b["fgm"] + b["fg3m"] + b["ftm"]
        ratio = np.where(raw > 0, b["pts"] / raw, 1.0)
    new = {s: b[s] * vol * f[s] for s in f}
    new["fgm"] = new["fga"] * np.where(b["fga"] > 0, np.clip(fgp + fg_shift, 0.2, 0.8), 0.0)
    new["ftm"] = new["fta"] * np.where(b["fta"] > 0, np.clip(ftp + ft_shift, 0.3, 0.98), 0.0)
    new["fg3m"] = np.minimum(new["fg3m"], new["fgm"])
    new["pts"] = (2 * new["fgm"] + new["fg3m"] + new["ftm"]) * ratio
    for s in STATS:
        df[s] = new[s]
    return ProjectionSet(df=df, path=f"{proj.path}#view-{seed}-{team}", sha256=f"{proj.sha256}:{seed}:{team}")


# ---------------------------------------------------------------- draft rooms
@dataclass
class Drafter:
    kind: str  # adp | opt | bpa
    ctx: DraftContext | None = None
    opt: DraftOptimizer | None = None


def run_room(base: DraftContext, drafters: dict[int, Drafter], room_order: list[str]) -> dict[int, list[str]]:
    """Play one full draft. `room_order`: the noisy market order ADP drafters follow. Returns slot -> ids."""
    slots = base.settings.starting_slots
    taken: dict[int, str] = {}
    gone: set[str] = set()
    rosters: dict[int, list[str]] = {t: [] for t in range(1, base.settings.num_teams + 1)}
    left = {t: sum(1 for p in base.order if p.slot == t) for t in rosters}
    ptr = 0
    for pk in base.order:
        d = drafters.get(pk.slot) or Drafter("adp")
        left[pk.slot] -= 1
        if d.kind == "adp":
            while room_order[ptr] in gone:
                ptr += 1
            pid = room_order[ptr]
        elif d.kind == "opt":
            assert d.opt is not None
            rec = d.opt.recommend(DraftState(taken=dict(taken), my_slot=pk.slot))
            pid = rec["recommendations"][0]["player_id"]
        elif d.kind == "bpa":
            assert d.ctx is not None
            cur = [d.ctx.positions[d.ctx.pos[p]] for p in rosters[pk.slot]]
            pid = None
            fallback = None
            for cand in d.ctx.ids:  # sorted by the drafter's own value
                if cand in gone:
                    continue
                fallback = fallback or cand
                f, _ = max_starting_matching(cur + [d.ctx.positions[d.ctx.pos[cand]]], slots)
                if len(slots) - f <= left[pk.slot]:
                    pid = cand
                    break
            pid = pid or fallback
        else:
            raise ValueError(f"unknown drafter kind {d.kind!r}")
        assert pid is not None and pid not in gone, (pk, pid)
        taken[pk.overall] = pid
        gone.add(pid)
        rosters[pk.slot].append(pid)
    return rosters


def noisy_market_order(base: DraftContext, seed: int, noise: float = 1.0) -> list[str]:
    rng = np.random.default_rng([seed, 11])
    mr = base.market_rank
    sampled = mr + rng.normal(0, 1, len(mr)) * adp_sd(mr) * base.sd_mult * noise
    return [base.ids[i] for i in np.argsort(sampled, kind="stable")]


def smart_order(seed: int, num_teams: int, my_slot: int) -> list[int]:
    """Order in which opponents become 'smart' in mixed rooms (C3 = first 3, C6 = first 6)."""
    others = [t for t in range(1, num_teams + 1) if t != my_slot]
    rng = np.random.default_rng([seed, 4242])
    return [others[i] for i in rng.permutation(len(others))]


@dataclass
class Experiment:
    """Fixed inputs shared by all seeds (built once per worker process)."""

    proj: ProjectionSet
    settings: LeagueSettings
    truths: dict[str, SimContext]
    n_season_sims: int = 500
    noise: float = 1.0
    perturb: dict = field(default_factory=dict)
    my_full_params: OptimizerParams = field(default_factory=OptimizerParams)
    market_proj: ProjectionSet | None = None  # needed for the C*m rooms

    def __post_init__(self) -> None:
        self.base = DraftContext(self.proj, self.settings)
        self.my_opt = DraftOptimizer(self.base, self.my_full_params)
        self.T = self.settings.num_teams
        self.me = self.settings.my_slot

    def views(self, seed: int, market: bool = False) -> dict[int, DraftContext]:
        src = self.market_proj if market else self.proj
        if src is None:
            raise ValueError("market rooms need Experiment.market_proj")
        return {t: DraftContext(perturb_projections(src, seed, t, self.perturb), self.settings)
                for t in range(1, self.T + 1)}

    def drafters(self, room: str, seed: int, views: dict[int, DraftContext]) -> dict[int, Drafter]:
        me_exact = Drafter("opt", self.base, self.my_opt)

        def bal(t: int) -> Drafter:
            return Drafter("opt", views[t], DraftOptimizer(views[t], OptimizerParams(strategy="balanced")))

        if room == "adp_all":
            return {}
        if room == "B":
            return {self.me: me_exact}
        if room in ("C3", "C6", "C11", "C3m", "C11m"):
            k = int(room[1:].rstrip("m"))
            return {self.me: me_exact, **{t: bal(t) for t in smart_order(seed, self.T, self.me)[:k]}}
        if room == "A_sym":
            return {t: bal(t) for t in range(1, self.T + 1)}
        if room == "A_bpa":
            return {t: Drafter("bpa", views[t]) for t in range(1, self.T + 1)}
        if room == "A_full":
            return {t: Drafter("opt", views[t], DraftOptimizer(views[t], OptimizerParams())) for t in range(1, self.T + 1)}
        raise ValueError(f"unknown room {room!r}")

    def grade(self, rosters: dict[int, list[str]], seed: int) -> dict[str, dict]:
        team_ids = list(range(1, self.T + 1))
        league = LeagueState(rosters=rosters, my_team=self.me, source=f"symmetric seed {seed}")
        out = {}
        for name, sctx in self.truths.items():
            res = sctx.simulator(SimParams(n_sims=self.n_season_sims, seed=seed)).run(league)
            idx = [res.tindex(t) for t in team_ids]
            out[name] = {
                "p_playoffs": [float(res.playoff[:, i].mean()) for i in idx],
                "p_title": [float((res.champion == i).mean()) for i in idx],
                "exp_rank": [float(res.rank[:, i].mean()) for i in idx],
                "exp_cat_wins": [float(res.cat_wins[:, i].mean()) for i in idx],
            }
        return out

    def run_seed(self, seed: int, rooms: list[str]) -> dict:
        t0 = time.perf_counter()
        own = self.views(seed) if any(r not in MARKET_ROOMS for r in rooms) else {}
        mkt = self.views(seed, market=True) if any(r in MARKET_ROOMS for r in rooms) else {}
        order = noisy_market_order(self.base, seed, self.noise)
        out: dict = {"seed": seed, "rooms": {}, "seconds": {}}
        for room in rooms:
            t1 = time.perf_counter()
            rosters = run_room(self.base, self.drafters(room, seed, mkt if room in MARKET_ROOMS else own), order)
            t2 = time.perf_counter()
            out["rooms"][room] = {"rosters": {str(t): r for t, r in rosters.items()}, "grade": self.grade(rosters, seed)}
            out["seconds"][room] = {"draft": round(t2 - t1, 2), "season": round(time.perf_counter() - t2, 2)}
        out["seconds"]["total"] = round(time.perf_counter() - t0, 2)
        return out


# ---------------------------------------------------------------- driver (resumable, multiprocess)
_EXP: Experiment | None = None


def _work(job: tuple[int, list[str]]) -> dict:
    assert _EXP is not None
    return _EXP.run_seed(*job)


def build_experiment(
    proj: ProjectionSet, settings: LeagueSettings, truths: tuple[str, ...] = ("ours", "market", "blend"),
    n_season_sims: int = 500, nba_schedule_file: Path | None = None, perturb: dict | None = None,
    noise: float = 1.0, blend_weight: float = 0.5, nba=None,  # noqa: ANN001
) -> tuple[Experiment, dict]:
    """Experiment with season-simulator contexts for each truth; returns (experiment, market diagnostics)."""
    from fantasy.sim.context import build_context
    from fantasy.sim.market_truth import blend_projections, market_implied_projections

    team_ids = list(range(1, settings.num_teams + 1))
    ours = build_context(proj, settings, team_ids, nba_schedule_file=nba_schedule_file, nba=nba)
    ctxs = {"ours": ours}
    mt = market_implied_projections(proj, settings)
    diag = mt.diagnostics
    if "market" in truths or "blend" in truths:
        if "market" in truths:
            ctxs["market"] = build_context(mt.proj, settings, team_ids, nba=ours.nba)
        if "blend" in truths:
            ctxs["blend"] = build_context(blend_projections(proj, mt, blend_weight), settings, team_ids, nba=ours.nba)
    exp = Experiment(proj=proj, settings=settings, truths={k: ctxs[k] for k in truths}, n_season_sims=n_season_sims,
                     noise=noise, perturb=perturb or {}, market_proj=mt.proj)
    return exp, diag


def run_experiment(
    exp: Experiment, seeds: list[int], out_path: Path, workers: int = 1,
    subset_seeds: set[int] | None = None, rooms: list[str] | None = None,
) -> list[dict]:
    """Run every seed (skipping rooms already stored in `out_path`, a JSON-lines file) and return all results
    for `seeds`. Workers are forked after `exp` is built, so they share it copy-on-write."""
    global _EXP
    rooms = rooms or ROOMS
    done = {r["seed"]: r for r in merged_results(out_path)} if out_path.exists() else {}
    jobs = []
    for s in seeds:
        want = rooms + (SUBSET_ROOMS if subset_seeds and s in subset_seeds else [])
        have = set(done.get(s, {}).get("rooms", {}))
        missing = [r for r in want if r not in have]
        if missing:
            jobs.append((s, missing))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _EXP = exp
    if jobs:
        if workers > 1:
            import multiprocessing as mp

            with mp.get_context("fork").Pool(workers) as pool:
                for res in pool.imap_unordered(_work, jobs):
                    _append(out_path, res)
        else:
            for job in jobs:
                _append(out_path, _work(job))
    return [r for r in merged_results(out_path) if r["seed"] in set(seeds)]


def _append(path: Path, res: dict) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(res) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def load_results(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def merged_results(path: Path) -> list[dict]:
    """Merge partial records of the same seed (a resumed run appends the missing rooms only)."""
    by: dict[int, dict] = {}
    for r in load_results(path):
        m = by.setdefault(r["seed"], {"seed": r["seed"], "rooms": {}, "seconds": {}})
        m["rooms"].update(r["rooms"])
        m["seconds"].update(r.get("seconds", {}))
    return [by[s] for s in sorted(by)]


# ---------------------------------------------------------------- summaries
def _metric(results: list[dict], room: str, truth: str, metric: str) -> np.ndarray:
    """(n_seeds, T) per-seed means for the seeds that have this room."""
    rows = [r["rooms"][room]["grade"][truth][metric] for r in results if room in r["rooms"]
            and truth in r["rooms"][room]["grade"]]
    return np.array(rows, dtype=float)


def _ms(x: np.ndarray) -> dict:
    n = len(x)
    return {"mean": round(float(x.mean()), 4), "se": round(float(x.std(ddof=1) / np.sqrt(n)), 4) if n > 1 else None, "n": n}


def _paired(results: list[dict], a: tuple[str, str], b: tuple[str, str], metric: str, slot: int) -> dict:
    """Mean and paired SE of metric(a) - metric(b) for `slot`, over seeds that have both."""
    xs = [(r["rooms"][a[0]]["grade"][a[1]][metric][slot - 1] - r["rooms"][b[0]]["grade"][b[1]][metric][slot - 1])
          for r in results if a[0] in r["rooms"] and b[0] in r["rooms"]
          and a[1] in r["rooms"][a[0]]["grade"] and b[1] in r["rooms"][b[0]]["grade"]]
    return _ms(np.array(xs, dtype=float)) if xs else {"mean": None, "se": None, "n": 0}


def summarize(results: list[dict], my_slot: int = 1) -> dict:
    rooms = [r for r in ROOMS + SUBSET_ROOMS + MARKET_ROOMS if any(r in x["rooms"] for x in results)]
    truths = sorted({t for x in results for r in x["rooms"].values() for t in r["grade"]})
    metrics = ("p_playoffs", "p_title", "exp_rank")
    j = my_slot - 1
    slot1 = {room: {truth: {m: _ms(_metric(results, room, truth, m)[:, j]) for m in metrics}
                    for truth in truths if _metric(results, room, truth, "p_title").size}
             for room in rooms}
    by_slot = {}
    for room in ("A_sym", "A_bpa", "A_full", "adp_all", "C11", "C11m"):
        if room not in rooms:
            continue
        by_slot[room] = {}
        for truth in truths:
            arrs = {m: _metric(results, room, truth, m) for m in metrics}
            if not arrs["p_title"].size:
                continue
            by_slot[room][truth] = [{"slot": t + 1, **{m: _ms(arrs[m][:, t]) for m in metrics}}
                                    for t in range(arrs["p_title"].shape[1])]
    diffs = {}
    pairs = {
        "B_minus_adp_all": (("B", "ours"), ("adp_all", "ours")),
        "B_minus_A_sym": (("B", "ours"), ("A_sym", "ours")),
        "B_minus_C11": (("B", "ours"), ("C11", "ours")),
        "B_minus_C3": (("B", "ours"), ("C3", "ours")),
        "B_minus_C6": (("B", "ours"), ("C6", "ours")),
        "C11_minus_A_sym": (("C11", "ours"), ("A_sym", "ours")),
        "A_full_minus_A_sym": (("A_full", "ours"), ("A_sym", "ours")),
        "A_bpa_minus_A_sym": (("A_bpa", "ours"), ("A_sym", "ours")),
        "B_ours_minus_B_market": (("B", "ours"), ("B", "market")),
        "B_ours_minus_B_blend": (("B", "ours"), ("B", "blend")),
        "B_minus_A_sym_market": (("B", "market"), ("A_sym", "market")),
        "B_minus_A_sym_blend": (("B", "blend"), ("A_sym", "blend")),
        "B_minus_adp_all_market": (("B", "market"), ("adp_all", "market")),
        "C11_minus_A_sym_market": (("C11", "market"), ("A_sym", "market")),
        "B_minus_C3m": (("B", "ours"), ("C3m", "ours")),
        "B_minus_C11m": (("B", "ours"), ("C11m", "ours")),
        "B_minus_C3m_market": (("B", "market"), ("C3m", "market")),
        "B_minus_C11m_market": (("B", "market"), ("C11m", "market")),
    }
    for name, (a, b) in pairs.items():
        if a[1] in truths and b[1] in truths:
            diffs[name] = {m: _paired(results, a, b, m, my_slot) for m in metrics}
    secs = [r["seconds"] for r in results]
    timing = {room: round(float(np.mean([s[room]["draft"] for s in secs if room in s])), 2)
              for room in rooms if any(room in s for s in secs)}
    return {"n_seeds": len(results), "my_slot": my_slot, "truths": truths, "rooms": rooms, "slot1": slot1,
            "by_slot": by_slot, "paired_diffs": diffs, "mean_draft_seconds": timing,
            "baselines": {"p_playoffs": 0.5, "p_title": round(1 / 12, 4), "exp_rank": 6.5}}


def decomposition_table(summary: dict) -> pd.DataFrame:
    rows = []
    for room in summary["rooms"]:
        for truth, v in summary["slot1"][room].items():
            rows.append({"room": room, "truth": truth, "n": v["p_title"]["n"],
                         "p_playoffs": v["p_playoffs"]["mean"], "se_po": v["p_playoffs"]["se"],
                         "p_title": v["p_title"]["mean"], "se_title": v["p_title"]["se"],
                         "exp_rank": v["exp_rank"]["mean"]})
    return pd.DataFrame(rows)
