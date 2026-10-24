"""Offline tests for fantasy.data.news (synthetic feeds, temp dirs, no network)."""

import gzip
import json
from datetime import date

import pandas as pd
import pytest

from fantasy.data.news import digest, espn_id_map, fetch_news, override_date, parse_feed, triage

TODAY = date(2026, 10, 24)


def _feed(*items):
    return json.dumps({"resultsCount": len(items), "feed": list(items)}).encode()


def _item(iid, headline, published="2026-10-23T12:00:00Z", story=""):
    return {"id": iid, "type": "Rotowire", "headline": headline, "description": headline, "story": story,
            "published": published, "images": [], "links": {"x": 1}}


@pytest.fixture
def env(tmp_path):
    league = {"my_team": "1", "teams": {
        "1": {"name": "Harrison's Best Team", "players": ["mine-a", "mine-b"]},
        "2": {"name": "Other Team", "players": ["kristaps-porzingis", "nomap-guy"]},
    }}
    lf = tmp_path / "league.json"
    lf.write_text(json.dumps(league))
    fa = {"players": [{"name": "Free Two", "player_id": "free-two", "list_rank": 2},
                      {"name": "Free One", "player_id": "free-one", "list_rank": 1}]}
    faf = tmp_path / "free_agents_2026-10-23.json"
    faf.write_text(json.dumps(fa))
    ov = tmp_path / "overrides.csv"
    ov.write_text(
        "# comment\nname,team,status,injury_note,gp,games_missed,mpg,usage_mult,note,source,gp_scale\n"
        'Kristaps Porziņģis,GSW,out,"illness",,17,,,,"ESPN injuries API 2026-10-18 (est return 2026-11-09)",\n'
        "Mine A,BOS,out,knee,,5,,,,ESPN 2026-10-23,\n"
    )
    proc = tmp_path / "processed"
    proc.mkdir()
    pd.DataFrame([
        {"player_id": "kristaps-porzingis", "name": "Kristaps Porziņģis", "gp": 50.0, "gp_sd": 10.0,
         "status": "out", "injury_note": "illness"},
        {"player_id": "mine-a", "name": "Mine A", "gp": 70.0, "gp_sd": 5.0, "status": "healthy", "injury_note": None},
    ]).to_csv(proc / "projections_2026_27.csv", index=False)
    ids = {"mine-a": "1", "mine-b": "2", "kristaps-porzingis": "3102531", "free-one": "9"}
    feeds = {
        "1": _feed(_item(11, "Mine A will move to the bench going forward", "2026-10-23T10:00:00Z"),
                   _item(12, "Mine A is questionable for Friday", "2026-10-22T10:00:00Z")),
        "2": gzip.compress(_feed(_item(21, "Mine B scored 20 points in the win"))),
        "3102531": _feed(
            _item(31, "Porzingis is contemplating his future in basketball", "2026-10-21T18:27:09Z",
                  story="already considered out indefinitely"),
            _item(32, "Old preseason item", "2026-08-01T00:00:00Z")),
        "9": _feed(_item(91, "Free One expected to miss two weeks with a sprain")),
    }
    return {"tmp": tmp_path, "lf": lf, "faf": faf, "ov": ov, "proc": proc, "ids": ids, "feeds": feeds}


def _run(env, feeds=None, scope="rostered+fa"):
    feeds = feeds or env["feeds"]
    return fetch_news(scope=scope, league_file=env["lf"], fa_file=env["faf"], fa_top=1,
                      out_dir=env["tmp"] / "news", id_map=env["ids"], get=lambda eid: feeds[eid], today=TODAY)


def _digest(env, **kw):
    out_dir = env["tmp"] / "news"
    nf = sorted(p for p in out_dir.glob("news_*.json") if "provenance" not in p.name)[-1]
    return digest(news_file=nf, league_file=env["lf"], fa_file=env["faf"], overrides_file=env["ov"],
                  processed_dir=env["proc"], **kw)


def test_parse_feed_plain_and_gzip():
    raw = _feed(_item(1, "a"), {"headline": "no id"})
    for body in (raw, gzip.compress(raw)):
        items = parse_feed(body)
        assert [i["id"] for i in items] == [1]
        assert "images" not in items[0] and items[0]["headline"] == "a"


def test_espn_id_map(tmp_path):
    a = tmp_path / "pg2026.csv.gz"
    b = tmp_path / "pg2027.csv.gz"
    pd.DataFrame({"espn_player_id": ["100", "200", None], "player_id": ["x", "y", "z"]}).to_csv(a, index=False)
    pd.DataFrame({"espn_player_id": ["101"], "player_id": ["x"]}).to_csv(b, index=False)
    inj = tmp_path / "inj.json"
    inj.write_text(json.dumps({"injuries": [{"injuries": [
        {"athlete": {"displayName": "New Rookie", "links": [{"href": "https://www.espn.com/nba/player/_/id/555/n"}]}},
        {"athlete": {"displayName": "Y", "links": [{"href": "https://www.espn.com/nba/player/_/id/999/y"}]}},
    ]}]}))
    m = espn_id_map([a, b, tmp_path / "missing.csv.gz"], inj)
    assert m == {"x": "101", "y": "200", "new-rookie": "555"}


