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


def test_predict_gp_matches_coefficients():
    c = A.GP_COEF
    # all three seasons present, age 30, no hinges active
    want = (c["const"] + c["g1"] * 60 + c["g2"] * 50 + c["g3"] * 40 + c["age_gt28"] * 2)
    assert A.predict_gp([60, 50, 40], 30.0) == pytest.approx(want)
    # missing older seasons use the fitted constants; ironman hinges
    want = (c["const"] + c["g1"] * 80 + c["m2"] + c["m3"] + c["age_lt24"] * 2
            + c["g1_gt70"] * 10 + c["gavg_gt70"] * 10)
    assert A.predict_gp([80, None, float("nan")], 22.0) == pytest.approx(want)


def test_predict_gp_shape():
    base = A.predict_gp([70, 70, 70], 27.0)
    assert A.predict_gp([40, 70, 70], 27.0) < base  # recent injuries matter most
    assert A.predict_gp([70, 70, 40], 27.0) < base
    assert A.predict_gp([70, 70, 70], 31.0) < base  # decline after 28
    assert A.predict_gp([82, 82, 82], 26.0) > 70  # durable players keep high expectations
    assert A.predict_gp([82, 82, 82], 26.0) <= 80  # cap
    # shrinkage: a 20-game season is not projected as 20 games
    assert 35 < A.predict_gp([20, 65, 65], 27.0) < 60
    with pytest.raises(ValueError):
        A.predict_gp([None, 60, 60], 27.0)


def test_gp_sd_calibration_shape():
    sds = [A.gp_sd_from_model(g) for g in (30, 45, 55, 62, 68, 75)]
    assert all(a >= b for a, b in zip(sds, sds[1:], strict=False))  # less room to miss when durable
    assert sds[0] == A.GP_SD_KNOTS_Y[0] and sds[-1] == A.GP_SD_KNOTS_Y[-1]  # clamped at the ends
    assert 14 < min(sds) and max(sds) < 26


def test_fitted_gp_calendar_lags_and_shift():
    rows = pd.DataFrame({"season": [2024, 2025, 2026], "g": [70.0, 60.0, 50.0]})
    gp, sd = P._fitted_gp(rows, 2026, 27.0, 80.0)
    assert gp == pytest.approx(A.predict_gp([50, 60, 70], 27.0))
    assert sd == pytest.approx(A.gp_sd_from_model(gp))
    # missed the latest season entirely: last played season becomes the most recent lag
    rows = pd.DataFrame({"season": [2024, 2025], "g": [70.0, 60.0]})
    gp, _ = P._fitted_gp(rows, 2026, 27.0, 80.0)
    assert gp == pytest.approx(A.predict_gp([60, 70, None], 27.0))
    # gap season -> missing middle lag
    rows = pd.DataFrame({"season": [2024, 2026], "g": [70.0, 60.0]})
    gp, _ = P._fitted_gp(rows, 2026, 27.0, 80.0)
    assert gp == pytest.approx(A.predict_gp([60, None, 70], 27.0))


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
    # healthy veterans (no manual override): gp_sd is the calibrated function of gp
    base = df[df["source"] == "bbref_baseline"]
    assert np.allclose(base["gp_sd"], [A.gp_sd_from_model(g) for g in base["gp"]], atol=0.1)
    assert any("manual_gp_scale" in n for v in notes.values() for n in v)


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
