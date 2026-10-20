"""Decision-grade trade evaluation (inseason-0.3.0; quant-review findings M1, M2, M4, M5, M6).

* **Base with the free move (M1).** A trade is compared with the league in which I already made the best free
  roster move (e.g. add the best free agent A, drop my worst player X): that move needs no partner, so a trade must
  beat it, not the untouched roster. If a trade gives away X, the free move drops my next-lowest-value player
  instead (when A is worth more than him), so both worlds have claimed A.
* **Realistic refill (M1, M6).** A roster left short by the trade adds the (k+1)-th best *realistic* free agent of
  the Yahoo capture (not tagged O/INJ; `refill_skip` k, default 5: others claim before a trade clears) or a
  replacement-level player. The best remaining free agent (k = 0) is reported only as the optimistic bound. Drops
  and refill order use each projection set's own values (consistent per set).
* **Schedule-neutral delta (M2).** The same trade re-simulated with every incoming player given the NBA schedule of
  the outgoing player he replaces (by value rank), base re-run with the same schedule (paired). raw - neutral =
  the schedule part of the gain; "schedule bet" when it is more than half of a positive ΔP(title).
* **Seeds (M4).** Deltas are averaged over fresh seeds (never the screen's seed); SE = max(paired SE / sqrt(n),
  seed-to-seed sd / sqrt(n)), and the empirical factor sd_across_seeds / paired SE is reported.
  Class: "helps" only if ΔP(title) > 2 SE and neither ΔP(playoffs) nor Δcat-wins < -1 SE; ΔP(title) > 2 SE with a
  falling floor = "speculative"; ΔP(title) or ΔP(playoffs) < -2 SE = "hurts"; ΔP(playoffs) > 2 SE with the title
  unresolved = "floor_only"; else "noise".
* **Partner (M5).** Acceptable only if the partner's simulated ΔP(playoffs) >= -1 pp under every projection set
  (`partner_ok`); `win_win` = it helps me and the partner's ΔP(playoffs) >= 0 under every set.

All outputs are SIMULATED (position-aware season sim); ± is Monte Carlo error only (no projection/model error, which
the projection sets bracket).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import sqrt
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from fantasy.inseason.possim import make_sim, replacement_id
from fantasy.inseason.trade import Trade, apply_trade, rostered
from fantasy.sim.engine import SimResult
from fantasy.sim.league import LeagueState
from fantasy.sim.report import paired_delta

if TYPE_CHECKING:
    from fantasy.sim.context import SimContext

METRICS = ("p_playoffs", "p_title", "exp_cat_wins")
PARTNER_PO_TOL = 0.01  # PRIOR: a partner accepts losing at most 1 pp of P(playoffs) (simulated)


@dataclass(frozen=True)
class Move:
    add: str
    drop: str


@dataclass
class SetSpec:
    """One projection set: its sim context and its realistic free-agent pool (best first by its own values)."""
    ctx: SimContext
    pool: list[str]
    note: str = ""
    sim_kw: dict[str, Any] = field(default_factory=dict)  # extra SimParams (e.g. playoff_teams in tests)


@dataclass
class EvalParams:
    n_sims: int = 2000
    seeds: tuple[int, ...] = (101, 102, 103, 104, 105)
    refill_skip: int = 5  # PRIOR: (k+1)-th best realistic free agent
    replacement: bool = False  # refill with replacement-level players instead
    neutral: bool = True
    optimistic: bool = True
    sig: float = 2.0


def apply_move(league: LeagueState, my_team: int, move: Move | None) -> LeagueState:
    new = league.copy()
    if move is not None:
        new.rosters[my_team] = [p for p in new.rosters[my_team] if p != move.drop] + [move.add]
    return new


def pre_trade_league(league: LeagueState, my_team: int, trade: Trade, move: Move | None,
                     values: pd.DataFrame) -> tuple[LeagueState, str]:
    """League on which `trade` is executed: the free move applied; if the trade gives away the move's drop, the
    free move drops my lowest-value player not in the trade instead (if the added player is worth more)."""
    if move is None:
        return league.copy(), "no free move"
    if move.drop not in trade.give:
        return apply_move(league, my_team, move), f"free move {move.add} for {move.drop}"
    val = values["value"]
    rest = [p for p in league.rosters[my_team] if p not in trade.give]
    alt = min(rest, key=lambda p: float(val.get(p, -99.0)))
    if float(val.get(move.add, -99.0)) > float(val.get(alt, -99.0)):
        return apply_move(league, my_team, Move(move.add, alt)), f"free move {move.add} for {alt} ({move.drop} traded)"
    return league.copy(), f"no free move ({move.drop} traded; {move.add} not better than {alt})"


def refill_pool(pool: Sequence[str], skip: int, replacement: bool) -> list[str]:
    return [replacement_id(f"refill{i}") for i in range(4)] if replacement else list(pool)[skip:]


def trade_world(pre: LeagueState, my_team: int, trade: Trade, values: pd.DataFrame, roster_size: int,
                pool: Sequence[str], refill_skip: int = 5, replacement: bool = False) -> tuple[LeagueState, dict]:
    """Trade executed on `pre`; short rosters refill from `pool` minus rostered players, skipping `refill_skip`."""
    free = [p for p in pool if p not in rostered(pre)]
    return apply_trade(pre, my_team, trade, values, roster_size, fa_pool=refill_pool(free, refill_skip, replacement))


def neutral_proj(df: pd.DataFrame, trade: Trade, values: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Projections with each incoming player on the NBA team (schedule) of the outgoing player of the same value
    rank (the last outgoing player for any extra incoming one)."""
    val = values["value"]
    give = sorted(trade.give, key=lambda p: -float(val.get(p, -99.0)))
    get = sorted(trade.get, key=lambda p: -float(val.get(p, -99.0)))
    out = df.copy()
    mapping = {}
    for j, p in enumerate(get):
        src = give[min(j, len(give) - 1)]
        if p in out.index and src in df.index:
            out.loc[p, "team"] = df.loc[src, "team"]
            mapping[p] = str(df.loc[src, "team"])
    return out, mapping


