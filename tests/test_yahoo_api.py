"""Tests for the read-only Yahoo client, using constructed fixtures and httpx.MockTransport."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from fantasy.league import yahoo_api as ya
from fantasy.league import yahoo_auth

FIX = Path(__file__).parent / "fixtures" / "yahoo"
LK = "470.l.44495"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


@pytest.fixture(autouse=True)
def _no_yahoo_env(monkeypatch, tmp_path):
    for k in ("YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET", "YAHOO_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("YAHOO_OAUTH_FILE", str(tmp_path / "yahoo_oauth.json"))


# ------------------------------------------------------------------ parsers


def test_settings_from_yahoo_maps_league_44495():
    s = ya.settings_from_yahoo(load("league_settings.json"))
    assert s["num_teams"] == 12
    assert s["categories"] == ["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"]
    assert s["category_codes"] == [
        "fg_pct",
        "ft_pct",
        "fg3m",
        "pts",
        "reb",
        "ast",
        "stl",
        "blk",
        "tov",
    ]
    assert s["lower_is_better"] == ["TO"]
    assert s["roster_slots"] == {
        "PG": 1,
        "SG": 1,
        "G": 1,
        "SF": 1,
        "PF": 1,
        "F": 1,
        "C": 2,
        "Util": 2,
        "BN": 3,
        "IL": 3,
    }
    assert s["draft_type"] == "live"
    assert s["is_auction_draft"] is False
    assert s["draft_pick_time"] == 60
    assert s["max_weekly_adds"] == 6
    assert s["waiver_days"] == 2
    assert s["waiver_type"] == "R"
    assert s["playoff_start_week"] == 18
    assert s["playoff_teams"] == 6
    assert s["playoff_weeks"] == [18, 19, 20]
    assert s["league_key"] == LK


def test_parse_league_settings_display_only_stats_flagged():
    p = ya.parse_league_settings(load("league_settings.json"))
    display_only = {c["display_name"] for c in p["categories"] if c["display_only"]}
    assert display_only == {"FGM/A", "FTM/A"}
    assert p["draft_status"] == "predraft"
    assert p["season"] == "2026"


def test_settings_missing_weekly_adds_is_none():
    raw = load("league_settings.json")
    raw["fantasy_content"]["league"][1]["settings"][0].pop("max_weekly_adds")
    assert ya.settings_from_yahoo(raw)["max_weekly_adds"] is None


def test_parse_draft_results_live_partial():
    picks = ya.parse_draft_results(load("draftresults_live.json"))
    assert len(picks) == 156
    made = [p for p in picks if p["player_key"]]
    assert len(made) == 14
    assert made[0] == {
        "pick": 1,
        "round": 1,
        "team_key": f"{LK}.t.1",
        "player_key": "470.p.5352",
        "cost": None,
    }
    slot1 = [p["pick"] for p in picks if p["team_key"] == f"{LK}.t.1"]
    assert slot1 == [1, 24, 36, 37, 60, 61, 84, 85, 108, 109, 132, 133, 156]


def test_parse_draft_results_predraft_empty():
    assert ya.parse_draft_results(load("draftresults_predraft.json")) == []


def test_parse_teams():
    teams = ya.parse_teams(load("league_teams.json"))
    assert len(teams) == 12
    mine = [t for t in teams if t["is_owned_by_current_login"]]
    assert [t["team_key"] for t in mine] == [f"{LK}.t.1"]
    assert teams[0]["draft_position"] == 1
    assert teams[1]["managers"][0]["is_commissioner"] is True


def test_parse_players_with_subresources():
    players = ya.parse_players(load("league_players.json"))
    assert [p["name"] for p in players][:2] == ["Nikola Jokic", "Shai Gilgeous-Alexander"]
    sga = players[1]
    assert sga["eligible_positions"] == ["PG", "SG", "G", "Util"]
    assert sga["draft_analysis"]["average_pick"] == pytest.approx(2.1)
    assert sga["percent_owned"] == 99
    assert sga["ownership_type"] == "freeagents"
    kawhi = players[3]
    assert kawhi["status"] == "INJ" and kawhi["injury_note"] == "Knee"
    sleeper = players[4]
    assert sleeper["draft_analysis"]["average_pick"] is None  # "-" means undrafted


def test_parse_roster_selected_positions():
    roster = ya.parse_roster(load("team_roster.json"))
    assert [(p["name"], p["selected_position"]) for p in roster] == [
        ("Nikola Jokic", "C"),
        ("Shai Gilgeous-Alexander", "PG"),
        ("Victor Wembanyama", "PF"),
        ("Kawhi Leonard", "IL"),
    ]
    assert roster[2]["eligible_positions"] == ["PF", "C", "F", "Util"]


def test_parse_game():
    g = ya.parse_game(load("game_nba.json"))
    assert g["game_key"] == "470" and g["season"] == "2026" and g["code"] == "nba"
    assert ya.league_key(g["game_key"], 44495) == LK


def test_merge_and_iter_helpers():
    assert ya.merge_fragments([{"a": 1}, [], [{"b": 2}], {"c": 3}]) == {"a": 1, "b": 2, "c": 3}
    coll = {"1": {"x": "b"}, "0": {"x": "a"}, "count": 2}
    assert list(ya.iter_collection(coll, "x")) == ["a", "b"]


# ------------------------------------------------------------------ credentials


def test_load_credentials_env_overrides_file(monkeypatch, tmp_path):
    path = tmp_path / "yahoo_oauth.json"
    creds = ya.YahooCredentials("cid", "sec", "rt-file", access_token="at", expires_at=9e12)
    ya.save_credentials(creds, path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    monkeypatch.setenv("YAHOO_REFRESH_TOKEN", "rt-env")
    loaded = ya.load_credentials(path)
    assert loaded.refresh_token == "rt-env"
    assert loaded.access_token is None  # new refresh token invalidates cached access token
    assert "rt-env" not in repr(loaded) and "sec" not in repr(loaded)


def test_load_credentials_missing_raises(tmp_path):
    with pytest.raises(ya.YahooAuthError, match="yahoo-auth"):
        ya.load_credentials(tmp_path / "nope.json")


# ------------------------------------------------------------------ client (mock transport)


def make_client(handler, tmp_path, **kw) -> ya.YahooClient:
    creds = ya.YahooCredentials("cid", "sec", "rt")
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ya.YahooClient(
        creds, http=http, token_file=tmp_path / "tok.json", min_interval=0.0, **kw
    )


def test_client_refreshes_then_gets_settings(tmp_path):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "api.login.yahoo.com":
            body = dict(req.url.params) | dict(
                x.split("=", 1) for x in req.content.decode().split("&")
            )
            assert body["grant_type"] == "refresh_token"
            return httpx.Response(200, json=load("token_response.json"))
        assert req.headers["Authorization"] == "Bearer FAKE_ACCESS_TOKEN"
        assert req.url.params["format"] == "json"
        assert req.url.path == f"/fantasy/v2/league/{LK}/settings"
        return httpx.Response(200, json=load("league_settings.json"))

    c = make_client(handler, tmp_path)
    s = c.get_league_settings(LK)
    assert s["num_teams"] == 12
    assert c.creds.refresh_token == "FAKE_REFRESH_TOKEN_2"
    saved = json.loads((tmp_path / "tok.json").read_text())
    assert saved["refresh_token"] == "FAKE_REFRESH_TOKEN_2"
    assert len(seen) == 2


def test_client_players_path_and_401_retry(tmp_path):
    calls = {"api": 0, "token": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.login.yahoo.com":
            calls["token"] += 1
            return httpx.Response(200, json=load("token_response.json"))
        calls["api"] += 1
        if calls["api"] == 1:
            return httpx.Response(401, json={"error": {"description": "token_expired"}})
        assert req.url.path.endswith(
            "players;start=0;count=25;status=A;sort=OR;out=draft_analysis,percent_owned,ownership"
        )
        return httpx.Response(200, json=load("league_players.json"))

    c = make_client(handler, tmp_path)
    players = c.get_players(LK, 0, 50, status="A")
    assert len(players) == 5
    assert calls == {"api": 2, "token": 2}


def test_client_roster_date_and_game(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.login.yahoo.com":
            return httpx.Response(200, json=load("token_response.json"))
        if req.url.path.endswith("game/nba"):
            return httpx.Response(200, json=load("game_nba.json"))
        assert req.url.path.endswith(f"team/{LK}.t.1/roster;date=2026-10-21")
        return httpx.Response(200, json=load("team_roster.json"))

    c = make_client(handler, tmp_path)
    assert c.resolve_league_key(44495) == LK
    assert len(c.get_roster(f"{LK}.t.1", "2026-10-21")) == 4


def test_client_rate_limit_and_errors(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.login.yahoo.com":
            return httpx.Response(200, json=load("token_response.json"))
        if "draftresults" in req.url.path:
            return httpx.Response(999, text="Request denied")
        return httpx.Response(400, json={"error": {"description": "Invalid league key"}})

    c = make_client(handler, tmp_path)
    with pytest.raises(ya.YahooRateLimitError):
        c.get_draft_results(LK)
    with pytest.raises(ya.YahooError, match="Invalid league key"):
        c.get_teams("bogus")


def test_client_is_read_only(tmp_path):
    c = make_client(lambda req: httpx.Response(500), tmp_path)
    with pytest.raises(ya.YahooReadOnlyError):
        c.request("POST", f"league/{LK}/transactions", content=b"<x/>")


def test_refresh_failure_message_has_no_secrets(tmp_path):
    c = make_client(lambda req: httpx.Response(400, json={"error": "invalid_grant"}), tmp_path)
    with pytest.raises(ya.YahooAuthError) as ei:
        c.get_teams(LK)
    msg = str(ei.value)
    assert "sec" not in msg and "invalid_grant" not in msg


# ------------------------------------------------------------------ auth helper


def test_extract_code_variants():
    assert yahoo_auth.extract_code(" abc123 ") == "abc123"
    assert yahoo_auth.extract_code("https://localhost:8080/?code=xyz&state=1") == "xyz"


def test_authorize_url():
    url = yahoo_auth.build_authorize_url("cid", "oob")
    assert url.startswith(ya.AUTH_URL)
    assert "client_id=cid" in url and "redirect_uri=oob" in url and "response_type=code" in url


def test_run_interactive_auth_saves_tokens(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        body = dict(x.split("=", 1) for x in req.content.decode().split("&"))
        assert body == {
            "grant_type": "authorization_code",
            "code": "thecode",
            "redirect_uri": "https%3A%2F%2Flocalhost%3A8080",  # new default (oob dropped by Yahoo)
        }
        return httpx.Response(200, json=load("token_response.json"))

    msgs: list[str] = []
    path = yahoo_auth.run_interactive_auth(
        "cid",
        "sec",
        path=tmp_path / "o.json",
        open_browser=False,
        input_fn=lambda prompt: "https://x/?code=thecode",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        out=msgs.append,
    )
    data = json.loads(path.read_text())
    assert data["refresh_token"] == "FAKE_REFRESH_TOKEN_2"
    assert all("FAKE" not in m for m in msgs)  # tokens never printed


def test_default_redirect_uri_is_https_localhost():
    assert ya.DEFAULT_REDIRECT_URI == "https://localhost:8080"
    url = yahoo_auth.build_authorize_url("cid")
    assert "redirect_uri=https%3A%2F%2Flocalhost%3A8080" in url
