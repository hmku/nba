"""Game-level variance ratio by minutes tier for the unified noise model (2026-10-19, sim_calibration.md section 8).

For each stat and minutes tier (15-24 / 24-30 / 30+ mpg) of the TEST player-seasons:
    ratio = sum over player-seasons of the sample game variance / sum of the model variance at the season mean
(1 = calibrated; > 1 = the model under-states variance). Unlike the weekly mean z^2 of noise_oos_2026-10-19.py,
this pools variance before dividing, so tiny-mean players do not dominate, and it is game level (no
within-week dependence).
Models: vf (var = a*m + b*m^2, OLS) and cv (pooled constant CV), fitted on TRAIN player-seasons with >= 24 mpg
(the GAME_VAR population) and, as a sensitivity check, >= 15 mpg. Split A only: train 2011-2018, test 2019-2024;
units with >= 30 games.

Input: box.pkl (player-game rows) built by the sim-calibration prep script from the NocturneBear 2010-24 box
scores (docs/research/sim_calibration.md, Data); run next to it. Output: noise_tier.json.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
S = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
TIERS = ["15-24", "24-30", "30+"]

b = pd.read_pickle(HERE / "box.pkl")
b = b[b["played"]].copy()
b["unit"] = b["season"].astype(str) + "_" + b["personId"].astype(str) + "_" + b["team"]
g = b.groupby("unit")
mean = g[S + ["min"]].mean()
var = g[S].var()
n = g.size()
u = pd.DataFrame({"n": n, "mpg": mean["min"], "season": g["season"].first()})
u = u[u.n >= 30]
u["tier"] = pd.cut(u.mpg, [15, 24, 30, 99], right=False, labels=TIERS)

out = {}
for lo in (24, 15):
    tr = u.index[(u.season < 2019) & (u.mpg >= lo)]
    te = u[(u.season >= 2019) & u.tier.notna()]
    row = {}
    for s in S:
        m, v = mean.loc[tr, s].to_numpy(), var.loc[tr, s].to_numpy()
        (a, bb), *_ = np.linalg.lstsq(np.c_[m, m * m], v, rcond=None)
        c2 = v.sum() / (m * m).sum()
        mt, vt = mean.loc[te.index, s], var.loc[te.index, s]
        r = {"a": a, "b": bb}
        for t in TIERS:
            ix = te.index[te.tier == t]
            mm, vv = mt[ix].to_numpy(), vt[ix].to_numpy()
            r[f"vf_{t}"] = vv.sum() / np.maximum(a * mm + bb * mm * mm, 1e-9).sum()
            r[f"cv_{t}"] = vv.sum() / (c2 * mm * mm).sum()
        row[s] = r
    df = pd.DataFrame(row).T
    print(f"fit on >= {lo} mpg")
    print(df.round(3))
    out[f"fit_ge{lo}"] = df.round(4).to_dict(orient="index")
with open(HERE / "noise_tier.json", "w") as f:
    json.dump(out, f, indent=1)
