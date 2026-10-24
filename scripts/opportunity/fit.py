"""Fit the opportunity detector's constants (opportunity-0.1.0) from ESPN box scores.

  python scripts/opportunity/fit.py [--seasons 2025,2026] [--out scripts/opportunity/report_fit.json]

* absence survival S(k) = P(L >= k | L >= 1) of >= 28-mpg players (panel.absence_spells);
* vacated share = sum of teammates' minute gains during absences of >= 3 games / star mpg (cluster SE by event);
* share weights by depth bucket x position match = mean (gain - placebo gain of the same bucket) / star mpg, where
  placebo windows have no >= 28-mpg absence (removes regression to the mean of the depth ranking).
Season 2025 = 2024-25, 2026 = 2025-26. Output -> docs/research/opportunity_detector.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from panel import absence_spells, groups_map, load_season, survival  # noqa: E402

from fantasy.inseason.opportunity import RANK_EDGES, rank_bucket  # noqa: E402

BASE_W, MAX_W, PLACEBO_W = 10, 10, 8


def _mats(p: pd.DataFrame) -> dict:
    out = {}
    for (s, t), g in p.groupby(["season", "team"]):
        M = g.pivot(index="player_id", columns="gi", values="min")
        INJ = g.pivot(index="player_id", columns="gi", values="inj").reindex_like(M)
        out[(s, t)] = (M, INJ)
    return out


def _teammate_rows(M: pd.DataFrame, INJ: pd.DataFrame, g0: int, Lw: int, exclude: str | None) -> pd.DataFrame | None:
    cols_b = list(range(g0 - BASE_W, g0))
    cols_w = list(range(g0, g0 + Lw))
    if cols_b[0] < 0 or cols_w[-1] > M.columns.max():
        return None
    b, w = M.reindex(columns=cols_b), M.reindex(columns=cols_w)
    ok = b.notna().all(axis=1) & w.notna().all(axis=1)
    if exclude is not None:
        ok &= M.index != exclude
    first_inj = INJ.reindex(columns=[g0]).iloc[:, 0].fillna(False).astype(bool)
    ok &= ~first_inj  # listed out at the start: production excludes Out teammates from the depth chart
    if ok.sum() == 0:
        return None
    r = pd.DataFrame({"base": b[ok].mean(axis=1), "win": w[ok].mean(axis=1)})
    r["rank"] = r["base"].rank(ascending=False, method="first").astype(int)
    r["bucket"] = [rank_bucket(int(x), RANK_EDGES) for x in r["rank"]]
    r["gain"] = r["win"] - r["base"]
    return r


def fit(seasons: list[int], panel: pd.DataFrame | None = None, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    p = panel if panel is not None else pd.concat([load_season(s) for s in seasons], ignore_index=True)
    p = p[p["season"].isin(seasons)]
    grp = groups_map()
    spells = absence_spells(p)
    S = survival(spells)
    mats = _mats(p)
    # placebo windows: no >= 28-mpg player missing nearby and no star spell overlapping
    q = p.sort_values(["season", "team", "player_id", "gi"]).copy()
    q["m10"] = q.groupby(["season", "team", "player_id"])["min"].transform(
        lambda s: s.where(s > 0).shift().rolling(10, min_periods=5).mean())
    bad = set(map(tuple, q[(q["min"] == 0) & (q["m10"] >= 28)][["season", "team", "gi"]].to_numpy()))
    plc = []
    for (s, t), (M, INJ) in mats.items():
        n = int(M.columns.max()) + 1
        sp = spells[(spells.season == s) & (spells.team == t)]
        for g0 in range(BASE_W + 2, n - PLACEBO_W, 3):
            if any((s, t, g) in bad for g in range(g0 - 1, g0 + PLACEBO_W)):
                continue
            if ((sp.g0 <= g0 + PLACEBO_W) & (sp.g0 + sp.L >= g0 - BASE_W)).any():
                continue
            r = _teammate_rows(M, INJ, g0, PLACEBO_W, None)
            if r is not None:
                plc.append(r.assign(win_id=f"{s}-{t}-{g0}"))
    P = pd.concat(plc)
    placebo = P.groupby("bucket")["gain"].agg(["mean", "sem", "count"])
    ev = spells[spells["L"] >= 3].reset_index(drop=True)
    recs = []
    for i, e in ev.iterrows():
        M, INJ = mats[(e.season, e.team)]
        r = _teammate_rows(M, INJ, int(e.g0), min(int(e.L), MAX_W), e.star)
        if r is None:
            continue
        sg = set(grp.get(e.star, "") or "")
        match = []
        for pid in r.index:
            tg = set(grp.get(pid, "") or "")
            match.append("unknown" if not (sg and tg) else ("same" if sg & tg else "other"))
        r = r.assign(ev=i, star_mpg=e.star_mpg, match=match, player_id=r.index)
        recs.append(r)
    X = pd.concat(recs, ignore_index=True)
    X["excess"] = X["gain"] - X["bucket"].map(placebo["mean"])
    X["frac"] = X["gain"] / X["star_mpg"]
    X["xfrac"] = X["excess"] / X["star_mpg"]
    E = X.groupby("ev").agg(sum_frac=("frac", "sum"), sum_xfrac=("xfrac", "sum"), n=("frac", "size"))

    def cboot(f, B=400):
        ids = X["ev"].unique()
        g = {k: d for k, d in X.groupby("ev")}
        v = [f(pd.concat([g[k] for k in rng.choice(ids, len(ids))])) for _ in range(B)]
        return float(np.std(v))

    W = {}
    for (b, m), d in X.groupby(["bucket", "match"]):
        W[f"{b}:{m}"] = {"mean": round(float(d["xfrac"].mean()), 4), "sem": round(float(d["xfrac"].sem()), 4),
                         "n": int(len(d)), "raw_frac": round(float(d["frac"].mean()), 4)}
    # weight sum on a typical team (all teammates' weights, floored at 0) vs the share
    wmap = {k: max(0.0, v["mean"]) for k, v in W.items()}
    X["w"] = [wmap.get(f"{b}:{m}", 0.0) for b, m in zip(X["bucket"], X["match"], strict=True)]
    big = X[X["gain"] >= 6]
    return {
        "seasons": seasons,
        "n_spells": int(len(spells)), "n_events_L3": int(len(ev)), "n_teammate_rows": int(len(X)),
        "survival": [round(x, 4) for x in S],
        "survival_given3": {k: round(float((spells.L >= k).sum() / max((spells.L >= 3).sum(), 1)), 3)
                            for k in (5, 10, 20)},
        "share_raw": {"mean": round(float(E["sum_frac"].mean()), 4), "se": round(float(E["sum_frac"].sem()), 4)},
        "share_excess": {"mean": round(float(E["sum_xfrac"].mean()), 4), "se": round(float(E["sum_xfrac"].sem()), 4)},
        "weight_sum_per_event": {"mean": round(float(X.groupby("ev")["w"].sum().mean()), 4)},
        "placebo_gain_by_bucket": {int(k): {"mean": round(float(v["mean"]), 3), "sem": round(float(v["sem"]), 3),
                                            "n": int(v["count"])} for k, v in placebo.iterrows()},
        "weights": W,
        "weights_cluster_se_example_bucket3_same": round(cboot(
            lambda d: d[(d.bucket == 3) & (d.match == "same")]["xfrac"].mean()), 4),
        "big_gainers": {"n": int(len(big)), "base": round(float(big["base"].mean()), 1),
                        "rank_median": float(big["rank"].median())},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2025,2026")
    ap.add_argument("--out", default=str(Path(__file__).with_name("report_fit.json")))
    a = ap.parse_args()
    seasons = [int(x) for x in a.seasons.split(",")]
    panel = pd.concat([load_season(s) for s in sorted(set(seasons) | {2025, 2026})], ignore_index=True)
    rep = {"all": fit(seasons, panel), "train_2025": fit([2025], panel)}
    Path(a.out).write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: {kk: v[kk] for kk in ("n_events_L3", "share_raw", "share_excess", "weight_sum_per_event",
                                               "survival_given3")} for k, v in rep.items()}, indent=1))


if __name__ == "__main__":
    main()
