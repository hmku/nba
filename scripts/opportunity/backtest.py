"""Backtest of the opportunity detector (opportunity-0.1.x) on 2025-26, constants fitted on 2024-25 only.

  python scripts/opportunity/backtest.py [--out scripts/opportunity/report_backtest.json] [--boot 300]

For every team game g (decision made BEFORE game g) the production `detect()` runs on the team with information
available then:
  prior / preseason mpg = ESPN's preseason projection of that season (data/raw/espn/projections_<season>.csv);
  mpg_next5 / mpg = minutes models A / B on his games played before g (unprojected players: prior 8 mpg, a PRIOR);
  positions = bbref PRIOR-season primary position (panel.positions_map; no later-season data);
  who is out at g = a non-coach DNP reason on game g's inactive list (published before tip-off; the proxy for the
  injury report) or he missed game g-1 (still out); the minutes of game g are never used;
  absences = such teammates with max(preseason, last-10-played) mpg >= 28; `missed` = consecutive games missed before g.
Universe ("FA-like"): ESPN-projected players outside the top 156 (12 x 13) by preseason value, not out at g.
Outcomes: mean minutes in his next 5 / 10 games PLAYED (same team); per-game 9-cat G-score of the next-10 line minus the
preseason line's (fixed preseason-pool scaling); INCREMENTAL minutes = next-5 minus model A (dA5).
Value calibration (`value_calib`): for first flags, predicted window value gain over the model-B line
= n_played x [v(rates x opp) - v(rates x mpg_B)] over the first ceil(window) team games, realised = sum over the same
played games of [v(actual game) - v(rates x mpg_B)] minus the unflagged pool's mean per-game residual (bias of the
preseason rates); ratio of sums (G-scores are linear in the line), player-cluster bootstrap SE.
Flags are deduplicated to the first day of each flagged run per player ("first flags").
"""

from __future__ import annotations

import argparse
import json
import math
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
from panel import STAR_MPG, load_season, positions_map  # noqa: E402

from fantasy.inseason.opportunity import Absence, OpportunityParams, detect  # noqa: E402
from fantasy.league.settings import load_settings  # noqa: E402
from fantasy.model.minutes import default_params, feature_panel, predict  # noqa: E402
from fantasy.model.valuation import ValuationParams, _cat_scores  # noqa: E402

STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
N_POOL = 156
UNPROJECTED_PRIOR_MPG = 8.0  # PRIOR


def params_from_fit(f: dict) -> OpportunityParams:
    w = {}
    for k, v in f["weights"].items():
        b, m = k.split(":")
        if m != "unknown":
            w[(int(b), m)] = max(0.0, float(v["mean"]))
    for (b, m), x in list(w.items()):  # same rule as production: unknown position -> the "other" weight
        if m == "other":
            w[(b, "unknown")] = x
    return OpportunityParams(share=float(f["share_excess"]["mean"]), survival=tuple(f["survival"]), weights=w)


def gscores(lines: pd.DataFrame, pool_lines: pd.DataFrame) -> pd.Series:
    """Per-game 9-cat G-score total of `lines` with the scaling of `pool_lines` (the preseason top 156)."""
    st = load_settings()
    pool = pool_lines.copy()
    pool.index = [f"__pool_{i}" for i in range(len(pool))]
    eff = pd.concat([pool[STATS], lines[STATS]])
    s = _cat_scores(eff.astype(float), pd.Index(pool.index), st, ValuationParams().kappa).sum(axis=1)
    return s.loc[lines.index]


def preseason(season_label: str) -> tuple[pd.DataFrame, pd.DataFrame]:
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


def _rate_line(pre: pd.DataFrame, pid: str, mpg: float) -> np.ndarray:
    m = float(pre.at[pid, "mpg"])
    return pre.loc[pid, STATS].to_numpy(float) * (mpg / m if m > 0 else 0.0)


