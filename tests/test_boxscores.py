"""ESPN box-score ingestion: pure parsers on inline synthetic JSON + an offline fetch via MockTransport."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pandas as pd
import pytest

from fantasy.data import boxscores as bs

LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK", "OREB", "DREB", "PF", "+/-"]


def _event(eid: str, when: str, home: str, away: str, *, stype: int = 2, comp: str = "STD",
           completed: bool = True, year: int = 2027) -> dict[str, Any]:
    return {
        "id": eid, "date": when, "season": {"year": year, "type": stype},
        "status": {"type": {"completed": completed}},
        "competitions": [{
            "type": {"abbreviation": comp},
            "competitors": [{"homeAway": "home", "team": {"abbreviation": home}},
                            {"homeAway": "away", "team": {"abbreviation": away}}],
        }],
    }


def _ath(pid: str, name: str, stats: list[str], *, starter: bool = False, dnp: bool = False,
         reason: str = "COACH'S DECISION") -> dict[str, Any]:
    return {"athlete": {"id": pid, "displayName": name}, "starter": starter, "didNotPlay": dnp,
            "reason": reason, "stats": stats}


def _summary(eid: str = "1", when: str = "2026-10-21T01:50Z", home: str = "SA", away: str = "GS",
             comp: str = "STD") -> dict[str, Any]:
    home_players = [
        _ath("10", "Victor Wembanyama", ["34", "28", "10-18", "2-5", "6-7", "14", "3", "2", "1", "5", "2",
                                         "12", "3", "+9"], starter=True),
        _ath("11", "Nikola Jokić", ["--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--",
                                    "--", "--"], dnp=True, reason="ANKLE"),
        _ath("12", "Garbage Timer", ["0", "0", "0-0", "0-0", "0-0", "0", "0", "0", "0", "0", "0", "0", "0", "0"]),
    ]
    away_players = [
        _ath("20", "Jaren Jackson", ["30", "20", "7-15", "3-8", "3-4", "6", "2", "1", "2", "2", "1", "5", "4",
                                     "-9"], starter=True),
        _ath("21", "Bench Guy", [], dnp=True),
        {"athlete": {"shortName": "Olbrich"}, "starter": False, "didNotPlay": True,
         "reason": "COACH'S DECISION", "stats": []},
    ]
    return {
        "header": {"id": eid, "season": {"year": 2027, "type": 2},
                   "competitions": [{"date": when, "type": {"abbreviation": comp},
                                     "competitors": [{"homeAway": "home", "team": {"abbreviation": home}},
                                                     {"homeAway": "away", "team": {"abbreviation": away}}]}]},
        "boxscore": {"players": [
            {"team": {"abbreviation": away}, "statistics": [{"labels": LABELS, "athletes": away_players}]},
            {"team": {"abbreviation": home}, "statistics": [{"labels": LABELS, "athletes": home_players}]},
        ]},
    }


def test_espn_team_map_and_et_date() -> None:
    assert bs.espn_team("GS") == "GSW" and bs.espn_team("UTAH") == "UTA" and bs.espn_team("WSH") == "WAS"
    assert bs.espn_team("BKN") == "BRK" and bs.espn_team("PHX") == "PHO" and bs.espn_team("bos") == "BOS"
    assert set(bs.espn_team(a) for a in bs.ESPN_TEAM_MAP) <= bs.TEAMS
    # 01:50 UTC on Oct 21 is the evening of Oct 20 in New York
    assert bs.et_date("2026-10-21T01:50Z") == "2026-10-20"
    assert bs.et_date("2026-10-20T23:00Z") == "2026-10-20"


def test_parse_scoreboard_and_filter() -> None:
    sb = {"events": [
        _event("1", "2026-10-21T01:50Z", "SA", "OKC"),
        _event("2", "2027-02-15T22:00Z", "Team A", "Team B", comp="ALLSTAR"),
        _event("3", "2026-12-17T01:30Z", "NY", "SA", comp="CC"),
        _event("4", "2026-10-21T23:00Z", "NY", "PHI", completed=False),
        _event("5", "2026-10-10T23:00Z", "NY", "PHI", stype=1),
        _event("6", "2027-04-15T23:30Z", "ORL", "ATL", stype=5),
    ]}
    games = bs.parse_scoreboard(sb)
    kept = [g for g in games if bs.keep_game(g)]
    assert [g["game_id"] for g in kept] == ["1", "3"]
    cc = next(g for g in kept if g["game_id"] == "3")
    assert cc["comp_type"] == "CC" and cc["regular_season"] is False
    assert kept[0]["home"] == "SAS" and kept[0]["game_date"] == "2026-10-20"
    rows = bs.team_games_rows(kept)
    assert len(rows) == 4
    assert {(r["team"], r["opp"], r["home"]) for r in rows if r["game_id"] == "1"} == {
        ("SAS", "OKC", True), ("OKC", "SAS", False)}


def test_parse_summary_rows() -> None:
    rows = bs.parse_summary(_summary())
    df = pd.DataFrame(rows).set_index("espn_player_id")
    assert len(df) == 6
    w = df.loc["10"]
    assert w["team"] == "SAS" and w["opp"] == "GSW" and bool(w["home"]) and bool(w["starter"])
    assert (w["min"], w["pts"], w["fgm"], w["fga"], w["fg3m"], w["fg3a"], w["ftm"], w["fta"]) == (
        34, 28, 10, 18, 2, 5, 6, 7)
    assert (w["reb"], w["ast"], w["tov"], w["stl"], w["blk"]) == (14, 3, 2, 1, 5)
    assert bool(w["played"]) and w["dnp_reason"] == ""  # ESPN's "COACH'S DECISION" ignored if played
    assert w["game_date"] == "2026-10-20" and w["season"] == 2027 and bool(w["regular_season"])
    j = df.loc["11"]
    assert not j["played"] and j["dnp_reason"] == "ANKLE" and pd.isna(j["pts"])
    assert j["player_id"] == "nikola-jokic"
    assert df.loc["20", "player_id"] == "jaren-jackson-jr"  # alias-aware canonical id
    assert not df.loc["12", "played"] and df.loc["12", "min"] == 0
    assert not df.loc["21", "played"] and pd.isna(df.loc["21", "min"])
    anon = df.loc[""]
    assert anon["player_name"] == "Olbrich" and anon["player_id"] == "" and not anon["played"]
    a = bs.anomalies(rows, _summary())
    assert a["athlete_missing_id"] == 1
    assert a["not_dnp_but_no_stats"] == 1 and a["unexpected_labels"] == 0


def test_parse_summary_uses_game_context_and_cc_flag() -> None:
    game = {"game_id": "99", "season": 2027, "game_date": "2026-12-16", "regular_season": False}
    rows = bs.parse_summary(_summary(comp="CC"), game)
    assert {r["game_id"] for r in rows} == {"99"} and not any(r["regular_season"] for r in rows)
    assert not any(r["regular_season"] for r in bs.parse_summary(_summary(comp="CC")))


def test_fetch_season_offline_incremental(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    boards = {
        "20261020": {"events": [_event("1", "2026-10-21T01:50Z", "SA", "GS")]},
        "20261021": {"events": [_event("2", "2026-10-21T23:30Z", "SA", "GS")]},
    }
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json=boards.get(req.url.params["dates"], {"events": []}))
        eid = req.url.params["event"]
        when = "2026-10-21T01:50Z" if eid == "1" else "2026-10-21T23:30Z"
        return httpx.Response(200, json=_summary(eid, when))

    real = httpx.Client
    monkeypatch.setattr(bs.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(bs, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(bs, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(bs, "RAW_DIR", tmp_path)

    out = bs.fetch_season(2027, date(2026, 10, 19), date(2026, 10, 20), today=date(2026, 10, 21), rps=1000)
    assert out["games"] == 1 and out["files"][0]["rows"] == 6
    out2 = bs.fetch_season(2027, end=date(2026, 10, 21), today=date(2026, 10, 22), rps=1000)
    assert out2["range"][0] == "2026-10-20"  # resumed from the latest date on file
    p = pd.read_csv(tmp_path / "out" / "player_games_2027.csv.gz", dtype={"game_id": str})
    t = pd.read_csv(tmp_path / "out" / "team_games_2027.csv.gz", dtype={"game_id": str})
    assert sorted(t["game_id"].unique()) == ["1", "2"] and len(t) == 4
    assert len(p) == 12 and not p.duplicated(["game_id", "team", "espn_player_id"]).any()
    assert list(p.columns) == bs.PLAYER_COLUMNS
    assert (tmp_path / "out" / "player_games_2027.csv.gz.provenance.json").exists()
    # summary for game 1 came from the cache on the second run
    assert sum("event=1" in c for c in calls) == 1
