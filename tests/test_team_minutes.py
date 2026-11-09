"""Team minute budget (fantasy.model.team_minutes) and the in-season population prior: synthetic data only."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fantasy.model import inseason as I
from fantasy.model import team_minutes as TM


def _team(mpgs, team="AAA", share=0.8):
    n = len(mpgs)
    return pd.DataFrame({"player_id": [f"{team}{i}" for i in range(n)], "team": team, "mpg": mpgs,
                         "pts": np.array(mpgs) * 0.5, "fgm": np.array(mpgs) * 0.2, "fga": np.array(mpgs) * 0.4,
                         "reb": np.array(mpgs) * 0.2, "share": share})


MPGS = [36.0, 34.0, 32.0, 30.0, 28.0, 24.0, 20.0, 18.0, 16.0, 14.0, 12.0, 10.0]  # sum x 0.8 = 219.2


@pytest.mark.parametrize("beta", [0.0, 2.0])
@pytest.mark.parametrize("mpgs", [MPGS, [m * 1.2 for m in MPGS]])  # under and over budget
def test_alpha_one_meets_the_budget_exactly(beta, mpgs):
    df = _team([min(m, 40.0) for m in mpgs])
    p = TM.TeamMinutesParams(alpha=1.0, beta=beta, unprojected_share=0.05)
    out, rep = TM.constrain(df, df["share"], p)
    assert (out["share"] * out["mpg"]).sum() == pytest.approx(TM.MINUTES_PER_TEAM_GAME * 0.95, rel=1e-9)
    assert rep["teams"]["AAA"]["after"] == pytest.approx(TM.MINUTES_PER_TEAM_GAME * 0.95, abs=0.06)
    # per-minute rates and percentages unchanged, counting stats follow minutes
    assert np.allclose(out["pts"] / out["mpg"], 0.5) and np.allclose(out["fgm"] / out["fga"], 0.5)
    assert np.allclose(out["team_min_scale"], out["mpg"] / df["mpg"], atol=1e-4)


def test_partial_alpha_closes_that_share_of_the_gap():
    df = _team([m * 1.2 for m in MPGS])
    e0 = float((df["share"] * df["mpg"]).sum())
    target = TM.MINUTES_PER_TEAM_GAME
    out, _ = TM.constrain(df, df["share"], TM.TeamMinutesParams(alpha=0.5))
    assert (out["share"] * out["mpg"]).sum() == pytest.approx(e0 + 0.5 * (target - e0))


def test_beta_zero_is_proportional_and_beta_tilts_toward_the_bench():
    df = _team([m * 1.2 for m in MPGS])
    prop, _ = TM.constrain(df, df["share"], TM.TeamMinutesParams(beta=0.0))
    r = prop["mpg"] / df["mpg"]
    assert np.allclose(r, r.iloc[0]) and r.iloc[0] < 1
    tilt, _ = TM.constrain(df, df["share"], TM.TeamMinutesParams(beta=2.0))
    rt = tilt["mpg"] / df["mpg"]
    assert rt.is_monotonic_decreasing  # the lower his minutes, the larger his relative cut (rows sorted by mpg desc)
    assert rt.iloc[0] > r.iloc[0] > rt.iloc[-1]  # the star keeps more, the deep bench loses more than proportional


def test_trim_leaves_under_budget_teams_and_small_teams_alone():
    under = _team(MPGS)
    out, _ = TM.constrain(under, under["share"], TM.TeamMinutesParams(direction="trim"))
    assert np.allclose(out["mpg"], under["mpg"])
    both, _ = TM.constrain(under, under["share"], TM.TeamMinutesParams(direction="both"))
    assert (both["mpg"] > under["mpg"]).all()
    small = _team([30.0, 30.0, 30.0], team="BBB")
    out, rep = TM.constrain(small, small["share"], TM.TeamMinutesParams())
    assert np.allclose(out["mpg"], 30.0) and "BBB" not in rep["teams"]


def test_teams_are_independent_and_mpg_stays_in_bounds():
    df = pd.concat([_team([m * 1.2 for m in MPGS], "AAA"), _team(MPGS, "BBB", share=1.0)], ignore_index=True)
    out, rep = TM.constrain(df, df["share"], TM.TeamMinutesParams(beta=4.0))
    for t in ("AAA", "BBB"):
        m = out["team"] == t
        assert (out.loc[m, "share"] * out.loc[m, "mpg"]).sum() == pytest.approx(TM.MINUTES_PER_TEAM_GAME)
    assert (out["mpg"] >= 0).all() and (out["mpg"] <= TM.MPG_CAP).all()
    # a huge cut cannot push anyone below 0: the clipped minutes are redistributed
    big = _team([40.0] * 12)
    out, _ = TM.constrain(big, big["share"].clip(upper=1) * 0 + 1.0, TM.TeamMinutesParams(beta=4.0))
    assert (out["mpg"] >= 0).all() and out["mpg"].sum() == pytest.approx(TM.MINUTES_PER_TEAM_GAME)


# ------------------------------------------------------------------------------------- in-season integration
def _sched(team: str, n: int, start: str = "2026-10-20") -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="2D").strftime("%Y-%m-%d")
    return pd.DataFrame({"game_id": [f"{team}{i}" for i in range(n)], "game_date": dates})


def _prior_row(pid, mpg=30.0, team="AAA"):
    return {"player_id": pid, "name": pid.title(), "team": team, "positions": "PG", "gp": 70.0, "gp_sd": 10.0,
            "mpg": mpg, "fgm": 6.0, "fga": 13.0, "ftm": 3.0, "fta": 4.0, "fg3m": 2.0, "pts": 17.0, "reb": 5.0,
            "ast": 5.0, "stl": 1.0, "blk": 0.5, "tov": 2.0, "status": "healthy", "playoff_mult": 1.0}


def _games(sched, pid, name=None, **stats):
    base = {"min": 24.0, "pts": 14.0, "fgm": 5.0, "fga": 10.0, "fg3m": 1.0, "fg3a": 3.0, "ftm": 3.0, "fta": 4.0,
            "reb": 5.0, "ast": 2.0, "stl": 1.0, "blk": 0.5, "tov": 1.0}
    base.update(stats)
    return sched.assign(team="AAA", player_id=pid, player_name=name or pid, regular_season=True, played=True,
                        **base)


def test_unprojected_box_score_players_get_population_prior_rows():
    sched = _sched("AAA", 20)
    tg = sched.assign(team="AAA", regular_season=True)
    # Hezonja-style: plays from game 9 on (a later signing), no projection row
    rows = pd.concat([_games(sched, "p1"), _games(sched.iloc[8:], "mario-hezonja", "Mario Hezonja")])
    prior = pd.DataFrame([_prior_row("p1")])
    out, summ = I.update_projections(prior, rows, tg, "2027-01-01", positions={"mario-hezonja": "SF|PF"})
    assert summ.unmatched == ["mario-hezonja"]
    h = out.set_index("player_id").loc["mario-hezonja"]
    assert h["prior_source"] == "population" and h["source"] == I.POP_SOURCE
    assert h["name"] == "Mario Hezonja" and h["team"] == "AAA" and h["positions"] == "SF|PF"
    assert h["games_played"] == 12 and h["team_games"] == 12  # timeline starts at his first listing
    # observed 24 mpg / 14 pts pull far from the population prior (14.9 mpg) with the small population n0
    assert 18 < h["mpg"] < 26 and h["pts"] > 9
    assert h["fg3m"] <= h["fgm"] <= h["fga"]
    assert out.set_index("player_id").loc["p1", "prior_source"] == "projection"
    assert "pop-prior" in h["inseason_version"]
    off, s_off = I.update_projections(prior, rows, tg, "2027-01-01", population_prior=False)
    assert "mario-hezonja" not in set(off["player_id"]) and s_off.unmatched == ["mario-hezonja"]
    assert "pop-prior" not in off["inseason_version"].iloc[0]


def test_population_shrink_uses_the_population_n0():
    prior = pd.Series(I.population_rows(
        _games(_sched("AAA", 1), "x").assign(played=True), set()).iloc[0])
    tot = pd.Series({c: 0.0 for c in I.COUNT_COLS}) + pd.Series({"min": 100.0, "reb": 20.0})
    full = I.shrink_line(prior, tot, 5.0, mpg=20.0)
    pop = I.shrink_line(prior, tot, 5.0, mpg=20.0, n0=I.POP_N0)
    # observed 0.2 reb/min vs prior 0.173: the population n0 (smaller) moves further toward the observed rate
    if I.POP_N0_MULT < 1:
        assert pop["reb"] > full["reb"]
    assert pop["reb"] <= 0.2 * 20.0 + 1e-9


def test_team_budget_in_update_projections():
    sched = _sched("AAA", 10)
    tg = sched.assign(team="AAA", regular_season=True)
    prior = pd.DataFrame([_prior_row(f"p{i}", mpg=m) for i, m in enumerate([m * 1.2 for m in MPGS])])
    rows = pd.concat([_games(sched, f"p{i}", min=m * 1.2) for i, m in enumerate(MPGS)])
    p = TM.TeamMinutesParams(alpha=1.0, beta=2.0, unprojected_share=0.02)
    out, summ = I.update_projections(prior, rows, tg, "2027-01-01", team_minutes=p)
    assert (out["ros_share"] * out["mpg"]).sum() == pytest.approx(TM.MINUTES_PER_TEAM_GAME * 0.98, rel=2e-3)
    assert summ.team_minutes["teams"]["AAA"]["before"] > summ.team_minutes["teams"]["AAA"]["after"]
    assert TM.TEAM_MINUTES_VERSION in out["inseason_version"].iloc[0]
    assert np.allclose(out["mpg_next5"] / out["mpg"], 1.0, atol=0.2)  # scaled together


def test_hezonja_style_unknown_id_resolves_once_he_has_a_row():
    from fantasy.data.ids import canonical_id
    from fantasy.league.sync import PlayerResolver

    # ESPN box-score name and Yahoo roster name slug to the same id
    assert canonical_id("Mario Hezonja") == "mario-hezonja"
    proj = pd.DataFrame([{"player_id": "mario-hezonja", "name": "Mario Hezonja", "team": "CLE"},
                         {"player_id": "p1", "name": "P One", "team": "AAA"}]).set_index("player_id")
    # previous state mapped his Yahoo id to unknown:<slug> (no row then): it re-resolves by name now
    r = PlayerResolver(proj, {5465: "unknown:mario-hezonja"}).resolve("Mario Hezonja", "CLE", 5465)
    assert r.player_id == "mario-hezonja" and r.method == "name"
    r = PlayerResolver(proj.drop("mario-hezonja"), {5465: "unknown:mario-hezonja"}).resolve("Mario Hezonja", "CLE", 5465)
    assert r.player_id == "unknown:mario-hezonja"


def test_population_player_on_two_teams_uses_his_latest_team():
    a, b = _sched("AAA", 20), _sched("BBB", 20, start="2026-10-21")
    tg = pd.concat([a.assign(team="AAA"), b.assign(team="BBB")]).assign(regular_season=True)
    rows = pd.concat([_games(a.iloc[2:8], "x-player", "X Player"),
                      _games(b.iloc[12:18], "x-player", "X Player").assign(team="BBB")])
    out, summ = I.update_projections(pd.DataFrame([_prior_row("p1")]), rows, tg, "2027-01-01")
    x = out.set_index("player_id").loc["x-player"]
    assert x["team"] == "BBB" and x["games_played"] == 12 and x["prior_source"] == "population"
    # listed in AAA's game 3 (< ENTRY_GRACE): AAA from game 1 until his first BBB listing (13 games), then BBB 13..20
    assert x["team_games"] == 13 + 8
    assert (out["player_id"] == "x-player").sum() == 1
    assert summ.population_summary.startswith("population rows: 1 (top by mpg: x-player")


def test_population_row_matching_an_unlisted_projected_player_warns():
    sched = _sched("AAA", 10)
    tg = sched.assign(team="AAA", regular_season=True)
    prior = pd.DataFrame([_prior_row("p1"), {**_prior_row("gg-jackson-ii"), "name": "GG Jackson II"}])
    rows = pd.concat([_games(sched, "p1"), _games(sched, "gg-jackson-x", "G.G. Jackson")])
    _, summ = I.update_projections(prior, rows, tg, "2027-01-01")
    assert any("possible id mismatch" in w and "gg-jackson-ii" in w for w in summ.warnings)


def test_zero_share_rows_untouched_and_apply_to_file_is_idempotent(tmp_path):
    import json

    df = _team([m * 1.2 for m in MPGS]).assign(gp=0.8 * 82)
    df.loc[0, "gp"] = 0.0  # out for the season
    path = tmp_path / "p.csv"
    df.drop(columns="share").to_csv(path, index=False)
    (tmp_path / "p.meta.json").write_text(json.dumps({"model_version": "m", "sha256": "x"}))
    meta = TM.apply_to_file(path, TM.TeamMinutesParams(alpha=1.0, unprojected_share=0.15))
    once = pd.read_csv(path)
    assert once.loc[0, "mpg"] == pytest.approx(df.loc[0, "mpg"], abs=0.051)
    assert once.loc[1, "mpg"] < df.loc[1, "mpg"] and meta["model_version"] == f"m+{TM.TEAM_MINUTES_VERSION}"
    TM.apply_to_file(path, TM.TeamMinutesParams(alpha=1.0, unprojected_share=0.15))
    assert pd.read_csv(path).equals(once)
