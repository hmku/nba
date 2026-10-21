"""Games-played model, playoff-week multiplier and availability in valuation (synthetic data)."""

import numpy as np
import pandas as pd
import pytest

from fantasy.league.settings import LeagueSettings
from fantasy.model import availability as A
from fantasy.model import projections as P
from fantasy.model.lines import PLAYOFF_CALENDAR_SHARE, availability, load_projections
from fantasy.model.valuation import ValuationParams, compute_values

# ------------------------------------------------------------------ fitted gp


FULL = [2500.0, 2400.0, 2300.0]  # minutes: starter-level (mpg >= 30 in every case below)


def _cal(raw: float) -> float:
    k = A.GP_CAL
    return raw + k["a"] + k["h"] * max(0.0, raw - k["knee"])


def test_predict_gp_matches_coefficients():
    c = A.GP_COEF
    # all three seasons present, age 30, no hinges active, 30 mpg (no role term), below the calibration knee
    raw = (c["const"] + c["g1"] * 60 + c["g2"] * 50 + c["g3"] * 40 + c["age_gt28"] * 2)
    assert A.predict_gp([60, 50, 40], 30.0, [1800, 1500, 1200]) == pytest.approx(_cal(raw))
    # missing older seasons use the fitted constants; ironman hinges; young bonus is off at g1 >= 70
    raw = (c["const"] + c["g1"] * 80 + c["m2"] + c["m3"] + c["g1_gt70"] * 10 + c["gavg_gt70"] * 10)
    assert A.predict_gp([80, None, float("nan")], 22.0, [2600, None, None]) == pytest.approx(_cal(raw))
    # role hinge: 20 mpg = 10 minutes below 30; young bonus on at g1 < 70
    raw = (c["const"] + c["g1"] * 60 + c["g2"] * 60 + c["g3"] * 60 + c["age_lt24"] * 2 + c["mpg_lt30"] * 10)
    assert A.predict_gp([60, 60, 60], 22.0, [1200, 1200, 1200]) == pytest.approx(_cal(raw))


def test_low_minute_model():
    lc = A.GP_LOW_COEF
    # < 400 minutes last season: low model only (prior minutes = max of the two earlier seasons, capped)
    want = (lc["const"] + lc["prior_k"] * 2.783 + lc["g1"] * 14 + lc["age_gt28"] * 2 + lc["mpg1"] * 389 / 14)
    assert A.predict_gp([14, 31, 78], 30.0, [389, 1012, 2783]) == pytest.approx(want)
    assert A.predict_gp([14, 31, 78], 30.0, [389, 1012, 5000]) == pytest.approx(want + lc["prior_k"] * 0.217)
    # injured starter (few games, big minutes) > end-of-bench player with the same games
    assert A.predict_gp([10, 70, 70], 27.0, [340, 2400, 2400]) > A.predict_gp([10, 70, 70], 27.0, [80, 2400, 2400]) + 10
    # lost season (0 games / no minutes) -> "returning from a lost season", ~35-45 games for a former starter
    lost = A.predict_gp([0, 70, 70], 27.0, [0, 2500, 2400])
    assert lost == A.predict_gp([None, 70, 70], 27.0, [None, 2500, 2400])
    assert lost == pytest.approx(lc["const"] + lc["prior_k"] * 2.5 + lc["lost"])
    assert 33 < lost < 48
    # a bench player with few minutes and no history is projected low, not like a rotation player
    assert A.predict_gp([20, None, None], 24.0, [200, None, None]) < 35
    # young prospects with few minutes grow into a role
    assert A.predict_gp([20, None, None], 20.0, [300, None, None]) == pytest.approx(
        A.predict_gp([20, None, None], 24.0, [300, None, None]) + 2 * lc["age_lt22"])
    assert A.is_low_minutes([499.0]) and not A.is_low_minutes([500.0]) and A.is_low_minutes([None])
    assert A.gp_sd_from_model(40.0, low_minutes=True) == A.GP_LOW_SD


