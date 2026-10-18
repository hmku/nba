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
