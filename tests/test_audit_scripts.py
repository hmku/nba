"""Offline tests for the weekly-audit helpers in scripts/audit (synthetic data only)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "audit"))

import common  # noqa: E402
import league_calibration as lc  # noqa: E402
import projection_bias as pb  # noqa: E402
from synthetic import make_projections  # noqa: E402


def _games(proj: pd.DataFrame, scale: float, n_games: int = 8, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for pid, r in proj.iterrows():
        for g in range(n_games):
            row = {"player_id": pid, "game_date": f"2026-10-{20 + g}", "min": r.mpg * scale + rng.normal(0, 2)}
            for s in common.STATS:
                row[s] = max(0.0, r[s] * scale + rng.normal(0, 0.3 * np.sqrt(r[s] + 0.1)))
            row["fgm"] = min(row["fgm"], row["fga"])
            row["ftm"] = min(row["ftm"], row["fta"])
            rows.append(row)
    return pd.DataFrame(rows)


def test_bias_recovers_known_shift() -> None:
    proj = make_projections(120).set_index("player_id")
    rows = {r["stat"]: r for r in pb.bias_rows(pb.residual_frame(_games(proj, 0.9), proj, "syn"))}
    assert abs(rows["pts"]["pct"] + 0.10) < 0.02
    assert rows["pts"]["t"] < -3
    assert abs(rows["min"]["pct"] + 0.10) < 0.02
    unbiased = {r["stat"]: r for r in pb.bias_rows(pb.residual_frame(_games(proj, 1.0, seed=1), proj, "syn"))}
    assert abs(unbiased["pts"]["t"]) < 3.5


def test_cluster_mean_wider_than_iid_with_player_effects() -> None:
    rng = np.random.default_rng(3)
    groups = np.repeat(np.arange(50), 10)
    x = np.repeat(rng.normal(0, 1, 50), 10) + rng.normal(0, 0.1, 500)
    mu, se = common.cluster_mean(groups, x)
    assert se > 2 * x.std(ddof=1) / np.sqrt(len(x))
    assert abs(mu - x.mean()) < 1e-12


def test_calibration_of_calibrated_forecasts() -> None:
    rng = np.random.default_rng(5)
    p = rng.uniform(0.2, 0.8, 600)
    y = (rng.uniform(size=600) < p).astype(float)
    c = common.calibration(p, y, np.arange(600) // 6, rng, B=200)
    assert abs(c["brier_gap"]) < 3 * c["brier_gap_se"] + 1e-9
    assert abs(c["slope"] - 1) < 3 * c["slope_se"]
    assert sum(b["n"] for b in c["bins"]) == 600


def test_compare_findings_new_persisting_resolved() -> None:
    prev = [common.finding("a", "x", "pts", -1.0, 0.2), common.finding("b", "x", "min", -1.0, 0.2),
            common.finding("c", "x", "reb", 0.1, 0.2)]
    cur = [common.finding("a", "x", "pts", -1.1, 0.2), common.finding("b", "x", "min", -0.2, 0.2),
           common.finding("c", "x", "reb", 0.9, 0.2)]
    cmp = common.compare_findings(cur, prev)
    assert cmp["new"] == ["c"] and cmp["resolved"] == ["b"] and [p["id"] for p in cmp["persisting"]] == ["a"]
    f = common.finding("s", "calibration", "slope", 1.9, 0.2, null=1.0)
    assert f["candidate_fix"] and abs(f["t"] - 4.5) < 1e-9


def test_scoring_and_standings() -> None:
    a = {"fgm": 40, "fga": 90, "ftm": 20, "fta": 25, "fg3m": 10, "pts": 110, "reb": 50, "ast": 30, "stl": 8, "blk": 5,
         "tov": 12}
    b = {**a, "pts": 100, "tov": 15, "reb": 50}
    assert lc.winner(a, b, "pts") == 1 and lc.winner(a, b, "tov") == 1 and lc.winner(a, b, "reb") == 0
    res = {c: lc.winner(a, b, c) for c in common.CATS}
    m = {"week": 1, "a": 1, "b": 2, "basis": "yahoo_final", "cat_res": res,
         "score": [sum(v > 0 for v in res.values()), sum(v < 0 for v in res.values())]}
    st = lc.standings([m], [1, 2])
    assert st[1]["W"] == 1 and st[2]["L"] == 1 and st[1]["ct"] == 7


def test_last_sunday() -> None:
    assert common.last_sunday(date(2026, 11, 9)) == date(2026, 11, 8)  # Monday
    assert common.last_sunday(date(2026, 11, 8)) == date(2026, 11, 1)  # Sunday: that week isn't over
    assert common.last_sunday(date(2026, 11, 12)) == date(2026, 11, 8)
