"""Validation of the trade-scan screen (inseason-0.2.0) against the paired season sim, real league 44495.

Reproduce (local session, from the repo root; market projections are git-ignored, see conservative_sim.md):
    python docs/research/trade_scan_validation_2026-10-20.py [--market var/projections_alt/market.csv]

1. Random sample of 40 enumerated trades (no screen filter, seed 1): screen proxy (all-play expected cats/week)
   vs simulated Δ category wins / ΔP(playoffs) / ΔP(title) for my team (blend, 1500 paired sims). Spearman rho.
2. The Murray trade from the 2026-10-20 review (Ware + VanVleet -> Jamal Murray, Yeet): its screen proxy, rank
   among Yeet trades, and its paired sims (blend and, if given, market) with the Champagnie refill.
Prints JSON. Output quoted in docs/research/streaming_and_trade_scan.md.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from scipy.stats import spearmanr

from fantasy.inseason.freeagents import load_free_agents, realistic_refills
from fantasy.inseason.possim import PositionAwareSimulator
from fantasy.inseason.trade import Trade, apply_trade
from fantasy.inseason.tradescan import Screen, enumerate_trades, games_per_week, start_shares
from fantasy.league.settings import load_settings
from fantasy.model.lines import load_projections
from fantasy.sim.context import build_context
from fantasy.sim.engine import SimParams
from fantasy.sim.league import from_json
from fantasy.sim.report import paired_delta

ap = argparse.ArgumentParser()
ap.add_argument("--market", type=Path, default=None)
ap.add_argument("--sims", type=int, default=1500)
ap.add_argument("--n", type=int, default=40)
args = ap.parse_args()

lg = from_json(Path("data/manual/league_rosters_2026-27.json"))
st = load_settings().model_copy(update={"num_teams": len(lg.rosters)})
sched = Path("data/manual/league_schedule.json")
ctx = build_context(load_projections(None), st, lg.team_ids, schedule_file=sched)
fas, _ = load_free_agents(Path("data/raw/yahoo/free_agents_2026-10-20.json"), __import__("datetime").date(2026, 10, 20))
taken = {p for ps in lg.rosters.values() for p in ps}
pool = [p for p in realistic_refills(fas, ctx.values["value"].to_dict()) if p not in taken]


def simulator(c):
    return PositionAwareSimulator(c.proj.df, c.values, c.replacement, c.settings, c.nba, c.calendar, c.matchups,
                                  SimParams(n_sims=args.sims, seed=0))


sim = simulator(ctx)
base = sim.run(lg)
regular = [w.week for w in ctx.calendar if w.week < min(st.playoff_weeks)]
days = [d for w in ctx.calendar if w.week in regular for d in w.days()]
gpw = games_per_week(ctx.nba, ctx.calendar, regular)
screen = Screen(base, lg, ctx.proj.df, ctx.values, st, gpw, start_shares(lg, ctx.proj.df, ctx.values, st, ctx.nba, days))
s0 = screen.score({1: lg.rosters[1]})[1]

trades = enumerate_trades(lg, 1, ("1-1", "2-1", "1-2"))
sample = random.Random(1).sample(trades, args.n)
rows = []
for tr in sample:
    new, _ = apply_trade(lg, 1, tr, ctx.values, st.num_rounds, fa_pool=pool)
    proxy = screen.score({1: new.rosters[1], tr.partner: new.rosters[tr.partner]})[1] - s0
    d = paired_delta(base, sim.run(new), 1)
    rows.append({"trade": tr.label(ctx.values["name"]), "proxy": proxy,
                 **{k: d[k]["delta"] for k in ("exp_cat_wins", "p_playoffs", "p_title")}})
px = [r["proxy"] for r in rows]
out: dict = {"n": len(rows), "sims": args.sims,
             "spearman": {k: round(float(spearmanr(px, [r[k] for r in rows]).statistic), 3)
                          for k in ("exp_cat_wins", "p_playoffs", "p_title")},
             "sign_agreement_cat_wins": round(sum((r["proxy"] > 0) == (r["exp_cat_wins"] > 0) for r in rows) / len(rows), 3)}

# Murray trade
murray = Trade(6, ["kelel-ware", "fred-vanvleet"], ["jamal-murray"])
yeet = [t for t in trades if t.partner == 6]
po_weeks = list(st.playoff_weeks)
po_screen = Screen(base, lg, ctx.proj.df, ctx.values, st, games_per_week(ctx.nba, ctx.calendar, po_weeks),
                   screen.shares, weeks=po_weeks)
p0 = po_screen.score({1: lg.rosters[1]})[1]
proxies, po_proxies = [], []
for tr in yeet:
    new, _ = apply_trade(lg, 1, tr, ctx.values, st.num_rounds, fa_pool=pool)
    key = (tuple(tr.give), tuple(tr.get))
    proxies.append((screen.score({1: new.rosters[1]})[1] - s0, key))
    po_proxies.append((po_screen.score({1: new.rosters[1]})[1] - p0, key))
proxies.sort(key=lambda x: -x[0])
po_proxies.sort(key=lambda x: -x[0])
po_px = [po_screen.score({1: apply_trade(lg, 1, tr, ctx.values, st.num_rounds, fa_pool=pool)[0].rosters[1]})[1] - p0
         for tr in sample]
out["spearman_playoff_screen"] = {k: round(float(spearmanr(po_px, [r[k] for r in rows]).statistic), 3)
                                  for k in ("exp_cat_wins", "p_playoffs", "p_title")}
mk = (tuple(murray.give), tuple(murray.get))
rank = 1 + [k for _, k in proxies].index(mk)
rank_po = 1 + [k for _, k in po_proxies].index(mk)
new, moves = apply_trade(lg, 1, murray, ctx.values, st.num_rounds, fa_pool=pool)
res = {"proxy": round(proxies[rank - 1][0], 4), "rank_among_yeet_trades": rank,
       "playoff_proxy": round(po_proxies[rank_po - 1][0], 4), "playoff_rank_among_yeet_trades": rank_po,
       "of": len(proxies), "refill": moves, "blend": paired_delta(base, sim.run(new), 1)}
if args.market:
    mctx = build_context(load_projections(args.market), st, lg.team_ids, schedule_file=sched, nba=ctx.nba)
    msim = simulator(mctx)
    res["market"] = paired_delta(msim.run(lg), msim.run(new), 1)
out["murray"] = res
out["sample"] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]
print(json.dumps(out, indent=1))