def test_low_main_blend_is_continuous():
    lo, hi = A.GP_BLEND_MIN
    for g1, prior, age in [(40, 2000, 27), (45, None, 23), (30, 800, 24), (25, 2500, 29)]:
        lags, mins = [g1, 60 if prior else None, None], lambda m, p=prior: [m, p, None]
        path = [A.predict_gp(lags, age, mins(m)) for m in np.linspace(lo - 1, hi + 1, 41)]
        assert max(abs(a - b) for a, b in zip(path, path[1:], strict=False)) < 1.5  # no jump at 500
        assert A.predict_gp(lags, age, mins(hi)) == pytest.approx(A._predict_main(
            [float(g1), 60.0 if prior else None, None], [hi, prior, None], age, 80.0))


def test_calibration_slope_and_cap():
    k = A.GP_CAL
    # above the knee the gap between two players shrinks by (1 + h)
    a = A._predict_main([80.0, 80.0, 80.0], [2600.0] * 3, 26.0, 200.0)
    b = A._predict_main([76.0, 80.0, 80.0], [2600.0] * 3, 26.0, 200.0)
    raw_gap = A.GP_COEF["g1"] * 4 + A.GP_COEF["g1_gt70"] * 4 + A.GP_COEF["gavg_gt70"] * 4 / 3
    assert a - b == pytest.approx(raw_gap * (1 + k["h"]))
    assert A.predict_gp([82, 82, 82], 26.0, [2800] * 3, cap=60.0) <= 60.0  # cap holds after calibration


def test_predict_gp_shape():
    base = A.predict_gp([70, 70, 70], 27.0, FULL)
    assert A.predict_gp([40, 70, 70], 27.0, [1300, 2400, 2300]) < base  # recent injuries matter most
    assert A.predict_gp([70, 70, 40], 27.0, FULL) < base
    assert A.predict_gp([70, 70, 70], 31.0, FULL) < base  # decline after 28
    assert A.predict_gp([82, 82, 82], 26.0, [2800] * 3) > 68  # durable players keep high expectations
    assert A.predict_gp([82, 82, 82], 26.0, [2800] * 3) <= 80  # cap
    # bench role: same games, fewer minutes per game -> fewer expected games (coach's decisions)
    assert A.predict_gp([70, 70, 70], 27.0, [1050, 1050, 1050]) < base - 4
    # young bonus does not stack on an already-durable young player
    assert A.predict_gp([78, 75, None], 21.0, [2500, 2400, None]) == pytest.approx(
        A.predict_gp([78, 75, None], 24.0, [2500, 2400, None]))
    # shrinkage: a 20-game season is not projected as 20 games
    assert 35 < A.predict_gp([20, 65, 65], 27.0, [700, 2200, 2200]) < 60
    with pytest.raises(ValueError):
        A.predict_gp([None, 60, 60], 27.0, [800, 2000, 2000])


def test_gp_sd_calibration_shape():
    sds = [A.gp_sd_from_model(g) for g in (30, 45, 55, 62, 68, 75)]
    assert all(a >= b for a, b in zip(sds, sds[1:], strict=False))  # less room to miss when durable
    assert sds[0] == A.GP_SD_KNOTS_Y[0] and sds[-1] == A.GP_SD_KNOTS_Y[-1]  # clamped at the ends
    assert 14 < min(sds) and max(sds) < 26


def test_fitted_gp_calendar_lags_and_lost_season():
    rows = pd.DataFrame({"season": [2024, 2025, 2026], "g": [70.0, 60.0, 50.0], "mp": [2300.0, 2000.0, 1600.0]})
    gp, sd = P._fitted_gp(rows, 2026, 27.0, 80.0)
    assert gp == pytest.approx(A.predict_gp([50, 60, 70], 27.0, [1600, 2000, 2300]))
    assert sd == pytest.approx(A.gp_sd_from_model(gp))
    # missed the latest season entirely: 0 games / 0 minutes -> lost-season (low-minute) model
    rows = pd.DataFrame({"season": [2024, 2025], "g": [70.0, 60.0], "mp": [2300.0, 2000.0]})
    gp, sd = P._fitted_gp(rows, 2026, 27.0, 80.0)
    assert gp == pytest.approx(A.predict_gp([0, 60, 70], 27.0, [0, 2000, 2300]))
    assert sd == A.GP_LOW_SD
    # gap season -> missing middle lag
    rows = pd.DataFrame({"season": [2024, 2026], "g": [70.0, 60.0], "mp": [2300.0, 1900.0]})
    gp, _ = P._fitted_gp(rows, 2026, 27.0, 80.0)
    assert gp == pytest.approx(A.predict_gp([60, None, 70], 27.0, [1900, None, 2300]))


