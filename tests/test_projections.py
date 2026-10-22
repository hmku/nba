import numpy as np
import pandas as pd
import pytest

from fantasy.model import projections as P

REQUIRED_NUMERIC = ["age", "gp", "gp_sd", "mpg", "fgm", "fga", "ftm", "fta", "fg3m", "pts",
                    "reb", "ast", "stl", "blk", "tov"]  # fmt: skip


@pytest.fixture(scope="module")
def proj() -> pd.DataFrame:
    df, _ = P.build()
    return df


def test_contract_columns(proj):
    assert list(proj.columns) == P.COLUMNS


def test_committed_csv_matches_contract():
    df = pd.read_csv(P.OUTPUT_PATH)
    assert list(df.columns) == P.COLUMNS
    assert len(df) >= 300


def test_build_is_deterministic(proj):
    again, _ = P.build()
    pd.testing.assert_frame_equal(proj, again)


def test_unique_slug_ids(proj):
    assert proj["player_id"].is_unique
    assert proj["player_id"].str.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*").all()


def test_makes_attempts_consistency(proj):
    assert (proj["fgm"] <= proj["fga"] + 1e-9).all()
    assert (proj["ftm"] <= proj["fta"] + 1e-9).all()
    assert (proj["fg3m"] <= proj["fgm"] + 1e-9).all()
    pts = 2 * proj["fgm"] + proj["fg3m"] + proj["ftm"]
    assert np.allclose(proj["pts"], pts, atol=0.01)
    fg = proj["fgm"] / proj["fga"]
    assert fg[proj["fga"] > 3].between(0.33, 0.75).all()


def test_gp_bounds(proj):
    assert proj["gp"].between(0, 82).all()
    assert (proj["gp_sd"] > 0).all()
    assert proj["mpg"].between(0, 40).all()


def test_no_nan_top200(proj):
    top = P.zscores(proj).head(200)
    assert not top[REQUIRED_NUMERIC].isna().any().any()
    assert top["positions"].notna().all() and (top["positions"] != "").all()


def test_positions_valid(proj):
    for p in proj["positions"]:
        parts = p.split("|")
        assert parts and all(x in P.POSITIONS for x in parts)


def test_status_values(proj):
    assert set(proj["status"]).issubset(P.STATUS_VALUES)


def test_sanity_top_players(proj):
    top = P.zscores(proj).head(15)["player_id"].tolist()
    for pid in ["nikola-jokic", "victor-wembanyama", "shai-gilgeous-alexander"]:
        assert pid in top


def test_age_curve_monotone_after_peak():
    vals = [P.age_mult(a) for a in range(27, 41)]
    assert all(b <= a for a, b in zip(vals, vals[1:], strict=False))


def test_overridden_players_survive_the_volume_cut(proj):
    """Every player in player_overrides must get a row, even if below max_rows by volume."""
    ov = set(P.load_manual("player_overrides_2026_27.csv")["player_id"])
    assert ov <= set(proj["player_id"])
    small, notes = P.build(P.Params(max_rows=50))
    assert ov <= set(small["player_id"])
    assert len(small) < len(proj)
    rescued = [k for k, v in notes.items() if any("volume cut" in n for n in v)]
    assert rescued and set(rescued) <= ov


