"""CLI hardening (2026-10-18): run end releases locks; approval wait exit code."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import update
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db.schema import locks as locks_t
from fantasy.ops import ledger, locks
from fantasy.ops.common import engine, now
from fantasy.ops.testing import use_test_db

runner = CliRunner()


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)


def cli(*args):
    res = runner.invoke(app, list(args))
    return res.exit_code, (json.loads(res.stdout) if "--json" in args else res.stdout)


@pytest.mark.parametrize("status", ["succeeded", "failed", "abandoned"])
def test_run_end_releases_its_locks(status):
    ra = ledger.start_run("a", mode="live")
    rb = ledger.start_run("b")
    locks.acquire("draft:x", ra, 60)
    locks.acquire(locks.YAHOO_BROWSER, ra, 60)
    locks.acquire(locks.YAHOO_ROSTER, rb, 60)
    # an expired lease still held by ra is cleaned up too
    locks.acquire("old:thing", ra, 60)
    with engine().begin() as conn:
        conn.execute(update(locks_t).where(locks_t.c.resource == "old:thing")
                     .values(expires_at=now() - timedelta(seconds=5)))
    code, out = cli("run", "end", "--status", status, "--run-id", ra, "--json")
    assert code == 0, out
    assert out["released_locks"] == ["draft:x", "old:thing", "yahoo:browser"]
    assert out["run"]["status"] == status
    assert locks.get_lock("draft:x") is None and locks.get_lock(locks.YAHOO_BROWSER) is None
    assert locks.get_lock(locks.YAHOO_ROSTER)["holder_run_id"] == rb  # other runs untouched
    events = ledger.list_events(ra)
    assert any((e["payload"] or {}).get("released_locks") for e in events)
    # nothing held -> empty list
    code, out = cli("run", "end", "--run-id", rb, "--json")
    assert code == 0 and out["released_locks"] == [locks.YAHOO_ROSTER]


def test_run_end_abandoned_run_releases_locks():
    ra = ledger.start_run("a")
    locks.acquire("draft:y", ra, 60)
    with engine().begin() as conn:  # simulate reaping
        from fantasy.db.schema import agent_runs
        conn.execute(update(agent_runs).where(agent_runs.c.run_id == ra).values(status="abandoned"))
    end = ledger.end_run(ra, "failed")
    assert end["released_locks"] == ["draft:y"] and locks.get_lock("draft:y") is None


def test_approval_wait_still_pending_exits_8():
    code, out = cli("approval", "request", "drop", "drop X", "--json")
    aid = out["approval_id"]
    code, out = cli("approval", "show", aid, "--wait", "0.01", "--json")
    assert code == 8 and out["ok"] is False and out["error"] == "still_pending"
    assert out["approval"]["status"] == "pending"
    # no wait -> plain show, exit 0 even if pending
    assert cli("approval", "show", aid, "--json")[0] == 0
    cli("approval", "decide", aid, "--approve", "--by", "human")
    code, out = cli("approval", "show", aid, "--wait", "0.01", "--json")
    assert code == 0 and out["approval"]["status"] == "approved"