# ------------------------------------------------------------------ playoff multiplier


def test_playoff_mult():
    contender = A.playoff_mult("OKC", 75)
    tanker = A.playoff_mult("WAS", 75)
    assert tanker < contender <= 1.0
    assert A.playoff_mult("OKC", 30) <= contender  # injury history (term is 0 in playoff-mult-0.2)
    assert A.playoff_mult("OKC", None) == pytest.approx(contender)  # no history: no injury term
    assert A.playoff_mult("BKN", 75) == A.playoff_mult("BRK", 75)
    assert A.playoff_mult("???", 75) == pytest.approx(A.PLAYOFF_COEF["const"])
    lo, hi = A.PLAYOFF_MULT_RANGE
    assert lo <= A.playoff_mult("WAS", 0) and A.playoff_mult("OKC", 82) <= hi


# ------------------------------------------------------------------ availability / valuation


def _frame():
    return pd.DataFrame({"gp": [70.0, 50.0, 50.0], "gp_sd": [15.0, 24.0, 24.0],
                         "playoff_mult": [1.0, 0.95, 0.80]})


def test_availability_neutral_and_risk():
    df = _frame()
    plain = df["gp"].to_numpy() / 82
    assert np.allclose(availability(df), plain)  # defaults: neutral
    assert np.allclose(availability(df, 0.0, PLAYOFF_CALENDAR_SHARE), plain)
    risk = availability(df, gp_risk_lambda=0.5)
    assert np.allclose(risk, (df["gp"] - 0.5 * df["gp_sd"]).to_numpy() / 82)
    assert np.all(availability(df, gp_risk_lambda=10.0) >= 0)


def test_availability_playoff_weight():
    df = _frame()
    plain = availability(df)
    tilted = availability(df, playoff_weight=0.5)
    assert tilted[0] == pytest.approx(plain[0])  # multiplier 1 -> unchanged
    assert tilted[2] < tilted[1] < plain[1]  # lower playoff availability -> lower value
    # weight 1 = playoff weeks only
    assert availability(df, playoff_weight=1.0)[2] == pytest.approx(plain[2] * 0.80)


def test_availability_tolerates_old_projection_files(proj_path):
    proj = load_projections(proj_path)  # synthetic file has gp_sd but no playoff_mult
    assert (proj.df["playoff_mult"] == 1.0).all()
    assert np.allclose(availability(proj.df, playoff_weight=0.5), availability(proj.df))


def test_valuation_params_move_values(proj_path):
    proj = load_projections(proj_path)
    df = proj.df.copy()
    a, b = df.index[20], df.index[21]
    df.loc[[a, b], "gp"] = 60.0
    df.loc[a, "gp_sd"], df.loc[b, "gp_sd"] = 25.0, 10.0
    df.loc[a, "playoff_mult"] = 0.8
    p2 = type(proj)(df=df, path=proj.path, sha256="x")
    s = LeagueSettings()
    v0, _, _ = compute_values(p2, s, ValuationParams(playoff_weight=PLAYOFF_CALENDAR_SHARE))
    v_pw, _, _ = compute_values(p2, s, ValuationParams(playoff_weight=0.5))
    v_risk, _, _ = compute_values(p2, s, ValuationParams(gp_risk_lambda=0.5, playoff_weight=0.15))
    gap0 = v0.loc[b, "value"] - v0.loc[a, "value"]
    assert v_pw.loc[b, "value"] - v_pw.loc[a, "value"] > gap0  # a loses playoff availability
    assert v_risk.loc[b, "value"] - v_risk.loc[a, "value"] > gap0  # a is the riskier one
    # defaults are the documented ones
    assert ValuationParams().gp_risk_lambda == 0.0
    assert ValuationParams().playoff_weight == pytest.approx(0.30)


# ------------------------------------------------------------------ projections integration


@pytest.fixture(scope="module")
def built():
    return P.build()


