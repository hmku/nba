"""Fit and out-of-sample test of the games-played model (gp-ols-*). See docs/research/availability_model.md.

Usage:
  python scripts/gp_model/fit_gp.py --raw DIR [--out report.json]
DIR holds "Player Totals.csv" (or Player_Totals.csv) and "Team Summaries.csv" from the sumitrodatta mirror.

gp-ols-0.2 = three parts, all printed as JSON constants for src/fantasy/model/availability.py:
  main   OLS on players with >= 500 min in T-1 (design_v2: 0.1 lags/age/ironman hinges, the young-player
         bonus only when g1 < 70, plus a role hinge max(0, 30 - mpg1): bench players lose games to coaching decisions)
  calib  recent-era calibration: raw + a + h*max(0, raw - 65), fitted on rolling-origin 1-year-ahead forecast
         errors (each season T forecast by a main fit on targets < T); corrects the forecasts' lag behind the
         league's falling games-played trend and the top-end optimism
  low    OLS on players with < 500 min in T-1, incl. lost seasons (no T-1 season; g1 = mp1 = 0):
         const + prior_k (max minutes in T-2/T-3, /1000, capped 3) + g1 + max(0, age-28) + max(0, 22-age)
         + lost + mpg1. Deployment blends low and main linearly over 400-600 minutes (availability.predict_gp).
Censoring: a 0-game target needs a later season to be seen, so the newest targets (2025, 2026) have almost no
0-game rows and their bias looks ~1 game (main) / ~3 games (low) better than it is; the calibration therefore uses
only forecast years 2019-2024 (CAL_YEARS), whose zeros are observed.
Out of sample: everything fit on targets <= 2024, tested on 2025 and 2026, vs the gp-ols-0.1 spec fit the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from panel import build_panel, player_seasons  # noqa: E402

FULL_MIN = 500.0  # main model population: >= 500 minutes in T-1


def design_v1(p: pd.DataFrame) -> pd.DataFrame:
    """gp-ols-0.1 features (same as fantasy.model.availability.predict_gp)."""
    X = pd.DataFrame(index=p.index)
    X["const"] = 1.0
    X["g1"] = p["g1"]
    X["g2"] = p["g2"].fillna(0)
    X["m2"] = p["g2"].isna().astype(float)
    X["g3"] = p["g3"].fillna(0)
    X["m3"] = p["g3"].isna().astype(float)
    a = p["age"]
    X["age_gt28"] = (a - 28).clip(lower=0)
    X["age_gt32"] = (a - 32).clip(lower=0)
    X["age_lt24"] = (24 - a).clip(lower=0)
    X["g1_gt70"] = (p["g1"] - 70).clip(lower=0)
    X["gavg_gt70"] = (p[["g1", "g2", "g3"]].mean(axis=1) - 70).clip(lower=0)
    return X


def design_v2(p: pd.DataFrame) -> pd.DataFrame:
    """gp-ols-0.2 main-model features."""
    X = design_v1(p)
    X["age_lt24"] = X["age_lt24"] * (p["g1"] < 70)
    X["mpg_lt30"] = (30 - p["mpg1"]).clip(lower=0)
    return X


def design_low(p: pd.DataFrame) -> pd.DataFrame:
    """gp-ols-0.2 low-minute features (< 500 min or no season in T-1)."""
    return pd.DataFrame({
        "const": 1.0,
        "prior_k": p[["mp2", "mp3"]].max(axis=1).fillna(0).clip(upper=3000) / 1000,
        "g1": p["g1"],
        "age_gt28": (p["age"] - 28).clip(lower=0),
        "age_lt22": (22 - p["age"]).clip(lower=0),  # teenage / age-21 prospects grow into a role
        "lost": p["lost"].astype(float),  # missed the whole season T-1 (injury), still in the league
        "mpg1": p["mpg1"],  # injured starters (few games, big minutes) vs end-of-bench players
    }, index=p.index)


def ols(X: pd.DataFrame, y: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Coefficients and heteroskedasticity-robust (HC1) standard errors."""
    A = X.to_numpy(float)
    b, *_ = np.linalg.lstsq(A, y.to_numpy(float), rcond=None)
    e = y.to_numpy(float) - A @ b
    n, k = A.shape
    inv = np.linalg.pinv(A.T @ A)
    cov = inv @ (A.T * e**2) @ A @ inv * n / (n - k)
    return pd.Series(b, X.columns), pd.Series(np.sqrt(np.diag(cov)), X.columns)


