"""Fit + out-of-sample test of the in-season availability update (ros-avail-*). See docs/research/inseason_updates.md.

Unit: a player-season timeline (timelines.py) observed after k of his team's games. Target: the share of his
remaining timeline games he plays, R = played[k:] / (n - k). Model per checkpoint k (WLS, weights = remaining games):
  k = 0:  R = b0 + b1 * p0                      (level of the prior vs the timeline share: "level", NOT deployed)
  k >= 1: R = b0 + b1 * p0 + b2 * o + b3 * log1p(streak) + b4 * [missed all k] + b5 * dnp / k
Deployed prediction: k = 0 -> p0 (the prior; its level shift has no out-of-sample support: equal RMSE, and the 2025-26
mean residual has the opposite sign); k >= 1 -> fitted value minus the level shift (b0 + b1 p0 - p0), so the change
from the prior is information from games only.
Population: production-like = on the opening-night roster (timeline starts at the team's first game) and not already
injured coming in (lost season, or a 10+ game absence at the end of T-1): production handles known preseason injuries
with games_missed / day-to-day windows instead.
p0 = preseason prior share (gp-ols gp / 82), o = observed share played[:k] / k, streak = current run of missed games
(an ongoing absence: injuries come in spells), dnp = missed games he was dressed for (coach's decision, not injury).
Features come from fantasy.model.inseason.timeline_features, as in production.
Baselines: static prior (R = p0); calibrated static (per-k WLS of R on p0 alone: the prior's recalibration without any
game information); iid Beta-binomial (Beta prior with mean p0 and sd gp_sd / 82, posterior mean); observed share.
Uncertainty: var(R - Rhat) = a_k * Rhat (1 - Rhat) + c_k / remaining + d_k * log1p(streak), fitted on squared residuals by non-negative least squares
(long ongoing absences are bimodal: he returns or he does not).

Usage: python scripts/inseason_model/fit_availability.py --tl TL.pkl --priors PRIORS.pkl [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import nnls

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from fantasy.model.inseason import timeline_features  # noqa: E402

KS = (0, 1, 2, 3, 5, 10, 15, 20, 30, 40, 50, 60)
MIN_REMAINING = 10
FEATURES = ["const", "p0", "o", "log_streak", "all_missed", "dnp"]
K0_FEATURES = ["const", "p0"]
EXCLUDE = (2020, 2021)  # COVID seasons: bubble / protocol absences
FIT_SEASONS = range(2011, 2025)
STREAK_BUCKETS = [(0, 0), (1, 2), (3, 9), (10, 10_000)]


def checkpoints(tl: pd.DataFrame, pri: pd.DataFrame, ks=KS) -> pd.DataFrame:
    """One row per (player-season, k) with features and the rest-of-season target."""
    p = pri.drop_duplicates(["season", "slug"]).set_index(["season", "slug"])[["gp_prior", "gp_sd_prior"]]
    rows = []
    for (season, slug), d in tl.groupby(["season", "slug"], sort=False):
        d = d.sort_values("t")
        if "listed" not in d:
            d = d.assign(listed=d["played"])
        played = d["played"].to_numpy(bool)
        n = len(played)
        gp, sd = p.loc[(season, slug)]
        for k in ks:
            if n - k < MIN_REMAINING:
                continue
            _, x, streak, dnp = timeline_features(d.iloc[:k])
            rows.append({"season": season, "slug": slug, "k": k, "n": n, "rem": n - k,
                         "p0": min(gp, 82.0) / 82.0, "sd0": sd / 82.0, "x": x,
                         "o": x / k if k else min(gp, 82.0) / 82.0, "streak": streak, "dnp": dnp,
                         "R": played[k:].mean(), "ros": int(played[k:].sum())})
    return pd.DataFrame(rows)


def population(c: pd.DataFrame, tl: pd.DataFrame, pri: pd.DataFrame) -> pd.DataFrame:
    """Flag `opening` (timeline starts at the team's first game) and `known_inj` (lost season or a 10+ game absence at
    the end of T-1), and keep the production-like rows (opening and not known_inj)."""
    first = tl.groupby(["season", "team"])["game_date"].min()
    g = tl.sort_values("t").groupby(["season", "slug"], sort=False)
    start = g[["team", "game_date"]].first()
    opening = pd.Series(start["game_date"].to_numpy() == first.reindex(
        list(zip(start.index.get_level_values(0), start["team"], strict=True))).to_numpy(), index=start.index)
    end_streak = g["played"].apply(lambda p: len(p) - 1 - int(np.flatnonzero(p.to_numpy())[-1]) if p.any() else len(p))
    lost = pri.drop_duplicates(["season", "slug"]).set_index(["season", "slug"])["lost"]
    key = list(zip(c["season"], c["slug"], strict=True))
    prev = [end_streak.get((s - 1, sl), np.nan) for s, sl in key]
    c = c.assign(opening=opening.reindex(key).to_numpy(),
                 known_inj=(np.asarray(prev, float) >= 10) | (lost.reindex(key).to_numpy() == 1))
    return c[c["opening"].astype(bool) & ~c["known_inj"].astype(bool)].reset_index(drop=True)


def design(c: pd.DataFrame) -> pd.DataFrame:
    """All features; at k = 0 only const and p0 are non-zero."""
    k0 = (c["k"] == 0).to_numpy()
    X = pd.DataFrame({"const": 1.0, "p0": c["p0"], "o": c["o"], "log_streak": np.log1p(c["streak"]),
                      "all_missed": (c["x"] == 0).astype(float), "dnp": c["dnp"] / c["k"].clip(lower=1)},
                     index=c.index)[FEATURES]
    X.loc[k0, ["o", "log_streak", "all_missed", "dnp"]] = 0.0
    return X


def wls(X: pd.DataFrame, y: pd.Series, w: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Weighted least squares with HC1 SEs (min-norm solution when features are collinear, e.g. k = 1)."""
    A, yy, ww = X.to_numpy(float), y.to_numpy(float), w.to_numpy(float)
    sw = np.sqrt(ww)
    b, *_ = np.linalg.lstsq(A * sw[:, None], yy * sw, rcond=None)
    e = yy - A @ b
    n, kk = A.shape
    inv = np.linalg.pinv((A * ww[:, None]).T @ A)
    meat = (A * (ww * e)[:, None]).T @ (A * (ww * e)[:, None])
    cov = inv @ meat @ inv * n / (n - kk)
    return pd.Series(b, X.columns), pd.Series(np.sqrt(np.clip(np.diag(cov), 0, None)), X.columns)