def _num(x: float) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 5)


def agg_seeds(ds: Sequence[dict]) -> dict:
    """Seed-averaged paired deltas; SE = max(paired, seed-to-seed) / sqrt(n)."""
    n = len(ds)
    out = {}
    for m in METRICS:
        x = np.array([d[m]["delta"] for d in ds], dtype=float)
        se = np.array([d[m]["delta_se"] for d in ds], dtype=float)
        rms = float(np.sqrt(np.mean(se**2)))
        se_paired = rms / sqrt(n)
        sd = float(x.std(ddof=1)) if n > 1 else float("nan")
        se_seed = sd / sqrt(n) if n > 1 else float("nan")
        used = max(se_paired, se_seed) if n > 1 else se_paired
        out[m] = {"before": _num(float(np.mean([d[m]["before"] for d in ds]))), "delta": _num(float(x.mean())),
                  "delta_se": _num(used), "se_paired": _num(se_paired), "sd_across_seeds": _num(sd),
                  "seed_se_factor": _num(sd / rms) if n > 1 and rms > 0 else None, "n_seeds": n}
    return out


def classify(me: dict, sig: float = 2.0) -> str:
    """'helps' | 'speculative' | 'hurts' | 'floor_only' | 'noise' (rule in the module docstring)."""
    def z(m: str) -> float:
        d, se = me[m]["delta"], me[m]["delta_se"] or 0.0
        return d / se if se > 0 else (np.inf if d > 0 else -np.inf if d < 0 else 0.0)
    zt, zp, zc = z("p_title"), z("p_playoffs"), z("exp_cat_wins")
    if zt > sig:
        return "helps" if (zp >= -1 and zc >= -1) else "speculative"
    if zt < -sig or zp < -sig:
        return "hurts"
    if zp > sig and zc >= -1:
        return "floor_only"
    return "noise"


def combine_classes(classes: Sequence[str]) -> str:
    """One class across projection sets: unanimous, else 'speculative' if every set is helps/speculative, 'hurts' if
    any set hurts and none helps, else 'mixed'."""
    cs = set(classes)
    if len(cs) == 1:
        return classes[0]
    if cs <= {"helps", "speculative"}:
        return "speculative"
    if "hurts" in cs and not cs & {"helps", "speculative"}:
        return "hurts"
    return "mixed"