def test_fetch_unmapped_errors_and_new_only(env):
    calls = []

    def flaky(eid):
        calls.append(eid)
        if eid == "2" and calls.count("2") == 1:
            raise ValueError("bad json")  # first attempt fails, the retry succeeds
        return env["feeds"][eid]

    s = fetch_news(scope="rostered+fa", league_file=env["lf"], fa_file=env["faf"], fa_top=1,
                   out_dir=env["tmp"] / "news", id_map=env["ids"], get=flaky, today=TODAY)
    assert s["unmapped"] == ["nomap-guy"]
    assert s["players_in_scope"] == 5 and s["players_fetched"] == 4 and not s["errors"]
    assert s["items_fetched"] == 6 and s["items_cached"] == 5  # August item not cached
    assert s["new_items"] == 5  # the August item is seen but too old to be "new"
    assert (env["tmp"] / "news" / "seen.json").exists()
    # second run the same day: today's file keeps the 5 earlier new items; a fresh item is added
    assert _run(env)["new_items"] == 5
    feeds = dict(env["feeds"])
    feeds["2"] = _feed(_item(22, "Mine B traded to Boston", "2026-10-24T09:00:00Z"), _item(21, "Mine B scored"))
    assert _run(env, feeds)["new_items"] == 6
    # next day's run: only the unseen item is new
    for f in (env["tmp"] / "news").glob("news_*"):
        f.unlink()
    feeds["2"] = _feed(_item(23, "Mine B traded to Boston", "2026-10-24T09:00:00Z"), _item(22, "x"))
    assert _run(env, feeds)["new_items"] == 1
    d = _digest(env)
    assert [p["player_id"] for p in d["players"]] == ["mine-b"]
    assert d["players"][0]["items"][0]["flags"] == ["trade"]


def test_fetch_error_reported(env):
    def boom(eid):
        raise ValueError("nope")

    s = fetch_news(scope="rostered", league_file=env["lf"], out_dir=env["tmp"] / "news", id_map=env["ids"],
                   get=boom, today=TODAY)
    assert set(s["errors"]) == {"mine-a", "mine-b", "kristaps-porzingis"} and s["items_fetched"] == 0


@pytest.mark.parametrize(
    "text,flag,sev",
    [
        ("Porzingis is contemplating his future in basketball", "retire", "high"),
        ("He is out indefinitely", "indefinite", "high"),
        ("Will undergo season-ending surgery", "season_ending", "high"),
        ("Acquired from the Hawks", "trade", "high"),
        ("The Nets waived him Monday", "waived", "high"),
        ("Suspended two games for a flagrant", "suspended", "high"),
        ("Expected to miss two to four weeks", "timeline", "medium"),
        ("He returned to practice Tuesday", "return", "medium"),
        ("Will be on a minutes restriction", "minutes_limit", "medium"),
        ("Expected to start in place of Smith", "role", "medium"),
        ("Is in the starting lineup for Friday's game", "lineup_game", "low"),
        ("Fox (wrist) is available for Friday's game", "game_status", "low"),
        ("Assigned to the G League affiliate", "g_league", "medium"),
        ("Listed as questionable for Friday", "game_status", "low"),
        ("White (calf) is out for Friday's game against the Hawks", "game_status", "low"),
        ("Flagg exited Friday's game with left shoulder soreness and did not return", "injury_exit", "medium"),
        ("Scored 22 points with eight rebounds in the win", None, "info"),
    ],
)
def test_triage(text, flag, sev):
    flags, _, s = triage(text)
    assert s == sev
    if flag:
        assert flag in flags
    else:
        assert flags == []


def test_triage_severity_from_headline_only():
    flags, ctx, sev = triage("Reid scored 20 points in the win", "Reid, acquired in a 2019 trade, has thrived")
    assert flags == [] and "trade" in ctx and sev == "info"


def test_override_date_prefers_source_first_date():
    assert override_date({"source": "ESPN 2026-10-18 (est return 2026-11-09)"}) == "2026-10-18"
    assert override_date({"source": "WebSearch", "note": "x", "injury_note": "back 2026-11-01"}) == "2026-11-01"
    assert override_date({"source": "June 2026"}) is None


def test_digest_order_stale_and_projection(env):
    _run(env)
    d = _digest(env)
    # my team first, then rostered, then FA
    groups = [p["group"] for p in d["players"]]
    assert groups == sorted(groups, key=["mine", "rostered", "fa", "other"].index)
    ids = [p["player_id"] for p in d["players"]]
    assert ids == ["mine-a", "kristaps-porzingis", "free-one"]  # mine-b is info-only -> filtered at min=low
    kp = d["players"][1]
    assert kp["override_stale"] is True and kp["needs_review"] is True and kp["override_date"] == "2026-10-18"
    assert kp["fantasy_team"] == "2: Other Team" and kp["projection"]["gp"] == 50.0
    assert "retire" in kp["items"][0]["flags"] and "indefinite" in kp["items"][0]["context_flags"]
    ma = d["players"][0]
    assert ma["fantasy_team"] == "1: Harrison's Best Team"
    assert [i["severity"] for i in ma["items"]] == ["medium", "low"]
    assert ma["override_stale"] is False and ma["needs_review"] is False  # override dated the same day
    fo = d["players"][2]
    assert fo["fantasy_team"] == "FA" and fo["fa_list_rank"] == 1
    assert fo["override"] is None and fo["override_stale"] is None and fo["needs_review"] is True
    assert "nomap-guy" in d["unmapped"]


def test_digest_min_severity_and_since(env):
    _run(env)
    allinfo = _digest(env, min_severity="info")
    assert [p["player_id"] for p in allinfo["players"]][:2] == ["mine-a", "mine-b"]
    high = _digest(env, min_severity="high")
    assert [p["player_id"] for p in high["players"]] == ["kristaps-porzingis"]
    since = _digest(env, since="2026-10-01", min_severity="info")
    kp = next(p for p in since["players"] if p["player_id"] == "kristaps-porzingis")
    assert [i["id"] for i in kp["items"]] == [31]


def test_parse_feed_truncates_long_stories():
    (it,) = parse_feed(_feed(_item(1, "a", story="x" * 5000)))
    assert len(it["story"]) < 1600 and it["story"].endswith("[truncated]")
