"""Fit and out-of-sample test of the in-season update of PER-GAME stats (shrink season-to-date toward the prior).

Usage:
  python scripts/inseason_model/fit_stats.py --tl tl.pkl --priors priors.pkl --totals "Player Totals.csv" \
      [--career data/raw/bbref_sumitrodatta/player_career_info.csv] [--espn-box data/raw/espn_boxscores] \
      [--n-boot 200] --out scripts/inseason_model/report_stats.json
tl.pkl / priors.pkl come from timelines.py (one row per player-season x team game; gp prior + roster info).
Test seasons 2025/2026 are built here from the repo's ESPN box scores (player_games_<season>.csv.gz) with
timelines.load_espn + timelines.timelines when the files exist.

Prior: the production projection core run on T-3..T-1 only (fantasy.model.proj_backtest.project_core, no leakage):
per-game line and mpg. Robustness: the production default prior, 0.5 ours + 0.5 ESPN preseason per game
(data/raw/espn/projections_YYYY_YY.csv, blend.BLEND_STATS incl. mpg; ours where ESPN has no row).

Posterior at checkpoint k team games (g = games played so far, M = minutes so far):
  mpg     mpg_post = (n0 * mpg_prior + g * mpg_obs) / (n0 + g)                   n0 in games
  rates   r_post = (n0 * r_prior + M * r_obs) / (n0 + M), r = stat per minute     n0 in minutes
          (reb, ast, stl, blk, tov, fg3m, fga, fta; pts also fitted directly for reference)
  pct     p_post = (n0 * p_prior + att * p_obs) / (n0 + att)                    n0 in attempts (FG%, FT%)
  line    stat_pg = r_post * mpg_post; fgm = FG% x fga, ftm = FT% x fta; pts = 2 fgm + fg3m + ftm
k = 0 gives the prior exactly; M, att, g -> inf gives the observed line. n0 is the sample size at which the observed
rate gets half weight, i.e. the "stabilisation point" (reliability 0.5) when the prior is the population mean.

Fit: each n0 minimises the weighted squared error of the REST-OF-SEASON actual rate (target r_rest = S_rest / M_rest,
weight M_rest; mpg weight = remaining games played; pct weight = remaining attempts), pooled over checkpoints
k in CHECKPOINTS, on training seasons TRAIN (2012-2022 without COVID 2020/2021). Sample: player-seasons with a prior,
g >= 1 at k and >= MIN_REST remaining games played. SE = cluster bootstrap over player-seasons (all checkpoints of a
player-season resampled together). Per-k optima are reported to check whether one constant suffices.

OOS: validation 2023-2024 (box scores of the same source as training), test 2025-2026 (ESPN box scores). Per stat
RMSE of the rest-of-season per-game value (weight = remaining games played; pct: weight = remaining attempts) for
prior-only, observed-only and shrunk; gains with cluster-bootstrap SE. Aggregate: 9-cat per-game error in z units
(category error / cross-player sd of the per-game stat among players with >= 20 mpg and >= 20 games in that season;
FG%/FT% as volume-weighted impact (p - league_p) * attempts per game), summed over categories (mean squared z).
Extensions (reported, not adopted unless OOS evidence): n0 by prior reliability (prior weighted minutes tercile)
and by age bin.
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
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from fantasy.data.ids import canonical_id  # noqa: E402
from fantasy.model.proj_backtest import project_core  # noqa: E402

CHECKPOINTS = [5, 10, 20, 30, 40, 50]
TRAIN = [2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2022]
VALID = [2023, 2024]
TEST = [2025, 2026]
MIN_REST = 10
RATES = ["reb", "ast", "stl", "blk", "tov", "fg3m", "fga", "fta", "pts"]  # pts: reference only (line derives it)
PCTS = {"fg_pct": ("fgm", "fga"), "ft_pct": ("ftm", "fta")}
RAW = ["min", "pts", "fgm", "fga", "fg3m", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"]
LINE = ["mpg", "pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fga", "fta", "fg_pct", "ft_pct"]
CATS9 = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct"]
PRIOR_STATS = ["mpg", "fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"]
BOUNDS = {"mpg": (0.05, 2000.0), "rate": (1.0, 1e6), "pct": (1.0, 1e6)}


# ----------------------------------------------------------------------------------------------- data
def checkpoints(tl: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, slug, k): season-to-date sums (o_*), rest-of-season sums (r_*), g and rest games."""
    tl = tl.sort_values(["season", "slug", "t"]).copy()
    tl["gp"] = tl["played"].astype(float)
    cols = ["gp", *RAW]
    grp = tl.groupby(["season", "slug"], sort=False)
    cum = grp[cols].cumsum()
    tot = grp[cols].transform("sum")
    keep = tl["t"].isin(CHECKPOINTS).to_numpy()
    out = tl.loc[keep, ["season", "slug", "t"]].rename(columns={"t": "k"}).reset_index(drop=True)
    for c in cols:
        o = cum.loc[keep, c].to_numpy()
        out[f"o_{c}"] = o
        out[f"r_{c}"] = tot.loc[keep, c].to_numpy() - o
    return out[(out["o_gp"] >= 1) & (out["r_gp"] >= MIN_REST)].reset_index(drop=True)


