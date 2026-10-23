"""ros-proj-0.4.0 review fixes (fantasy.model.inseason): early damping, sd continuity, fantasy-season window,
silent-feed status, ET feed age, id collisions. Synthetic data only."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from test_inseason_update import _empty, _games, _prior_row, _sched

from fantasy.model import inseason as I


def _avail(p0, k, x, streak, dnp=0, rem=60, sd0=0.25):
    mu, sd = I.ros_availability([p0], [sd0], [k], [x], [streak], [dnp], [rem])
    return mu[0], sd[0]


def _undamped(p0, k, x, streak, dnp=0, rem=60, sd0=0.25):
    o, d = x / k, dnp / k
    m, v = I._knot(k, *(np.array([a], float) for a in (p0, sd0, o, streak, x, d, rem)))
    return m[0], v[0]


def test_first_games_are_damped_toward_the_prior():
    p0 = 0.72
    for k, x, streak in ((1, 1, 0), (1, 0, 1), (3, 3, 0), (3, 0, 3)):
        mu, _ = _avail(p0, k, x, streak)
        fit, _ = _undamped(p0, k, x, streak)
        assert mu == pytest.approx(p0 + k / I.EARLY_K * (fit - p0), abs=1e-9)
    # one game moves gp (82 scale) by at most a few games, not ~13 as the raw fit did
    assert abs(_avail(p0, 1, 1, 0)[0] - p0) * 82 < 4 and abs(_avail(p0, 1, 0, 1)[0] - p0) * 82 < 6
    # from EARLY_K on: the fitted update itself
    k = I.EARLY_K
    assert _avail(p0, k, 2, 0)[0] == pytest.approx(_undamped(p0, k, 2, 0)[0])


def test_sd_does_not_collapse_after_one_game():
    sd0 = 17.3 / 82
    _, sd1 = _avail(0.6, 1, 1, 0, sd0=sd0)
    _, sd_fit = _undamped(0.6, 1, 1, 0, sd0=sd0)
    assert sd1**2 == pytest.approx(sd0**2 + (sd_fit - sd0**2) / I.EARLY_K)
    assert sd1 > 0.95 * sd0
    assert _avail(0.6, 0, 0, 0, sd0=sd0)[1] == pytest.approx(sd0)


def test_remaining_games_run_to_the_fantasy_end_from_the_schedule():
    sched = _sched("AAA", 10)
    tg = sched.assign(team="AAA", regular_season=True)
    out, _ = I.update_projections(pd.DataFrame([_prior_row(gp=60.0)]), _games(sched), tg, "2027-06-01",
                                  fantasy_games={"AAA": 68})
    r = out.iloc[0]
    assert r["ros_team_games"] == 58  # 68 scheduled through the fantasy end - 10 completed
    assert r["fantasy_gp_exp"] == pytest.approx(10 + r["ros_games"], abs=0.11)
    assert r["season_gp_exp"] == pytest.approx(10 + r["ros_share"] * 72, abs=0.11)  # 82-game total
    assert r["gp"] == pytest.approx(r["ros_share"] * 82, abs=0.06)  # a rate, not a count


def test_fantasy_season_games_counts_schedule(tmp_path, monkeypatch):
    from fantasy.data import provenance

    d = tmp_path / "bbref_schedule"
    d.mkdir()
    pd.DataFrame({"date": ["2027-03-20", "2027-03-21", "2027-03-22"], "visitor": ["AAA", "BBB", "AAA"],
                  "home": ["BBB", "AAA", "CCC"]}).to_csv(d / "nba_2027_games.csv", index=False)
    monkeypatch.setattr(provenance, "RAW_DIR", tmp_path)
    assert I.fantasy_season_games("2027-03-21") == {"AAA": 2, "BBB": 2}


def test_silent_feed_keeps_preseason_out_while_window_lasts():
    sched = _sched("AAA", 5)
    tg = sched.assign(team="AAA", regular_season=True)
    prior = pd.DataFrame([_prior_row(gp=50.0, status="out", injury_note="Knee: out 3-4 weeks"),
                          _prior_row(player_id="p2", gp=50.0, status="out")])
    feed = pd.DataFrame({"player_id": ["zz"], "status": ["out"], "injury_note": ["x"]})
    out, _ = I.update_projections(prior, _empty(sched), tg, "2027-06-01", games_missed={"p1": 12.0, "p2": 3.0},
                                  injuries=feed)
    out = out.set_index("player_id")
    assert out.loc["p1", "status"] == "out" and out.loc["p1", "injury_note"].startswith("Knee")  # 7 games to go
    assert out.loc["p2", "status"] == "healthy"  # window over, feed silent


def test_injury_feed_age_uses_eastern_date(tmp_path, monkeypatch):
    from fantasy.data import provenance

    d = tmp_path / "espn"
    d.mkdir()
    (d / "injuries.json").write_text(json.dumps({"injuries": [{"displayName": "AAA", "injuries": [
        {"athlete": {"displayName": "P One", "team": {"abbreviation": "AAA"}}, "status": "Out",
         "details": {"type": "Ankle"}, "date": "2026-11-01T00:00Z", "shortComment": "out"}]}]}))
    monkeypatch.setattr(provenance, "RAW_DIR", tmp_path)
    # 02:00 UTC on Nov 2 is 22:00 ET on Nov 1: same ET day as as_of (the UTC date said "fetched tomorrow")
    monkeypatch.setattr(provenance, "read_provenance", lambda p: {"fetched_at": "2026-11-02T02:00:00Z"})
    assert I.load_injury_feed("2026-11-01")[0] is not None
    assert I.load_injury_feed("2026-10-31")[0] is not None  # fetched the morning after as_of: allowed
    assert I.load_injury_feed("2026-10-30")[0] is None  # 2 days in the future: would leak


def test_id_collisions_warn_and_rows_dedupe():
    sched = _sched("AAA", 4)
    tg = sched.assign(team="AAA", regular_season=True)
    a = _games(sched).assign(espn_player_id="1")
    b = _games(sched, min=10.0).assign(espn_player_id="2")  # a namesake: same slug, other ESPN id
    out, summ = I.update_projections(pd.DataFrame([_prior_row()]), pd.concat([a, b]), tg, "2027-06-01")
    assert any("collisions" in w and "p1" in w for w in summ.warnings)
    r = out.iloc[0]
    assert r["games_played"] == 4 and r["team_games"] == 4  # one row per game, not 8
