"""Fit and out-of-sample test of the recency-weighted in-season minutes model (fantasy.model.minutes).

Usage (repo root, offline; uses committed data only):
  PYTHONPATH=src python scripts/inseason_model/fit_minutes.py [--n-boot 200] --out scripts/inseason_model/report_minutes.json

Data: ESPN box scores data/raw/espn_boxscores/player_games_{2025,2026}.csv.gz (regular season). Train 2024-25, test
2025-26 (strictly out of sample). Older seasons (NocturneBear 2011-2024, used by fit_stats.py) are not in the repo and
have no starter flag; not used here.
Prior: production-like preseason mpg = mean of the projection core on T-3..T-1 (proj_backtest.project_core, no
leakage) and ESPN's preseason projection (data/raw/espn/projections_<T-1>_<T>.csv), whichever exist (the production
default is the 50/50 ours+ESPN blend).
Samples: every player-season with a prior, at every t >= 1 games played that has >= 1 later game played (needed for a
target; no other selection on the future). Features use games 0..t-1 only (fantasy.model.minutes.feature_panel).
Targets: mean minutes over the next game / next k = 5 (primary, deployed) / 10 games played (or fewer if fewer remain;
weight = number of target games) and over all remaining games played (`ros`, weight = remaining games).
Model (no intercept): y = b_short EWMA(hs) + b_long EWMA(hl) + b_prior prior, EWMAs of minutes in games played
initialised at the prior (fantasy.model.minutes.ewma). Half-lives (hs, hl) chosen on a grid by 5-fold CV on the
TRAINING season with folds = disjoint player sets; coefficients by WLS. Variants compared by the same CV and OOS:
with intercept (diagnostic), coefficients by games-played bucket, + start share of the last 3 games, + teammate
absence (out_now = sum of season-to-date mpg (prior if < 5 games) of >= 28-mpg teammates who missed his team's latest
game; out_rec = the same averaged over his last 3 games). SEs: cluster bootstrap over players (train: coefficients;
test: RMSE/MAE and paired differences vs the new model). Baselines: old stat-shrink formula (n0 = 5.2, equal weights),
last-3, season mean, prior. Subgroups and calibration slope (WLS of actual on predicted).
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

from fantasy.data.ids import canonical_id  # noqa: E402
from fantasy.model import minutes as MM  # noqa: E402
from fantasy.model.proj_backtest import project_core  # noqa: E402

TRAIN, TEST = 2025, 2026
OLD_N0 = 5.2  # inseason.STAT_N0['mpg']
HIGH_MPG = 28.0
TARGETS = {"next1": ("y1", "n1"), "next5": ("y5", "n5"), "next10": ("y10", "n10"), "ros": ("yr", "nr")}
DEPLOY = "next5"
HMAX = 90
BUCKETS = [(1, 2), (3, 5), (6, 10), (11, 20), (21, 40), (41, 90)]
HS_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
HL_GRID = [6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 40.0, 1000.0]  # 1000 ~ equal weights (with the prior's decaying weight)


# ----------------------------------------------------------------------------------------------------- data
def priors(seasons) -> pd.DataFrame:
    tot = pd.read_csv(ROOT / "data/raw/bbref_sumitrodatta/player_totals.csv")
    car = pd.read_csv(ROOT / "data/raw/bbref_sumitrodatta/player_career_info.csv")
    out = []
    for T in seasons:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            d = project_core(tot, car, T).reset_index()
        o = d[["player_id", "mpg"]].rename(columns={"mpg": "ours"}).drop_duplicates("player_id", keep=False)
        e = pd.read_csv(ROOT / f"data/raw/espn/projections_{T - 1}_{T % 100:02d}.csv")
        e["player_id"] = e["name"].map(canonical_id)
        e = e[["player_id", "mpg"]].rename(columns={"mpg": "espn"}).drop_duplicates("player_id", keep=False)
        m = o.merge(e, on="player_id", how="outer").assign(season=T)
        out.append(m)
    p = pd.concat(out, ignore_index=True)
    p["prior"] = p[["ours", "espn"]].mean(axis=1)
    return p[p["prior"] > 0]


def load_box(season: int) -> pd.DataFrame:
    d = pd.read_csv(ROOT / f"data/raw/espn_boxscores/player_games_{season}.csv.gz")
    d = d[d["regular_season"].astype(bool)].copy()
    d["min"] = pd.to_numeric(d["min"], errors="coerce").fillna(0.0)
    d["played"] = d["played"].astype(bool) & (d["min"] > 0)
    d = d[d["player_id"].astype(str).str.len() > 0]
    d = d.sort_values("min", ascending=False).drop_duplicates(["game_id", "player_id"])
    tg = (d[["team", "game_id", "game_date"]].drop_duplicates(["team", "game_id"])
          .sort_values(["team", "game_date", "game_id"]))
    tg["tgi"] = tg.groupby("team").cumcount()
    return d.merge(tg[["team", "game_id", "tgi"]], on=["team", "game_id"]).sort_values(["game_date", "game_id"])


def team_absence(d: pd.DataFrame, prior: pd.Series) -> dict[tuple[str, str], np.ndarray]:
    """(team, player) -> per team game: summed reference mpg of >= HIGH_MPG teammates who missed that game.
    Roster at game g: listed for this team at some game <= g and not listed for another team since (no look-ahead;
    a waived player keeps counting as absent). Reference mpg: season-to-date mpg BEFORE game g if >= 5 games played,
    else the prior."""
    last_team: dict[str, list[tuple[str, str]]] = {}
    for r in d[["player_id", "team", "game_date"]].drop_duplicates().itertuples(index=False):
        last_team.setdefault(r.player_id, []).append((r.game_date, r.team))
    out = {}
    for team, dt in d.groupby("team"):
        n = int(dt["tgi"].max()) + 1
        dates = dt.drop_duplicates("tgi").set_index("tgi")["game_date"].sort_index().to_numpy()
        players = dt["player_id"].unique()
        M = np.full((len(players), n), np.nan)
        idx = {p: i for i, p in enumerate(players)}
        for r in dt.itertuples(index=False):
            M[idx[r.player_id], r.tgi] = r.min if r.played else 0.0
        for i, p in enumerate(players):
            first = int(np.flatnonzero(~np.isnan(M[i])).min())
            other = sorted(dt_ for dt_, tm in last_team[p] if tm != team and dt_ > dates[first])
            end = n if not other else int(np.searchsorted(dates, other[0], side="left"))
            seg = M[i, first:end]
            seg[np.isnan(seg)] = 0.0
        mins = np.nan_to_num(M)
        cg = np.cumsum(mins > 0, axis=1)
        cs = np.cumsum(mins, axis=1)
        gb = np.concatenate([np.zeros((len(players), 1)), cg[:, :-1]], axis=1)
        sb = np.concatenate([np.zeros((len(players), 1)), cs[:, :-1]], axis=1)
        pr = np.array([prior.get(p, 0.0) for p in players])[:, None]
        ref = np.where(gb >= 5, sb / np.maximum(gb, 1), pr)
        contrib = np.where((M == 0) & (ref >= HIGH_MPG), ref, 0.0)
        tot = contrib.sum(axis=0)
        for i, p in enumerate(players):
            out[(team, p)] = tot - contrib[i]
    return out


def samples(season: int, pri: pd.DataFrame) -> pd.DataFrame:
    d = load_box(season)
    pp = pri[pri["season"] == season].set_index("player_id")["prior"]
    absn = team_absence(d, pp)
    rows = []
    for pid, dp in d[d["played"]].groupby("player_id"):
        if pid not in pp.index:
            continue
        m = dp["min"].to_numpy(float)
        st = dp["starter"].astype(bool).to_numpy()
        teams, tgis = dp["team"].to_numpy(), dp["tgi"].to_numpy()
        n = len(m)
        if n < 2:
            continue
        f = MM.feature_panel(m, float(pp[pid]), MM.default_params())
        s3 = np.array([st[max(0, t - 3):t].mean() if t else 0.0 for t in range(n + 1)])
        # team games missed right before each game played (same team; 0 after a trade)
        gap = np.zeros(n, int)
        same = teams[1:] == teams[:-1]
        gap[1:] = np.where(same, tgis[1:] - tgis[:-1] - 1, 0)
        out_g = np.array([absn[(teams[j], pid)][tgis[j]] for j in range(n)])
        cs = np.concatenate([[0.0], np.cumsum(m)])
        for t in range(1, n):
            fut = m[t:]
            lo = max(0, t - 3)
            rows.append({
                "season": season, "player": pid, "t": t, "prior": float(pp[pid]),
                "start3": s3[t], "mean": f["mean"].iloc[t], "last3": f["last3"].iloc[t],
                "before3": (cs[lo] / lo) if lo >= 3 else np.nan,  # mean of games before the last 3 (jump subgroup)
                "s_before": st[:lo].mean() if lo >= 3 else np.nan,
                "back": bool((gap[lo:t] >= 3).any()),  # one of his last 3 games came right after a >= 3-game absence
                "out_now": out_g[t - 1], "out_rec": out_g[lo:t].mean(),
                "y1": fut[0], "n1": 1, "y5": fut[:5].mean(), "n5": min(5, len(fut)), "y10": fut[:10].mean(), "n10": min(10, len(fut)),
                "yr": fut.mean(), "nr": len(fut),
                "hist": m[:t][::-1][:HMAX].copy(),  # newest first
            })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------- model
class Design:
    def __init__(self, D: pd.DataFrame):
        self.H = np.zeros((len(D), HMAX))
        for i, h in enumerate(D["hist"].to_numpy()):
            self.H[i, :len(h)] = h
        self.t = D["t"].to_numpy(float)
        self.prior = D["prior"].to_numpy(float)
        self.cols = {c: D[c].to_numpy(float) for c in ("start3", "out_now", "out_rec")}
        self.bucket = np.digitize(self.t, [b[0] for b in BUCKETS[1:]])
        self._cache: dict[float, np.ndarray] = {}

    def ew(self, hl: float) -> np.ndarray:
        """EWMA initialised at the prior after t games: a^t prior + (1 - a) sum_age a^age m (= MM.ewma)."""
        if hl not in self._cache:
            a = MM.decay(hl)
            self._cache[hl] = a ** self.t * self.prior + (1 - a) * (self.H @ a ** np.arange(HMAX))
        return self._cache[hl]

    def X(self, hs: float, hl: float, variant: str = "base", extra=()) -> np.ndarray:
        base = [self.ew(hs), self.ew(hl), self.prior]
        if variant == "bucket":  # coefficients by games-played bucket
            base = [c * (self.bucket == b) for b in range(len(BUCKETS)) for c in base]
        cols = base + [self.cols[c] for c in extra]
        if variant == "intercept":
            cols = [np.ones(len(self.t)), *cols]
        return np.column_stack(cols)


def wls(X, y, w):
    sw = np.sqrt(w)
    return np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)[0]


def wmse(y, w, p) -> float:
    return float(np.sum(w * (y - p) ** 2) / w.sum())


def cv_mse(des: Design, y, w, codes, hs, hl, variant="base", extra=(), folds=5, seed=0) -> float:
    """5-fold CV MSE with folds = disjoint sets of players (clustered)."""
    fold = np.random.default_rng(seed).integers(0, folds, codes.max() + 1)[codes]
    X = des.X(hs, hl, variant, extra)
    se = 0.0
    for k in range(folds):
        trn, tst = fold != k, fold == k
        b = wls(X[trn], y[trn], w[trn])
        se += float(np.sum(w[tst] * (y[tst] - X[tst] @ b) ** 2))
    return se / w.sum()


# ---------------------------------------------------------------------------------------------- evaluation
def cluster_index(players: np.ndarray):
    codes, uniq = pd.factorize(players)
    return codes, len(uniq)


def metrics(y, w, preds: dict[str, np.ndarray], codes, ncl, rng, n_boot) -> dict:
    """Weighted RMSE / MAE per model, cluster-bootstrap SEs, and paired RMSE differences vs 'new'."""
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
                  "mae": round(ma[k], 3), "mae_se": round(float(np.std([b[1][k] for b in boots])), 3)}
        if k != "new" and "new" in names:
            out[k]["rmse_minus_new"] = round(rm[k] - rm["new"], 3)
            out[k]["rmse_minus_new_se"] = round(float(np.std([b[0][k] - b[0]["new"] for b in boots])), 3)
    return out


def calibration(y, w, p) -> dict:
    a, b = wls(np.column_stack([np.ones(len(p)), p]), y, w)
    return {"intercept": round(float(a), 3), "slope": round(float(b), 3),
            "mean_resid": round(float(np.sum(w * (y - p)) / w.sum()), 3)}


def boot_coef(X, y, w, codes, ncl, rng, n_boot) -> tuple[np.ndarray, np.ndarray]:
    b = wls(X, y, w)
    bs = np.array([wls(X, y, w * np.bincount(rng.integers(0, ncl, ncl), minlength=ncl)[codes])
                   for _ in range(n_boot)])
    return b, bs


def rnd(d: dict) -> dict:
    return {k: round(float(v), 4) for k, v in d.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-boot", type=int, default=200)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "report_minutes.json")
    a = ap.parse_args()
    rng = np.random.default_rng(20261024)
    pri = priors([TRAIN, TEST])
    tr, te = samples(TRAIN, pri), samples(TEST, pri)
    dtr, dte = Design(tr), Design(te)
    ctr, ntr = cluster_index(tr["player"].to_numpy())
    cte, nte = cluster_index(te["player"].to_numpy())
    rep: dict = {"model": MM.MINUTES_MODEL_VERSION, "train": TRAIN, "test": TEST, "n_boot": a.n_boot,
                 "n_train": len(tr), "train_players": ntr, "n_test": len(te), "test_players": nte,
                 "prior_rows": {int(s): int(len(g)) for s, g in pri.groupby("season")},
                 "grid": {"short": HS_GRID, "long": HL_GRID}, "cv": {}, "params": {}, "oos": {},
                 "calibration": {}, "subgroups": {}, "variants": {}, "cross_horizon": {}}
    chosen = {}
    names3 = ["b_short", "b_long", "b_prior"]
    for name, (ycol, wcol) in TARGETS.items():
        y, w = tr[ycol].to_numpy(float), tr[wcol].to_numpy(float)
        grid = {(hs, hl): cv_mse(dtr, y, w, ctr, hs, hl) for hs in HS_GRID for hl in HL_GRID if hs < hl}
        hs, hl = min(grid, key=grid.get)
        cvv = {"base": grid[(hs, hl)],
               "intercept": cv_mse(dtr, y, w, ctr, hs, hl, "intercept"),
               "bucket": cv_mse(dtr, y, w, ctr, hs, hl, "bucket"),
               "base+start3": cv_mse(dtr, y, w, ctr, hs, hl, extra=("start3",)),
               "base+teammates": cv_mse(dtr, y, w, ctr, hs, hl, extra=("out_now", "out_rec"))}
        rep["cv"][name] = {"grid_rmse": {f"{k[0]:g}/{k[1]:g}": round(float(np.sqrt(v)), 4) for k, v in grid.items()},
                           "best": [hs, hl], "variants_rmse": {k: round(float(np.sqrt(v)), 4) for k, v in cvv.items()}}
        b, bs = boot_coef(dtr.X(hs, hl), y, w, ctr, ntr, rng, a.n_boot)
        rep["params"][name] = {"hl_short": hs, "hl_long": hl, **rnd(dict(zip(names3, b, strict=True))),
                               "se": rnd(dict(zip(names3, bs.std(axis=0), strict=True))),
                               "coef_sum": round(float(b.sum()), 4), "coef_sum_se": round(float(bs.sum(1).std()), 4)}
        bi, bsi = boot_coef(dtr.X(hs, hl, "intercept"), y, w, ctr, ntr, rng, a.n_boot)
        chosen[name] = (hs, hl, b)
        yt, wt = te[ycol].to_numpy(float), te[wcol].to_numpy(float)
        old = ((OLD_N0 * te["prior"] + te["t"] * te["mean"]) / (OLD_N0 + te["t"])).to_numpy()
        preds = {"new": dte.X(hs, hl) @ b, "old_shrink": old, "last3": te["last3"].to_numpy(),
                 "season_mean": te["mean"].to_numpy(), "prior": te["prior"].to_numpy()}
        rep["oos"][name] = metrics(yt, wt, preds, cte, nte, rng, a.n_boot)
        rep["calibration"][name] = {k: calibration(yt, wt, p) for k, p in preds.items()}
        var = {"new": preds["new"], "intercept": dte.X(hs, hl, "intercept") @ bi}
        for vname, (variant, extra) in {"bucket": ("bucket", ()), "start3": ("base", ("start3",)),
                                        "teammates": ("base", ("out_now", "out_rec"))}.items():
            bv = wls(dtr.X(hs, hl, variant, extra), y, w)
            var[vname] = dte.X(hs, hl, variant, extra) @ bv
            if extra:
                rep.setdefault("extension_coef", {}).setdefault(name, {})[vname] = np.round(bv[3:], 4).tolist()
        k4 = ["const", *names3]
        rep["variants"][name] = {"oos": metrics(yt, wt, var, cte, nte, rng, a.n_boot),
                                 "intercept_coef": rnd(dict(zip(k4, bi, strict=True))),
                                 "intercept_se": rnd(dict(zip(k4, bsi.std(axis=0), strict=True))),
                                 "intercept_coef_sum_slopes": round(float(bi[1:].sum()), 4)}
        groups = {f"t {lo}-{hi}": te["t"].between(lo, hi).to_numpy() for lo, hi in BUCKETS}
        groups.update({"jump_ge_8": (te["last3"] - te["before3"]).to_numpy() >= 8,
                       "drop_ge_8": (te["before3"] - te["last3"]).to_numpy() >= 8,
                       "promoted_to_starter": (te["start3"] - te["s_before"]).to_numpy() >= 2 / 3,
                       "back_from_absence_ge3": te["back"].to_numpy()})
        sg = {}
        for g, msk in groups.items():
            c, ncl = cluster_index(te["player"].to_numpy()[msk])
            sg[g] = metrics(yt[msk], wt[msk], {k: p[msk] for k, p in preds.items() if k != "prior"}, c, ncl, rng,
                            a.n_boot)
            for k in ("new", "old_shrink"):
                sg[g][f"mean_pred_{k}"] = round(float(np.sum(wt[msk] * preds[k][msk]) / wt[msk].sum()), 2)
            sg[g]["mean_actual"] = round(float(np.sum(wt[msk] * yt[msk]) / wt[msk].sum()), 2)
        rep["subgroups"][name] = sg
    for name, (ycol, wcol) in TARGETS.items():  # every fitted horizon's model on every target
        yt, wt = te[ycol].to_numpy(float), te[wcol].to_numpy(float)
        rep["cross_horizon"][name] = {f"fit_{k}": round(float(np.sqrt(wmse(yt, wt, dte.X(*v[:2]) @ v[2]))), 3)
                                      for k, v in chosen.items()}
    hs, hl, b = chosen[DEPLOY]
    prm = MM.MinutesParams(hl_short=hs, hl_long=hl, b_short=round(float(b[0]), 4), b_long=round(float(b[1]), 4),
                           b_prior=round(float(b[2]), 4), target=DEPLOY)
    p_mod = [MM.project_mpg(r.hist[::-1], r.prior, prm) for r in te.head(500).itertuples()]
    rep["module_consistency_maxabs"] = float(np.max(np.abs(np.array(p_mod) - (dte.X(hs, hl) @ b)[:500])))
    rep["deployed"] = prm.as_dict()
    rep["git_sha"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                    cwd=ROOT).stdout.strip()
    a.out.write_text(json.dumps(rep, indent=1, default=float) + "\n")
    print(json.dumps({k: rep[k] for k in ("params", "deployed", "cross_horizon", "module_consistency_maxabs")},
                     indent=1))
    for name in TARGETS:
        print(name, "cv", rep["cv"][name]["best"], rep["cv"][name]["variants_rmse"])
        print("  oos", {k: (v["rmse"], v.get("rmse_minus_new"), v.get("rmse_minus_new_se"))
                        for k, v in rep["oos"][name].items() if isinstance(v, dict)})
        print("  var", {k: (v["rmse"], v.get("rmse_minus_new"), v.get("rmse_minus_new_se"))
                        for k, v in rep["variants"][name]["oos"].items() if isinstance(v, dict)})
        print("  int", rep["variants"][name]["intercept_coef"], rep["variants"][name]["intercept_se"])


if __name__ == "__main__":
    main()