def season_lines(tl: pd.DataFrame) -> pd.DataFrame:
    """Full-season per-game lines (for z-score scales)."""
    s = tl.assign(gp=tl["played"].astype(float)).groupby(["season", "slug"])[["gp", *RAW]].sum()
    s = s[s["gp"] > 0]
    out = s[RAW].div(s["gp"], axis=0).rename(columns={"min": "mpg"})
    out["gp"] = s["gp"]
    return out.reset_index()


def our_priors(totals: pd.DataFrame, career: pd.DataFrame, seasons: list[int],
               slug_of: dict[str, str]) -> pd.DataFrame:
    rows = []
    for T in seasons:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            d = project_core(totals, career, target=T)
        d = d.reset_index()
        d["slug"] = [slug_of.get(b, p) for b, p in zip(d["bbref_id"], d["player_id"], strict=True)]
        d["season"] = T
        rows.append(d[["season", "slug", "age", "weighted_minutes", *PRIOR_STATS]])
    p = pd.concat(rows, ignore_index=True)
    return p.drop_duplicates(["season", "slug"], keep=False)


def blend_priors(ours: pd.DataFrame, seasons: list[int]) -> pd.DataFrame:
    """0.5 ours + 0.5 ESPN preseason per game (ours where ESPN has no row); production default prior."""
    out = []
    for T in seasons:
        f = ROOT / "data" / "raw" / "espn" / f"projections_{T - 1}_{T % 100:02d}.csv"
        o = ours[ours["season"] == T].set_index("slug")
        if not f.exists():
            continue
        e = pd.read_csv(f)
        e["slug"] = e["name"].map(canonical_id)
        e = e.drop_duplicates("slug").set_index("slug").reindex(o.index)
        b = o.copy()
        for c in PRIOR_STATS:
            ev = pd.to_numeric(e[c], errors="coerce")
            b[c] = np.where(ev.notna(), 0.5 * o[c] + 0.5 * ev, o[c])
        b["espn_matched"] = e["mpg"].notna().to_numpy()
        b["ours_mpg"] = o["mpg"]
        out.append(b.reset_index())
    return pd.concat(out, ignore_index=True)


def attach_prior(cp: pd.DataFrame, pri: pd.DataFrame) -> pd.DataFrame:
    d = cp.merge(pri, on=["season", "slug"], how="inner")
    d = d[(d["mpg"] >= 1.0) & (d["fga"] > 0) & (d["fta"] > 0)].copy()
    d["p_mpg"] = d["mpg"]
    for s in ["reb", "ast", "stl", "blk", "tov", "fg3m", "fga", "fta"]:
        d[f"p_{s}"] = d[s] / d["mpg"]
    d["p_pts"] = (2 * d["fgm"] + d["fg3m"] + d["ftm"]) / d["mpg"]
    d["p_fg_pct"] = d["fgm"] / d["fga"]
    d["p_ft_pct"] = d["ftm"] / d["fta"]
    d["cluster"] = pd.factorize(d["season"].astype(str) + "|" + d["slug"])[0]
    return d.drop(columns=PRIOR_STATS).reset_index(drop=True)


