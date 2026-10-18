from datetime import timedelta

import pytest
from sqlalchemy import update

from fantasy.db.schema import agent_runs
from fantasy.ops import ledger
from fantasy.ops.common import InvalidStateError, NotFoundError, engine, now


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("FANTASY_DB_URL", f"sqlite:///{tmp_path / 'ops.db'}")


def test_run_lifecycle():
    rid = ledger.start_run("draft-manager", "mock draft", "dry_run", "session-x")
    run = ledger.get_run(rid)
    assert run["status"] == "running" and run["mode"] == "dry_run"
    ledger.log_event(rid, "observation", "pick 1 made", {"pick": 1})
    with pytest.raises(ValueError):
        ledger.log_event(rid, "bogus")
    hb = ledger.heartbeat(rid)
    assert hb["heartbeat_at"] >= run["heartbeat_at"]
    end = ledger.end_run(rid, "succeeded", "done")
    assert end["status"] == "succeeded" and end["ended_at"]
    with pytest.raises(InvalidStateError):
        ledger.end_run(rid)
    with pytest.raises(InvalidStateError):
        ledger.heartbeat(rid)
    with pytest.raises(NotFoundError):
        ledger.heartbeat("run-nope")
    with pytest.raises(ValueError):
        ledger.start_run("x", mode="yolo")


def test_stale_runs_reaped():
    old = ledger.start_run("a")
    fresh = ledger.start_run("b")
    with engine().begin() as conn:
        conn.execute(
            update(agent_runs)
            .where(agent_runs.c.run_id == old)
            .values(heartbeat_at=now() - timedelta(minutes=30))
        )
    assert [r["run_id"] for r in ledger.stale_runs(15)] == [old]
    assert ledger.reap_stale_runs(15) == [old]
    assert ledger.get_run(old)["status"] == "abandoned"
    assert ledger.get_run(fresh)["status"] == "running"
    assert any(e["payload"] == {"reaped": True} for e in ledger.list_events(old))
    # an abandoned run that comes back can still close itself out
    assert ledger.end_run(old, "failed")["status"] == "failed"


@pytest.mark.parametrize(
    "rec,choice,expected",
    [
        ({"player_id": "a"}, {"player_id": "a", "note": "x"}, True),
        ({"player_id": "a"}, {"player_id": "b"}, False),
        ([{"player_id": "a"}, {"player_id": "b"}], {"player_id": "b"}, False),
        ([{"player_id": "a"}, {"player_id": "b"}], {"player_id": "a"}, True),
        ("start-x", "start-x", True),
        (None, {"player_id": "a"}, None),
    ],
)
def test_agrees_with_model(rec, choice, expected):
    assert ledger.agrees_with_model(rec, choice) is expected


def test_record_decision_logs_event():
    rid = ledger.start_run("a")
    d = ledger.record_decision(rid, "draft_pick", {"player_id": "a"}, {"player_id": "b"}, "need C")
    assert d["agrees_with_model"] is False
    ds = ledger.list_decisions(rid)
    assert ds[0]["rationale"] == "need C"
    assert [e["kind"] for e in ledger.list_events(rid)] == ["decision"]
