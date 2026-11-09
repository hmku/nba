"""Preseason-budget variants with leave-one-season-out u (= the other season's full-season share of minutes to players
outside the prior), plus a league-wide (not per-team) control. Writes runs/pre2_<T>_<cp>.pkl."""
import itertools
import pickle
import time

import bt
import pandas as pd

from fantasy.model import team_minutes as TM

U_FULL = {2025: 0.1345, 2026: 0.1558}


def global_constrain(prior, share, p):
    d = prior.assign(_t=prior["team"], team="ALL")
    q = TM.TeamMinutesParams(alpha=p.alpha, beta=p.beta, unprojected_share=p.unprojected_share,
                             direction=p.direction, min_players=1, minutes_per_game=p.minutes_per_game * 30)
    out, _ = TM.constrain(d, share, q)
    return out.assign(team=out["_t"]).drop(columns="_t")


if __name__ == "__main__":
    for T in (2025, 2026):
        pg, tg, fg = bt.load(T)
        prior = pd.read_csv(bt.OUT / f"priors_{T}.csv")
        u = U_FULL[2026 if T == 2025 else 2025]
        for cp in bt.checkpoints(tg):
            t0 = time.time()
            bt.set_pop(T, 1.0)
            res = {}
            for a, b, d in itertools.product([0.25, 0.5, 0.75], [0.0, 2.0], ["both", "trim"]):
                p = TM.TeamMinutesParams(alpha=a, beta=b, direction=d, unprojected_share=u)
                pri2, _ = TM.constrain(prior, prior["gp"] / 82, p)
                res[f"pre_a{a:g}_b{b:g}_{d}"] = bt.run(T, cp, pri2, pg, tg, fg)
            for a in (0.5, 1.0):
                p = TM.TeamMinutesParams(alpha=a, unprojected_share=u)
                res[f"glob_a{a:g}"] = bt.run(T, cp, global_constrain(prior, prior["gp"] / 82, p), pg, tg, fg)
            pickle.dump(res, open(bt.RUNS / f"pre2_{T}_{cp}.pkl", "wb"))
            print(T, cp, f"{time.time() - t0:.0f}s", flush=True)