def evaluate_trade(trade: Trade, pre: LeagueState, base_league: LeagueState, my_team: int, sets: Mapping[str, SetSpec],
                   roster_size: int, params: EvalParams, base_cache: dict | None = None) -> dict:
    """Seed-averaged paired season deltas of `trade` (executed on `pre`) vs `base_league`, under every set."""
    cache = base_cache if base_cache is not None else {}
    by_set: dict[str, dict] = {}
    for name, spec in sets.items():
        ctx = spec.ctx
        vals = ctx.values
        world, moves = trade_world(pre, my_team, trade, vals, roster_size, spec.pool, params.refill_skip,
                                   params.replacement)
        opt_world = trade_world(pre, my_team, trade, vals, roster_size, spec.pool, 0, False)[0]
        ndf, mapping = neutral_proj(ctx.proj.df, trade, vals) if params.neutral else (None, {})
        me, pt, neu, opt = [], [], [], []
        for seed in params.seeds:
            sim = make_sim(ctx, params.n_sims, seed, **spec.sim_kw)
            key = (name, seed, params.n_sims, tuple((t, tuple(ps)) for t, ps in sorted(base_league.rosters.items())))
            if key not in cache:
                cache[key] = sim.run(base_league)
            base: SimResult = cache[key]
            alt = sim.run(world)
            me.append(paired_delta(base, alt, my_team))
            pt.append(paired_delta(base, alt, trade.partner))
            if params.optimistic:
                opt.append(paired_delta(base, sim.run(opt_world), my_team))
            if ndf is not None:
                simn = make_sim(ctx, params.n_sims, seed, ndf, **spec.sim_kw)
                neu.append(paired_delta(simn.run(base_league), simn.run(world), my_team))
        names = vals["name"]
        r: dict[str, Any] = {
            "me": agg_seeds(me), "partner": agg_seeds(pt),
            "refill_moves": {str(t): {k: [str(names.get(p, p)) for p in v] for k, v in m.items()}
                             for t, m in moves.items()},
        }
        r["class"] = classify(r["me"], params.sig)
        r["partner_ok"] = (r["partner"]["p_playoffs"]["delta"] or 0.0) >= -PARTNER_PO_TOL
        if opt:
            r["optimistic_me"] = agg_seeds(opt)
        if neu:
            r["neutral_me"] = agg_seeds(neu)
            raw, nt = r["me"]["p_title"]["delta"] or 0.0, r["neutral_me"]["p_title"]["delta"] or 0.0
            share = (raw - nt) / raw if raw > 0 else None
            r["schedule"] = {"incoming_team_used": mapping, "title_raw": _num(raw), "title_neutral": _num(nt),
                             "schedule_share_of_title_gain": _num(share) if share is not None else None,
                             "schedule_bet": bool(share is not None and share > 0.5)}
        by_set[name] = r
    classes = [r["class"] for r in by_set.values()]
    overall = combine_classes(classes)
    partner_ok = all(r["partner_ok"] for r in by_set.values())
    shares = [r["schedule"] for r in by_set.values() if "schedule" in r]
    raw_m = float(np.mean([s["title_raw"] for s in shares])) if shares else 0.0
    neu_m = float(np.mean([s["title_neutral"] for s in shares])) if shares else 0.0
    return {
        "by_set": by_set, "class": overall, "class_by_set": dict(zip(by_set, classes, strict=True)),
        "partner_ok": partner_ok,
        "win_win": overall == "helps" and all((r["partner"]["p_playoffs"]["delta"] or 0.0) >= 0
                                              for r in by_set.values()),
        "schedule_bet": bool(shares) and raw_m > 0 and (raw_m - neu_m) > 0.5 * raw_m,
        "seeds": list(params.seeds), "n_sims": params.n_sims,
        "refill": "replacement level" if params.replacement else f"realistic free agent #{params.refill_skip + 1}",
    }


def choose_free_move(sim: Any, league: LeagueState, my_team: int, add: str, drops: Sequence[str],
                     sig: float = 2.0, base: SimResult | None = None) -> tuple[Move | None, list[dict]]:
    """Best free move add -> drop among `drops` (whole season, one seed): the largest Δcat-wins among moves whose
    Δcat-wins > sig SE and whose ΔP(playoffs)/ΔP(title) are not < -sig SE. None if no move qualifies."""
    base = base or sim.run(league)
    rows: list[dict[str, Any]] = []
    for d in drops:
        dl = paired_delta(base, sim.run(apply_move(league, my_team, Move(add, d))), my_team)
        ok = (dl["exp_cat_wins"]["delta"] > sig * dl["exp_cat_wins"]["delta_se"]
              and all(dl[m]["delta"] >= -sig * dl[m]["delta_se"] for m in ("p_playoffs", "p_title")))
        rows.append({"add": add, "drop": d, "season": dl, "qualifies": ok})
    good = [r for r in rows if r["qualifies"]]
    if not good:
        return None, rows
    best = max(good, key=lambda r: r["season"]["exp_cat_wins"]["delta"])
    return Move(add, str(best["drop"])), rows
