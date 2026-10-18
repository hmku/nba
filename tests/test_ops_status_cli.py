import json

import pytest
from sqlalchemy import create_engine, func, select
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db.schema import drafts, model_runs, tasks
from fantasy.ops.common import engine, now

runner = CliRunner()


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("FANTASY_DB_URL", f"sqlite:///{tmp_path / 'ops.db'}")
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_PUBLISH_URL", raising=False)


def cli(*args, env=None):
    res = runner.invoke(app, list(args), env=env)
    out = json.loads(res.stdout) if "--json" in args else res.stdout
    return res.exit_code, out


def test_status_json_shape():
    with engine().begin() as conn:
        conn.execute(model_runs.insert().values(
            run_id="m1", kind="valuation", model_version="v1", created_at=now()))
        conn.execute(drafts.insert().values(
            draft_id="d1", status="live", num_teams=12, num_rounds=13, my_slot=5,
            order_type="snake_3rr", updated_at=now()))
    code, rid = cli("run", "start", "--agent", "t", "--mode", "live")
    rid = rid.strip()
    env = {"FANTASY_RUN_ID": rid}
    assert cli("lock", "acquire", "yahoo:roster", "--json", env=env)[0] == 0
    cli("txn", "plan", "drop", "--payload", '{"player_id":"x"}', "--json", env=env)
    cli("task", "seed")
    code, out = cli("status", "--json")
    assert code == 0 and out["ok"] is True
    s = out["status"]
    assert set(s) >= {
        "as_of", "runs", "locks", "approvals_pending", "txns_needing_reconciliation",
        "txns_awaiting_approval", "tasks_open", "model_freshness", "active_draft",
    }
    assert set(s["runs"]) == {"active", "stale", "stale_minutes"}
    assert s["runs"]["active"][0]["run_id"] == rid
    assert s["locks"][0]["resource"] == "yahoo:roster"
    assert len(s["approvals_pending"]) == 1
    assert len(s["txns_awaiting_approval"]) == 1
    assert s["txns_needing_reconciliation"] == []
    assert sorted(s["tasks_open"]) == ["P0", "P1", "P2", "P3"]
    assert s["model_freshness"][0]["kind"] == "valuation"
    assert s["model_freshness"][0]["age_hours"] >= 0
    assert s["active_draft"]["draft_id"] == "d1"
    code, text = cli("status")
    assert code == 0 and "pending approvals: 1" in text


def test_cli_exit_codes():
    _, a = cli("run", "start", "--agent", "a", "--mode", "live", "--json")
    _, b = cli("run", "start", "--agent", "b", "--json")
    ra, rb = a["run_id"], b["run_id"]
    assert cli("lock", "acquire", "yahoo:roster", "--run-id", ra, "--json")[0] == 0
    code, out = cli("lock", "acquire", "yahoo:roster", "--run-id", rb, "--json")
    assert code == 3 and out["error"] == "lock_held" and out["holder"]["holder_run_id"] == ra
    assert cli("lock", "release", "yahoo:roster", "--run-id", rb, "--json")[0] == 3

    payload = '{"date":"2026-10-21","starters":["a"]}'
    code, t = cli("txn", "plan", "lineup", "--payload", payload, "--run-id", ra, "--json")
    assert code == 0
    code, dup = cli("txn", "plan", "lineup", "--payload", payload, "--json")
    assert code == 5 and dup["existing"]["txn_id"] == t["txn"]["txn_id"]

    code, d = cli("txn", "plan", "drop", "--payload", '{"p":1}', "--run-id", ra, "--json")
    did = d["txn"]["txn_id"]
    code, out = cli("txn", "mark", did, "submitted", "--run-id", ra, "--json")
    assert code == 4 and out["error"] == "not_allowed"
    code, out = cli("approval", "decide", d["txn"]["approval_id"], "--approve", "--by", "harry",
                    "--json")
    assert code == 0 and out["approval"]["status"] == "approved"
    assert cli("approval", "decide", d["txn"]["approval_id"], "--reject", "--by", "h",
               "--json")[0] == 7
    assert cli("txn", "mark", did, "submitted", "--run-id", ra, "--json")[0] == 0
    code, rec = cli("txn", "reconcile", "--json")
    assert [x["txn_id"] for x in rec["txns"]] == [did]
    assert cli("txn", "mark", did, "submitted", "--run-id", ra, "--json")[0] == 7
    assert cli("txn", "mark", did, "verified", "--evidence", '{"seen":true}', "--json")[0] == 0

    # dry run cannot submit
    _, lt = cli("txn", "plan", "lineup", "--payload", '{"x":2}', "--run-id", rb, "--json")
    cli("lock", "release", "yahoo:roster", "--run-id", ra)
    cli("lock", "acquire", "yahoo:roster", "--run-id", rb)
    assert cli("txn", "mark", lt["txn"]["txn_id"], "submitted", "--run-id", rb, "--json")[0] == 4

    assert cli("task", "add", "a task", "--json")[0] == 0
    assert cli("task", "add", "a task", "--json")[0] == 5
    assert cli("task", "claim", "T-001", "--run-id", ra, "--json")[0] == 0
    assert cli("task", "claim", "T-001", "--owner", "someone", "--json")[0] == 3
    assert cli("task", "show", "T-999", "--json")[0] == 6
    code, out = cli("run", "event", "note", "hi", "--json")  # no run id
    assert code == 2 and out["error"] == "usage"
    code, out = cli("run", "end", "--run-id", ra, "--json")
    assert code == 0 and out["run"]["status"] == "succeeded"
    assert cli("run", "heartbeat", "--run-id", ra, "--json")[0] == 7


def test_db_init_and_publish(tmp_path):
    code, out = cli("db", "init", "--json")
    assert code == 0 and out["dialect"] == "sqlite"
    cli("task", "seed")
    cli("run", "start", "--agent", "a")
    target = f"sqlite:///{tmp_path / 'target.db'}"
    code, out = cli("publish", "--target", target, "--json")
    assert code == 0 and out["rows"]["tasks"] == 12
    code, out = cli("publish", "--json", env={"FANTASY_PUBLISH_URL": target})  # idempotent
    assert code == 0
    with create_engine(target).connect() as conn:
        assert conn.execute(select(func.count()).select_from(tasks)).scalar() == 12
    assert cli("publish", "--json")[0] == 2  # no target
