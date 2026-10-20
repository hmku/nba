"""Yahoo roster sync (fantasy.league.sync / `fantasy league sync|import-capture`) on synthetic data."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pytest
from synthetic import make_projections
from typer.testing import CliRunner

from fantasy.league.sync import (
    CaptureError,
    PlayerResolver,
    diff_states,
    load_capture,
    normalise_free_agents,
    parse_capture,
    sync,
)
from fantasy.league.sync_cli import app, prev_path
from fantasy.model.lines import load_projections
from fantasy.sim.league import from_json

SPECIAL = [  # (player_id, projections name, team, positions)
    ("nikola-jokic", "Nikola Jokić", "DEN", "C"),
    ("egor-demin", "Egor Dёmin", "BRK", "PG|SG"),  # Cyrillic ё (as bbref spells it)
    ("ronald-holland-ii", "Ronald Holland II", "DET", "SF"),
    ("nickeil-alexander-walker", "Nickeil Alexander-Walker", "ATL", "SG|SF"),
    ("kentavious-caldwell-pope", "Kentavious Caldwell-Pope", "MEM", "SG"),
    ("jaren-jackson-jr", "Jaren Jackson Jr.", "MEM", "PF|C"),
    ("trey-murphy-iii", "Trey Murphy III", "NOP", "SF|PF"),
    ("jalen-williams", "Jalen Williams", "OKC", "SG|SF"),
    ("jaylin-williams", "Jaylin Williams", "OKC", "PF|C"),
    ("lamelo-ball", "LaMelo Ball", "CHO", "PG"),
]


@pytest.fixture()
def proj_csv(tmp_path: Path) -> Path:
    df = make_projections(220)
    for i, (pid, name, team, pos) in enumerate(SPECIAL):
        df.loc[i, ["player_id", "name", "team", "positions"]] = [pid, name, team, pos]
    path = tmp_path / "proj.csv"
    df.to_csv(path, index=False)
    return path


@pytest.fixture()
def proj(proj_csv: Path) -> pd.DataFrame:
    return load_projections(proj_csv).df


SLOTS = ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util", "BN", "BN", "BN"]


def make_capture(proj: pd.DataFrame, per_team: int = 13) -> dict:
    """12 Yahoo teams; Yahoo team id = 13 - draft slot (so ids != slots); yahoo player id = 1000 + row."""
    ids = [p for p in proj.index if p not in ("jalen-williams", "jaylin-williams")]
    teams = {}
    for slot in range(1, 13):
        players = []
        for k in range(per_team):
            pid = ids[(slot - 1) * per_team + k]
            row = proj.loc[pid]
            players.append({"slot": SLOTS[k], "name": str(row["name"]), "player_id": None,
                            "team": str(row["team"]), "positions": ",".join(row["positions"]),
                            "status": None, "yahoo_player_id": 1000 + list(proj.index).index(pid)})
        teams[str(13 - slot)] = {"name": f"Team {slot}", "draft_slot": slot, "players": players}
    return {"source": "test", "captured_at": "2026-10-21T13:00:00Z", "teams": teams}


def state_from(proj: pd.DataFrame, cap: dict, tmp_path: Path) -> dict:
    res = sync(parse_capture(copy.deepcopy(cap)), None, proj)
    return res["state"]


# ------------------------------------------------------------------ name resolution


@pytest.mark.parametrize("name,team,expected", [
    ("Nikola Jokic", "DEN", "nikola-jokic"),            # diacritics dropped
    ("Nikola Jokić", None, "nikola-jokic"),
    ("Egor Dëmin", "BKN", "egor-demin"),                # Latin ë (Yahoo) vs Cyrillic ё (ours)
    ("Egor Dёmin", None, "egor-demin"),
    ("Ron Holland", None, "ronald-holland-ii"),          # alias
    ("Ronald Holland II", None, "ronald-holland-ii"),
    ("N. Alexander-Walker", None, "nickeil-alexander-walker"),  # abbreviated first name
    ("K. Caldwell-Pope", None, "kentavious-caldwell-pope"),
    ("Jaren Jackson", None, "jaren-jackson-jr"),         # suffix missing
    ("Trey Murphy", None, "trey-murphy-iii"),
])
def test_resolve_name_edge_cases(proj, name, team, expected):
    r = PlayerResolver(proj).resolve(name, team)
    assert r.player_id == expected and r.method == "name"


def test_resolve_ambiguous_unknown_fuzzy(proj):
    res = PlayerResolver(proj)
    amb = res.resolve("J. Williams")
    assert amb.method == "ambiguous" and amb.player_id == "unknown:j-williams"
    assert {c[0] for c in amb.candidates} == {"jalen-williams", "jaylin-williams"}
    assert res.resolve("Totally Newguy Jr.").player_id == "unknown:totally-newguy-jr"
    # a misspelling is accepted only when the NBA team agrees
    assert res.resolve("Nikola Jokicc", "DEN").method == "fuzzy"
    miss = res.resolve("Nikola Jokicc", "BOS")
    assert miss.method == "unknown" and miss.candidates[0][0] == "nikola-jokic"


def test_capture_player_id_and_yahoo_id_take_precedence(proj):
    res = PlayerResolver(proj, {555: "lamelo-ball"})
    assert res.resolve("Whatever", None, None, "nikola-jokic").method == "capture"
    assert res.resolve("L. Ball (renamed)", None, 555).player_id == "lamelo-ball"
    # a stale capture id that is not a projection id is re-resolved
    assert res.resolve("Nikola Jokic", None, None, "bogus-id").player_id == "nikola-jokic"


# ------------------------------------------------------------------ capture validation


def test_validation_errors(proj):
    cap = make_capture(proj)
    parse_capture(cap)  # ok
    bad = copy.deepcopy(cap)
    bad["teams"].pop("1")
    with pytest.raises(CaptureError, match="expected 12 teams"):
        parse_capture(bad)
    bad = copy.deepcopy(cap)
    bad["teams"]["1"]["players"][0]["slot"] = "QB"
    with pytest.raises(CaptureError, match="unknown slot"):
        parse_capture(bad)
    bad = copy.deepcopy(cap)
    bad["teams"]["1"]["players"] += [dict(bad["teams"]["1"]["players"][0], yahoo_player_id=9000 + i)
                                     for i in range(4)]
    with pytest.raises(CaptureError, match="max roster"):
        parse_capture(bad)


def test_status_tag_rows_filled_from_projections(proj):
    cap = make_capture(proj)
    p0 = cap["teams"]["12"]["players"][0]  # draft slot 1, first player = nikola-jokic
    p0.update(team=None, positions=None, status="O")
    lamelo = next(p for t in cap["teams"].values() for p in t["players"] if p["name"] == "LaMelo Ball")
    lamelo["team"] = "CHA"
    c = parse_capture(cap)
    res = sync(c, None, proj)
    p = c.teams[12].players[0]
    assert (p.player_id, p.team, p.positions, p.status) == ("nikola-jokic", "DEN", "C", "O")
    assert next(p for t in c.teams.values() for p in t.players if p.name == "LaMelo Ball").team == "CHO"
    t1 = res["state"]["teams"]["1"]
    assert t1["status"] == {"nikola-jokic": "O"} and t1["lineup"]["nikola-jokic"] == "PG"
    assert t1["yahoo_team_id"] == 12 and t1["yahoo_ids"]["nikola-jokic"] == 1000


# ------------------------------------------------------------------ state + diff


def test_state_is_a_valid_league_file(proj, tmp_path):
    state = state_from(proj, make_capture(proj), tmp_path)
    path = tmp_path / "league.json"
    path.write_text(json.dumps(state))
    league = from_json(path)  # extra keys (lineup/status/yahoo_ids) are tolerated
    assert league.team_ids == list(range(1, 13)) and league.my_team == 1
    assert len(league.rosters[1]) == 13 and league.rosters[1][0] == "nikola-jokic"
    assert league.names[3] == "Team 3"


def test_unchanged_capture_gives_empty_diff(proj, tmp_path):
    cap = make_capture(proj)
    old = state_from(proj, cap, tmp_path)
    d = sync(parse_capture(cap), old, proj)["diff"]
    assert not d["roster_changed"] and not d["status_changed"] and d["teams"] == {} and d["moves"] == []
    assert d["status_baseline"] is True


def test_trade_add_drop_and_status_change(proj, tmp_path):
    cap = make_capture(proj)
    old = state_from(proj, cap, tmp_path)
    new = copy.deepcopy(cap)
    t1, t2 = new["teams"]["12"]["players"], new["teams"]["11"]["players"]  # draft slots 1 and 2
    a, b = t1.pop(1), t2.pop(1)  # trade 1-for-1
    t1.append(dict(b, slot="BN"))
    t2.append(dict(a, slot="BN"))
    fa_row = proj.iloc[200]
    dropped = t1.pop(2)
    t1.append({"slot": "BN", "name": str(fa_row["name"]), "player_id": None, "team": str(fa_row["team"]),
               "positions": "PG", "status": None, "yahoo_player_id": 5000})
    t1[0]["status"] = "Q"
    res = sync(parse_capture(new), old, proj)
    d = res["diff"]
    assert d["roster_changed"] and d["status_changed"]
    moved = {(m["player_id"], m["from"], m["to"]) for m in d["moves"]}
    a_id, b_id = old["teams"]["1"]["players"][1], old["teams"]["2"]["players"][1]
    assert moved == {(a_id, 1, 2), (b_id, 2, 1)}
    team1 = d["teams"]["1"]
    assert [p["player_id"] for p in team1["adds"]] == [str(proj.index[200])]
    assert [p["name"] for p in team1["drops"]] == [dropped["name"]]
    assert team1["status_changes"] == [{"player_id": "nikola-jokic", "name": "Nikola Jokić", "old": None, "new": "Q"}]
    assert "adds" not in d["teams"]["2"] or d["teams"]["2"]["adds"] == []  # a trade is not an add
    assert d["my_team"]["drops"] == team1["drops"] and d["my_team"]["moved_in"][0]["from"] == 2
    assert {c["player_id"] for c in d["my_team"]["lineup_changes"]} >= {b_id}


def test_unknown_players_kept_and_flagged(proj, tmp_path):
    cap = make_capture(proj)
    cap["teams"]["12"]["players"][-1] = {"slot": "BN", "name": "Fringe Guy", "player_id": None, "team": "UTA",
                                         "positions": "C", "status": None, "yahoo_player_id": 7777}
    res = sync(parse_capture(cap), None, proj)
    assert "unknown:fringe-guy" in res["state"]["teams"]["1"]["players"]
    assert res["resolve"]["unknown_players"][0]["name"] == "Fringe Guy"
    assert res["state"]["unknown_players"] == res["resolve"]["unknown_players"]


def test_old_state_without_status_map(proj, tmp_path):
    cap = make_capture(proj)
    cap["teams"]["12"]["players"][0]["status"] = "Q"
    new = sync(parse_capture(cap), None, proj)["state"]
    old = {"my_team": 1, "teams": {k: {"name": v["name"], "players": v["players"]} for k, v in new["teams"].items()}}
    d = diff_states(old, new)
    assert not d["roster_changed"] and not d["status_changed"] and d["status_baseline"] is False
    assert d["status_now"] == {"1": {"nikola-jokic": "Q"}}


def test_draft_slot_from_previous_state_or_slot_map(proj, tmp_path):
    cap = make_capture(proj)
    old = state_from(proj, cap, tmp_path)
    for t in cap["teams"].values():
        t["draft_slot"] = None
    assert sync(parse_capture(copy.deepcopy(cap)), old, proj)["diff"]["teams"] == {}
    smap = {13 - s: s for s in range(1, 13)}
    assert sync(parse_capture(copy.deepcopy(cap)), None, proj, smap)["state"]["teams"]["1"]["yahoo_team_id"] == 12
    with pytest.raises(CaptureError, match="no draft slot"):
        sync(parse_capture(copy.deepcopy(cap)), None, proj)


# ------------------------------------------------------------------ CLI


def _write(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj))
    return path


def test_cli_dry_run_then_write(proj, proj_csv, tmp_path):
    runner = CliRunner()
    cap = make_capture(proj)
    state = tmp_path / "rosters.json"
    _write(state, state_from(proj, cap, tmp_path))
    before = state.read_text()
    new = copy.deepcopy(cap)
    new["teams"]["12"]["players"][0]["status"] = "O"
    capf = _write(tmp_path / "cap.json", new)
    args = ["sync", "--capture", str(capf), "--state", str(state), "--projections", str(proj_csv), "--json"]
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["ok"] and out["written"] is None and out["diff"]["status_changed"]
    assert state.read_text() == before  # dry run
    r = runner.invoke(app, args + ["--write"])
    assert r.exit_code == 0, r.output
    assert prev_path(state).read_text() == before
    assert json.loads(state.read_text())["teams"]["1"]["status"] == {"nikola-jokic": "O"}
    r = runner.invoke(app, args)  # now in sync
    assert json.loads(r.output)["diff"]["status_changed"] is False
    r = runner.invoke(app, args[:-1])  # text mode
    assert r.exit_code == 0 and "rosters unchanged" in r.output


def test_cli_validation_failure_exit_2(proj, proj_csv, tmp_path):
    cap = make_capture(proj)
    cap["teams"].pop("3")
    capf = _write(tmp_path / "cap.json", cap)
    r = CliRunner().invoke(app, ["sync", "--capture", str(capf), "--state", str(tmp_path / "none.json"),
                                 "--projections", str(proj_csv), "--json"])
    assert r.exit_code == 2
    out = json.loads(r.output)
    assert out["ok"] is False and out["code"] == "invalid_capture" and "expected 12 teams" in out["message"]
    r = CliRunner().invoke(app, ["sync", "--capture", str(tmp_path / "missing.json"), "--json",
                                 "--projections", str(proj_csv)])
    assert r.exit_code == 2


def test_cli_import_capture(proj, proj_csv, tmp_path):
    cap = make_capture(proj)
    for t in cap["teams"].values():
        t["draft_slot"] = None
    raw = _write(tmp_path / "raw.json", json.dumps(cap))  # the JS tool returns a JSON *string*
    smap = _write(tmp_path / "sched.json", {"teams": {str(13 - s): {"draft_slot": s} for s in range(1, 13)}})
    out = tmp_path / "norm.json"
    r = CliRunner().invoke(app, ["import-capture", "--raw", str(raw), "--kind", "rosters", "--out", str(out),
                                 "--state", str(tmp_path / "none.json"), "--slot-map", str(smap),
                                 "--projections", str(proj_csv), "--json"])
    assert r.exit_code == 0, r.output
    norm = load_capture(out)
    assert norm.teams[12].draft_slot == 1 and norm.teams[12].players[0].player_id == "nikola-jokic"

    fa = {"source": "t", "captured_at": "2026-10-21T13:00:00Z",
          "players": [{"name": "LaMelo Ball", "team": "CHA", "positions": None, "status": "Q",
                       "yahoo_player_id": 1, "ownership": "W (Oct 22)", "waiver_until": "Oct 22"},
                      {"name": "Nobody Known", "team": "LAL", "positions": "C", "status": None,
                       "yahoo_player_id": 2, "ownership": "FA", "waiver_until": None}]}
    norm_fa = normalise_free_agents(fa, PlayerResolver(proj))
    assert norm_fa["players"][0]["player_id"] == "lamelo-ball" and norm_fa["players"][0]["team"] == "CHO"
    assert norm_fa["players"][0]["positions"] == "PG" and norm_fa["players"][0]["ownership"] == "W (Oct 22)"
    assert norm_fa["players"][1]["player_id"] is None and norm_fa["unknown_players"][0]["name"] == "Nobody Known"
    r = CliRunner().invoke(app, ["import-capture", "--raw", str(_write(tmp_path / "fa.json", fa)), "--kind", "bogus",
                                 "--json"])
    assert r.exit_code == 2 and json.loads(r.output)["code"] == "usage"
