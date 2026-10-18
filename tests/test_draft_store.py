import json

import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db.engine import get_engine
from fantasy.draft import store
from fantasy.draft.names import NameResolutionError, resolve_player
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import load_projections


def test_pick_idempotency_and_conflicts(db_url):
    eng = get_engine(db_url)
    d = store.create_draft("mock-x", LeagueSettings(), is_mock=True, engine=eng)
    assert store.record_pick(d, 1, "a", "A", "manual", engine=eng)["status"] == "recorded"
    assert store.record_pick(d, 1, "a", "A", "manual", engine=eng)["status"] == "unchanged"
    with pytest.raises(store.PickConflict):
        store.record_pick(d, 1, "b", "B", "manual", engine=eng)
    with pytest.raises(store.PickConflict):
        store.record_pick(d, 2, "a", "A", "manual", engine=eng)
    store.record_pick(d, 1, "b", "B", "manual", force=True, engine=eng)
    assert [p["player_id"] for p in store.get_picks("mock-x", engine=eng)] == ["b"]
    assert store.get_picks("mock-x", engine=eng)[0]["team_slot"] == 1


def test_name_resolution(proj_path):
    v = load_projections(proj_path).df
    assert resolve_player("Player 011", v) == "player-011"
    assert resolve_player("player-011", v) == "player-011"
    with pytest.raises(NameResolutionError):
        resolve_player("Somebody Else Entirely", v)


def test_cli_end_to_end(db_url, proj_path):
    r = CliRunner()
    P = ["--projections", str(proj_path)]
    out = r.invoke(app, ["draft", "init", "--draft-id", "mock-cli", "--mock", "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["my_picks"][:3] == [1, 24, 36]
    out = r.invoke(app, ["draft", "recommend", "--draft-id", "mock-cli", "--json", *P])
    assert out.exit_code == 0, out.output
    rec = json.loads(out.output)
    top = rec["recommendations"][0]
    out = r.invoke(app, ["draft", "pick", top["name"], "--draft-id", "mock-cli", "--source", "agent", "--json", *P])
    assert out.exit_code == 0, out.output
    out = r.invoke(app, ["draft", "pick", "Nobody Atall", "--draft-id", "mock-cli", "--json", *P])
    assert out.exit_code == 2
    out = r.invoke(app, ["draft", "board", "--draft-id", "mock-cli", "--json"])
    board = json.loads(out.output)
    assert board["picks_made"] == 1 and board["on_the_clock"]["overall"] == 2


def test_prepare_and_confirm_pick(db_url, proj_path, monkeypatch):
    r = CliRunner()
    P = ["--projections", str(proj_path)]
    assert r.invoke(app, ["draft", "init", "--draft-id", "mock-pp", "--mock"]).exit_code == 0
    run_live = json.loads(r.invoke(app, ["run", "start", "--agent", "t", "--mode", "live", "--json"]).output)["run_id"]
    run_dry = json.loads(r.invoke(app, ["run", "start", "--agent", "t", "--json"]).output)["run_id"]
    rec = json.loads(r.invoke(app, ["draft", "recommend", "--draft-id", "mock-pp", "--json", *P]).output)
    name = rec["recommendations"][0]["name"]
    base = ["draft", "prepare-pick", name, "--overall", "1", "--draft-id", "mock-pp", "--json", *P]
    # dry run is refused
    out = r.invoke(app, [*base, "--run-id", run_dry])
    assert out.exit_code == 4 and json.loads(out.output)["action"] == "DO_NOT_CLICK"
    # live run without the draft lock is refused
    out = r.invoke(app, [*base, "--run-id", run_live])
    assert out.exit_code == 4
    assert r.invoke(app, ["lock", "acquire", "draft:mock-pp", "--run-id", run_live]).exit_code == 0
    out = r.invoke(app, [*base, "--run-id", run_live])
    assert out.exit_code == 0, out.output
    res = json.loads(out.output)
    assert res["action"] == "CLICK" and res["agrees_with_model"] is True
    # second attempt for the same pick must not produce another CLICK
    again = r.invoke(app, [*base, "--run-id", run_live])
    assert again.exit_code in (5, 7) and json.loads(again.output)["action"] == "DO_NOT_CLICK"
    out = r.invoke(app, ["draft", "confirm-pick", res["txn_id"], "--evidence", "seen", "--run-id", run_live, "--json", *P])
    assert out.exit_code == 0, out.output
    board = json.loads(r.invoke(app, ["draft", "board", "--draft-id", "mock-pp", "--json"]).output)
    assert board["my_roster"][0]["player_name"] == name