def test_data_top_json_shape():
    import json

    from typer.testing import CliRunner

    from fantasy.cli import app

    res = CliRunner().invoke(app, ["data", "top", "--n", "3", "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out["ok"] is True and len(out["players"]) == 3 and "player_id" in out["players"][0]


# ---------------------------------------------------------------- review fixes (baseline-0.3.0), synthetic


def _synthetic_seasons() -> pd.DataFrame:
    g = pd.DataFrame({
        "season": [2024, 2025, 2026], "mp": [2000.0, 2200.0, 2400.0], "fgm": [500.0, 560.0, 620.0],
        "fga": [1100.0, 1200.0, 1300.0], "ftm": [250.0, 270.0, 300.0], "fta": [300.0, 320.0, 350.0],
        "fg3m": [120.0, 140.0, 150.0], "reb": [400.0, 420.0, 450.0], "ast": [300.0, 330.0, 360.0],
        "stl": [60.0, 65.0, 70.0], "blk": [30.0, 32.0, 35.0], "tov": [150.0, 160.0, 170.0],
    })  # fmt: skip
    g["w"] = g["season"].map(P.Params().season_weights)
    return g


def _prior_rates() -> pd.Series:
    return pd.Series({"fgm": 0.17, "fga": 0.38, "ftm": 0.07, "fta": 0.09, "fg3m": 0.05, "reb": 0.18,
                      "ast": 0.09, "stl": 0.03, "blk": 0.02, "tov": 0.05})  # fmt: skip


def test_tov_follows_age_curve_so_tov_per_usage_is_age_invariant():
    """Turnovers scale with usage: TOV / (FGA + 0.44 FTA + AST) must not depend on the age multiplier."""
    g, prior, params = _synthetic_seasons(), _prior_rates(), P.Params()
    emin = P.effective_minutes(g["w"], g["mp"], params.season_weights)
    ratios = []
    for age in (22.0, 27.0, 34.0, 40.0):
        r = P._per_minute_rates(g, emin, prior, age, params)
        ratios.append(r["tov"] / (r["fga"] + 0.44 * r["fta"] + r["ast"]))
    assert np.allclose(ratios, ratios[0], rtol=1e-12)
    old, young = (P._per_minute_rates(g, emin, prior, a, params) for a in (40.0, 27.0))
    assert old["tov"] / young["tov"] == pytest.approx(P.age_mult(40) / P.age_mult(27))


def test_prior_min_is_in_effective_minutes_and_preserves_pre_030_strength():
    g, prior, params = _synthetic_seasons(), _prior_rates(), P.Params()
    w = params.season_weights
    # three equal seasons of M minutes count as 3M effective minutes
    eq = g.assign(mp=1000.0)
    assert P.effective_minutes(eq["w"], eq["mp"], w) == pytest.approx(3000.0)
    # same rates as the pre-0.3.0 formula with prior 400 *weighted* minutes (weights summing to 10)
    emin = P.effective_minutes(g["w"], g["mp"], w)
    new = P._per_minute_rates(g, emin, prior, 27.0, params)
    wmin = float((g["w"] * g["mp"]).sum())
    for s in ["reb", "ast", "stl", "blk", "tov"]:
        legacy = ((g["w"] * g[s]).sum() + 400.0 * prior[s]) / (wmin + 400.0)
        assert new[s] == pytest.approx(legacy, rel=1e-12), s
    # a player with exactly prior_min effective minutes sits halfway between his rate and the prior
    one = g.tail(1).copy()
    one["mp"] = params.prior_min * (sum(w.values()) / len(w)) / one["w"].iloc[0]
    one["reb"] = 0.40 * one["mp"]
    e1 = P.effective_minutes(one["w"], one["mp"], w)
    assert e1 == pytest.approx(params.prior_min)
    r = P._per_minute_rates(one, e1, prior, 27.0, params)
    assert r["reb"] == pytest.approx((0.40 + prior["reb"]) / 2)


def test_mpg_growth_tapers_without_a_cliff():
    grid = np.linspace(20, 34, 1401)
    for age in (19, 20, 21, 22, 23, 24, 25):
        full = P.MPG_GROWTH.get(age, 0.0)
        assert P.mpg_growth(20.0, age) == full and P.mpg_growth(P.MPG_GROWTH_FULL_BELOW, age) == full
        assert P.mpg_growth(P.MPG_GROWTH_CAP, age) == 0.0 and P.mpg_growth(33.0, age) == 0.0
        proj = [m + P.mpg_growth(m, age) for m in grid]
        assert all(b >= a - 1e-12 for a, b in zip(proj, proj[1:], strict=False)), age
    assert P.mpg_growth(28.5, 21) == pytest.approx(2.5 * 0.5)


def test_manual_gp_scale_column():
    notes: dict[str, list[str]] = {}
    mg = P._ManualGp(P.Params(), notes)
    assert mg("a", 60.0) == pytest.approx(54.0)  # blank = baseline01 (legacy rows unchanged)
    assert mg("b", 60.0, "baseline01") == pytest.approx(54.0)
    assert mg("c", 60.0, "fitted") == 60.0 and "c" not in notes  # no double haircut
    with pytest.raises(ValueError, match="gp_scale"):
        mg("d", 60.0, "fited")
    shrink = P._ManualGp(P.Params(gp_model="shrink"), {})
    assert shrink("e", 60.0) == 60.0 and shrink("f", 60.0, "fitted") == 60.0


def test_override_gp_scale_values_valid():
    ov = P.load_manual("player_overrides_2026_27.csv")
    assert "gp_scale" in ov.columns
    assert set(ov["gp_scale"].str.strip()) <= P.GP_SCALES | {""}
