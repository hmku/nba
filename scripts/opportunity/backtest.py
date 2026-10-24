"""Backtest of the opportunity detector (opportunity-0.1.0) on 2025-26, constants fitted on 2024-25 only.

  python scripts/opportunity/backtest.py [--out scripts/opportunity/report_backtest.json]

For every team game g of 2025-26 (decision made before game g, using box scores of games < g only, plus the
pregame-known inactive list of game g as the proxy for the injury report's "Out"), the production `detect()` is run on
the team with:
  prior / preseason mpg = ESPN's 2025-26 preseason projection (data/raw/espn/projections_2025_26.csv);
  mpg_next5 / mpg = minutes models A / B (fantasy.model.minutes) on his games played so far;
  absences = teammates with max(preseason, last-10-played) mpg >= 28 who are out at g (non-coach DNP reason or not
  listed), `missed` = consecutive games already missed; window from the FITTED (2024-25) survival.
Universe ("FA-like"): ESPN-projected players outside the top 156 (12 x 13) by preseason value, not out at g.
Outcomes: mean minutes in his next 5 / 10 games PLAYED (same team), and the per-game 9-cat G-score of his actual
next-10 line minus his preseason line's (fixed preseason-pool scaling, fantasy.model.valuation._cat_scores).
Flags are deduplicated to the first day of each flagged run per player ("first flags"). SEs: player-cluster bootstrap.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
from fit import fit  # noqa: E402
from panel import STAR_MPG, load_season  # noqa: E402

from fantasy.inseason.opportunity import Absence, OpportunityParams, detect  # noqa: E402
from fantasy.league.settings import load_settings  # noqa: E402
from fantasy.model.minutes import default_params, feature_panel, predict  # noqa: E402
from fantasy.model.valuation import ValuationParams, _cat_scores  # noqa: E402

STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
N_POOL = 156


def params_from_fit(f: dict) -> OpportunityParams:
    w = {}
    for k, v in f["weights"].items():
        b, m = k.split(":")
        w[(int(b), m)] = max(0.0, float(v["mean"]))
    return OpportunityParams(share=float(f["share_excess"]["mean"]), survival=tuple(f["survival"]), weights=w)


def gscores(lines: pd.DataFrame, pool_lines: pd.DataFrame) -> pd.Series:
    """Per-game 9-cat G-score total of `lines` with the scaling of `pool_lines` (the preseason top 156)."""
    st = load_settings()
    pool = pool_lines.copy()
    pool.index = [f"__pool_{i}" for i in range(len(pool))]
    eff = pd.concat([pool[STATS], lines[STATS]])
    s = _cat_scores(eff.astype(float), pd.Index(pool.index), st, ValuationParams().kappa).sum(axis=1)
    return s.loc[lines.index]


def preseason(season_label: str = "2025_26") -> tuple[pd.DataFrame, pd.DataFrame]:
    e = pd.read_csv(ROOT / "data" / "raw" / "espn" / f"projections_{season_label}.csv").drop_duplicates("player_id")
    e = e.set_index("player_id")
    e[STATS] = e[STATS].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    pool = e.sort_values("pts", ascending=False).head(N_POOL)
    for _ in range(3):
        g = gscores(e, pool)
        tot = g * e["gp"].clip(0, 82) / 82
        pool = e.loc[tot.sort_values(ascending=False).index[:N_POOL]]
    e["pre_value"] = gscores(e, pool)
    e["is_pool"] = e.index.isin(pool.index)
    return e, pool


def run(panel: pd.DataFrame, train: list[int], test: int, calib: float = 1.0, seed: int = 0, boot: int = 400) -> dict:
    """Detector constants fitted on `train` seasons, evaluated on `test` (2025 = 2024-25, 2026 = 2025-26)."""
    rng = np.random.default_rng(seed)
    f_train = fit(train, panel)
    prm = replace(params_from_fit(f_train), vacated_calib=calib)
    pre, pool_lines = preseason(f"{test - 1}_{str(test)[2:]}")
    pos = pd.read_csv(ROOT / "data" / "processed" / "projections_2026_27.csv").drop_duplicates("player_id")
    posmap = pos.set_index("player_id")["positions"].to_dict()
    P = panel[panel["season"] == test]
    pa, pb = default_params("next5"), default_params("ros")
    recs = []
    for team, T in P.groupby("team"):
        M = T.pivot(index="player_id", columns="gi", values="min")  # NaN = not on the team
        INJ = T.pivot(index="player_id", columns="gi", values="inj").reindex_like(M).fillna(False).astype(bool)
        PL = M.fillna(0) > 0
        stats = {s: T.pivot(index="player_id", columns="gi", values=s).reindex_like(M) for s in STATS}
        n = int(M.columns.max()) + 1
        pids = list(M.index)
        # minutes-model predictions after t games played (per player, this team stint)
        predA, predB, tcount = {}, {}, {}
        for pid in pids:
            m = M.loc[pid].to_numpy()
            played_min = m[(~np.isnan(m)) & (m > 0)]
            prior = float(pre["mpg"].get(pid, np.nan))
            if not np.isfinite(prior):
                prior = float(played_min[:5].mean()) if len(played_min) else 8.0  # PRIOR for unprojected players
            fe = feature_panel(played_min, prior, pa)
            predA[pid], predB[pid] = predict(fe, pa), predict(feature_panel(played_min, prior, pb), pb)
            tcount[pid] = np.concatenate([[0], np.cumsum((~np.isnan(m)) & (m > 0))])  # games played before gi
        for g in range(3, n - 1):
            on = [p for p in pids if not np.isnan(M.at[p, g])]
            if len(on) < 8:
                continue
            out_now = {p for p in on if INJ.at[p, g] or (M.at[p, g] == 0 and not np.isnan(M.at[p, g - 1])
                                                         and M.at[p, g - 1] == 0)}
            # absent from the box score inside the stint (NaN filled as 0 by panel) also counts as out
            rows, absences = [], []
            for p in on:
                t = int(tcount[p][g])
                n5, nb = float(predA[p][t]), float(predB[p][t])
                pre_mpg = float(pre["mpg"].get(p, np.nan))
                rows.append((p, team, posmap.get(p, ""), nb, n5, pre_mpg if np.isfinite(pre_mpg) else n5, p in out_now))
                if M.at[p, g] == 0:  # a >= 28-mpg player sitting game g: known pregame (inactive list / report)
                    hist = M.loc[p, :g - 1].to_numpy()
                    played = hist[(~np.isnan(hist)) & (hist > 0)][-10:]
                    lvl = max(pre_mpg if np.isfinite(pre_mpg) else 0.0, float(played.mean()) if len(played) >= 5 else 0.0)
                    if lvl >= STAR_MPG:
                        k = 0
                        while g - 1 - k >= 0 and not np.isnan(M.at[p, g - 1 - k]) and M.at[p, g - 1 - k] == 0:
                            k += 1
                        absences.append(Absence(p, p, team, lvl, missed=k))
            df = pd.DataFrame(rows, columns=["player_id", "team", "positions", "mpg", "mpg_next5", "mpg_pre", "out"])
            df = df.set_index("player_id")
            df.loc[[a.player_id for a in absences], "out"] = True
            uni =[p for p in on if p in pre.index and not pre.at[p, "is_pool"] and p not in out_now]
            if not uni:
                continue
            flags = {p: list(PL.loc[p, :g - 1].to_numpy()) for p in uni}
            cands = {c["player_id"]: c for c in detect(df, absences, prm, flags, uni)}
            for p in uni:
                fut = M.loc[p, g:].to_numpy()
                idx = np.flatnonzero((~np.isnan(fut)) & (fut > 0))
                stint_left = int(np.sum(~np.isnan(fut)))
                if stint_left < 5:
                    continue
                r = {"team": team, "g": g, "player_id": p, "pre_mpg": float(pre.at[p, "mpg"]),
                     "mpg_next5": float(df.at[p, "mpg_next5"]), "flag": p in cands,
                     "signal": cands[p]["signal"] if p in cands else "",
                     "opp_mpg": cands[p]["opp_mpg"] if p in cands else np.nan,
                     "window": cands[p]["window_games"] if p in cands else np.nan,
                     "n_abs": len(absences), "during": np.nan, "pred_extra": np.nan}
                if p in cands and cands[p]["signal"].startswith("vacated"):
                    fresh = [d for d in cands[p]["drivers"] if not d["observed"]]
                    if fresh:
                        star = max(fresh, key=lambda d: d["extra_mpg"])["player_id"]
                        mins = []
                        for j in range(g, n):
                            sm = M.at[star, j] if j in M.columns else np.nan
                            if np.isnan(sm) or sm > 0 or len(mins) >= 5:
                                break
                            if fut[j - g] > 0:
                                mins.append(float(fut[j - g]))
                        r["during"] = float(np.mean(mins)) if mins else np.nan
                        r["pred_extra"] = cands[p]["opp_mpg"] - cands[p]["mpg_next5"]
                for h in (5, 10):
                    sel = idx[:h]
                    r[f"min{h}"] = float(fut[sel].mean()) if len(sel) >= min(3, h) else np.nan
                    tg = fut[:h]
                    r[f"min{h}_tg"] = float(np.nan_to_num(tg).mean())  # incl. DNPs (per team game)
                sel = idx[:10]
                if len(sel) >= 3:
                    cols = [M.columns[g + j] for j in sel]
                    for s in STATS:
                        r[f"a_{s}"] = float(stats[s].loc[p, cols].sum()) / len(sel)
                recs.append(r)
    X = pd.DataFrame(recs)
    X = X.sort_values(["player_id", "team", "g"]).reset_index(drop=True)
    prev = X.groupby(["player_id", "team"])["flag"].shift(1).fillna(False).astype(bool)
    prevg = X.groupby(["player_id", "team"])["g"].shift(1)
    X["first_flag"] = X["flag"] & ~(prev & (X["g"] - prevg == 1))
    # values: actual next-10 line vs preseason line, same scaling
    have = X.dropna(subset=["a_pts"]).copy()
    lines = have[[f"a_{s}" for s in STATS]].rename(columns=lambda c: c[2:])
    lines.index = [f"r{i}" for i in have.index]
    X.loc[have.index, "act_value"] = gscores(lines, pool_lines).to_numpy()
    X["pre_value"] = X["player_id"].map(pre["pre_value"])
    X["d_value"] = X["act_value"] - X["pre_value"]
    # predicted value of the opportunity line (ESPN per-minute rates x opportunity minutes)
    fl = X[X["flag"]].copy()
    sc = (fl["opp_mpg"] / fl["pre_mpg"]).to_numpy()[:, None]
    pl = pre.loc[fl["player_id"], STATS].to_numpy() * sc
    pl = pd.DataFrame(pl, columns=STATS, index=[f"f{i}" for i in fl.index])
    X.loc[fl.index, "pred_d_value"] = gscores(pl, pool_lines).to_numpy() - X.loc[fl.index, "pre_value"].to_numpy()
    X["d5"] = X["min5"] - X["pre_mpg"]
    X["d10"] = X["min10"] - X["pre_mpg"]
    X["big10"] = (X["d10"] >= 5).astype(float).where(X["d10"].notna())

    players = X["player_id"].unique()
    groups = {p: d.index.to_numpy() for p, d in X.groupby("player_id")}

    def boot_stat(fn) -> tuple[float, float]:
        v0 = fn(X)
        vs = []
        for _ in range(boot):
            ix = np.concatenate([groups[p] for p in rng.choice(players, len(players))])
            vs.append(fn(X.loc[ix]))
        return round(float(v0), 3), round(float(np.nanstd(vs)), 3)

    def compare(mask_fn, col):
        def lift(d):
            a, b = d.loc[mask_fn(d), col].mean(), d.loc[~d["flag"], col].mean()
            return a - b
        return {"flagged": boot_stat(lambda d: d.loc[mask_fn(d), col].mean()),
                "unflagged": boot_stat(lambda d: d.loc[~d["flag"], col].mean()),
                "lift": boot_stat(lift), "n_flagged": int(mask_fn(X).sum()), "n_unflagged": int((~X["flag"]).sum())}

    ff = lambda d: d["first_flag"]  # noqa: E731
    X["dA5"] = X["min5"] - X["mpg_next5"]
    V = X[X["first_flag"] & X["during"].notna() & X["pred_extra"].notna()]
    vg = {pl: d.index.to_numpy() for pl, d in V.groupby("player_id")}

    def beta(d: pd.DataFrame) -> float:
        x, y = d["pred_extra"].to_numpy(), (d["during"] - d["mpg_next5"]).to_numpy()
        return float((x * y).sum() / max((x * x).sum(), 1e-9))

    bs = []
    vp = list(vg)
    for _ in range(boot):
        ix = np.concatenate([vg[q] for q in rng.choice(vp, len(vp))]) if vp else np.array([], int)
        bs.append(beta(V.loc[ix]) if len(ix) else np.nan)
    res = {"constants_fit_on": f"seasons {train}", "test": f"season {test}", "vacated_calib_applied": calib,
           "params": prm.to_dict(), "n_rows": int(len(X)), "n_players": int(len(players)),
           "n_first_flags": int(X["first_flag"].sum()),
           "first_flags_by_signal": X.loc[X["first_flag"], "signal"].value_counts().to_dict()}
    for col in ("d5", "d10", "big10", "d_value", "dA5"):
        res[col] = compare(ff, col)
    res["vacated_during"] = {
        "n": int(len(V)), "mean_during": round(float(V["during"].mean()), 2) if len(V) else None,
        "mean_model_a": round(float(V["mpg_next5"].mean()), 2) if len(V) else None,
        "mean_pred_extra": round(float(V["pred_extra"].mean()), 2) if len(V) else None,
        "mean_realised_extra": round(float((V["during"] - V["mpg_next5"]).mean()), 2) if len(V) else None,
        "beta": round(beta(V), 3) if len(V) else None, "beta_se": round(float(np.nanstd(bs)), 3) if len(V) else None,
        "note": "realised extra = mean minutes in his games played while the driving teammate stays out (<= 5) minus "
                "model A; beta = slope through the origin of realised on allocated extra (cluster bootstrap SE)"}
    for sig in ("jump", "vacated"):
        m = lambda d, s=sig: d["first_flag"] & d["signal"].str.startswith(s)  # noqa: E731
        res[f"by_signal_{sig}"] = {col: compare(m, col) for col in ("d5", "d10", "d_value", "dA5")}
    # calibration: opportunity mpg vs model A as predictors of next-5 minutes (first flags)
    F = X[X["first_flag"] & X["min5"].notna()]
    for sig in ("vacated", "jump"):
        Fs = F[F["signal"].str.startswith(sig)]
        if len(Fs):
            res[f"calibration_{sig}"] = {
                "n": int(len(Fs)), "mean_actual_min5": round(float(Fs["min5"].mean()), 2),
                "mean_opp_mpg": round(float(Fs["opp_mpg"].mean()), 2),
                "mean_model_a": round(float(Fs["mpg_next5"].mean()), 2),
                "mean_preseason": round(float(Fs["pre_mpg"].mean()), 2),
                "rmse_opp": round(float(np.sqrt(((Fs["min5"] - Fs["opp_mpg"]) ** 2).mean())), 2),
                "rmse_model_a": round(float(np.sqrt(((Fs["min5"] - Fs["mpg_next5"]) ** 2).mean())), 2),
                "mean_pred_d_value": round(float(Fs["pred_d_value"].mean()), 3),
                "mean_actual_d_value": round(float(Fs["d_value"].mean()), 3)}
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "report_backtest.json"))
    ap.add_argument("--boot", type=int, default=400)
    a = ap.parse_args()
    panel = pd.concat([load_season(2025), load_season(2026)], ignore_index=True)
    # 1) calibration of the allocation for the FA-like universe, fitted on 2024-25 (in-sample for the share weights)
    r25 = run(panel, [2025], 2025, 1.0, boot=a.boot)
    b = float(r25["vacated_during"]["beta"])
    # 2) out of sample on 2025-26 (constants and calibration from 2024-25): uncalibrated and calibrated
    rep = {"train_2025_insample": r25, "test_2026_uncalibrated": run(panel, [2025], 2026, 1.0, boot=a.boot),
           "test_2026_calibrated": run(panel, [2025], 2026, b, boot=a.boot)}
    Path(a.out).write_text(json.dumps(rep, indent=1, default=str))
    for k, v in rep.items():
        print(k, json.dumps({kk: v[kk] for kk in ("n_first_flags", "first_flags_by_signal", "vacated_during", "d5",
                                                   "d_value", "dA5")}, default=str))


if __name__ == "__main__":
    main()