# ----------------------------------------------------------------------------------------------- model
def stat_arrays(d: pd.DataFrame, stat: str) -> tuple[np.ndarray, ...]:
    """(prior, observed, n_obs, rest target, rest weight) on the stat's own scale."""
    if stat == "mpg":
        n = d["o_gp"].to_numpy()
        obs = d["o_min"].to_numpy() / n
        w = d["r_gp"].to_numpy()
        return d["p_mpg"].to_numpy(), obs, n, d["r_min"].to_numpy() / w, w
    if stat in PCTS:
        m, a = PCTS[stat]
        n = d[f"o_{a}"].to_numpy()
        obs = np.divide(d[f"o_{m}"].to_numpy(), n, out=np.zeros_like(n), where=n > 0)
        w = d[f"r_{a}"].to_numpy()
        tgt = np.divide(d[f"r_{m}"].to_numpy(), w, out=np.zeros_like(w), where=w > 0)
        return d[f"p_{stat}"].to_numpy(), obs, n, tgt, w
    n = d["o_min"].to_numpy()
    w = d["r_min"].to_numpy()
    return d[f"p_{stat}"].to_numpy(), d[f"o_{stat}"].to_numpy() / n, n, d[f"r_{stat}"].to_numpy() / w, w


def shrink(prior: np.ndarray, obs: np.ndarray, n: np.ndarray, n0: float) -> np.ndarray:
    return (n0 * prior + n * obs) / (n0 + n)


def kind(stat: str) -> str:
    return "mpg" if stat == "mpg" else ("pct" if stat in PCTS else "rate")


def fit_n0(prior, obs, n, tgt, w, k: str = "rate") -> float:
    lo, hi = BOUNDS[k]

    def loss(x: float) -> float:
        e = shrink(prior, obs, n, np.exp(x)) - tgt
        return float(np.sum(w * e * e))

    r = minimize_scalar(loss, bounds=(np.log(lo), np.log(hi)), method="bounded", options={"xatol": 1e-4})
    return float(np.exp(r.x))


def fit_all(d: pd.DataFrame, stats: list[str], n_boot: int, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    cl = d["cluster"].to_numpy()
    ncl = cl.max() + 1
    counts = [np.bincount(rng.integers(0, ncl, ncl), minlength=ncl)[cl] for _ in range(n_boot)]
    out = {}
    for s in stats:
        pr, ob, n, tg, w = stat_arrays(d, s)
        kd = kind(s)
        est = fit_n0(pr, ob, n, tg, w, kd)
        boot = [fit_n0(pr, ob, n, tg, w * c, kd) for c in counts] or [est, est]
        per_k = {}
        for k in CHECKPOINTS:
            m = d["k"].to_numpy() == k
            per_k[str(k)] = round(fit_n0(pr[m], ob[m], n[m], tg[m], w[m], kd), 1)
        lb = np.log(boot)
        out[s] = {"n0": round(est, 2), "se": round(float(np.std(boot, ddof=1)), 2),
                  "log_se": round(float(np.std(lb, ddof=1)), 3),
                  "ci90": [round(float(np.quantile(boot, q)), 1) for q in (0.05, 0.95)],
                  "units": {"mpg": "games", "rate": "minutes", "pct": "attempts"}[kind(s)],
                  "per_k": per_k, "n_rows": int(len(d)), "n_player_seasons": int(ncl)}
    return out


def predict(d: pd.DataFrame, n0: dict[str, float], method: str) -> pd.DataFrame:
    """Per-game rest-of-season line under method in {prior, obs, shrunk}; n0 per stat (may be arrays)."""
    out = pd.DataFrame(index=d.index)
    for s in ["mpg", "reb", "ast", "stl", "blk", "tov", "fg3m", "fga", "fta", "fg_pct", "ft_pct"]:
        pr, ob, n, _, _ = stat_arrays(d, s)
        if s in PCTS:
            ob = np.where(n > 0, ob, pr)  # no attempts yet: observed-only falls back to the prior
        if method == "prior":
            v = pr
        elif method == "obs":
            v = ob
        else:
            v = shrink(pr, ob, n, n0[s])
        out[s] = v
    for s in ["reb", "ast", "stl", "blk", "tov", "fg3m", "fga", "fta"]:
        out[s] = out[s] * out["mpg"]
    out["fgm"] = out["fg_pct"] * out["fga"]
    out["ftm"] = out["ft_pct"] * out["fta"]
    out["pts"] = 2 * out["fgm"] + out["fg3m"] + out["ftm"]
    return out


def actual(d: pd.DataFrame) -> pd.DataFrame:
    g = d["r_gp"]
    out = pd.DataFrame({s: d[f"r_{s}"] / g for s in ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m",
                                                     "fga", "fta", "fgm", "ftm"]})
    out["mpg"] = d["r_min"] / g
    out["fg_pct"] = d["r_fgm"] / d["r_fga"].where(d["r_fga"] > 0)
    out["ft_pct"] = d["r_ftm"] / d["r_fta"].where(d["r_fta"] > 0)
    return out


