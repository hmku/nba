"""Recency-weighted minutes model (fantasy.model.minutes) and its use in the in-season update. Synthetic data only."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from fantasy.model import inseason as I
from fantasy.model import minutes as M


def _old(minutes, prior):  # pre-0.5.0 equal-weight shrink
    n0 = I.STAT_N0["mpg"]
    return (n0 * prior + float(np.sum(minutes))) / (n0 + len(minutes))


HORIZONS = ["next5", "ros"]


def test_old_shrink_constant_matches_stat_n0():
    assert M.OLD_SHRINK_N0 == I.STAT_N0["mpg"]


@pytest.mark.parametrize("h", HORIZONS)
def test_no_games_is_the_prior(h):
    p = M.default_params(h)
    assert M.project_mpg([], 23.4, p) == pytest.approx(23.4)
    f = M.feature_panel([30.0, 31.0], 23.4, p)
    assert all(f.loc[0, f"e_{x:g}"] == pytest.approx(23.4) for x in p.half_lives)
    assert M.predict(f.iloc[[0]], p)[0] == pytest.approx(23.4)


def test_next5_has_no_intercept_and_keeps_a_steady_player_at_his_prior():
    # no intercept and coefficients summing to ~1: a player playing exactly his prior is projected at his prior
    p = M.default_params("next5")
    assert p.const == 0.0 and p.as_dict()["coef_sum"] == pytest.approx(1.0, abs=0.01)
    assert M.project_mpg([25.0] * 30, 25.0, p) == pytest.approx(25.0, abs=0.3)


@pytest.mark.parametrize("h", HORIZONS)
def test_role_jump_moves_more_than_the_old_formula(h):
    p = M.default_params(h)
    hist = [15.0] * 37 + [30.0] * 3  # bench role, then 3 games at 30 mpg
    new, old = M.project_mpg(hist, 15.0, p), _old(hist, 15.0)
    assert old < 16.5 < 20.0 < new < 30.0
    # and the reverse: a role loss
    drop = [32.0] * 37 + [18.0] * 3
    assert M.project_mpg(drop, 32.0, p) < _old(drop, 32.0) - 3


def test_fallback_uses_old_shrink_for_early_games():
    p = M.MinutesParams(half_lives=(1.5, 10.0), b_ewma=(0.5, 0.5), b_prior=0.0, fallback_t=5)
    hist = [30.0, 32.0, 28.0]
    assert M.project_mpg(hist, 15.0, p) == pytest.approx(_old(hist, 15.0))
    hist6 = hist + [30.0, 30.0, 30.0]
    assert M.project_mpg(hist6, 15.0, p) != pytest.approx(_old(hist6, 15.0))


def test_shrink_term_and_intercept_enter_the_prediction():
    p = M.MinutesParams(half_lives=(2.0,), b_ewma=(0.0,), b_prior=0.0, const=1.0, shrink_n0=4.0, b_shrink=1.0)
    assert M.project_mpg([20.0, 20.0], 10.0, p) == pytest.approx(1.0 + (40.0 + 40.0) / 6.0)


def test_ewma_matches_closed_form():
    m = np.array([10.0, 20.0, 30.0, 25.0])
    a = M.decay(2.0)
    e = M.ewma(m, 15.0, 2.0)
    t = len(m)
    closed = a**t * 15.0 + (1 - a) * sum(a ** (t - 1 - i) * m[i] for i in range(t))
    assert e[-1] == pytest.approx(closed)


def test_no_look_ahead_in_feature_builder():
    rng = np.random.default_rng(0)
    m = rng.uniform(5, 40, 30)
    p = M.default_params("ros")
    base = M.feature_panel(m, 22.0, p)
    for t in (0, 1, 7, 20, 29):
        alt = m.copy()
        alt[t:] = rng.uniform(0, 48, len(m) - t)  # change games t.. (the future as of row t)
        f = M.feature_panel(alt, 22.0, p)
        pd.testing.assert_frame_equal(base.iloc[: t + 1], f.iloc[: t + 1])
        assert np.allclose(M.predict(base.iloc[: t + 1], p), M.predict(f.iloc[: t + 1], p))


def test_unknown_horizon_raises():
    with pytest.raises(ValueError):
        M.default_params("bogus")


# ------------------------------------------------------------------------------------------- in-season update
def _setup(minutes):
    n = len(minutes)
    dates = pd.date_range("2026-10-20", periods=n, freq="2D").strftime("%Y-%m-%d")
    sched = pd.DataFrame({"game_id": [f"G{i}" for i in range(n)], "game_date": dates})
    tg = sched.assign(team="AAA", regular_season=True)
    pg = sched.assign(team="AAA", player_id="p1", regular_season=True, played=True, min=minutes, pts=10.0,
                      fgm=4.0, fga=9.0, fg3m=1.0, fg3a=3.0, ftm=1.0, fta=1.5, reb=4.0, ast=2.0, stl=0.7, blk=0.3,
                      tov=1.0)
    prior = pd.DataFrame([{"player_id": "p1", "name": "P", "team": "AAA", "positions": "SF", "gp": 70.0,
                           "gp_sd": 10.0, "mpg": 15.0, "fgm": 2.4, "fga": 5.5, "ftm": 0.8, "fta": 1.0, "fg3m": 0.7,
                           "pts": 6.3, "reb": 2.5, "ast": 1.2, "stl": 0.4, "blk": 0.2, "tov": 0.6,
                           "status": "healthy", "playoff_mult": 1.0},
                          {"player_id": "p2", "name": "Q", "team": "AAA", "positions": "C", "gp": 70.0,
                           "gp_sd": 10.0, "mpg": 21.0, "fgm": 3.0, "fga": 6.0, "ftm": 1.0, "fta": 1.5, "fg3m": 0.1,
                           "pts": 7.1, "reb": 6.0, "ast": 1.0, "stl": 0.5, "blk": 1.0, "tov": 1.0,
                           "status": "healthy", "playoff_mult": 1.0}])
    return prior, pg, tg, dates[-1]


def test_update_uses_minutes_model_and_keeps_rates():
    minutes = [15.0] * 37 + [30.0] * 3
    prior, pg, tg, as_of = _setup(minutes)
    new, _ = I.update_projections(prior, pg.sample(frac=1, random_state=1), tg, as_of)  # row order irrelevant
    old, _ = I.update_projections(prior, pg, tg, as_of, minutes_model="shrink")
    n1, o1 = new.set_index("player_id").loc["p1"], old.set_index("player_id").loc["p1"]
    assert n1["mpg"] == pytest.approx(M.project_mpg(minutes, 15.0, M.default_params("ros")), abs=1e-3)
    assert n1["mpg_next5"] == pytest.approx(M.project_mpg(minutes, 15.0, M.default_params("next5")), abs=1e-3)
    assert o1["mpg"] == pytest.approx(_old(minutes, 15.0), abs=1e-3) == o1["mpg_next5"]
    assert n1["mpg"] > o1["mpg"] + 4
    # per-minute rates unchanged: every rate stat scales with mpg
    for s in I.RATE_STATS:
        assert n1[s] / n1["mpg"] == pytest.approx(o1[s] / o1["mpg"], rel=1e-2)  # output rounded to 0.001
    # no games -> preseason mpg either way
    p2 = new.set_index("player_id").loc["p2"]
    assert p2["mpg"] == pytest.approx(21.0) == p2["mpg_next5"]
    # the version string depends on the minutes model (review blocker): shrink = the 0.4.0 model, no minutes part
    assert I.inseason_model_version("ewma") == I.INSEASON_MODEL_VERSION
    assert (new["inseason_version"] == I.INSEASON_MODEL_VERSION).all()
    assert (old["inseason_version"] == I.inseason_model_version("shrink")).all()
    assert M.MINUTES_MODEL_VERSION in I.INSEASON_MODEL_VERSION and "ros-proj-0.5.0" in I.INSEASON_MODEL_VERSION
    assert I.inseason_model_version("shrink").startswith("ros-proj-0.4.0") and "minutes" not in \
        I.inseason_model_version("shrink")
    with pytest.raises(ValueError):
        I.update_projections(prior, pg, tg, as_of, minutes_model="bogus")


def test_write_inseason_records_minutes_model(tmp_path, monkeypatch):
    minutes = [20.0] * 10
    prior, pg, tg, as_of = _setup(minutes)
    prior_path = tmp_path / "prior.csv"
    prior.to_csv(prior_path, index=False)
    pg_path, tg_path = tmp_path / "pg.csv", tmp_path / "tg.csv"
    pg.to_csv(pg_path, index=False)
    tg.to_csv(tg_path, index=False)
    import fantasy.data.boxscores as B
    import fantasy.model.projections as P

    monkeypatch.setattr(B, "load_player_games", lambda season: pg)
    monkeypatch.setattr(B, "load_team_games", lambda season: tg)
    monkeypatch.setattr(B, "player_games_path", lambda season: pg_path)
    monkeypatch.setattr(B, "team_games_path", lambda season: tg_path)
    ov = tmp_path / "ov.csv"
    pd.DataFrame({"player_id": [], "games_missed": []}).to_csv(ov, index=False)
    monkeypatch.setattr(P, "load_manual", lambda name: pd.read_csv(ov))
    monkeypatch.setattr(I, "load_injury_feed", lambda as_of: (None, "no feed"))
    monkeypatch.setattr(I, "playoff_share_remaining", lambda as_of: (0.15, 0.15))
    monkeypatch.setattr(I, "fantasy_season_games", lambda end=None: {"AAA": 70})
    import fantasy.league.settings as S

    monkeypatch.setattr(S, "REPO_ROOT", tmp_path)
    (tmp_path / "data" / "manual").mkdir(parents=True)
    pd.DataFrame({"player_id": [], "games_missed": []}).to_csv(
        tmp_path / "data" / "manual" / "player_overrides_2026_27.csv", index=False)
    for mm in ("ewma", "shrink"):
        out = tmp_path / f"out_{mm}.csv"
        meta = I.write_inseason(as_of, prior_path=prior_path, out_path=out, minutes_model=mm)
        disk = json.loads(out.with_name(out.stem + ".meta.json").read_text())
        assert disk["model_version"] == I.inseason_model_version(mm) == meta["model_version"]
        assert (pd.read_csv(out)["inseason_version"] == I.inseason_model_version(mm)).all()
        assert disk["minutes"]["model"] == mm
    assert meta["minutes"]["n0_games"] == I.STAT_N0["mpg"]
    ew = json.loads((tmp_path / "out_ewma.meta.json").read_text())["minutes"]
    assert ew["version"] == M.MINUTES_MODEL_VERSION
    assert ew["mpg"]["params"]["const"] == M.default_params("ros").const
    assert ew["mpg_next5"]["params"]["half_lives"] == list(M.default_params("next5").half_lives)
