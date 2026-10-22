"""External projection parsers, the blend, and the projection backtest helpers (no network)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from synthetic import make_projections

from fantasy.data import external
from fantasy.model import blend as BL
from fantasy.model import proj_backtest as B
from fantasy.model.blend import BlendSpec, blend, impute_attempts

ESPN_PAYLOAD = {
    "players": [
        {
            "player": {
                "fullName": "Nikola Jokić",
                "proTeamId": 7,
                "stats": [
                    {"id": "102026", "averageStats": {"0": 1.0, "42": 1.0}},  # other season
                    {
                        "id": "102027",
                        "averageStats": {
                            "0": 28.25, "1": 0.81, "2": 1.6, "3": 10.1, "6": 12.69, "11": 3.4,
                            "13": 10.58, "14": 18.4, "15": 5.39, "16": 6.6, "17": 1.69,
                            "40": 35.6, "42": 72.0,
                        },  # fmt: skip
                    },
                ],
            }
        },
        {"player": {"fullName": "No Projection", "proTeamId": 0, "stats": []}},
        {
            "player": {
                "fullName": "Walter Clayton Jr.",
                "proTeamId": 29,
                "stats": [{"id": "102027", "averageStats": {"0": 8.0, "42": 60.0, "13": 3.0, "14": 7.0}}],
            }
        },
    ]
}

FP_HTML = """
<table id="data"><tbody>
<tr class="mpb-player-3485"><td class="player-label"><a href="/x" class="player-name" fp-player-name="Shai Gilgeous-Alexander">Shai Gilgeous-Alexander</a> <small>(OKC - PG)</small></td>
<td class="center">2,425</td><td class="center">351</td><td class="center">485</td><td class="center">63</td>
<td class="center">124</td><td class="center">.528</td><td class="center">.887</td><td class="center">134</td>
<td class="center">77</td><td class="center">2,643</td><td class="center">180</td>
</tr>
<tr class="mpb-player-1"><td class="player-label"><a fp-player-name="Zero Games">Zero Games</a> <small>(PHX - C)</small></td>
<td class="center">0</td><td class="center">0</td><td class="center">0</td><td class="center">0</td>
<td class="center">0</td><td class="center">-</td><td class="center">-</td><td class="center">0</td>
<td class="center">0</td><td class="center">0</td><td class="center">0</td></tr>
</tbody></table>
"""


def test_parse_espn_picks_season_projection_and_maps_ids():
    df = external.parse_espn(json.loads(json.dumps(ESPN_PAYLOAD)), 2027)
    assert list(df.columns) == external.EXTERNAL_COLUMNS
    assert set(df["player_id"]) == {"nikola-jokic", "walter-clayton"}  # alias applied
    j = df.set_index("player_id").loc["nikola-jokic"]
    assert j["team"] == "DEN" and j["gp"] == 72 and j["mpg"] == pytest.approx(35.6)
    assert j["fga"] == pytest.approx(18.4) and j["fg_pct"] == pytest.approx(10.58 / 18.4)
    assert df.set_index("player_id").loc["walter-clayton", "team"] == "MEM"


def test_parse_fantasypros_totals_to_per_game():
    df = external.parse_fantasypros(FP_HTML, 2027)
    assert len(df) == 1  # zero-GP row dropped
    r = df.iloc[0]
    assert r["player_id"] == "shai-gilgeous-alexander" and r["team"] == "OKC"
    assert r["pts"] == pytest.approx(2425 / 77) and r["mpg"] == pytest.approx(2643 / 77)
    assert r["fg_pct"] == pytest.approx(0.528) and np.isnan(r["fga"])


def test_direct_percentages_must_be_fractions():
    """A percent-scale FG% (52.8) would be clipped silently by impute_attempts: reject it at parse time."""
    with pytest.raises(AssertionError, match="fg_pct"):
        external.parse_fantasypros(FP_HTML.replace(">.528<", ">52.8<"), 2027)
    with pytest.raises(AssertionError, match="ft_pct"):
        external.parse_fantasypros(FP_HTML.replace(">.887<", ">0<"), 2027)


def _ours(n: int = 260) -> pd.DataFrame:
    df = make_projections(n)
    df["source"] = "bbref_baseline"
    return df


def _ext_from(ours: pd.DataFrame, scale: float, n: int) -> pd.DataFrame:
    e = ours.head(n).copy()
    for c in ["fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov", "mpg"]:
        e[c] = e[c] * scale
    e["pts"] = 2 * e["fgm"] + e["fg3m"] + e["ftm"]
    e["fg_pct"] = e["fgm"] / e["fga"]
    e["ft_pct"] = e["ftm"] / e["fta"]
    e["gp"] = 75.0
    return e[[c for c in external.EXTERNAL_COLUMNS if c in e.columns]]


def test_blend_weights_coverage_and_consistency():
    ours = _ours()
    ext = _ext_from(ours, 1.2, 100)
    out, rep = blend(ours, {"espn": ext}, BlendSpec.parse("ours=0.5,espn=0.5"))
    assert list(out.columns) == list(ours.columns) and len(out) == len(ours)
    o, b = ours.set_index("player_id"), out.set_index("player_id")
    covered, uncovered = ext["player_id"].iloc[0], ours["player_id"].iloc[200]
    assert b.loc[covered, "reb"] == pytest.approx(o.loc[covered, "reb"] * 1.1, abs=2e-3)
    assert b.loc[uncovered, "reb"] == pytest.approx(o.loc[uncovered, "reb"], abs=1e-3)
    assert (b["gp"] == o["gp"].round(1)).all()  # games stay ours by default
    assert (out["fgm"] <= out["fga"] + 1e-9).all() and (out["fg3m"] <= out["fgm"] + 1e-9).all()
    np.testing.assert_allclose(out["pts"], 2 * out["fgm"] + out["fg3m"] + out["ftm"], atol=2e-3)
    assert rep["blended_players"] == 100 and b.loc[covered, "source"].endswith("+blend")


def test_blend_gp_weights_and_bad_specs():
    ours = _ours()
    ext = _ext_from(ours, 1.0, 50)
    out, _ = blend(ours, {"espn": ext}, BlendSpec.parse("ours=1", "ours=0,espn=1"))
    pid = ext["player_id"].iloc[0]
    assert out.set_index("player_id").loc[pid, "gp"] == 75.0
    for bad in ["ours=-1", "nope=1", "ours", "ours=0"]:
        with pytest.raises(ValueError):
            BlendSpec.parse(bad)


def test_impute_attempts_matches_source_points():
    ours = _ours().set_index("player_id")
    ext = _ext_from(ours.reset_index(), 1.1, 30).set_index("player_id")
    ext[["fgm", "fga", "ftm", "fta"]] = np.nan
    imp = impute_attempts(ext, ours)
    pts = 2 * imp["fgm"] + imp["fg3m"] + imp["ftm"]
    np.testing.assert_allclose(pts, ext["pts"], rtol=1e-9)
    np.testing.assert_allclose(imp["fgm"] / imp["fga"], ext["fg_pct"], rtol=1e-9)


# ---------------------------------------------------------------- backtest helpers


def _totals(seasons: range, n: int = 40, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        for s in seasons:
            g = int(rng.integers(30, 80))
            mp = g * float(rng.uniform(12, 34))
            fga = mp * 0.4
            rows.append({
                "season": s, "lg": "NBA", "player": f"Player {i}", "player_id": f"p{i:02d}",
                "age": 22 + i % 12 + (s - seasons[0]), "team": "AAA", "pos": ["PG", "SF", "C"][i % 3],
                "g": g, "mp": mp, "fg": fga * 0.46, "fga": fga, "ft": mp * 0.08, "fta": mp * 0.1,
                "x3p": mp * 0.04, "trb": mp * 0.2, "ast": mp * 0.1, "stl": mp * 0.03,
                "blk": mp * 0.02, "tov": mp * 0.05,
            })  # fmt: skip
    return pd.DataFrame(rows)


def test_shifted_totals_has_no_leakage():
    t = _totals(range(2010, 2016))
    s = B.shifted_totals(t, 2014)
    assert sorted(s["season"].unique()) == [2024, 2025, 2026]  # 2011-2013 relabelled
    assert len(s) == 3 * 40


def test_project_core_uses_only_prior_seasons():
    t = _totals(range(2010, 2016))
    career = pd.DataFrame({"player_id": t["player_id"].unique(), "birth_date": ""})
    a = B.project_core(t, career, 2014)
    t2 = t.copy()
    t2.loc[t2["season"] >= 2014, ["fg", "fga", "trb", "mp"]] *= 3  # change the future
    b = B.project_core(t2, career, 2014)
    pd.testing.assert_frame_equal(a, b)
    assert {"reb", "pts", "mpg", "age"} <= set(a.columns)


def test_baselines_last_season_and_values():
    t = _totals(range(2010, 2016))
    bl = B.baselines(t, 2014)
    row = t[(t["season"] == 2013) & (t["player_id"] == "p00")].iloc[0]
    assert bl["last"].loc["p00", "reb"] == pytest.approx(row["trb"] / row["g"])
    assert set(bl) == {"last", "avg3", "mw3"}
    from fantasy.league.settings import LeagueSettings

    lines = make_projections(260).set_index("player_id")
    v = B.per_game_values(lines, LeagueSettings())
    assert v.notna().all() and v.corr(lines["pts"]) > 0.5
    lines2 = lines.assign(gp=5.0)  # per-game value ignores games played
    pd.testing.assert_series_equal(v, B.per_game_values(lines2, LeagueSettings()))


def test_bootstrap_se_and_spearman():
    rng = np.random.default_rng(0)
    groups = {s: pd.DataFrame({"x": rng.normal(size=200)}) for s in range(3)}
    groups = {s: g.assign(y=g["x"] + rng.normal(size=200)) for s, g in groups.items()}
    est, se = B.bootstrap(groups, lambda d: {"r": B.spearman(d["x"], d["y"])}, n_boot=100)
    assert 0.6 < est["r"] < 0.8 and 0.005 < se["r"] < 0.05


def test_load_config(tmp_path):
    cfg = tmp_path / "projection_blend.json"
    assert BL.load_config(cfg) is None  # absent -> unblended
    cfg.write_text(json.dumps({"weights": {"ours": 0.5, "espn": 0.5}, "gp_weights": None}))
    spec = BL.load_config(cfg)
    assert spec == BlendSpec({"ours": 0.5, "espn": 0.5}, None)
    cfg.write_text(json.dumps({"weights": {"ours": 1.0}}))
    assert BL.load_config(cfg) is None  # all weight on ours -> unblended
    cfg.write_text(json.dumps({"weights": {"ours": 1, "espn": 0}, "gp_weights": {"espn": 1}}))
    assert BL.load_config(cfg).gp_weights == {"espn": 1.0}


def test_committed_default_is_the_configured_blend():
    """data/processed/projections_2026_27.csv is the blend of the committed base file per the
    committed config (adopted 2026-10-19), and its meta records weights and input shas."""
    import hashlib

    spec = BL.load_config()
    assert spec == BlendSpec({"ours": 0.5, "espn": 0.5}, None)
    from fantasy.model.projections import OUTPUT_PATH

    meta = json.loads(OUTPUT_PATH.with_name(OUTPUT_PATH.stem + ".meta.json").read_text())
    assert meta["weights"] == spec.weights and meta["projections_version"] == BL.PROJECTIONS_VERSION
    assert meta["sha256"] == hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    base = {i["path"]: i["sha256"] for i in meta["inputs"]}["data/processed/projections_2026_27.base.csv"]
    assert base == hashlib.sha256(BL.BASE_OUTPUT.read_bytes()).hexdigest()


def test_write_default_none_copies_base(tmp_path):
    base = tmp_path / "p.base.csv"
    _ours().to_csv(base, index=False)
    out = tmp_path / "p.csv"
    meta = BL.write_default(None, {"sha256": "x", "model_version": "m"}, out, base)
    assert out.read_bytes() == base.read_bytes()
    assert meta["blend"].startswith("none") and json.loads((tmp_path / "p.meta.json").read_text())["output"]


def test_alt_projections_cli_writes_only_out(tmp_path, monkeypatch):
    """`fantasy data alt-projections` writes a third-party-only file to --out and never touches
    the default projections; uncovered players keep our numbers, games stay ours."""
    import hashlib

    from typer.testing import CliRunner

    from fantasy.cli import app
    from fantasy.model.projections import OUTPUT_PATH

    ours = _ours()
    base = tmp_path / "base.csv"
    ours.to_csv(base, index=False)
    ext = _ext_from(ours, 1.2, 100)
    ext_csv = tmp_path / "espn.csv"
    ext.to_csv(ext_csv, index=False)
    monkeypatch.setattr(BL, "BASE_OUTPUT", base)
    monkeypatch.setattr(external, "load", lambda s, season: ext)
    monkeypatch.setattr(external, "csv_path", lambda s, season: ext_csv)
    before = hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    out = tmp_path / "alt" / "espn.csv"
    r = CliRunner().invoke(app, ["data", "alt-projections", "--blend", "espn-only", "--out", str(out), "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["coverage"] == {"espn": 100}
    assert hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest() == before
    o, a = ours.set_index("player_id"), pd.read_csv(out).set_index("player_id")
    covered, uncovered = ext["player_id"].iloc[0], ours["player_id"].iloc[200]
    assert a.loc[covered, "reb"] == pytest.approx(o.loc[covered, "reb"] * 1.2, abs=2e-3)
    assert a.loc[uncovered, "reb"] == pytest.approx(o.loc[uncovered, "reb"], abs=1e-3)
    assert (a["gp"] == o["gp"].round(1)).all()
    r = CliRunner().invoke(app, ["data", "alt-projections", "--blend", "none", "--out", str(tmp_path / "n.csv")])
    assert r.exit_code == 0 and (tmp_path / "n.csv").read_bytes() == base.read_bytes()
