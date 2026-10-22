"""In-season projection update (fantasy.model.inseason): synthetic data only."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fantasy.model import inseason as I


def _sched(team: str, n: int, start: str = "2026-10-20") -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="2D").strftime("%Y-%m-%d")
    return pd.DataFrame({"game_id": [f"{team}{i}" for i in range(n)], "game_date": dates})


def _avail(p0, k, x, streak, dnp=0, rem=None, sd0=0.25):
    rem = 82 - k if rem is None else rem
    mu, sd = I.ros_availability([p0], [sd0], [k], [x], [streak], [dnp], [rem])
    return mu[0], sd[0]


def test_constants_cover_checkpoints():
    assert 0 in I.ROS_AVAIL_COEF and set(I.ROS_AVAIL_COEF) == set(I.ROS_AVAIL_VAR)
    assert all(set(c) == set(I.FEATURES) for c in I.ROS_AVAIL_COEF.values())
    assert all(v["a"] >= 0 and v["c"] >= 0 and v["d"] >= 0 for v in I.ROS_AVAIL_VAR.values())
    assert {"mpg", "fg_pct", "ft_pct", *I.RATE_STATS} <= set(I.STAT_N0)
    assert all(v > 0 for v in I.STAT_N0.values())


def test_k0_is_the_prior_and_k1_changes_are_information_only():
    m0, s0 = _avail(0.5, 0, 0, 0, sd0=0.2)
    assert m0 == pytest.approx(0.5) and s0 == pytest.approx(0.2)
    # the fitted level shift is removed at k >= 1: playing / missing game 1 moves the prior in opposite directions
    played, _ = _avail(0.5, 1, 1, 0)
    missed, _ = _avail(0.5, 1, 0, 1)
    dressed, _ = _avail(0.5, 1, 0, 1, dnp=1)
    assert missed < 0.5 < played
    assert missed < dressed  # a coach's-decision DNP is milder than not being in the box score at all


def test_dnp_share_is_the_players_own_between_checkpoints():
    # dressed for every game but never played (dnp share = 1): bounded and smooth in k (the old bug divided dnp by
    # the checkpoint's k instead of the player's, giving shares > 1 and jumps between checkpoints)
    mus = np.array([_avail(0.6, k, 0, k, dnp=k)[0] for k in range(1, 61)])
    assert np.all((mus >= 0) & (mus <= 1))
    assert np.abs(np.diff(mus)).max() < 0.1


def test_playing_every_game_raises_and_missing_lowers():
    up, _ = _avail(38.5 / 82, 20, 20, 0)
    down, _ = _avail(38.5 / 82, 20, 0, 20)
    assert up > 38.5 / 82 > down
    assert up < 0.9  # not an iid Beta-binomial: a 20-for-20 start does not imply ~full availability


def test_ongoing_absence_lowers_more_than_early_absence_and_long_absence_is_uncertain():
    ongoing, sd_on = _avail(0.7, 20, 15, 5)
    over, sd_over = _avail(0.7, 20, 15, 0)
    assert ongoing < over
    _, sd_long = _avail(0.7, 40, 10, 30)
    _, sd_short = _avail(0.7, 40, 10, 0)
    assert sd_long > sd_short  # variance grows with the current streak


def test_player_timeline_rules():
    sched = {"AAA": _sched("AAA", 30), "BBB": _sched("BBB", 30, start="2026-10-21")}
    # on AAA from opening night (start team), listed only from game 3, traded to BBB at its game 20
    a_rows = sched["AAA"].iloc[2:15].assign(team="AAA", played=True)
    b_rows = sched["BBB"].iloc[19:25].assign(team="BBB", played=[True, False, True, True, True, True])
    tl = I.player_timeline(pd.concat([b_rows, a_rows]), sched, "AAA")
    assert tl["team"].iloc[0] == "AAA" and tl["game_id"].iloc[0] == "AAA0"
    assert (tl["team"] == "BBB").sum() == 11  # BBB games 20..30
    assert tl.loc[tl["team"] == "AAA", "game_date"].max() < b_rows["game_date"].min()
    k, x, streak, dnp = I.timeline_features(tl)
    assert x == 13 + 5 and streak == 30 - 25 and dnp == 1  # dressed for the BBB game he did not play
    late = sched["BBB"].iloc[11:13].assign(team="BBB", played=True)  # mid-season signing
    assert len(I.player_timeline(late, sched, "AAA")) == 30 - 11
    none = I.player_timeline(None, sched, "AAA")
    assert len(none) == 30 and not none["played"].any() and not none["listed"].any()


def _prior_row(**kw) -> dict:
    r = {"player_id": "p1", "name": "P One", "team": "AAA", "positions": "PG", "gp": 60.0, "gp_sd": 15.0,
         "mpg": 30.0, "fgm": 6.0, "fga": 13.0, "ftm": 3.0, "fta": 4.0, "fg3m": 2.0, "pts": 17.0, "reb": 5.0,
         "ast": 5.0, "stl": 1.0, "blk": 0.5, "tov": 2.0, "status": "healthy", "playoff_mult": 0.95}
    r.update(kw)
    return r


def test_shrink_line_limits_and_identity():
    prior = pd.Series(_prior_row())
    zero = pd.Series({c: 0.0 for c in I.COUNT_COLS})
    same = I.shrink_line(prior, zero, 0.0)
    for s in I.STATS:
        assert same[s] == pytest.approx(prior[s], rel=1e-6)
    g = 1e6
    obs = pd.Series({"min": 20 * g, "fgm": 4 * g, "fga": 8 * g, "ftm": 1 * g, "fta": 2 * g, "fg3m": 0.0,
                     "fg3a": 0.0, "reb": 10 * g, "ast": 1 * g, "stl": 0.5 * g, "blk": 2 * g, "tov": 1 * g,
                     "pts": 9 * g})
    big = I.shrink_line(prior, obs, g)
    assert big["mpg"] == pytest.approx(20, rel=1e-3)
    assert big["reb"] == pytest.approx(10, rel=1e-3)
    assert big["fgm"] / big["fga"] == pytest.approx(0.5, rel=1e-3)


def _games(sched, team="AAA", pid="p1", n=None, **stats):
    s = sched.iloc[: (len(sched) if n is None else n)]
    base = {"min": 34.0, "pts": 25.0, "fgm": 9.0, "fga": 17.0, "fg3m": 3.0, "fg3a": 7.0, "ftm": 4.0, "fta": 5.0,
            "reb": 6.0, "ast": 7.0, "stl": 1.5, "blk": 0.5, "tov": 3.0}
    base.update(stats)
    return s.assign(team=team, player_id=pid, regular_season=True, played=True, **base)


def _empty(sched):
    return _games(sched).iloc[:0]


def test_update_projections_end_to_end():
    sched = _sched("AAA", 20)
    tg = sched.assign(team="AAA", regular_season=True)
    prior = pd.DataFrame([_prior_row(gp=38.5, gp_sd=24.0, status="out"), _prior_row(player_id="p2", name="P Two")])
    out, summ = I.update_projections(prior, _games(sched), tg, "2027-01-01")
    p1, p2 = out.set_index("player_id").loc["p1"], out.set_index("player_id").loc["p2"]
    assert summ.updated == 1 and summ.max_team_games == 20 and summ.unlisted == ["p2"]
    assert p1["games_played"] == 20 and p1["team_games"] == 20 and p1["ros_team_games"] == 62
    assert p1["gp"] > 38.5 and p1["gp_preseason"] == 38.5
    assert 30 < p1["mpg"] < 34 and p1["pts"] > 17
    assert p1["status"] == "healthy" and p1["status_preseason"] == "out"  # back from injury: status cleared
    assert p2["games_played"] == 0 and p2["missed_streak"] == 20 and p2["gp"] < 60
    assert p2["pts"] == pytest.approx(17.0)
    early, _ = I.update_projections(prior, _games(sched), tg, sched["game_date"].iloc[4])
    assert early.set_index("player_id").loc["p1", "team_games"] == 5


def test_before_any_game_the_preseason_projection_is_unchanged():
    sched = _sched("AAA", 5)
    tg = sched.assign(team="AAA", regular_season=True)
    m = 20
    prior = pd.DataFrame([_prior_row(gp=60.0, gp_sd=15.0),
                          _prior_row(player_id="p2", gp=60 * (82 - m) / 82, gp_sd=15.0 + 0.15 * m),
                          _prior_row(player_id="p3", gp=0.0, gp_sd=0.0)])
    out, _ = I.update_projections(prior, _empty(sched), tg, "2026-01-01", games_missed={"p2": m})
    out = out.set_index("player_id")
    for pid, gp, sd in (("p1", 60.0, 15.0), ("p2", round(60 * 62 / 82, 1), 18.0)):
        assert out.loc[pid, "team_games"] == 0
        assert out.loc[pid, "gp"] == pytest.approx(gp, abs=0.051)
        assert out.loc[pid, "gp_sd"] == pytest.approx(sd, abs=0.051)
    assert out.loc["p3", "gp_sd"] == I.GP_SD_FLOOR


def test_expected_absence_window_is_not_news_and_keeps_return_uncertainty():
    sched = _sched("AAA", 10)
    tg = sched.assign(team="AAA", regular_season=True)
    m = 20
    prior = pd.DataFrame([_prior_row(gp=60 * (82 - m) / 82, gp_sd=15.0 + 0.15 * m)])
    out, _ = I.update_projections(prior, _empty(sched), tg, "2027-06-01", games_missed={"p1": m})
    r = out.iloc[0]
    # 10 games missed inside the window: no news; of the 72 left, 10 more out + 62 at the healthy rate
    assert r["season_gp_exp"] == pytest.approx(60 / 82 * 62, abs=0.1)
    assert r["gp_sd"] > 0.15 * 10 * 82 / 72 - 1e-9  # return-timing sd of the 10 window games still to come
    plain, _ = I.update_projections(prior, _empty(sched), tg, "2027-06-01")
    assert plain.iloc[0]["season_gp_exp"] < r["season_gp_exp"]  # without the window the absence is news
    # day-to-day skip: the window is skipped but the prior is NOT rescaled
    skip, _ = I.update_projections(prior, _empty(sched), tg, "2027-06-01", skip_window={"p1": m})
    assert skip.iloc[0]["season_gp_exp"] < r["season_gp_exp"]
    # skip_window does not rescale: before any game the gtd player keeps ~ his preseason gp (minus the 1 game)
    pre, _ = I.update_projections(prior, _empty(sched), tg, "2026-01-01", skip_window={"p1": 1})
    assert pre.iloc[0]["gp"] == pytest.approx(prior.iloc[0]["gp"] * 81 / 82, abs=0.06)


def test_traded_player_uses_new_team_and_injury_feed_sets_status():
    a, b = _sched("AAA", 20), _sched("BBB", 12, start="2026-10-21")
    tg = pd.concat([a.assign(team="AAA"), b.assign(team="BBB")]).assign(regular_season=True)
    rows = pd.concat([_games(a, n=10), _games(b, team="BBB").iloc[6:]])
    prior = pd.DataFrame([_prior_row()])
    inj = pd.DataFrame({"player_id": ["p1"], "status": ["gtd"], "injury_note": ["Ankle: questionable"]})
    out, _ = I.update_projections(prior, rows, tg, "2027-06-01", injuries=inj, playoff_share_remaining=0.2)
    r = out.iloc[0]
    assert r["team"] == "BBB" and r["ros_team_games"] == 82 - 12
    assert r["status"] == "gtd" and r["injury_note"].startswith("Ankle")
    assert r["fg3m"] <= r["fgm"]
    assert r["playoff_mult"] < 1 and r["playoff_mult_preseason"] == 0.95


def test_playoff_mult_remaining_identities():
    m = np.array([0.8, 0.95, 1.0])
    assert I.playoff_mult_remaining(m, 0.15, 0.15) == pytest.approx(m)
    assert I.playoff_mult_remaining(m, 1.0, 0.15) == pytest.approx(np.ones(3))
    assert np.all(I.playoff_mult_remaining(m[:2], 0.4, 0.15) > m[:2])


def test_unmatched_box_score_players_are_reported():
    sched = _sched("AAA", 3)
    tg = sched.assign(team="AAA", regular_season=True)
    rows = pd.concat([_games(sched), _games(sched, pid="rookie-x")])
    _, summ = I.update_projections(pd.DataFrame([_prior_row()]), rows, tg, "2027-01-01")
    assert summ.unmatched == ["rookie-x"] and summ.unlisted == []


def test_injury_feed_age_window(tmp_path, monkeypatch):
    import json

    from fantasy.data import provenance

    d = tmp_path / "espn"
    d.mkdir()
    (d / "injuries.json").write_text(json.dumps({"injuries": [{"displayName": "AAA", "injuries": [
        {"athlete": {"displayName": "P One", "team": {"abbreviation": "AAA"}}, "status": "Out",
         "details": {"type": "Ankle"}, "date": "2026-11-01T00:00Z", "shortComment": "out"}]}]}))
    monkeypatch.setattr(provenance, "RAW_DIR", tmp_path)
    monkeypatch.setattr(provenance, "read_provenance", lambda p: {"fetched_at": "2026-11-01T10:00:00Z"})
    feed, note = I.load_injury_feed("2026-11-02")
    assert feed is not None and feed.set_index("player_id").loc["p-one", "status"] == "out"
    assert I.load_injury_feed("2026-11-10")[0] is None  # stale
    assert I.load_injury_feed("2026-10-30")[0] is None  # fetched after as_of: would leak the future