def z_scales(lines: pd.DataFrame, season: int) -> tuple[dict[str, float], dict[str, float]]:
    s = lines[(lines["season"] == season) & (lines["mpg"] >= 20) & (lines["gp"] >= 20)]
    lg = {"fg_pct": s["fgm"].sum() / s["fga"].sum(), "ft_pct": s["ftm"].sum() / s["fta"].sum()}
    sd = {c: float(s[c].std()) for c in ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"]}
    sd["fg_pct"] = float(((s["fgm"] / s["fga"] - lg["fg_pct"]) * s["fga"]).std())
    sd["ft_pct"] = float(((s["ftm"] / s["fta"].where(s["fta"] > 0) - lg["ft_pct"]) * s["fta"]).std())
    return sd, lg


def sq_errors(d: pd.DataFrame, pred: pd.DataFrame, act: pd.DataFrame, lines: pd.DataFrame) -> dict:
    """Per-row squared errors and weights: per stat (natural units) and the summed 9-cat z^2."""
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    g = d["r_gp"].to_numpy()
    for s in LINE:
        e = (pred[s] - act[s]).to_numpy()
        w = d[f"r_{PCTS[s][1]}"].to_numpy() if s in PCTS else g
        e = np.nan_to_num(e)
        out[s] = (e * e, w)
    z2 = np.zeros(len(d))
    for season in d["season"].unique():
        m = (d["season"] == season).to_numpy()
        sd, lg = z_scales(lines, season)
        for c in CATS9:
            if c in PCTS:
                mk, at = PCTS[c]
                ip = ((pred[c] - lg[c]) * pred[at]).to_numpy()
                ia = ((act[c].fillna(lg[c]) - lg[c]) * act[at]).to_numpy()
                e = (ip - ia) / sd[c]
            else:
                e = (pred[c] - act[c]).to_numpy() / sd[c]
            z2[m] += e[m] ** 2
    out["z9"] = (z2, g)
    return out


def compare(d: pd.DataFrame, errs: dict[str, dict], n_boot: int, seed: int = 1) -> dict:
    """RMSE per method and gains of 'shrunk' over the others, with cluster-bootstrap SE."""
    rng = np.random.default_rng(seed)
    cl = d["cluster"].to_numpy()
    ncl = cl.max() + 1
    C = np.stack([np.bincount(rng.integers(0, ncl, ncl), minlength=ncl) for _ in range(n_boot)])
    res = {}
    for key in errs["shrunk"]:
        per = {}
        boots = {}
        for meth, e in errs.items():
            se2, w = e[key]
            num = np.bincount(cl, weights=w * se2, minlength=ncl)
            den = np.bincount(cl, weights=w, minlength=ncl)
            per[meth] = float(np.sqrt(num.sum() / den.sum()))
            boots[meth] = np.sqrt((C @ num) / (C @ den))
        r = {f"rmse_{m}": round(v, 4) for m, v in per.items()}
        if key == "z9":
            r = {f"rms_z_{m}": round(v, 4) for m, v in per.items()}
        for base in ["prior", "obs"]:
            gain = boots[base] - boots["shrunk"]
            r[f"gain_vs_{base}"] = round(per[base] - per["shrunk"], 4)
            r[f"gain_vs_{base}_se"] = round(float(np.std(gain, ddof=1)), 4)
            r[f"gain_vs_{base}_pct"] = round(100 * (1 - per["shrunk"] / per[base]), 1)
        res[key] = r
    return res


def evaluate(d: pd.DataFrame, n0: dict, lines: pd.DataFrame, n_boot: int, extra: dict | None = None) -> dict:
    act = actual(d)
    errs = {m: sq_errors(d, predict(d, n0, m), act, lines) for m in ["prior", "obs", "shrunk"]}
    for name, n0x in (extra or {}).items():
        errs[name] = sq_errors(d, predict(d, n0x, "shrunk"), act, lines)
    out = {"pooled": compare(d, errs, n_boot), "n_rows": int(len(d)),
           "n_player_seasons": int(d["cluster"].max() + 1)}
    out["per_k_z9"] = {}
    out["per_k"] = {}
    for k in CHECKPOINTS:
        m = (d["k"] == k).to_numpy()
        if m.sum() < 30:
            continue
        dk = d[m].reset_index(drop=True)
        dk["cluster"] = pd.factorize(dk["cluster"])[0]
        ek = {meth: {s: (v[0][m], v[1][m]) for s, v in e.items()} for meth, e in errs.items()}
        ck = compare(dk, ek, n_boot)
        out["per_k_z9"][str(k)] = ck["z9"]
        out["per_k"][str(k)] = {s: {kk: v[kk] for kk in ("rmse_prior", "rmse_obs", "rmse_shrunk")}
                                for s, v in ck.items() if s != "z9"}
    if extra:
        for name in extra:
            se_g = {}
            for key in ["z9"]:
                a, w = errs["shrunk"][key]
                b, _ = errs[name][key]
                se_g[key] = _paired_gain(d["cluster"].to_numpy(), a, b, w, n_boot)
            out[f"{name}_vs_shrunk"] = se_g
    return out


def _paired_gain(cl: np.ndarray, a: np.ndarray, b: np.ndarray, w: np.ndarray, n_boot: int) -> dict:
    """RMSE(a) - RMSE(b) (positive = b better) with cluster-bootstrap SE."""
    rng = np.random.default_rng(2)
    ncl = cl.max() + 1
    C = np.stack([np.bincount(rng.integers(0, ncl, ncl), minlength=ncl) for _ in range(n_boot)])
    na, nb = np.bincount(cl, w * a, ncl), np.bincount(cl, w * b, ncl)
    dn = np.bincount(cl, w, ncl)
    g = np.sqrt(na.sum() / dn.sum()) - np.sqrt(nb.sum() / dn.sum())
    gb = np.sqrt((C @ na) / (C @ dn)) - np.sqrt((C @ nb) / (C @ dn))
    return {"gain": round(float(g), 4), "se": round(float(np.std(gb, ddof=1)), 4)}


def group_n0(d_tr: pd.DataFrame, d_te: pd.DataFrame, col: str, edges: list[float], base: dict) -> tuple:
    """Fit n0 per group of `col` on training; return (report, per-row n0 arrays for the test frame)."""
    labels = [f"<{edges[0]:g}"] + [f"{a:g}-{b:g}" for a, b in zip(edges[:-1], edges[1:], strict=True)] \
        + [f">={edges[-1]:g}"]
    gtr = np.digitize(d_tr[col].to_numpy(), edges)
    gte = np.digitize(d_te[col].to_numpy(), edges)
    rep: dict = {}
    arr = {}
    for s in base:
        pr, ob, n, tg, w = stat_arrays(d_tr, s)
        vals = []
        for j in range(len(labels)):
            m = gtr == j
            vals.append(fit_n0(pr[m], ob[m], n[m], tg[m], w[m], kind(s)) if m.sum() > 200 else base[s])
        rep[s] = {lab: round(v, 1) for lab, v in zip(labels, vals, strict=True)}
        arr[s] = np.array(vals)[gte]
    return rep, arr


# ----------------------------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tl", type=Path, required=True)
    ap.add_argument("--priors", type=Path, required=True)
    ap.add_argument("--totals", type=Path, required=True)
    ap.add_argument("--career", type=Path,
                    default=ROOT / "data" / "raw" / "bbref_sumitrodatta" / "player_career_info.csv")
    ap.add_argument("--espn-box", type=Path, default=ROOT / "data" / "raw" / "espn_boxscores")
    ap.add_argument("--n-boot", type=int, default=200)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "report_stats.json")
    a = ap.parse_args()

    tl = pd.read_pickle(a.tl)
    pri_gp = pd.read_pickle(a.priors)
    test = []
    for T in TEST:
        f = a.espn_box / f"player_games_{T}.csv.gz"
        if f.exists():
            import timelines as TL  # noqa: PLC0415 (heavy import, only when ESPN seasons exist)
            box = TL.load_espn([f])
            tl = pd.concat([tl[tl["season"] != T], TL.timelines(box, pri_gp[pri_gp["season"] == T])], ignore_index=True)
            test.append(T)
    totals = pd.read_csv(a.totals)
    career = pd.read_csv(a.career)
    slug_of = dict(zip(pri_gp["player_id"], pri_gp["slug"], strict=True))
    seasons = sorted(set(TRAIN + VALID + test))
    ours = our_priors(totals, career, seasons, slug_of)
    cp = checkpoints(tl[tl["season"].isin(seasons)])
    lines = season_lines(tl)
    d = attach_prior(cp, ours)
    tr = d[d["season"].isin(TRAIN)].reset_index(drop=True)
    tr["cluster"] = pd.factorize(tr["cluster"])[0]
    stats = ["mpg", *RATES, "fg_pct", "ft_pct"]
    fits = fit_all(tr, stats, a.n_boot)
    n0 = {s: fits[s]["n0"] for s in stats}

    def sub(df: pd.DataFrame, yrs: list[int]) -> pd.DataFrame:
        x = df[df["season"].isin(yrs)].reset_index(drop=True)
        x["cluster"] = pd.factorize(x["cluster"])[0]
        return x

    # extensions: n0 by prior reliability (prior weighted minutes) and by age, fit on training
    wm_edges = [float(np.round(q, -2)) for q in tr.drop_duplicates("cluster")["weighted_minutes"].quantile([1 / 3,
                                                                                                         2 / 3])]
    age_edges = [24.0, 30.0]
    ext_rep = {}
    rep_eval = {}
    for name, yrs in [("valid_2023_2024", VALID), ("test_2025_2026", test)]:
        if not yrs:
            continue
        de = sub(d, yrs)
        extra = {}
        kk = de["k"].astype(str).to_numpy()
        extra["n0_per_k"] = {s: np.array([fits[s]["per_k"][x] for x in kk]) for s in stats}
        for col, edges in [("weighted_minutes", wm_edges), ("age", age_edges)]:
            r, arr = group_n0(tr, de, col, edges, n0)
            ext_rep[col] = r
            extra[f"n0_by_{col}"] = arr
        rep_eval[name] = evaluate(de, n0, lines, a.n_boot, extra)
        rep_eval[name]["seasons"] = yrs

    # robustness: production default prior (0.5 ours + 0.5 ESPN)
    bl = blend_priors(ours, [*VALID, *test, 2019, 2022])
    db = attach_prior(cp, bl.drop(columns=["espn_matched", "ours_mpg"]))
    rob = {"espn_match_rate": {int(T): round(float(g["espn_matched"].mean()), 3)
                               for T, g in bl.groupby("season")},
           "espn_match_rate_ours_mpg_ge_20": {
               int(T): round(float(g.loc[g["ours_mpg"] >= 20, "espn_matched"].mean()), 3)
               for T, g in bl.groupby("season")}}
    dtr_b = sub(db, [2019, 2022])
    refit = fit_all(dtr_b, stats, 0)
    rob["n0_refit_on_2019_2022_blend"] = {s: refit[s]["n0"] for s in stats}
    for name, yrs in [("valid_2023_2024", VALID), ("test_2025_2026", test)]:
        if yrs:
            rob[name] = evaluate(sub(db, yrs), n0, lines, a.n_boot,
                                 {"n0_refit_blend": {s: refit[s]["n0"] for s in stats}})

    pr, ob, n, _, _ = stat_arrays(tr, "reb")
    sanity = {"k0_equals_prior_maxabs": float(np.max(np.abs(shrink(pr, ob, 0 * n, n0["reb"]) - pr))),
              "n_1e9_equals_obs_maxabs": float(np.max(np.abs(shrink(pr, ob, 0 * n + 1e9, n0["reb"]) - ob)))}
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                         cwd=ROOT).stdout.strip()
    rep = {
        "model": "inseason-stats-shrink-0.1", "git_sha": sha,
        "method": "see module docstring of scripts/inseason_model/fit_stats.py",
        "train_seasons": TRAIN, "valid_seasons": VALID, "test_seasons": test,
        "checkpoints": CHECKPOINTS, "min_rest_games": MIN_REST, "n_boot": a.n_boot,
        "note_pts": "pts n0 fitted for reference; the line derives pts = 2*FG%*fga + fg3m + FT%*fta",
        "sanity": sanity,
        "n0": fits,
        "eval_ours_prior": rep_eval,
        "extensions_n0_by_group": {"edges": {"weighted_minutes": wm_edges, "age": age_edges}, **ext_rep},
        "robustness_blend_prior": rob,
    }
    a.out.write_text(json.dumps(rep, indent=1))
    print(json.dumps({"n0": {s: (f["n0"], f["se"], f["units"]) for s, f in fits.items()},
                      "test": {k: v["pooled"]["z9"] for k, v in rep_eval.items()}}, indent=1))


if __name__ == "__main__":
    main()