def mean_se(x: np.ndarray) -> tuple[float, float, int]:
    x = np.asarray(x, float)
    return float(x.mean()), float(x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else float("nan"), len(x)


def load(raw: Path) -> pd.DataFrame:
    def f(*names: str) -> Path:
        return next(raw / n for n in names if (raw / n).exists())
    ps = player_seasons(pd.read_csv(f("Player Totals.csv", "Player_Totals.csv")),
                        pd.read_csv(f("Team Summaries.csv", "Team_Summaries.csv")))
    return build_panel(ps, first_target=2000)


CAL_KNEE = 65.0
CAL_YEARS = range(2019, 2025)  # rolling-origin forecast years in the calibration (censoring-clean, see doc)
FIT_FIRST = 2014


def fit_main(m: pd.DataFrame, design, mask) -> pd.Series:
    return ols(design(m[mask]), m["y"][mask])[0]


def rolling_preds(m: pd.DataFrame, design, years) -> pd.Series:
    out = []
    for T in years:
        b = fit_main(m, design, (m["T"] < T).to_numpy())
        t = m[m["T"] == T]
        out.append(np.clip(design(t) @ b, 0, 80))
    return pd.concat(out)


def calibrate(m: pd.DataFrame, design, years) -> tuple[pd.Series, pd.Series]:
    raw = rolling_preds(m, design, years)
    Z = pd.DataFrame({"a": 1.0, "h": np.maximum(0, raw - CAL_KNEE)})
    return ols(Z, m["y"][raw.index] - raw)


def apply_cal(raw: pd.Series, cal: pd.Series) -> pd.Series:
    return np.clip(raw + cal["a"] + cal["h"] * np.maximum(0, raw - CAL_KNEE), 0, 80)


def subgroups(t: pd.DataFrame, ref_pred: pd.Series) -> dict[str, np.ndarray]:
    return {
        "all": np.ones(len(t), bool),
        "pred_v1>=65 & mp1>=1000": ((ref_pred >= 65) & (t["mp1"] >= 1000)).to_numpy(),
        "age<24 & g1>=70 & mp1>=1500": ((t["age"] < 24) & (t["g1"] >= 70) & (t["mp1"] >= 1500)).to_numpy(),
        "mp1>=1500": (t["mp1"] >= 1500).to_numpy(),
        "mp1<1000": (t["mp1"] < 1000).to_numpy(),
        "injured starter (mpg1>=28, g1<50)": ((t["mpg1"] >= 28) & (t["g1"] < 50)).to_numpy(),
        "healthy bench (mpg1<20, g1>=70)": ((t["mpg1"] < 20) & (t["g1"] >= 70)).to_numpy(),
        "age>=30": (t["age"] >= 30).to_numpy(),
    }


def summarize(y: np.ndarray, pred: np.ndarray, groups: dict[str, np.ndarray]) -> dict:
    r = y - pred
    out = {"rmse": float(np.sqrt((r**2).mean())), "n": int(len(r))}
    for g, s in groups.items():
        mu, se, n = mean_se(r[s])
        out[f"resid {g}"] = [round(mu, 2), round(se, 2), n]
    return out


def boot_gain(y: np.ndarray, a: np.ndarray, b: np.ndarray, n_boot: int = 2000, seed: int = 0) -> list[float]:
    """RMSE(a) - RMSE(b) and its paired-bootstrap SE."""
    rng = np.random.default_rng(seed)
    ra, rb = (y - a) ** 2, (y - b) ** 2
    g = [np.sqrt(ra[i].mean()) - np.sqrt(rb[i].mean()) for i in (rng.integers(0, len(y), len(y)) for _ in range(n_boot))]
    return [round(float(np.sqrt(ra.mean()) - np.sqrt(rb.mean())), 2), round(float(np.std(g)), 2)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    p = load(args.raw)
    p = p[p["T"] >= FIT_FIRST].reset_index(drop=True)
    m = p[p["mp1"] >= FULL_MIN].reset_index(drop=True)
    low = p[p["mp1"] < FULL_MIN].reset_index(drop=True)
    rep: dict = {"n_main": len(m), "n_low": len(low), "n_lost": int(low["lost"].sum())}

    # ---- strict out of sample: fit on targets <= 2024, test 2025 and 2026
    tr = (m["T"] <= 2024).to_numpy()
    b1, b2 = fit_main(m, design_v1, tr), fit_main(m, design_v2, tr)
    cal_oos = calibrate(m, design_v2, CAL_YEARS)[0]
    bl = ols(design_low(low[low["T"] <= 2024]), low["y"][low["T"] <= 2024])[0]
    rep["oos_calibration"] = cal_oos.round(3).to_dict()
    for T in (2025, 2026):
        t = m[m["T"] == T]
        p1 = np.clip(design_v1(t) @ b1, 0, 80)
        p2 = apply_cal(np.clip(design_v2(t) @ b2, 0, 80), cal_oos)
        gr = subgroups(t, p1)
        tl = low[low["T"] == T]
        pl1 = np.clip(design_v1(tl).fillna(0) @ b1, 0, 80)
        pl2 = np.clip(design_low(tl) @ bl, 0, 80)
        est = (tl[["mp2", "mp3"]].max(axis=1) >= 1000).to_numpy()
        rep[f"oos_{T}"] = {
            "v1": summarize(t["y"].to_numpy(), p1.to_numpy(), gr),
            "v2": summarize(t["y"].to_numpy(), p2.to_numpy(), gr),
            "rmse_gain_v2_vs_v1": boot_gain(t["y"].to_numpy(), p1.to_numpy(), p2.to_numpy()),
            "low_v1": summarize(tl["y"].to_numpy(), pl1.to_numpy(), {"all": np.ones(len(tl), bool), "prior>=1000": est}),
            "low_v2": summarize(tl["y"].to_numpy(), pl2.to_numpy(), {"all": np.ones(len(tl), bool), "prior>=1000": est}),
            "low_rmse_gain": boot_gain(tl["y"].to_numpy(), pl1.to_numpy(), pl2.to_numpy()),
        }

    # ---- production: all targets 2014-2026
    bp, sep = ols(design_v2(m), m["y"])
    cal, cal_se = calibrate(m, design_v2, CAL_YEARS)  # same as cal_oos by construction: fits on targets < T
    rep["calibration_incl_2025_26"] = calibrate(m, design_v2, range(CAL_YEARS.start, int(m["T"].max()) + 1))[0].round(3).to_dict()
    rep["zero_share_main"] = m.groupby("T")["y"].apply(lambda v: round(float((v == 0).mean()), 3)).to_dict()
    rep["zero_share_low"] = low.groupby("T")["y"].apply(lambda v: round(float((v == 0).mean()), 3)).to_dict()
    blp, blse = ols(design_low(low), low["y"])
    rep["main"] = {k: [round(float(bp[k]), 4), round(float(sep[k]), 4)] for k in bp.index}
    rep["calibration"] = {k: [round(float(cal[k]), 3), round(float(cal_se[k]), 3)] for k in cal.index}
    rep["low"] = {k: [round(float(blp[k]), 4), round(float(blse[k]), 4)] for k in blp.index}

    # ---- gp_sd: residual sd by predicted level on calibrated rolling-origin forecasts (honest errors)
    years = range(CAL_YEARS.start, int(m["T"].max()) + 1)
    pr = apply_cal(rolling_preds(m, design_v2, years), cal)
    r = m["y"][pr.index] - pr
    q = pd.qcut(pr, 6)
    rep["gp_sd_knots"] = {"x": [round(float(v), 1) for v in pr.groupby(q, observed=True).mean()],
                          "y": [round(float(v), 1) for v in r.groupby(q, observed=True).std()]}
    lp = np.clip(design_low(low) @ blp, 0, 80)
    rep["low_resid_sd"] = round(float((low["y"] - lp).std()), 1)
    txt = json.dumps(rep, indent=1)
    print(txt)
    if args.out:
        args.out.write_text(txt)


if __name__ == "__main__":
    main()