def _var_design(pred: np.ndarray, d: pd.DataFrame) -> np.ndarray:
    return np.column_stack([pred * (1 - pred), 1.0 / d["rem"].to_numpy(float), np.log1p(d["streak"].to_numpy(float))])


def level_shift(model: dict, p0) -> np.ndarray:
    """Fitted k = 0 level of the prior: (b0 + b1 p0) - p0."""
    c = model[0]["coef"]
    return c["const"] + (c["p0"] - 1.0) * np.asarray(p0, float)


def fit(c: pd.DataFrame) -> dict:
    """Per-k coefficients (all FEATURES; zeros where unused) and variance parameters of the DEPLOYED prediction
    (k = 0: the prior p0; k >= 1: fitted minus the level shift)."""
    out: dict = {}
    for k, d in sorted(c.groupby("k"), key=lambda kv: kv[0]):
        cols = K0_FEATURES if k == 0 else FEATURES
        X = design(d)[cols]
        b, se = wls(X, d["R"], d["rem"])
        b = b.reindex(FEATURES, fill_value=0.0)
        raw = design(d).to_numpy(float) @ b.to_numpy()
        out[int(k)] = {"coef": b.round(4).to_dict()}
        pred = np.clip(d["p0"].to_numpy() if k == 0 else raw - level_shift(out, d["p0"]), 0, 1)
        r2 = (d["R"].to_numpy() - pred) ** 2
        Z = _var_design(pred, d)
        if k == 0:
            Z = Z[:, :2]
        v, _ = nnls(Z, r2)  # variance components are non-negative
        v = np.append(v, 0.0) if k == 0 else v
        out[int(k)] = {"coef": b.round(4).to_dict(),
                       "se": None if k == 1 else se.reindex(cols).round(4).to_dict(),  # k = 1: collinear, SEs n/a
                       "n": len(d), "var": {"a": round(float(v[0]), 4), "c": round(float(v[1]), 4),
                                            "d": round(float(v[2]), 4)}}
    return out


