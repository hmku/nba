"""Lineup view data: `lineup plan --save` (lineup_plan), `lineup record` / `league sync --record-lineup` /
`lineup backfill` (lineup_actual). Temp SQLite; REST via the fake PostgREST."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from fake_postgrest import FakePostgrest
from sqlalchemy import insert, select
from test_lineup import _cli_world
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db import rest
from fantasy.db.schema import model_runs, transactions
from fantasy.lineup import record
from fantasy.ops.common import engine
from fantasy.ops.testing import use_test_db

runner = CliRunner()


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)


def runs(kind=None):
    with engine().connect() as conn:
        q = select(model_runs).order_by(model_runs.c.created_at)
        if kind:
            q = q.where(model_runs.c.kind == kind)
        return [dict(r._mapping) for r in conn.execute(q)]


def invoke(args):
    r = runner.invoke(app, args)
    return r.exit_code, (json.loads(r.output) if r.output.strip().startswith("{") else r.output)


# ---------------------------------------------------------------- lineup_plan
def test_plan_save_writes_compact_row_idempotently(tmp_path, proj_path):
    base, mine = _cli_world(tmp_path, proj_path, statuses={0: "Q"})
    args = base + ["--date", "2026-10-21", "--save"]
    code, out = invoke(args)
    assert code == 0, out
    assert out["saved"]["inserted"] is True and out["saved"]["run_id"].startswith("lp-2026-10-21-")
    assert "week_plan" not in out  # display output unchanged without --week ...
    [row] = runs("lineup_plan")
    s = row["summary"]
    assert row["model_version"] == "lineup-0.4.1" and row["params"]["rest_share_label"] == "prior"
    assert s["schema"] == "lineup_plan/1" and s["date"] == "2026-10-21" and s["week"] == 1
    assert s["opponent"]["team_id"] == 2
    # ... but the saved row always has the rest of the week
    assert [d["date"] for d in s["week_plan"]] == ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24",
                                                    "2026-10-25"]
    assert {e["player_id"] for e in s["slots"]} == set(mine)
    assert set(s["players"]) == set(mine) and all("name" in p for p in s["players"].values())
    # every player in the synthetic world plays BOS-DET daily: markers on every day
    some = s["slots"][0]
    assert some["game"] in ("@DET", "vs BOS") and s["games"][some["player_id"]]["2026-10-22"]
    assert s["expected"]["availability_assumption"] and "PRIOR" in s["expected"]["evidence"]
    assert "optimized" in s["expected"]
    for t in s["decide_at_tipoff"]:
        assert t["deadline"] is None or "tip-off" in t["deadline"]
    # identical re-run: same run_id, no new row
    code, out2 = invoke(args)
    assert code == 0 and out2["saved"] == {**out["saved"], "inserted": False}
    assert len(runs("lineup_plan")) == 1


def test_plan_summary_deadlines_and_bench_with_game():
    out = {"date": "2026-10-21", "week": 1, "lineup": [{"slot": "PG", "player_id": "a", "name": "A", "has_game": True,
                                                        "p_play": 0.5, "value": 1.0}],
           "bench": [{"slot": "BN", "player_id": "b", "name": "B", "has_game": True, "p_play": 0.5, "value": 0.5},
                     {"slot": "BN", "player_id": "c", "name": "C", "has_game": False, "p_play": 0, "value": 0}],
           "il": [], "players": [{"player_id": k, "name": k.upper(), "team": "BOS"} for k in "abc"],
           "decide_at_tipoff": [{"player_id": "a", "name": "A", "status": "Q", "p_play": 0.5, "slot": "PG",
                                 "swap_with": "b", "note": "x"},
                                {"player_id": "b", "name": "B", "status": "Q", "p_play": 0.5, "slot": "BN",
                                 "swap_with": "a", "note": "y"}],
           "expected": {"optimized": {"expected_cats": 5.0, "p_win_matchup": 0.6}}, "monte_carlo": {}}
    games = {"a": {"2026-10-21": "@DET"}, "b": {"2026-10-21": "vs NYK"}}
    s = record.plan_summary(out, games)
    assert s["decide_at_tipoff"][0]["deadline"] == "before A's tip-off (@DET)"
    assert s["decide_at_tipoff"][1]["deadline"] == "before A's tip-off (@DET)"
    assert s["decide_at_tipoff"][1]["swap_with_name"] == "A"
    assert s["bench_with_game"] == [{"player_id": "b", "name": "B", "game": "vs NYK"}]
    assert "monte_carlo" not in s["expected"]


def test_game_markers():
    from datetime import date

    from fantasy.sim.schedule import NBASchedule

    nba = NBASchedule(games=[(date(2026, 10, 21), "BOS", "DET"), (date(2026, 10, 22), "NYK", "BOS")], source="test")
    m = record.game_markers(nba, {"x": "BOS", "y": "DET", "z": None}, ["2026-10-21", "2026-10-22"])
    assert m == {"x": {"2026-10-21": "@DET", "2026-10-22": "vs NYK"}, "y": {"2026-10-21": "vs BOS"}}


# ---------------------------------------------------------------- lineup_actual: normalising slot maps
def roster():
    return record.Roster({"nikola-jokic": {"name": "Nikola Jokić", "yahoo_player_id": 5352, "status": None},
                          "jalen-brunson": {"name": "Jalen Brunson", "yahoo_player_id": 6014, "status": "Q"},
                          "kyle-filipowski": {"name": "Kyle Filipowski", "yahoo_player_id": 7000}})


@pytest.mark.parametrize("raw", [
    {"nikola-jokic": "C", "jalen-brunson": "PG", "kyle-filipowski": "BN"},
    {"Nikola Jokic": "C", "6014": "PG", "Filipowski": "BN1"},
    {"C": "Nikola Jokić", "PG": "jalen-brunson", "BN": "kyle-filipowski"},
    [{"slot": "C", "player_id": "nikola-jokic"}, {"slot": "PG", "name": "Jalen Brunson"},
     {"slot": "BN", "yahoo_player_id": 7000}, {"slot": "Util", "player_id": None}],
    {"slots": {"nikola-jokic": "c", "jalen-brunson": "pg", "kyle-filipowski": "bn"}},
])
def test_normalise_slots_accepts_the_shapes_agents_write(raw):
    slots, warnings = record.normalise_slots(raw, roster())
    assert [(s["slot"], s["player_id"]) for s in slots] == [("PG", "jalen-brunson"), ("C", "nikola-jokic"),
                                                            ("BN", "kyle-filipowski")]
    assert warnings == [] and slots[0]["status"] == "Q"


def test_normalise_slots_errors_and_unknown_players():
    with pytest.raises(record.RecordError, match="unknown slot"):
        record.normalise_slots({"nikola-jokic": "XX"}, roster())
    with pytest.raises(record.RecordError, match="more than one slot"):
        record.normalise_slots([{"slot": "C", "player_id": "nikola-jokic"}, {"slot": "BN", "name": "Jokic"}], roster())
    slots, warnings = record.normalise_slots({"Somebody Else": "BN"}, roster())
    assert slots[0]["player_id"] is None and "not on my roster" in warnings[0]
    assert record.norm_slot("Util2") == "Util" and record.norm_slot("IL+") == "IL+" and record.norm_slot("x") is None


# ---------------------------------------------------------------- lineup record CLI
def _league(tmp_path, ids):
    lf = tmp_path / "league_state.json"
    lf.write_text(json.dumps({"my_team": 1, "teams": {"1": {"name": "Me", "players": ids, "yahoo_team_id": 11,
                                                            "yahoo_ids": {ids[0]: 9001}}}}))
    return lf


def test_record_slots_verified_needs_evidence_and_is_idempotent(tmp_path, proj_path):
    from fantasy.model.lines import load_projections

    ids = list(load_projections(proj_path).df.index)[:3]
    lf = _league(tmp_path, ids)
    common = ["lineup", "record", "--date", "2026-10-22", "--league-file", str(lf), "--projections", str(proj_path),
              "--nba-schedule", str(tmp_path / "none.csv"), "--json"]
    slots = json.dumps({"9001": "C", ids[1]: "PG", ids[2]: "BN"})
    code, out = invoke(common + ["--slots", slots])
    assert code == 2 and "--txn or --how" in out["message"]
    code, out = invoke(common + ["--slots", slots, "--txn", "txn-1", "--how", "server re-read"])
    assert code == 0 and out["inserted"] and out["complete"] is True and out["source"] == "yahoo_verified"
    [row] = runs("lineup_actual")
    s = row["summary"]
    assert row["run_id"].startswith("la-2026-10-22-") and s["schema"] == "lineup_actual/1"
    assert s["txn_id"] == "txn-1" and {x["player_id"]: x["slot"] for x in s["slots"]}[ids[0]] == "C"
    assert s["games_known"] is False
    # same state read again later: no new row (observed_at is not part of the identity)
    code, out = invoke(common + ["--slots", slots, "--txn", "txn-1", "--how", "server re-read"])
    assert code == 0 and out["inserted"] is False and len(runs("lineup_actual")) == 1
    # partial map -> complete false; bad usage
    code, out = invoke(common + ["--slots", json.dumps({ids[1]: "PG"}), "--how", "x"])
    assert code == 0 and out["complete"] is False
    code, out = invoke(common + ["--how", "x"])
    assert code == 2


def test_record_from_capture_uses_my_team_and_capture_date(tmp_path, proj_path):
    base, mine = _cli_world(tmp_path, proj_path, captured_at="2026-10-21T13:05:00Z", statuses={1: "O"})
    lf, capf = tmp_path / "league.json", tmp_path / "cap.json"
    code, out = invoke(["lineup", "record", "--from-capture", str(capf), "--league-file", str(lf),
                        "--projections", str(proj_path), "--json"])
    assert code == 0, out
    s = runs("lineup_actual")[0]["summary"]
    assert s["date"] == "2026-10-21" and s["source"] == "yahoo_capture" and s["observed_at"] == "2026-10-21T13:05:00Z"
    assert [x["player_id"] for x in s["slots"]] and {x["player_id"] for x in s["slots"]} == set(mine)
    assert next(x for x in s["slots"] if x["player_id"] == mine[1])["status"] == "O"
    code, out = invoke(["lineup", "record", "--from-capture", str(capf), "--date", "2026-10-22", "--league-file",
                        str(lf), "--json"])
    assert code == 2 and "capture is from 2026-10-21" in out["message"]


def test_league_sync_record_lineup(tmp_path, proj_path):
    from test_league_sync import make_capture

    from fantasy.model.lines import load_projections

    cap = make_capture(load_projections(proj_path).df)
    capf = tmp_path / "cap.json"
    capf.write_text(json.dumps(cap))
    state = tmp_path / "state.json"
    args = ["league", "sync", "--capture", str(capf), "--state", str(state), "--projections", str(proj_path), "--json"]
    code, out = invoke(args + ["--record-lineup"])
    assert code == 2 and "needs --write" in out["message"]
    code, out = invoke(args + ["--write", "--record-lineup"])
    assert code == 0, out
    lr = out["lineup_record"]
    assert lr.get("error") is None and lr["inserted"] is True
    [row] = runs("lineup_actual")
    assert row["summary"]["source"] == "yahoo_capture" and row["params"]["command"] == "league sync --record-lineup"


# ---------------------------------------------------------------- backfill
def _txn(txn_id, status, payload, verification, created):
    with engine().begin() as conn:
        conn.execute(insert(transactions).values(
            txn_id=txn_id, idempotency_key=txn_id, kind="lineup", status=status, payload=payload,
            verification=verification, created_at=created, updated_at=created))


def test_backfill_from_verified_txns_and_captures(tmp_path, proj_path):
    base, mine = _cli_world(tmp_path, proj_path, captured_at="2026-10-20T04:43:21Z")
    lf, capf = tmp_path / "league.json", tmp_path / "cap.json"
    t = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)
    _txn("txn-a", "verified", {"date": "2026-10-21", "changes": []},
         {"evidence": {"page": "/nba/44495/11", "slots": {p: ("BN" if k >= 10 else "Util") for k, p in
                                                          enumerate(mine)}},
          "verified_at": "2026-10-21T01:05:00+00:00"}, t)
    _txn("txn-b", "verified", {"date": "2026-10-22"}, {"evidence": {"note": "no slot map"}}, t)
    _txn("txn-c", "failed", {"date": "2026-10-22"}, {}, t)
    _txn("txn-d", "verified", {"date": "2026-11-01"}, {"evidence": {"slots": {mine[0]: "C"}}}, t)
    args = ["lineup", "backfill", "--from", "2026-10-20", "--to", "2026-10-22", "--capture", str(capf),
            "--league-file", str(lf), "--projections", str(proj_path), "--json"]
    code, out = invoke(args)
    assert code == 0, out
    assert not out["written"] and runs("lineup_actual") == []
    assert sorted((r["date"], r["source"]) for r in out["rows"]) == [("2026-10-20", "yahoo_capture"),
                                                                     ("2026-10-21", "yahoo_verified")]
    reasons = {s.get("txn_id"): s["reason"] for s in out["skipped"]}
    assert "no slot map" in reasons["txn-b"] and "only verified" in reasons["txn-c"] and "txn-d" not in reasons
    code, out = invoke(args + ["--write"])
    assert code == 0 and all(r["inserted"] for r in out["rows"]) and len(runs("lineup_actual")) == 2
    v = next(r for r in runs("lineup_actual") if r["summary"]["txn_id"] == "txn-a")["summary"]
    assert v["observed_at"] == "2026-10-21T01:05:00+00:00" and "/nba/44495/11" in v["how"]
    code, out = invoke(args + ["--write"])
    assert code == 0 and not any(r["inserted"] for r in out["rows"]) and len(runs("lineup_actual")) == 2


# ---------------------------------------------------------------- REST
def test_rest_store_insert_missing(tmp_path):
    fake = FakePostgrest(f"sqlite:///{tmp_path / 'remote.db'}")
    cl = rest.RestClient(base_url="https://example.supabase.co", key="sb_secret_TEST",
                         transport=httpx.MockTransport(fake), sleep=lambda s: None)
    st = record.store(use_rest=True, client=cl)
    summary = record.actual_summary("2026-10-21", [{"slot": "C", "player_id": "x", "name": "X"}],
                                    source="yahoo_verified", team=1, observed_at=None, how="test")
    row = record.actual_row(summary)
    assert record.save(row, st)["inserted"] is True
    assert record.save(record.actual_row(summary), st)["inserted"] is False
    [got] = st.rows("lineup_actual")
    assert got["run_id"] == row["run_id"] and got["summary"]["slots"][0]["slot"] == "C"


def test_backfill_replays_verified_changes_without_slot_map(tmp_path, proj_path):
    """The real 10-21/10-22 txns carry only `changes` + the page re-read: replay them on the 10-20 capture
    (Yahoo carries an edited lineup forward), chained day by day; an inconsistent `from` is skipped."""
    base, mine = _cli_world(tmp_path, proj_path, captured_at="2026-10-20T04:43:21Z")
    lf, capf = tmp_path / "league.json", tmp_path / "cap.json"
    cap = json.loads(capf.read_text())
    slots20 = {p["player_id"]: p["slot"] for p in cap["teams"]["1"]["players"]}
    pg, bn = mine[0], mine[10]
    assert slots20[pg] == "PG" and slots20[bn] == "BN"
    t = datetime(2026, 10, 20, 4, 4, tzinfo=UTC)
    ev = {"evidence": {"page": "/nba/44495/11/team?&date=2026-10-21"}, "verified_at": "2026-10-20T04:04:22+00:00"}
    _txn("txn-21", "verified", {"date": "2026-10-21", "changes": [{"player": pg, "from": "PG", "to": "BN"},
                                                                  {"player": bn, "from": "BN", "to": "PG"}]}, ev, t)
    _txn("txn-22", "verified", {"date": "2026-10-22", "changes": [{"player": pg, "from": "BN", "to": "PG"},
                                                                  {"player": bn, "from": "PG", "to": "BN"}]}, ev,
         datetime(2026, 10, 20, 4, 48, tzinfo=UTC))
    _txn("txn-bad", "verified", {"date": "2026-10-22", "changes": [{"player": pg, "from": "C", "to": "BN"}]}, ev,
         datetime(2026, 10, 20, 5, 0, tzinfo=UTC))
    code, out = invoke(["lineup", "backfill", "--from", "2026-10-20", "--to", "2026-10-22", "--capture", str(capf),
                        "--league-file", str(lf), "--projections", str(proj_path), "--json", "--write"])
    assert code == 0, out
    by_txn = {r["txn_id"]: r for r in out["rows"]}
    assert by_txn["txn-21"]["slots"][pg] == "BN" and by_txn["txn-21"]["slots"][bn] == "PG"
    assert by_txn["txn-22"]["slots"][pg] == "PG" and by_txn["txn-22"]["slots"][bn] == "BN"
    assert by_txn["txn-21"]["complete"] is True
    [bad] = [s for s in out["skipped"] if s.get("txn_id") == "txn-bad"]
    assert "base lineup, not C" in bad["reason"]
    s21 = next(r for r in runs("lineup_actual") if r["summary"]["txn_id"] == "txn-21")["summary"]
    assert s21["derived_from"]["base_date"] == "2026-10-20" and "derived" in s21["how"]
    s22 = next(r for r in runs("lineup_actual") if r["summary"]["txn_id"] == "txn-22")["summary"]
    assert s22["derived_from"]["base_date"] == "2026-10-21"