def run(panel: pd.DataFrame, train: list[int], test: int, prm: OpportunityParams | None = None,
        calib: float | None = None, seed: int = 0, boot: int = 300) -> dict:
    """Detector on `test` (2025 = 2024-25, 2026 = 2025-26). Constants fitted on `train` (or `prm` as given)."""
    rng = np.random.default_rng(seed)
    if prm is None:
        prm = params_from_fit(fit(train, panel))
    if calib is not None:
        prm = replace(prm, vacated_calib=calib)
    pre, pool_lines = preseason(f"{test - 1}_{str(test)[2:]}")
    posmap = positions_map(test)
    P = panel[panel["season"] == test]
    pa, pb = default_params("next5"), default_params("ros")
    recs, games = [], []  # games: (rec index, kind, line) for the value calibration
    for team, T in P.groupby("team"):
        M = T.pivot(index="player_id", columns="gi", values="min")  # NaN = not on the team
        INJ = T.pivot(index="player_id", columns="gi", values="inj").reindex_like(M).fillna(False).astype(bool)
        PL = M.fillna(0) > 0
        stats = {s: T.pivot(index="player_id", columns="gi", values=s).reindex_like(M) for s in STATS}
        n = int(M.columns.max()) + 1
        pids = list(M.index)
        predA, predB, tcount = {}, {}, {}
        for pid in pids:
            m = M.loc[pid].to_numpy()
            played_min = m[(~np.isnan(m)) & (m > 0)]
            prior = float(pre["mpg"].get(pid, np.nan))
            if not np.isfinite(prior):
                prior = UNPROJECTED_PRIOR_MPG  # no future data
            predA[pid] = predict(feature_panel(played_min, prior, pa), pa)
            predB[pid] = predict(feature_panel(played_min, prior, pb), pb)
            tcount[pid] = np.concatenate([[0], np.cumsum((~np.isnan(m)) & (m > 0))])  # games played before gi
        for g in range(3, n - 1):
            on = [p for p in pids if not np.isnan(M.at[p, g])]
            if len(on) < 8:
                continue

            def missed_before(p: str, g: int = g, M: pd.DataFrame = M) -> int:
                k = 0
                while g - 1 - k >= 0 and not np.isnan(M.at[p, g - 1 - k]) and M.at[p, g - 1 - k] == 0:
                    k += 1
                return k

            # pregame information only: game g's inactive list (non-coach reason) or still out from game g-1
            out_now = {p for p in on if INJ.at[p, g] or (not np.isnan(M.at[p, g - 1]) and M.at[p, g - 1] == 0
                                                         and INJ.at[p, g - 1])}
            rows, absences = [], []
            for p in on:
                t = int(tcount[p][g])
                n5, nb = float(predA[p][t]), float(predB[p][t])
                pre_mpg = float(pre["mpg"].get(p, np.nan))
                rows.append((p, team, posmap.get(p, ""), nb, n5, pre_mpg if np.isfinite(pre_mpg) else n5, p in out_now))
                if p in out_now:
                    hist = M.loc[p, :g - 1].to_numpy()
                    played = hist[(~np.isnan(hist)) & (hist > 0)][-10:]
                    lvl = max(pre_mpg if np.isfinite(pre_mpg) else 0.0, float(played.mean()) if len(played) >= 5 else 0.0)
                    if lvl >= STAR_MPG:
                        absences.append(Absence(p, p, team, lvl, missed=missed_before(p)))
            df = pd.DataFrame(rows, columns=["player_id", "team", "positions", "mpg", "mpg_next5", "mpg_pre", "out"])
            df = df.set_index("player_id")
            uni = [p for p in on if p in pre.index and not pre.at[p, "is_pool"] and p not in out_now]
            if not uni:
                continue
            flags = {p: list(PL.loc[p, :g - 1].to_numpy()) for p in uni}
            cands = {c["player_id"]: c for c in detect(df, absences, prm, flags, uni)}
            for p in uni:
                fut = M.loc[p, g:].to_numpy()
                idx = np.flatnonzero((~np.isnan(fut)) & (fut > 0))
                if int(np.sum(~np.isnan(fut))) < 5:
                    continue
                c = cands.get(p)
                r = {"team": team, "g": g, "player_id": p, "pre_mpg": float(pre.at[p, "mpg"]),
                     "mpg_next5": float(df.at[p, "mpg_next5"]), "mpg_b": float(df.at[p, "mpg"]), "flag": c is not None,
                     "signal": c["signal"] if c else "", "opp_mpg": c["opp_mpg"] if c else np.nan,
                     "window": c["window_games"] if c else np.nan, "n_abs": len(absences),
                     "during": np.nan, "pred_extra": np.nan}
                if c and c["signal"].startswith("vacated"):
                    fresh = [d for d in c["drivers"] if not d["observed"]]
                    if fresh:
                        star = max(fresh, key=lambda d: d["extra_mpg"])["player_id"]
                        mins = []
                        for j in range(g, n):
                            sm = M.at[star, j]
                            if np.isnan(sm) or sm > 0 or len(mins) >= 5:
                                break
                            if fut[j - g] > 0:
                                mins.append(float(fut[j - g]))
                        r["during"] = float(np.mean(mins)) if mins else np.nan
                        r["pred_extra"] = c["opp_mpg"] - c["mpg_next5"]
                for h in (5, 10):
                    sel = idx[:h]
                    r[f"min{h}"] = float(fut[sel].mean()) if len(sel) >= min(3, h) else np.nan
                sel = idx[:10]
                if len(sel) >= 3:
                    cols = [M.columns[g + j] for j in sel]
                    for s in STATS:
                        r[f"a_{s}"] = float(stats[s].loc[p, cols].sum()) / len(sel)
                # per-game lines for the value calibration: flags over the window; a sample of unflagged rows (bias)
                k = len(recs)
                if c is not None or rng.random() < 0.15:
                    nw = max(1, math.ceil(float(c["window_games"]))) if c else 3
                    wsel = [j for j in idx if j < nw] if c else list(idx[:3])
                    r["n_played_window"] = len(wsel)
                    for j in wsel:
                        gl = np.array([float(stats[s].at[p, M.columns[g + j]]) for s in STATS])
                        games.append((k, "actual", gl))
                    games.append((k, "base", _rate_line(pre, p, r["mpg_b"])))
                    if c:
                        games.append((k, "opp", _rate_line(pre, p, float(c["opp_mpg"]))))
                recs.append(r)
    X = pd.DataFrame(recs)
    # values of the game lines (one G-score call; linear in the line, so sums of game values are window totals)
    G = pd.DataFrame([gl for _, _, gl in games], columns=STATS, index=[f"x{i}" for i in range(len(games))])
    gv = gscores(G, pool_lines).to_numpy()
    meta = pd.DataFrame({"rec": [k for k, _, _ in games], "kind": [kd for _, kd, _ in games], "v": gv})
    act = meta[meta.kind == "actual"].groupby("rec")["v"].sum()
    base = meta[meta.kind == "base"].set_index("rec")["v"]
    opp = meta[meta.kind == "opp"].set_index("rec")["v"]
    X["v_act_sum"] = X.index.map(act)
    X["v_base"] = X.index.map(base)
    X["v_opp"] = X.index.map(opp)
    # first flags (dedupe): sort but keep the original index for the value maps above
    X = X.sort_values(["player_id", "team", "g"])
    prev = X.groupby(["player_id", "team"])["flag"].shift(1).fillna(False).astype(bool)
    prevg = X.groupby(["player_id", "team"])["g"].shift(1)
    X["first_flag"] = X["flag"] & ~(prev & (X["g"] - prevg == 1))
    have = X.dropna(subset=["a_pts"])
    lines = have[[f"a_{s}" for s in STATS]].rename(columns=lambda c: c[2:])
    lines.index = [f"r{i}" for i in have.index]
    X.loc[have.index, "act_value"] = gscores(lines, pool_lines).to_numpy()
    X["pre_value"] = X["player_id"].map(pre["pre_value"])
    X["d_value"] = X["act_value"] - X["pre_value"]
    X["d5"] = X["min5"] - X["pre_mpg"]
    X["d10"] = X["min10"] - X["pre_mpg"]
    X["dA5"] = X["min5"] - X["mpg_next5"]
    X["big10"] = (X["d10"] >= 5).astype(float).where(X["d10"].notna())
    X = X.reset_index(drop=True)

    players = X["player_id"].unique()
    groups = {p: d.index.to_numpy() for p, d in X.groupby("player_id")}

    def boot_stat(fn) -> tuple[float, float]:
        v0 = fn(X)
        vs = []
        for _ in range(boot):
            ix = np.concatenate([groups[q] for q in rng.choice(players, len(players))])
            vs.append(fn(X.loc[ix]))
        return round(float(v0), 3), round(float(np.nanstd(vs)), 3)

    def compare(mask_fn, col):
        def lift(d):
            return d.loc[mask_fn(d), col].mean() - d.loc[~d["flag"], col].mean()
        return {"flagged": boot_stat(lambda d: d.loc[mask_fn(d), col].mean()),
                "unflagged": boot_stat(lambda d: d.loc[~d["flag"], col].mean()),
                "lift": boot_stat(lift), "n_flagged": int(mask_fn(X).sum()), "n_unflagged": int((~X["flag"]).sum())}

    def beta(d: pd.DataFrame) -> float:
        V = d[d["first_flag"] & d["during"].notna() & d["pred_extra"].notna()]
        x, y = V["pred_extra"].to_numpy(), (V["during"] - V["mpg_next5"]).to_numpy()
        return float((x * y).sum() / max((x * x).sum(), 1e-9))

    def value_ratio(d: pd.DataFrame, sig: str | None = None) -> float:
        u = d[~d["flag"] & d["v_act_sum"].notna() & (d["n_played_window"] > 0)]
        bias = float((u["v_act_sum"] - u["n_played_window"] * u["v_base"]).sum() / max(u["n_played_window"].sum(), 1))
        F = d[d["first_flag"] & d["v_opp"].notna() & (d["n_played_window"] > 0)]
        if sig:
            F = F[F["signal"].str.startswith(sig)]
        n_ = F["n_played_window"]
        real = (F["v_act_sum"] - n_ * F["v_base"] - n_ * bias).sum()
        pred = (n_ * (F["v_opp"] - F["v_base"])).sum()
        return float(real / pred) if abs(pred) > 1e-9 else np.nan

    ff = lambda d: d["first_flag"]  # noqa: E731
    res = {"constants": f"fitted on seasons {train}" if train else "production", "test": f"season {test}",
           "vacated_calib": prm.vacated_calib, "params": prm.to_dict(), "n_rows": int(len(X)),
           "n_players": int(len(players)), "n_first_flags": int(X["first_flag"].sum()),
           "first_flags_by_signal": X.loc[X["first_flag"], "signal"].value_counts().to_dict()}
    for col in ("d5", "d10", "big10", "d_value", "dA5"):
        res[col] = compare(ff, col)
    for sig in ("jump", "vacated"):
        m = lambda d, s=sig: d["first_flag"] & d["signal"].str.startswith(s)  # noqa: E731
        res[f"by_signal_{sig}"] = {col: compare(m, col) for col in ("d5", "d10", "d_value", "dA5")}
    V = X[X["first_flag"] & X["during"].notna() & X["pred_extra"].notna()]
    res["vacated_during"] = {
        "n": int(len(V)), "mean_pred_extra": round(float(V["pred_extra"].mean()), 2) if len(V) else None,
        "mean_realised_extra": round(float((V["during"] - V["mpg_next5"]).mean()), 2) if len(V) else None,
        "beta": boot_stat(beta),
        "note": "realised extra = mean minutes in his games played while the driving teammate stays out (<= 5) minus "
                "model A; beta = slope through the origin of realised on allocated extra"}
    res["value_calib"] = {"all": boot_stat(value_ratio),
                          "jump": boot_stat(lambda d: value_ratio(d, "jump")),
                          "vacated": boot_stat(lambda d: value_ratio(d, "vacated")),
                          "note": "realised / predicted window value gain over the model-B line (bias-corrected)"}
    F = X[X["first_flag"] & X["min5"].notna()]
    for sig in ("vacated", "jump"):
        Fs = F[F["signal"].str.startswith(sig)]
        if len(Fs):
            res[f"calibration_{sig}"] = {
                "n": int(len(Fs)), "mean_actual_min5": round(float(Fs["min5"].mean()), 2),
                "mean_opp_mpg": round(float(Fs["opp_mpg"].mean()), 2),
                "mean_model_a": round(float(Fs["mpg_next5"].mean()), 2),
                "rmse_opp": round(float(np.sqrt(((Fs["min5"] - Fs["opp_mpg"]) ** 2).mean())), 2),
                "rmse_model_a": round(float(np.sqrt(((Fs["min5"] - Fs["mpg_next5"]) ** 2).mean())), 2)}
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "report_backtest.json"))
    ap.add_argument("--boot", type=int, default=300)
    a = ap.parse_args()
    panel = pd.concat([load_season(2025), load_season(2026)], ignore_index=True)
    # 1) calibrations for the FA-like universe, fitted on 2024-25 (in-sample for the share weights)
    r25 = run(panel, [2025], 2025, calib=1.0, boot=a.boot)
    b = float(r25["vacated_during"]["beta"][0])
    r25c = run(panel, [2025], 2025, calib=b, boot=a.boot)  # value ratio with the minutes calibration applied
    # 2) out of sample on 2025-26 with both 2024-25 calibrations; 3) production constants (in-sample weights)
    rep = {"train_2025_minutes_calib": r25, "train_2025_calibrated": r25c,
           "test_2026_calibrated": run(panel, [2025], 2026, calib=b, boot=a.boot),
           "test_2026_production_constants": run(panel, [], 2026, prm=OpportunityParams(), boot=a.boot)}
    Path(a.out).write_text(json.dumps(rep, indent=1, default=str))
    for k, v in rep.items():
        print(k, json.dumps({kk: v[kk] for kk in ("vacated_calib", "n_first_flags", "vacated_during", "value_calib",
                                                   "d5", "dA5")}, default=str))


if __name__ == "__main__":
    main()