def test_projection_gp_columns(built):
    df, notes = built
    assert df["playoff_mult"].between(*A.PLAYOFF_MULT_RANGE).all()
    assert df["gp"].between(0, 82).all()
    raw = P.load_totals()
    mp26 = raw[raw["season"] == 2026].set_index("player_id")["mp"]
    for _, r in P.baseline().iterrows():  # every model row: sd from the branch its minutes select
        low = A.is_low_minutes([mp26.get(r["bbref_id"])])
        assert r["gp_sd"] == pytest.approx(A.gp_sd_from_model(r["gp"], low_minutes=low), abs=0.1), r["name"]
    assert any("manual_gp_scale" in n for v in notes.values() for n in v)
    # a manual gp sets gp_sd from its own level
    ov = P.load_manual("player_overrides_2026_27.csv")
    manual = ov[ov["gp"].astype(str).str.strip().ne("") & ov["gp"].notna()]
    d = df.set_index("player_id")
    for pid in manual["player_id"]:
        if pid in d.index and not P.load_manual("player_overrides_2026_27.csv").query(
                "player_id == @pid")["games_missed"].astype(str).str.strip().replace("nan", "").ne("").any():
            assert d.at[pid, "gp_sd"] == pytest.approx(A.gp_sd_from_model(d.at[pid, "gp"]), abs=0.1), pid


def test_manual_gp_scale_applies_to_rookie_priors(built):
    df, _ = built
    rk = P.load_manual("rookies_2026_27.csv")
    if rk.empty:
        pytest.skip("no rookie priors")
    r = rk.iloc[0]
    row = df.set_index("player_id").loc[r["player_id"]]
    assert row["gp"] == pytest.approx(float(r["gp"]) * P.Params().manual_gp_scale, abs=0.06)


def test_shrink_mode_reproduces_legacy_rule():
    df = P.baseline(P.Params(gp_model="shrink"))
    row = df[df["bbref_id"] == "leonaka01"]
    if row.empty:
        pytest.skip("player not in cached data")
    assert float(row["gp"].iloc[0]) == pytest.approx(60.8, abs=0.05)  # baseline-0.1 value


# ------------------------------------------------------------------ fit panel (scripts/gp_model)


def test_gp_panel_rules():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "gp_model"))
    from panel import build_panel, player_seasons

    def row(pid, season, team, g, mp, age=25.0):
        return {"season": season, "lg": "NBA", "player": pid, "player_id": pid, "age": age, "team": team,
                "g": g, "mp": mp}

    totals = pd.DataFrame([
        row("a", 2020, "AAA", 60, 1800), row("a", 2021, "AAA", 70, 2100),  # plain
        row("b", 2020, "AAA", 70, 2000), row("b", 2022, "AAA", 50, 1500),  # lost 2021, returns 2022
        row("c", 2020, "AAA", 70, 2000),  # never plays again -> dropped
        row("d", 2020, "2TM", 60, 1200), row("d", 2020, "AAA", 30, 600), row("d", 2020, "BBB", 30, 600),
        row("d", 2021, "BBB", 41, 900),
    ])
    teams = pd.DataFrame({"season": [2020, 2020, 2021, 2021, 2022], "lg": "NBA",
                          "abbreviation": ["AAA", "BBB", "AAA", "BBB", "AAA"],
                          "w": [41, 41, 41, 21, 41], "l": [41, 41, 41, 20, 41]})
    p = build_panel(player_seasons(totals, teams), first_target=2021).set_index(["player_id", "T"])
    assert p.loc[("a", 2021), "y"] == 70 and p.loc[("a", 2021), "g1"] == 60
    assert p.loc[("a", 2021), "mpg1"] == pytest.approx(30.0)
    assert p.loc[("b", 2021), "y"] == 0  # lost season while still in the league
    lost = p.loc[("b", 2022)]
    assert lost["lost"] == 1 and lost["g1"] == 0 and lost["mp1"] == 0 and lost["mp2"] == 2000
    assert ("c", 2021) not in p.index
    assert ("b", 2022) not in p.index or p.loc[("b", 2022), "lost"] == 1
    assert ("a", 2022) not in p.index  # last season in the data: a missing T row is censored, never a 0
    assert p.loc[("d", 2021), "g1"] == 60 and p.loc[("d", 2021), "y"] == pytest.approx(82.0)  # 41 of 41 -> 82
