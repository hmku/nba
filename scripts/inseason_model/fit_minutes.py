"""Fit and out-of-sample test of the recency-weighted in-season minutes models (fantasy.model.minutes).

Usage (repo root):
  PYTHONPATH=src python scripts/inseason_model/fit_minutes.py \
      --nocturne <dir with regular_season_box_scores_2010_2024_part_{1,2,3}.csv> \
      --totals <sumitrodatta "Player Totals.csv", all seasons> [--n-boot 200] --out scripts/inseason_model/report_minutes.json
Without --nocturne only the committed ESPN 2024-25 season is used for fitting.

Data: fit seasons 2010-11..2024-25 (NocturneBear nba.com box scores 2011-2024, see README.md; ESPN ingester 2025 from
data/raw/espn_boxscores); test 2025-26 (ESPN) strictly out of sample. 2019-20 ends 2020-03-11 (COVID suspension,
bubble games not in the source) and 2020-21 has 72 games: kept (prediction points only need later games played), and a
sensitivity fit without them is reported. Starter flag: ESPN `starter`; nba.com `position` non-empty.
Prior: `proj_backtest.project_core(T)` (production projection core on T-3..T-1 only, no leakage) for every season --
"core prior". Production uses the 50/50 core + ESPN-preseason blend ("blend prior", only 2025 and 2026 here): the prior
check fits 2024-25 with each and compares coefficients; the test season is evaluated with both.
Samples: every player-season with a prior, at every t >= 1 games played with >= 1 later game played; features use
games 0..t-1 only (fantasy.model.minutes.feature_panel). Clusters (CV folds, bootstrap) = players across seasons.
Targets: mean minutes over the next 5 games played (model A) and over all remaining games played (model B, `ros`),
weight = number of target games (also the next game and next 10, for reference).
Models (all minus the intercept variant have no intercept): EWMAs of minutes in games played initialised at the
prior (half-lives by 5-fold player-clustered CV on the fit seasons) + prior;
  B variants: + intercept; + a third, equal-weight shrink term (n0*prior + sum m)/(n0 + t) (n0 by CV); + a third EWMA.
OOS: RMSE / MAE vs the old stat-shrink formula (n0 = 5.2, equal weights), last 3, season mean, prior; cluster-bootstrap
SEs and paired differences; games-played buckets and subgroups; calibration slope (WLS of actual on predicted).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from fantasy.data.ids import canonical_id  # noqa: E402
from fantasy.model import minutes as MM  # noqa: E402
from fantasy.model.proj_backtest import project_core  # noqa: E402

TEST = 2026
ESPN_FIT = [2025]
OLD_N0 = 5.2  # inseason.STAT_N0['mpg']
TARGETS = {"next1": ("y1", "n1"), "next5": ("y5", "n5"), "next10": ("y10", "n10"), "ros": ("yr", "nr")}
HMAX = 90
BUCKETS = [(1, 2), (3, 5), (6, 10), (11, 20), (21, 40), (41, 90)]
HS_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
HL_GRID = [6.0, 10.0, 15.0, 20.0, 30.0, 40.0]
THIRD_EWMA = [40.0, 60.0, 100.0]
SHRINK_N0 = [2.0, 5.2, 10.0, 20.0, 40.0]


# ----------------------------------------------------------------------------------------------------- data
def core_priors(totals: pd.DataFrame, seasons) -> pd.DataFrame:
    car = pd.read_csv(ROOT / "data/raw/bbref_sumitrodatta/player_career_info.csv")
    out = []
    for T in seasons:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            d = project_core(totals, car, T).reset_index()
        out.append(d[["player_id", "mpg"]].drop_duplicates("player_id", keep=False).assign(season=T))
    p = pd.concat(out, ignore_index=True).rename(columns={"mpg": "core"})
    return p[p["core"] > 0]


def blend_priors(core: pd.DataFrame, seasons) -> pd.DataFrame:
    out = []
    for T in seasons:
        e = pd.read_csv(ROOT / f"data/raw/espn/projections_{T - 1}_{T % 100:02d}.csv")
        e["player_id"] = e["name"].map(canonical_id)
        e = e[["player_id", "mpg"]].rename(columns={"mpg": "espn"}).drop_duplicates("player_id", keep=False)
        m = core[core["season"] == T].merge(e, on="player_id", how="outer").assign(season=T)
        m["blend"] = m[["core", "espn"]].mean(axis=1)
        out.append(m)
    return pd.concat(out, ignore_index=True)


def load_espn(season: int) -> pd.DataFrame:
    d = pd.read_csv(ROOT / f"data/raw/espn_boxscores/player_games_{season}.csv.gz")
    d = d[d["regular_season"].astype(bool)].copy()
    d["min"] = pd.to_numeric(d["min"], errors="coerce").fillna(0.0)
    d["played"] = d["played"].astype(bool) & (d["min"] > 0)
    d["starter"] = d["starter"].astype(bool)
    return d[["season", "game_id", "game_date", "team", "player_id", "min", "played", "starter"]]


def load_nocturne(folder: Path) -> pd.DataFrame:
    import timelines as TL

    paths = sorted(folder.glob("regular_season_box_scores_2010_2024_part_*.csv"))
    raw = pd.concat([pd.read_csv(p, low_memory=False, usecols=["gameId", "personName", "position"])
                     for p in paths], ignore_index=True)
    d = TL.load_nocturne(paths)
    d["starter"] = raw["position"].notna().to_numpy() & d["played"].to_numpy()
    return d[["season", "game_id", "game_date", "team", "player_id", "min", "played", "starter"]]


def prep(d: pd.DataFrame) -> pd.DataFrame:
    d = d[d["player_id"].astype(str).str.len() > 0].copy()
    d["game_id"] = d["game_id"].astype(str)
    d = d.sort_values("min", ascending=False).drop_duplicates(["season", "game_id", "player_id"])
    tg = (d[["season", "team", "game_id", "game_date"]].drop_duplicates(["season", "team", "game_id"])
          .sort_values(["season", "team", "game_date", "game_id"]))
    tg["tgi"] = tg.groupby(["season", "team"]).cumcount()
    d = d.merge(tg[["season", "team", "game_id", "tgi"]], on=["season", "team", "game_id"])
    return d[d["played"]].sort_values(["season", "player_id", "game_date", "game_id"])


def samples(d: pd.DataFrame, prior: pd.DataFrame, col: str) -> pd.DataFrame:
    """Prediction points for every (season, player) with a prior in prior[col]."""
    pp = prior.dropna(subset=[col]).set_index(["season", "player_id"])[col]
    pp = pp[pp > 0]
    rows = []
    for (season, pid), dp in d.groupby(["season", "player_id"], sort=False):
        if (season, pid) not in pp.index:
            continue
        m = dp["min"].to_numpy(float)
        n = len(m)
        if n < 2:
            continue
        st = dp["starter"].to_numpy(bool)
        teams, tgis = dp["team"].to_numpy(), dp["tgi"].to_numpy()
        gap = np.zeros(n, int)
        gap[1:] = np.where(teams[1:] == teams[:-1], tgis[1:] - tgis[:-1] - 1, 0)
        cs = np.concatenate([[0.0], np.cumsum(m)])
        cst = np.concatenate([[0.0], np.cumsum(st)])
        rc = np.cumsum(m[::-1])[::-1]  # sum of games t..n-1
        prior = float(pp[(season, pid)])
        for t in range(1, n):
            lo = max(0, t - 3)
            nr = n - t
            rows.append((season, pid, t, prior, cs[t] / t, (cs[t] - cs[lo]) / (t - lo),
                         cs[lo] / lo if lo >= 3 else np.nan, (cst[t] - cst[lo]) / (t - lo),
                         cst[lo] / lo if lo >= 3 else np.nan, bool((gap[lo:t] >= 3).any()),
                         m[t], 1, m[t:t + 5].mean(), min(5, nr), m[t:t + 10].mean(), min(10, nr), rc[t] / nr, nr,
                         m[:t][::-1][:HMAX].astype(np.float32)))
    cols = ["season", "player", "t", "prior", "mean", "last3", "before3", "start3", "s_before", "back",
            "y1", "n1", "y5", "n5", "y10", "n10", "yr", "nr", "hist"]
    return pd.DataFrame(rows, columns=cols)


# ------------------------------------------------------------------------------------------------- model
class Design:
    def __init__(self, D: pd.DataFrame):
        self.H = np.zeros((len(D), HMAX), np.float32)
        for i, h in enumerate(D["hist"].to_numpy()):
            self.H[i, :len(h)] = h
        self.t = D["t"].to_numpy(float)
        self.prior = D["prior"].to_numpy(float)
        self.sum = self.H.sum(axis=1).astype(float)
        self._cache: dict[float, np.ndarray] = {}

    def ew(self, hl: float) -> np.ndarray:
        """EWMA initialised at the prior after t games: a^t prior + (1 - a) sum_age a^age m (= MM.ewma)."""
        if hl not in self._cache:
            a = MM.decay(hl)
            self._cache[hl] = a ** self.t * self.prior + (1 - a) * (self.H @ (a ** np.arange(HMAX)).astype(
                np.float32)).astype(float)
        return self._cache[hl]

    def X(self, spec: dict) -> np.ndarray:
        cols = [self.ew(h) for h in spec["hls"]]
        if spec.get("shrink_n0"):
            n0 = spec["shrink_n0"]
            cols.append((n0 * self.prior + self.sum) / (n0 + self.t))
        cols.append(self.prior)
        if spec.get("intercept"):
            cols = [np.ones(len(self.t)), *cols]
        return np.column_stack(cols)


def coef_names(spec: dict) -> list[str]:
    names = [f"ewma_{h:g}" for h in spec["hls"]]
    if spec.get("shrink_n0"):
        names.append(f"shrink_n0_{spec['shrink_n0']:g}")
    names.append("prior")
    return (["const"] if spec.get("intercept") else []) + names


def wls(X, y, w):
    sw = np.sqrt(w)
    return np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)[0]


def wmse(y, w, p) -> float:
    return float(np.sum(w * (y - p) ** 2) / w.sum())


def cv_mse(des: Design, y, w, codes, spec, folds=5, seed=0) -> float:
    fold = np.random.default_rng(seed).integers(0, folds, codes.max() + 1)[codes]
    X = des.X(spec)
    se = 0.0
    for k in range(folds):
        trn, tst = fold != k, fold == k
        b = wls(X[trn], y[trn], w[trn])
        se += float(np.sum(w[tst] * (y[tst] - X[tst] @ b) ** 2))
    return se / w.sum()


def cluster_index(players: np.ndarray):
    codes, uniq = pd.factorize(players)
    return codes, len(uniq)


def boot_coef(X, y, w, codes, ncl, rng, n_boot):
    b = wls(X, y, w)
    bs = np.array([wls(X, y, w * np.bincount(rng.integers(0, ncl, ncl), minlength=ncl)[codes])
                   for _ in range(n_boot)])
    return b, bs


def coef_table(spec, b, bs) -> dict:
    out = {n: {"coef": round(float(v), 4), "se": round(float(s), 4)}
           for n, v, s in zip(coef_names(spec), b, bs.std(axis=0), strict=True)}
    sl = slice(1, None) if spec.get("intercept") else slice(None)
    out["sum_slopes"] = {"coef": round(float(b[sl].sum()), 4), "se": round(float(bs[:, sl].sum(1).std()), 4)}
    return out


# ---------------------------------------------------------------------------------------------- evaluation
def metrics(y, w, preds: dict[str, np.ndarray], codes, ncl, rng, n_boot, ref="new") -> dict:
    names = list(preds)
    se2 = {k: np.bincount(codes, w * (y - p) ** 2, ncl) for k, p in preds.items()}
    ae = {k: np.bincount(codes, w * np.abs(y - p), ncl) for k, p in preds.items()}
    W = np.bincount(codes, w, ncl)

    def stat(ix):
        Wt = W[ix].sum()
        return ({k: np.sqrt(se2[k][ix].sum() / Wt) for k in names}, {k: ae[k][ix].sum() / Wt for k in names})

    rm, ma = stat(np.arange(ncl))
    boots = [stat(rng.integers(0, ncl, ncl)) for _ in range(n_boot)]
    out: dict = {"n": int(len(y)), "players": int(ncl)}
    for k in names:
        out[k] = {"rmse": round(rm[k], 3), "rmse_se": round(float(np.std([b[0][k] for b in boots])), 3),
                  "mae": round(ma[k], 3)}
        if k != ref:
            out[k][f"minus_{ref}"] = round(rm[k] - rm[ref], 3)
            out[k][f"minus_{ref}_se"] = round(float(np.std([b[0][k] - b[0][ref] for b in boots])), 3)
    return out


def calibration(y, w, p) -> dict:
    a, b = wls(np.column_stack([np.ones(len(p)), p]), y, w)
    return {"intercept": round(float(a), 3), "slope": round(float(b), 3),
            "mean_resid": round(float(np.sum(w * (y - p)) / w.sum()), 3)}


def by_groups(te: pd.DataFrame, y, w, preds, rng, n_boot, ref="new") -> dict:
    groups = {f"t {lo}-{hi}": te["t"].between(lo, hi).to_numpy() for lo, hi in BUCKETS}
    groups["t 1-5"] = te["t"].between(1, 5).to_numpy()
    groups.update({"jump_ge_8": (te["last3"] - te["before3"]).to_numpy() >= 8,
                   "drop_ge_8": (te["before3"] - te["last3"]).to_numpy() >= 8,
                   "promoted_to_starter": (te["start3"] - te["s_before"]).to_numpy() >= 2 / 3,
                   "back_from_absence_ge3": te["back"].to_numpy()})
    out = {}
    for g, msk in groups.items():
        c, ncl = cluster_index(te["player"].to_numpy()[msk])
        out[g] = metrics(y[msk], w[msk], {k: p[msk] for k, p in preds.items()}, c, ncl, rng, n_boot, ref)
        out[g]["mean_actual"] = round(float(np.sum(w[msk] * y[msk]) / w[msk].sum()), 2)
        out[g]["mean_pred"] = {k: round(float(np.sum(w[msk] * p[msk]) / w[msk].sum()), 2) for k, p in preds.items()}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nocturne", type=Path, default=None)
    ap.add_argument("--totals", type=Path, default=ROOT / "data/raw/bbref_sumitrodatta/player_totals.csv")
    ap.add_argument("--n-boot", type=int, default=200)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "report_minutes.json")
    a = ap.parse_args()
    rng = np.random.default_rng(20261024)
    box = [load_espn(s) for s in [*ESPN_FIT, TEST]]
    if a.nocturne:
        box.append(load_nocturne(a.nocturne))
    d = prep(pd.concat(box, ignore_index=True))
    fit_seasons = sorted(s for s in d["season"].unique() if s != TEST)
    totals = pd.read_csv(a.totals)
    core = core_priors(totals, [*fit_seasons, TEST])
    blend = blend_priors(core, [2025, TEST])
    tr = samples(d[d["season"] != TEST], core, "core")
    te_core = samples(d[d["season"] == TEST], core, "core")
    te = samples(d[d["season"] == TEST], blend, "blend")  # production-like prior (primary test)
    dtr = Design(tr)
    ctr, ntr = cluster_index(tr["player"].to_numpy())
    cte, nte = cluster_index(te["player"].to_numpy())
    rep: dict = {"model": MM.MINUTES_MODEL_VERSION, "fit_seasons": [int(s) for s in fit_seasons], "test": TEST,
                 "n_boot": a.n_boot, "n_train": len(tr), "train_players": ntr, "n_test": len(te),
                 "test_players": nte, "n_test_core_prior": len(te_core),
                 "train_points_by_season": {int(k): int(v) for k, v in tr["season"].value_counts().sort_index().items()},
                 "grid": {"short": HS_GRID, "long": HL_GRID, "third_ewma": THIRD_EWMA, "shrink_n0": SHRINK_N0}}
    dte, dtc = Design(te), Design(te_core)
    old = ((OLD_N0 * te["prior"] + te["t"] * te["mean"]) / (OLD_N0 + te["t"])).to_numpy()
    base = {"old_shrink": old, "last3": te["last3"].to_numpy(), "season_mean": te["mean"].to_numpy(),
            "prior": te["prior"].to_numpy()}
    specs: dict[str, dict] = {}
    # ---- half-lives per target (no intercept, 2 EWMAs + prior)
    rep["cv"] = {}
    for name, (ycol, wcol) in TARGETS.items():
        y, w = tr[ycol].to_numpy(float), tr[wcol].to_numpy(float)
        grid = {(hs, hl): cv_mse(dtr, y, w, ctr, {"hls": [hs, hl]}) for hs in HS_GRID for hl in HL_GRID}
        hs, hl = min(grid, key=grid.get)
        specs[name] = {"hls": [hs, hl]}
        rep["cv"][name] = {"grid_rmse": {f"{k[0]:g}/{k[1]:g}": round(float(np.sqrt(v)), 4) for k, v in grid.items()},
                           "best": [hs, hl]}
    # ---- model B variants (rest of season)
    y, w = tr["yr"].to_numpy(float), tr["nr"].to_numpy(float)
    hs, hl = specs["ros"]["hls"]
    third = {f"ewma3_{h:g}": {"hls": [hs, hl, h]} for h in THIRD_EWMA}
    third.update({f"shrink_{n:g}": {"hls": [hs, hl], "shrink_n0": n} for n in SHRINK_N0})
    cv3 = {k: cv_mse(dtr, y, w, ctr, s) for k, s in third.items()}
    best3 = min(cv3, key=cv3.get)
    # differences are reported vs the first entry (the intercept variant)
    b_specs = {"B_intercept": {**specs["ros"], "intercept": True}, "B_no_intercept": specs["ros"],
               "B_third_term": third[best3]}
    rep["ros_variants_cv_rmse"] = {**{k: round(float(np.sqrt(v)), 4) for k, v in cv3.items()},
                                   "B_no_intercept": round(float(np.sqrt(cv_mse(dtr, y, w, ctr, specs["ros"]))), 4),
                                   "B_intercept": round(float(np.sqrt(cv_mse(dtr, y, w, ctr, b_specs["B_intercept"]))),
                                                        4)}
    rep["ros_third_term_chosen"] = best3
    # ---- fit, coefficients, OOS
    models: dict[str, tuple[dict, np.ndarray]] = {}
    rep["coef"] = {}
    for name, spec in {"A_next5": specs["next5"], **b_specs, "next1": specs["next1"],
                       "next10": specs["next10"]}.items():
        tgt = "ros" if name.startswith("B_") else name.removeprefix("A_")
        ycol, wcol = TARGETS[tgt]
        b, bs = boot_coef(dtr.X(spec), tr[ycol].to_numpy(float), tr[wcol].to_numpy(float), ctr, ntr, rng, a.n_boot)
        models[name] = (spec, b)
        rep["coef"][name] = {"target": tgt, "spec": spec, **coef_table(spec, b, bs)}
    rep["oos"], rep["calibration"], rep["groups"] = {}, {}, {}
    for label, names, tgt in (("A", ["A_next5"], "next5"), ("B", list(b_specs), "ros"),
                              ("next1", ["next1"], "next1"), ("next10", ["next10"], "next10")):
        ycol, wcol = TARGETS[tgt]
        yt, wt = te[ycol].to_numpy(float), te[wcol].to_numpy(float)
        preds = {n: dte.X(models[n][0]) @ models[n][1] for n in names} | base
        ref = names[0]
        rep["oos"][label] = metrics(yt, wt, preds, cte, nte, rng, a.n_boot, ref)
        rep["calibration"][label] = {k: calibration(yt, wt, p) for k, p in preds.items()}
        if label in ("A", "B"):
            rep["groups"][label] = by_groups(te, yt, wt, {k: v for k, v in preds.items() if k != "prior"}, rng,
                                             a.n_boot, ref)
            # same with the core prior at test time
            ytc, wtc = te_core[ycol].to_numpy(float), te_core[wcol].to_numpy(float)
            cc, ncc = cluster_index(te_core["player"].to_numpy())
            oldc = ((OLD_N0 * te_core["prior"] + te_core["t"] * te_core["mean"]) / (OLD_N0 + te_core["t"])).to_numpy()
            rep["oos"][f"{label}_core_prior_at_test"] = metrics(
                ytc, wtc, {**{n: dtc.X(models[n][0]) @ models[n][1] for n in names}, "old_shrink": oldc}, cc, ncc,
                rng, a.n_boot, ref)
    # ---- prior check: 2024-25 alone, core vs blend prior (same half-lives)
    rep["prior_check_2025"] = {}
    for pname, src, col in (("core", core, "core"), ("blend", blend, "blend")):
        s25 = samples(d[d["season"] == 2025], src, col)
        d25 = Design(s25)
        c25, n25 = cluster_index(s25["player"].to_numpy())
        rep["prior_check_2025"][pname] = {"n": len(s25)}
        for name in ("A_next5", "B_no_intercept"):
            spec = models[name][0]
            ycol, wcol = TARGETS["next5" if name == "A_next5" else "ros"]
            b, bs = boot_coef(d25.X(spec), s25[ycol].to_numpy(float), s25[wcol].to_numpy(float), c25, n25, rng,
                              a.n_boot)
            rep["prior_check_2025"][pname][name] = coef_table(spec, b, bs)
    # ---- sensitivity: fit without 2019-20 / 2020-21
    keep = ~tr["season"].isin([2020, 2021]).to_numpy()
    if (~keep).any():
        rep["sensitivity_no_2020_2021"] = {}
        for name in ("A_next5", "B_no_intercept"):
            spec = models[name][0]
            ycol, wcol = TARGETS["next5" if name == "A_next5" else "ros"]
            b = wls(dtr.X(spec)[keep], tr[ycol].to_numpy(float)[keep], tr[wcol].to_numpy(float)[keep])
            yt, wt = te[ycol].to_numpy(float), te[wcol].to_numpy(float)
            rep["sensitivity_no_2020_2021"][name] = {
                "coef": dict(zip(coef_names(spec), np.round(b, 4).tolist(), strict=True)),
                "oos_rmse": round(float(np.sqrt(wmse(yt, wt, dte.X(spec) @ b))), 3)}
    # ---- decisions (rules from the review): ROS = intercept only if it beats every no-intercept variant by > 2 SE;
    # t <= 5 fallback to the old shrink unless the deployed model beats it at t 1-5 by > 2 SE
    ob = rep["oos"]["B"]
    no_int = min(("B_no_intercept", "B_third_term"), key=lambda k: ob[k]["rmse"])
    use_int = all(ob[k]["minus_B_intercept"] > 2 * ob[k]["minus_B_intercept_se"]
                  for k in ("B_no_intercept", "B_third_term"))
    b_choice = "B_intercept" if use_int else no_int
    rep["deployed"] = {}
    for horizon, name in (("next5", "A_next5"), ("ros", b_choice)):
        # paired comparison vs the old formula at t <= 5
        ycol, wcol = TARGETS[horizon]
        msk = te["t"].between(1, 5).to_numpy()
        c, ncl = cluster_index(te["player"].to_numpy()[msk])
        mm = metrics(te[ycol].to_numpy(float)[msk], te[wcol].to_numpy(float)[msk],
                     {name: (dte.X(models[name][0]) @ models[name][1])[msk], "old_shrink": old[msk]}, c, ncl,
                     rng, a.n_boot, name)
        gain, se = mm["old_shrink"][f"minus_{name}"], mm["old_shrink"][f"minus_{name}_se"]
        fb = 0 if gain > 2 * se else 5
        prm = to_params(models[name][0], models[name][1], horizon, fallback_t=fb)
        rep["deployed"][horizon] = {"model": name, "t_le_5_old_minus_model": gain, "t_le_5_se": se,
                                    **prm.as_dict()}
    rep["deployed"]["ros_choice_rule"] = {"intercept_beats_all_no_intercept_by_2se": bool(use_int),
                                          "best_no_intercept": no_int}
    # module consistency (deployed forms)
    rep["module_consistency_maxabs"] = {}
    for name in ("A_next5", "B_no_intercept", "B_intercept", "B_third_term"):
        spec, b = models[name]
        p = to_params(spec, b, "x", fallback_t=0)
        pm = np.array([MM.project_mpg(r.hist[::-1], r.prior, p) for r in te.head(400).itertuples()])
        rep["module_consistency_maxabs"][name] = float(np.max(np.abs(pm - np.clip(dte.X(spec) @ b, 0, 48)[:400])))
    rep["git_sha"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                    cwd=ROOT).stdout.strip()
    a.out.write_text(json.dumps(rep, indent=1, default=float) + "\n")
    print(json.dumps({k: rep[k] for k in ("deployed", "module_consistency_maxabs")}, indent=1))


def to_params(spec: dict, b: np.ndarray, target: str, fallback_t: int) -> MM.MinutesParams:
    names = coef_names(spec)
    c = dict(zip(names, (round(float(v), 4) for v in b), strict=True))
    return MM.MinutesParams(half_lives=tuple(spec["hls"]), b_ewma=tuple(c[f"ewma_{h:g}"] for h in spec["hls"]),
                            b_prior=c["prior"], const=c.get("const", 0.0), shrink_n0=spec.get("shrink_n0") or 0.0,
                            b_shrink=c.get(f"shrink_n0_{spec.get('shrink_n0', 0):g}", 0.0), target=target,
                            fallback_t=fallback_t)


if __name__ == "__main__":
    main()
