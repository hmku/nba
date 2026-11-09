"""Evaluate the backtest runs (bt.py). python ev.py [t022|t023|all]"""
from __future__ import annotations

import itertools
import json
import pickle
import sys

import bt
import numpy as np
import pandas as pd

from fantasy.model import team_minutes as TM

CATS = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct"]
PCT = {"fg_pct": ("fgm", "fga"), "ft_pct": ("ftm", "fta")}
B = 300
rng = np.random.default_rng(0)


def targets(pg, as_of):
    f = pg[(pg.game_date > as_of) & pg.played.astype(bool) & (pg["min"] > 0)].sort_values(["game_date", "game_id"])
    f = f.drop_duplicates(["game_id", "player_id"])
    g = f.groupby("player_id")
    t = g[bt.C].sum()
    t["n"] = g.size()
    t["next5"] = g["min"].apply(lambda s: s.iloc[:5].mean())
    t["n5"] = g.size().clip(upper=5)
    return t


def zscales(pg):
    p = pg[pg.played.astype(bool) & (pg["min"] > 0)]
    g = p.groupby("player_id")
    s = g[bt.C].sum().div(g.size(), axis=0)
    s["n"] = g.size()
    s = s[(s["min"] >= 20) & (s["n"] >= 20)]
    lg = {"fg_pct": s.fgm.sum() / s.fga.sum(), "ft_pct": s.ftm.sum() / s.fta.sum()}
    sd = {c: float(s[c].std()) for c in ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"]}
    sd["fg_pct"] = float(((s.fgm / s.fga - lg["fg_pct"]) * s.fga).std())
    sd["ft_pct"] = float(((s.ftm / s.fta.where(s.fta > 0) - lg["ft_pct"]) * s.fta).std())
    return sd, lg


def errors(pred: pd.DataFrame, tgt: pd.DataFrame, sd, lg) -> pd.DataFrame:
    p = pred.drop_duplicates("player_id").set_index("player_id")
    ids = p.index.intersection(tgt.index)
    p, t = p.loc[ids], tgt.loc[ids]
    n = t["n"]
    act = t[bt.C].div(n, axis=0)
    e = pd.DataFrame(index=ids)
    e["w"] = n
    e["mpg"] = p["mpg"].astype(float) - act["min"]
    e["next5"] = p["mpg_next5"].astype(float) - t["next5"]
    e["w5"] = t["n5"]
    z2 = np.zeros(len(ids))
    for c in CATS:
        if c in PCT:
            mk, at = PCT[c]
            pp = p[mk] / p[at].where(p[at] > 0)
            pa = act[mk] / act[at].where(act[at] > 0)
            ip = ((pp.fillna(lg[c]) - lg[c]) * p[at]).to_numpy(float)
            ia = ((pa.fillna(lg[c]) - lg[c]) * act[at]).to_numpy(float)
            z = (ip - ia) / sd[c]
        else:
            z = (p[c].astype(float) - act[c]).to_numpy() / sd[c]
            e[c] = p[c].astype(float) - act[c]
        z2 += z ** 2
    e["z2"] = z2
    e["prior_mpg"] = p["mpg_prior"] if "mpg_prior" in p else np.nan
    e["src"] = p["prior_source"]
    e["ros_share"] = p["ros_share"]
    return e


def boot(frames: dict[str, pd.DataFrame], base: str, cols=("mpg", "z2")):
    """frames: variant -> stacked error table with 'cl' cluster ids (same rows). RMSE, bias, paired diff +- SE."""
    b = frames[base]
    cl = pd.factorize(b["cl"])[0]
    ncl = cl.max() + 1
    Cm = np.stack([np.bincount(rng.integers(0, ncl, ncl), minlength=ncl) for _ in range(B)])
    w = b["w"].to_numpy(float)
    den = np.bincount(cl, weights=w, minlength=ncl)
    res = {}
    for v, f in frames.items():
        r = {}
        for c in cols:
            x = f[c].to_numpy(float) ** 2 if c != "z2" else f[c].to_numpy(float)
            num = np.bincount(cl, weights=w * np.nan_to_num(x), minlength=ncl)
            r[c] = np.sqrt(num.sum() / den.sum())
            r[c + "_b"] = np.sqrt((Cm @ num) / (Cm @ den))
        bn = np.bincount(cl, weights=w * f["mpg"].to_numpy(float), minlength=ncl)
        r["bias"] = bn.sum() / den.sum()
        r["bias_se"] = float(np.std((Cm @ bn) / (Cm @ den), ddof=1))
        res[v] = r
    out = {}
    for v, r in res.items():
        o = {"mpg_rmse": round(r["mpg"], 4), "mpg_bias": round(r["bias"], 3), "mpg_bias_se": round(r["bias_se"], 3),
             "z9_rms": round(float(r["z2"]), 4)}
        if v != base:
            for c, k in (("mpg", "d_mpg"), ("z2", "d_z9")):
                d = r[c + "_b"] - res[base][c + "_b"]
                o[k] = round(float(r[c] - res[base][c]), 4)
                o[k + "_se"] = round(float(np.std(d, ddof=1)), 4)
        out[v] = o
    return out


def u_estimates():
    """share of minutes AFTER each checkpoint going to players not in the update's row set (pop1)."""
    u = {}
    for T in (2025, 2026):
        pg, tg, fg = bt.load(T)
        for j, cp in enumerate(bt.checkpoints(tg)):
            res = pickle.load(open(bt.RUNS / f"{T}_{cp}.pkl", "rb"))
            tgt = targets(pg, cp)
            known = set(res["pop1"].player_id)
            u[(T, j)] = float(tgt.loc[~tgt.index.isin(known), "min"].sum() / tgt["min"].sum())
    return u


def stacked(variant_fn, ks=None):
    """variant_fn(T, j, res) -> {variant: pred}; returns {variant: stacked errors} over seasons x checkpoints."""
    acc: dict[str, list] = {}
    for T in (2025, 2026):
        pg, tg, fg = bt.load(T)
        pg["played"] = pg["played"].astype(bool)
        sd, lg = zscales(pg)
        for j, cp in enumerate(bt.checkpoints(tg)):
            if ks is not None and j not in ks:
                continue
            res = pickle.load(open(bt.RUNS / f"{T}_{cp}.pkl", "rb"))
            tgt = targets(pg, cp)
            preds = variant_fn(T, j, res)
            common = None
            errs = {v: errors(p, tgt, sd, lg) for v, p in preds.items()}
            for e in errs.values():
                common = e.index if common is None else common.intersection(e.index)
            for v, e in errs.items():
                e = e.loc[common].assign(cl=[f"{T}:{i}" for i in common], T=T, k=j)
                acc.setdefault(v, []).append(e)
    return {v: pd.concat(x) for v, x in acc.items()}


def t022():
    u = u_estimates()
    print("u(k) future-minutes share to players outside the row set:",
          {f"{T}/{bt.DAYS[j]}": round(v, 4) for (T, j), v in u.items()})
    grid = list(itertools.product([0.25, 0.5, 0.75, 1.0], [0.0, 1.0, 2.0, 4.0], ["both", "trim"]))

    def fn(T, j, res):
        other = 2026 if T == 2025 else 2025
        base = res["pop1"]
        out = {"base": base}
        for a, b, d in grid:
            p = TM.TeamMinutesParams(alpha=a, beta=b, direction=d, unprojected_share=u[(other, j)])
            out[f"ros_a{a:g}_b{b:g}_{d}"], _ = TM.constrain(base, base["ros_share"], p, extra_mpg_cols=("mpg_next5",))
        for v in res:
            if v.startswith("pre_"):
                out[v] = res[v]
        return out

    allres = {}
    for name, ks in (("k>=1 (in-season checkpoints)", [1, 2, 3, 4]), ("k=0 (preseason)", [0])):
        fr = stacked(fn, ks)
        rep = boot(fr, "base")
        allres[name] = rep
        print("\n==", name, "rows", len(fr["base"]))
        top = sorted(rep.items(), key=lambda kv: kv[1]["mpg_rmse"])[:12]
        for v, o in [("base", rep["base"])] + top:
            print(f"{v:28s}", o)
        for v in [k for k in rep if k.startswith("pre_")]:
            print(f"{v:28s}", rep[v])
    json.dump(allres, open(bt.OUT / "t022.json", "w"), indent=1)
    return u




def obs_only(res_pop, pg, cp):
    """naive alternative for population rows: observed per-game line to date (mpg = observed)."""
    p = res_pop.copy()
    past = pg[(pg.game_date <= cp) & pg.played.astype(bool) & (pg["min"] > 0)].drop_duplicates(["game_id", "player_id"])
    g = past.groupby("player_id")
    a = g[bt.C].sum().div(g.size(), axis=0)
    m = p["player_id"].isin(a.index) & (p["prior_source"] == "population")
    for c in bt.C:
        col = "mpg" if c == "min" else c
        p.loc[m, col] = p.loc[m, "player_id"].map(a[c])
    p.loc[m, "mpg_next5"] = p.loc[m, "mpg"]
    return p


def t023():
    def fn(T, j, res):
        pg, tg, fg = bt.load(T)
        cp = bt.checkpoints(tg)[j]
        out = {v: res[v] for v in ("pop1", "pop0.25", "pop0.5", "pop2")}
        out["observed"] = obs_only(res["pop1"], pg, cp)
        return out

    fr = stacked(fn, [1, 2, 3, 4])
    m = (fr["pop1"]["src"] == "population").to_numpy()
    sub = {v: f[m] for v, f in fr.items()}
    print("population rows (player x checkpoint):", int(m.sum()), "players", sub["pop1"]["cl"].nunique(),
          "future games", int(sub["pop1"]["w"].sum()))
    rep = boot(sub, "pop1")
    for v, o in rep.items():
        print(f"{v:10s}", o)
    # availability calibration of population rows: predicted ros_share vs realised share of remaining games
    rows = []
    for T in (2025, 2026):
        pg, tg, fg = bt.load(T)
        end = tg.game_date.max()
        from datetime import date, timedelta
        fend = (date.fromisoformat(end) - timedelta(days=21)).isoformat()
        for j, cp in enumerate(bt.checkpoints(tg)):
            if j == 0:
                continue
            res = pickle.load(open(bt.RUNS / f"{T}_{cp}.pkl", "rb"))["pop1"]
            r = res[(res.prior_source == "population") & (res.ros_team_games > 0)]
            fut = pg[(pg.game_date > cp) & (pg.game_date <= fend) & pg.played.astype(bool) & (pg["min"] > 0)]
            n = fut.drop_duplicates(["game_id", "player_id"]).groupby("player_id").size()
            rows.append(pd.DataFrame({"pred": r.ros_share.to_numpy(), "act": (r.player_id.map(n).fillna(0) /
                                      r.ros_team_games).clip(upper=1).to_numpy(), "w": r.ros_team_games.to_numpy(),
                                      "T": T, "k": j}))
    a = pd.concat(rows)
    print("population availability: pred share", round(np.average(a.pred, weights=a.w), 3), "actual",
          round(np.average(a.act, weights=a.w), 3), "rmse games/82", round(82 * np.sqrt(np.average((a.pred - a.act) ** 2, weights=a.w)), 2),
          "n", len(a))
    for j, d in a.groupby("k"):
        print("  k", bt.DAYS[j], "pred", round(np.average(d.pred, weights=d.w), 3), "act", round(np.average(d.act, weights=d.w), 3))
    json.dump(rep, open(bt.OUT / "t023.json", "w"), indent=1)



def t022b():
    """preseason-budget variants with LOSO u (bt2) + in-season budget on top (ros, small u(k))."""
    u = u_estimates()

    def fn(T, j, res):
        tg = bt.load(T)[1]
        cp = bt.checkpoints(tg)[j]
        r2 = pickle.load(open(bt.RUNS / f"pre2_{T}_{cp}.pkl", "rb"))
        out = {"base": res["pop1"], **r2}
        other = 2026 if T == 2025 else 2025
        for a in (0.25, 0.5):
            p = TM.TeamMinutesParams(alpha=a, direction="trim", unprojected_share=u[(other, j)])
            for src in ("pre_a0.5_b0_both", "pre_a0.5_b2_both"):
                out[f"{src}+ros{a:g}"], _ = TM.constrain(r2[src], r2[src]["ros_share"], p, extra_mpg_cols=("mpg_next5",))
        return out

    allres = {}
    for name, ks in (("k>=1", [1, 2, 3, 4]), ("k=0", [0])) + tuple((f"k={bt.DAYS[j]}d", [j]) for j in (1, 2, 3, 4)):
        fr = stacked(fn, ks)
        rep = boot(fr, "base")
        allres[name] = rep
        print("\n==", name, "rows", len(fr["base"]))
        for v, o in sorted(rep.items(), key=lambda kv: kv[1]["mpg_rmse"]):
            print(f"{v:28s}", {k: (round(float(x), 4) if isinstance(x, float) else x) for k, x in o.items()})
        if name == "k>=1":
            # per season
            for T in (2025, 2026):
                sub = {v: f[f["T"] == T] for v, f in fr.items()}
                rp = boot(sub, "base")
                print("  season", T, {v: (rp[v].get("d_mpg"), rp[v].get("d_mpg_se"), rp[v].get("d_z9"), rp[v].get("d_z9_se"))
                                      for v in ("pre_a0.5_b0_both", "pre_a0.5_b2_both", "glob_a0.5", "pre_a0.25_b0_both")})
    json.dump(allres, open(bt.OUT / "t022b.json", "w"), indent=1)



def t023b():
    def fn(T, j, res):
        tg = bt.load(T)[1]
        cp = bt.checkpoints(tg)[j]
        r3 = pickle.load(open(bt.RUNS / f"pop3_{T}_{cp}.pkl", "rb"))
        return {"pop1": res["pop1"], **r3}

    fr = stacked(fn, [1, 2, 3, 4])
    m = (fr["pop1"]["src"] == "population").to_numpy()
    sub = {v: f[m] for v, f in fr.items()}
    rep = boot(sub, "pop1")
    for v, o in sorted(rep.items(), key=lambda kv: kv[1]["z9_rms"]):
        print(f"{v:14s}", o)
    for T in (2025, 2026):
        s2 = {v: f[f["T"] == T] for v, f in sub.items()}
        rp = boot(s2, "pop1")
        print(T, {v: (rp[v]["z9_rms"], rp[v]["mpg_rmse"], rp[v]["mpg_bias"]) for v in ("pop1", "m0.25_sNone", "m0.1_sNone", "m0.05_sNone", "m0.02_sNone")})
    json.dump(rep, open(bt.OUT / "t023b.json", "w"), indent=1)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "t022"
    {"t022": t022, "t023": t023, "t022b": t022b, "t023b": t023b}[what]()