def predict(c: pd.DataFrame, model: dict) -> tuple[np.ndarray, np.ndarray]:
    """Deployed prediction (k = 0: p0; k >= 1: fitted minus the level shift) and its sd."""
    mu, sd = np.zeros(len(c)), np.zeros(len(c))
    for k, idx in c.groupby("k").indices.items():
        m = model[int(k)]
        d = c.iloc[idx]
        raw = design(d).to_numpy(float) @ np.array([m["coef"][f] for f in FEATURES])
        p = np.clip(d["p0"].to_numpy() if k == 0 else raw - level_shift(model, d["p0"]), 0, 1)
        mu[idx] = p
        v = m["var"]
        sd[idx] = np.sqrt(np.maximum(_var_design(p, d) @ np.array([v["a"], v["c"], v["d"]]), 1e-6))
    return mu, sd


def calibrated_static(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Per-k WLS of R on p0 alone (fit on train): the prior's recalibration without game information."""
    out = np.zeros(len(test))
    for k, idx in test.groupby("k").indices.items():
        d = train[train["k"] == k]
        b, _ = wls(pd.DataFrame({"const": 1.0, "p0": d["p0"]}), d["R"], d["rem"])
        out[idx] = np.clip(b["const"] + b["p0"] * test.iloc[idx]["p0"].to_numpy(), 0, 1)
    return out


def beta_binomial(c: pd.DataFrame) -> np.ndarray:
    """iid Beta-binomial posterior mean with the preseason Beta(mean p0, sd sd0)."""
    m = c["p0"].clip(0.02, 0.98)
    v = np.minimum(c["sd0"] ** 2, m * (1 - m) * 0.95)
    s = m * (1 - m) / v - 1
    return ((m * s + c["x"]) / (s + c["k"])).to_numpy()


def sq_err_games(c: pd.DataFrame, pred: np.ndarray) -> np.ndarray:
    """Per-row squared error in remaining games (pred share x remaining games)."""
    return ((pred - c["R"]) * c["rem"]).to_numpy() ** 2


def compare(c: pd.DataFrame, preds: dict[str, np.ndarray], n_boot: int = 1000, seed: int = 0) -> dict:
    """RMSE (remaining games) per k and method, and the gain of `update` over each baseline with a
    player-season cluster bootstrap SE (one row per player-season per k)."""
    rng = np.random.default_rng(seed)
    out = {}
    for k, idx in c.groupby("k").indices.items():
        d = c.iloc[idx]
        se2 = {m: sq_err_games(d, p[idx]) for m, p in preds.items()}
        row = {m: round(float(np.sqrt(v.mean())), 2) for m, v in se2.items()}
        row["n"] = n = len(d)
        boots = {m: [] for m in preds if m != "update"}
        for _ in range(n_boot):
            j = rng.integers(0, n, n)
            u = np.sqrt(se2["update"][j].mean())
            for m in boots:
                boots[m].append(np.sqrt(se2[m][j].mean()) - u)
        for m, b in boots.items():
            row[f"gain_vs_{m}"] = [round(float(np.sqrt(se2[m].mean()) - np.sqrt(se2["update"].mean())), 2),
                                   round(float(np.std(b)), 2)]
        out[int(k)] = row
    return out


def coverage(c: pd.DataFrame, mu: np.ndarray, sd: np.ndarray) -> dict:
    z = (c["R"].to_numpy() - mu) / sd
    out = {"cover50": round(float((np.abs(z) < 0.674).mean()), 3),
           "cover80": round(float((np.abs(z) < 1.2816).mean()), 3),
           "mean_z2": round(float((z**2).mean()), 3),
           "mean_resid_games": round(float(((c["R"] - mu) * c["rem"]).mean()), 2)}
    s = c["streak"].to_numpy()
    for lo, hi in STREAK_BUCKETS:
        m = (s >= lo) & (s <= hi) & (c["k"].to_numpy() > 0)
        if m.sum() >= 20:
            out[f"streak_{lo}-{hi}"] = {"n": int(m.sum()), "mean_z2": round(float((z[m] ** 2).mean()), 2),
                                        "cover80": round(float((np.abs(z[m]) < 1.2816).mean()), 3)}
    return out


def example(model: dict, gp: float = 38.5, k: int = 20, x: int = 20, n: int = 82) -> dict:
    d = pd.DataFrame({"k": [k], "p0": [gp / 82], "o": [x / k], "streak": [0 if x else k], "x": [x], "dnp": [0],
                      "rem": [n - k], "sd0": [24 / 82]})
    mu, sd = predict(d, model)
    d0 = d.assign(k=0, o=gp / 82, streak=0, x=0, rem=n)
    mu0, _ = predict(d0, model)
    bb = beta_binomial(d)
    return {"prior_gp": gp, "k": k, "played": x, "ros_share": round(float(mu[0]), 3),
            "ros_games": round(float(mu[0] * (n - k)), 1), "ros_sd_games": round(float(sd[0] * (n - k)), 1),
            "season_gp": round(float(x + mu[0] * (n - k)), 1),
            "static_season_gp": round(x + gp / 82 * (n - k), 1),
            "k0_season_gp": round(float(mu0[0] * n), 1),
            "beta_binomial_season_gp": round(float(x + bb[0] * (n - k)), 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tl", required=True)
    ap.add_argument("--priors", required=True)
    ap.add_argument("--test", default="2025,2026")
    ap.add_argument("--out")
    a = ap.parse_args()
    tl, pri = pd.read_pickle(a.tl), pd.read_pickle(a.priors)
    c_all = checkpoints(tl, pri)
    c_all = c_all[~c_all["season"].isin(EXCLUDE)]
    c = population(c_all, tl, pri)
    test_seasons = [int(s) for s in a.test.split(",")]
    train = c[c["season"].isin(FIT_SEASONS)].reset_index(drop=True)
    test = c[c["season"].isin(test_seasons)].reset_index(drop=True)
    m_train = fit(train)
    mu, sd = predict(test, m_train)
    preds = {"update": mu, "static": test["p0"].to_numpy(), "calibrated_static": calibrated_static(train, test),
             "beta_binomial": beta_binomial(test), "observed": test["o"].to_numpy()}
    m_prod = fit(c)
    k1 = c[c["k"] > 0]
    zero = {int(s): round(float((d["x"] == 0).mean()), 4) for s, d in k1.groupby("season")}
    bias = {int(k): {"update": round(float(((test.iloc[i]["R"] - mu[i]) * test.iloc[i]["rem"]).mean()), 2),
                     "static": round(float(((test.iloc[i]["R"] - test.iloc[i]["p0"]) * test.iloc[i]["rem"]).mean()), 2)}
            for k, i in test.groupby("k").indices.items()}
    rep = {"population": "opening-night roster, not injured coming in (lost season / 10+ game absence at end of T-1)",
           "population_share_of_all_checkpoints": round(len(c) / len(c_all), 3),
           "fit_seasons": [min(FIT_SEASONS), max(FIT_SEASONS)], "test_seasons": test_seasons,
           "n_train": len(train), "n_test": len(test), "train_model": m_train,
           "oos_rmse_games": compare(test, preds),
           "oos_calibration": {int(k): coverage(test.iloc[i], mu[i], sd[i])
                               for k, i in test.groupby("k").indices.items()},
           "oos_mean_residual_games": bias,
           "all_missed_share_by_season": zero,
           "production_seasons": sorted(int(s) for s in set(c["season"])), "production_model": m_prod,
           "example_38_5_plays_20_of_20": example(m_prod),
           "example_38_5_misses_20_of_20": example(m_prod, x=0)}
    s = json.dumps(rep, indent=1)
    print(s)
    if a.out:
        Path(a.out).write_text(s)


if __name__ == "__main__":
    main()
