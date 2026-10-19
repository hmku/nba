"""Out-of-sample comparison of game-to-game noise models (2026-10-19, unified noise model).

Models (fitted on TRAIN seasons, rostered player-seasons: >= 24 mpg, >= 30 games played):
  cv      var = (c * m)^2           pooled c^2 = sum var / sum m^2   (old GAME_CV_FITTED method)
  lin     var = a * m               a = sum var*m / sum m^2 (1-parameter quasi-Poisson)
  vf      var = a * m + b * m^2     OLS of player-season sample variance on (m, m^2) (GAME_VAR method)
Evaluated on TEST seasons, every player-season with >= 30 games and >= 10 mpg, by minutes tier:
  (1) game-level Gaussian log-likelihood with the unit's own mean (mean per game; VF - CV diff, SE by player
      cluster bootstrap),
  (2) weekly calibration: z = (sum_w x - g m) / sqrt(g var(m)) per player-week (Mon-Sun, g >= 1 games);
      mean z^2 should be 1 (> 1 = model under-states weekly variance).
Split A: train 2011-2018, test 2019-2024. Split B (reverse): train 2018-2024, test 2011-2017.

Input: box.pkl (player-game rows) built by the sim-calibration prep script from the NocturneBear 2010-24 box
scores (docs/research/sim_calibration.md, Data); run next to it. Output: noise_oos.json. Results: section 8.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
S = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
rng = np.random.default_rng(0)

b = pd.read_pickle(HERE / "box.pkl")
b = b[b["played"]].copy()
b["unit"] = b["season"].astype(str) + "_" + b["personId"].astype(str) + "_" + b["team"]
g = b.groupby("unit")
mean = g[S + ["min"]].mean()
var = g[S].var()
n = g.size()
units = pd.DataFrame({"n": n, "mpg": mean["min"], "season": g["season"].first(), "pid": g["personId"].first()})
units = units[units["n"] >= 30]


def tier(mpg: pd.Series) -> pd.Series:
    return pd.cut(mpg, [10, 15, 24, 30, 99], right=False, labels=["10-15", "15-24", "24-30", "30+"])


units["tier"] = tier(units["mpg"])
units = units[units["tier"].notna()]


def fit(train_units: pd.Index) -> dict:
    out = {}
    for s in S:
        m, v = mean.loc[train_units, s].to_numpy(), var.loc[train_units, s].to_numpy()
        c2 = v.sum() / (m**2).sum()
        a_lin = (v * m).sum() / (m**2).sum()
        coef, *_ = np.linalg.lstsq(np.c_[m, m**2], v, rcond=None)
        out[s] = {"cv": float(np.sqrt(c2)), "lin": float(a_lin), "vf": (float(coef[0]), float(coef[1]))}
    return out


def model_var(params: dict, s: str, m: np.ndarray, kind: str) -> np.ndarray:
    p = params[s]
    if kind == "cv":
        v = (p["cv"] * m) ** 2
    elif kind == "lin":
        v = p["lin"] * m
    else:
        v = p["vf"][0] * m + p["vf"][1] * m * m
    return np.maximum(v, 0.05)  # floor: tiny means (0.0x blocks) would otherwise give -inf LL


KINDS = ["cv", "lin", "vf"]


def evaluate(params: dict, test_units: pd.Index) -> dict:
    tu = units.loc[test_units]
    x = b[b["unit"].isin(test_units)].copy()
    x["tier"] = x["unit"].map(tu["tier"]).astype(str)
    x["pid"] = x["personId"]
    x["week"] = x["date"].dt.to_period("W-SUN")
    res: dict = {"games": int(len(x)), "units": int(len(tu)), "ll": {}, "wz2": {}}
    for s in S:
        m = x["unit"].map(mean[s]).to_numpy()
        xv = x[s].to_numpy(dtype=float)
        ll = {}
        for k in KINDS:
            v = model_var(params, s, m, k)
            ll[k] = -0.5 * (np.log(2 * np.pi * v) + (xv - m) ** 2 / v)
        df = pd.DataFrame({"tier": x["tier"].to_numpy(), "pid": x["pid"].to_numpy(),
                           **{k: ll[k] for k in KINDS}})
        df["d_vf_cv"] = df["vf"] - df["cv"]
        df["d_vf_lin"] = df["vf"] - df["lin"]
        r = {}
        for t, sub in [("all", df), *df.groupby("tier")]:
            per_p = sub.groupby("pid")[["d_vf_cv", "d_vf_lin"]].agg(["sum", "size"])
            tot = per_p[("d_vf_cv", "size")].to_numpy()
            dcv = per_p[("d_vf_cv", "sum")].to_numpy()
            dli = per_p[("d_vf_lin", "sum")].to_numpy()
            bs_cv, bs_li = [], []
            for _ in range(200):
                ix = rng.integers(0, len(tot), len(tot))
                bs_cv.append(dcv[ix].sum() / tot[ix].sum())
                bs_li.append(dli[ix].sum() / tot[ix].sum())
            r[str(t)] = {k: float(sub[k].mean()) for k in KINDS} | {
                "vf_minus_cv": float(dcv.sum() / tot.sum()), "vf_minus_cv_se": float(np.std(bs_cv)),
                "vf_minus_lin": float(dli.sum() / tot.sum()), "vf_minus_lin_se": float(np.std(bs_li)),
                "games": int(tot.sum())}
        res["ll"][s] = r
        # weekly calibration
        wk = pd.DataFrame({"unit": x["unit"].to_numpy(), "week": x["week"].to_numpy(), "x": xv,
                           "tier": x["tier"].to_numpy()})
        agg = wk.groupby(["unit", "week"]).agg(sx=("x", "sum"), gcount=("x", "size"), tier=("tier", "first"))
        mu = agg.index.get_level_values("unit").map(mean[s]).to_numpy()
        wr = {}
        for k in KINDS:
            v = model_var(params, s, mu, k) * agg["gcount"].to_numpy()
            z2 = (agg["sx"].to_numpy() - agg["gcount"].to_numpy() * mu) ** 2 / v
            col = pd.Series(z2, index=agg.index)
            wr[k] = {"all": float(z2.mean()), **{str(t): float(col[agg["tier"] == t].mean())
                                                 for t in ["10-15", "15-24", "24-30", "30+"]}}
        res["wz2"][s] = wr
    return res


splits = {"A_train2011-18_test2019-24": (range(2011, 2019), range(2019, 2025)),
          "B_train2018-24_test2011-17": (range(2018, 2025), range(2011, 2018))}
out = {}
for name, (tr, te) in splits.items():
    tr_u = units.index[(units["season"].isin(tr)) & (units["mpg"] >= 24)]
    te_u = units.index[units["season"].isin(te)]
    params = fit(tr_u)
    ev = evaluate(params, te_u)
    out[name] = {"n_train_units": int(len(tr_u)), "params": params, **ev}
    print(name, "train units", len(tr_u), "test units", len(te_u), file=sys.stderr)
    for s in S:
        ll = ev["ll"][s]
        print(f"{s:5s} LL/game vf-cv all {ll['all']['vf_minus_cv']:+.4f}±{ll['all']['vf_minus_cv_se']:.4f} "
              f"15-24 {ll['15-24']['vf_minus_cv']:+.4f} 30+ {ll['30+']['vf_minus_cv']:+.4f} | vf-lin "
              f"{ll['all']['vf_minus_lin']:+.4f}±{ll['all']['vf_minus_lin_se']:.4f} | wz2 cv "
              + " ".join(f"{ev['wz2'][s]['cv'][t]:.2f}" for t in ["15-24", "24-30", "30+"]) + " vf "
              + " ".join(f"{ev['wz2'][s]['vf'][t]:.2f}" for t in ["15-24", "24-30", "30+"]), file=sys.stderr)
json.dump(out, open(HERE / "noise_oos.json", "w"), indent=1)
