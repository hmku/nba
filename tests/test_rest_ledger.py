"""`fantasy run|lock|approval|txn|task ... --rest`: single-row ledger writes over PostgREST.

The "remote" is tests/fake_postgrest.py: an in-process PostgREST stand-in backed by a second SQLite
DB with the real schema (constraints included), plugged in through httpx.MockTransport. No network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fake_postgrest import FakePostgrest
from sqlalchemy import update
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db import rest
from fantasy.db.schema import agent_runs, approvals, locks, metadata, tasks, transactions
from fantasy.ops import rest_ledger
from fantasy.ops.common import engine
from fantasy.ops.testing import use_test_db

runner = CliRunner()


@pytest.fixture()
def remote(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)  # the *local* DB: must stay untouched by --rest
    for k in (*rest.KEY_ENVS, "SUPABASE_URL", "FANTASY_RUN_ID"):
        monkeypatch.delenv(k, raising=False)
    fake = FakePostgrest(f"sqlite:///{tmp_path / 'remote.db'}")

    def client():
        return rest.RestClient(base_url="https://example.supabase.co",
                               transport=httpx.MockTransport(fake), sleep=lambda s: None)

    monkeypatch.setattr(rest_ledger, "default_client", client)
    return fake


def cli(*args) -> tuple[int, dict]:
    res = runner.invoke(app, [str(a) for a in args] + ["--json"])
    try:
        return res.exit_code, json.loads(res.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return res.exit_code, {"raw": res.stdout + str(res.exception)}


def set_remote(fake: FakePostgrest, table, where, **values) -> None:
    with fake.engine.begin() as c:
        c.execute(update(table).where(where).values(**values))


def local_counts() -> dict[str, int]:
    from sqlalchemy import func, select
    with engine().connect() as c:
        return {t.name: c.execute(select(func.count()).select_from(t)).scalar_one()
                for t in metadata.sorted_tables}


def start(mode="live") -> str:
    code, out = cli("run", "start", "--agent", "cloud", "--mode", mode, "--rest")
    assert code == 0, out
    return out["run_id"]


# ------------------------------------------------------------------ runs / events / decisions


def test_run_lifecycle_rest_and_local_db_untouched(remote):
    rid = start("dry_run")
    assert remote.rows("agent_runs")[0]["status"] == "running"
    code, out = cli("run", "event", "observation", "saw X", "--payload", '{"a": 1}',
                    "--run-id", rid, "--rest")
    assert code == 0 and out["event_id"].startswith("evt-")
    code, out = cli("run", "decision", "other", "--choice", '{"player_id":"b"}',
                    "--model", '[{"player_id":"a"}]', "--rationale", "r", "--run-id", rid, "--rest")
    assert code == 0 and out["agrees_with_model"] is False
    assert cli("run", "heartbeat", "--run-id", rid, "--rest")[0] == 0
    code, out = cli("run", "show", rid, "--rest")
    assert code == 0 and len(out["events"]) == 2 and len(out["decisions"]) == 1
    assert out["run"]["started_at"].endswith("+00:00")  # aware UTC like the local path
    code, out = cli("run", "end", "--summary", "done", "--run-id", rid, "--rest")
    assert code == 0 and out["run"]["status"] == "succeeded" and out["released_locks"] == []
    # CAS: ending twice -> invalid_state (exit 7), heartbeat on an ended run too
    code, out = cli("run", "end", "--run-id", rid, "--rest")
    assert code == 7 and out["code"] == "invalid_state"
    assert cli("run", "heartbeat", "--run-id", rid, "--rest")[0] == 7
    assert cli("run", "list", "--rest")[1]["runs"][0]["run_id"] == rid
    assert cli("run", "show", "run-nope", "--rest")[0] == 6
    assert sum(local_counts().values()) == 0  # nothing went to the local DB
    assert set(remote.methods()) <= {"GET", "POST", "PATCH", "DELETE"}


def test_rest_output_keys_match_local(remote):
    """Same JSON keys with and without --rest (values differ: ids, timestamps)."""
    _, loc = cli("run", "start", "--agent", "x")
    _, rem = cli("run", "start", "--agent", "x", "--rest")
    assert loc.keys() == rem.keys() and loc["run"].keys() == rem["run"].keys()
    _, loc_t = cli("txn", "plan", "lineup", "--payload", '{"a":1}', "--run-id", loc["run_id"])
    _, rem_t = cli("txn", "plan", "lineup", "--payload", '{"a":1}', "--run-id", rem["run_id"],
                   "--rest")
    assert loc_t.keys() == rem_t.keys() and loc_t["txn"].keys() == rem_t["txn"].keys()
    assert loc_t["txn"]["idempotency_key"] == rem_t["txn"]["idempotency_key"]  # same key rule


# ------------------------------------------------------------------ transactions


def test_txn_plan_idempotent_replan(remote):
    rid = start()
    code, out = cli("txn", "plan", "lineup", "--payload", '{"move":"x"}', "--scope", "2026-11-01",
                    "--run-id", rid, "--rest")
    assert code == 0 and out["txn"]["status"] == "planned" and out["txn"]["approval_id"] is None
    tid = out["txn"]["txn_id"]
    code, dup = cli("txn", "plan", "lineup", "--payload", '{"move":"x"}', "--scope", "2026-11-01",
                    "--run-id", rid, "--rest")
    assert code == 5 and dup["code"] == "duplicate" and dup["existing"]["txn_id"] == tid
    assert len(remote.rows("transactions")) == 1
    # attempt 2 is a different key -> new txn
    code, out = cli("txn", "plan", "lineup", "--payload", '{"move":"x"}', "--scope", "2026-11-01",
                    "--attempt", 2, "--run-id", rid, "--rest")
    assert code == 0 and out["txn"]["txn_id"] != tid and out["txn"]["verification"]["attempt"] == 2


def test_txn_plan_lost_race_returns_duplicate(remote):
    """Another session inserts the same idempotency key between our GET and our INSERT."""
    rid = start()
    store = rest_ledger.backend()
    key = rest_ledger._txns.idempotency_key("lineup", {"m": 1}, "s", 1)

    def other_session():
        with remote.engine.begin() as c:
            c.execute(transactions.insert().values(txn_id="txn-other", idempotency_key=key,
                                                   kind="lineup", status="planned"))
    remote.on("POST", "transactions", other_session)
    with pytest.raises(rest_ledger.DuplicateError) as ei:
        store.transactions.plan("lineup", {"m": 1}, rid, "s")
    assert ei.value.details["existing"]["txn_id"] == "txn-other"


def test_add_drop_approval_flow_and_submit_gates(remote):
    rid = start()
    code, out = cli("txn", "plan", "add_drop", "--payload", '{"add":"a","drop":"b"}',
                    "--summary", "add a / drop b", "--run-id", rid, "--rest")
    assert code == 0 and out["txn"]["status"] == "awaiting_approval"
    tid, aid = out["txn"]["txn_id"], out["txn"]["approval_id"]
    code, a = cli("approval", "show", aid, "--rest")
    assert code == 0 and a["approval"]["status"] == "pending"
    assert a["approval"]["payload"]["txn_id"] == tid and a["approval"]["summary"] == "add a / drop b"
    assert [x["approval_id"] for x in cli("approval", "list", "--rest")[1]["approvals"]] == [aid]
    # no lock + pending approval -> not allowed (4)
    code, out = cli("txn", "mark", tid, "submitted", "--run-id", rid, "--rest")
    assert code == 4 and "approval" in out["message"]
    # --wait times out while pending -> 8
    code, out = cli("approval", "show", aid, "--wait", "0.05", "--rest")
    assert code == 8 and out["code"] == "still_pending"
    # the user approves on the dashboard (a direct write to the remote DB)
    set_remote(remote, approvals, approvals.c.approval_id == aid, status="approved",
               decided_by="harry", decided_at=datetime.now(UTC))
    code, out = cli("txn", "check", tid, "--run-id", rid, "--rest")
    assert code == 4 and "yahoo:roster" in out["message"]  # still needs the lock
    assert cli("lock", "acquire", "yahoo:roster", "--run-id", rid, "--rest")[0] == 0
    assert cli("txn", "check", tid, "--run-id", rid, "--rest")[0] == 0
    code, out = cli("txn", "mark", tid, "submitted", "--run-id", rid, "--rest")
    assert code == 0 and out["txn"]["status"] == "submitted"
    assert out["txn"]["verification"]["submitted_by"] == rid
    assert [t["txn_id"] for t in cli("txn", "reconcile", "--rest")[1]["txns"]] == [tid]
    # verified needs evidence (usage error, 2), then works; a second verify is invalid (7)
    assert cli("txn", "mark", tid, "verified", "--run-id", rid, "--rest")[0] == 2
    code, out = cli("txn", "mark", tid, "verified", "--evidence", '{"seen":"roster"}',
                    "--run-id", rid, "--rest")
    assert code == 0 and out["txn"]["verification"]["evidence"] == {"seen": "roster"}
    assert out["txn"]["verification"]["scope"]  # merged, not replaced
    code, out = cli("txn", "mark", tid, "verified", "--evidence", '{"x":1}', "--rest")
    assert code == 7 and out["status"] == "verified"
    # run end releases the lock it holds, over REST
    code, out = cli("run", "end", "--run-id", rid, "--rest")
    assert code == 0 and out["released_locks"] == ["yahoo:roster"] and remote.rows("locks") == []


def test_dry_run_cannot_submit_over_rest(remote):
    rid = start("dry_run")
    _, out = cli("txn", "plan", "lineup", "--payload", '{"m":1}', "--run-id", rid, "--rest")
    assert cli("lock", "acquire", "yahoo:roster", "--run-id", rid, "--rest")[0] == 0
    code, out = cli("txn", "mark", out["txn"]["txn_id"], "submitted", "--run-id", rid, "--rest")
    assert code == 4 and "dry_run" in out["message"]


def test_concurrent_status_change_fails_cleanly_not_clobbered(remote):
    """Someone cancels the txn between our read and our PATCH: CAS on status -> exit 7."""
    rid = start()
    _, out = cli("txn", "plan", "lineup", "--payload", '{"m":2}', "--run-id", rid, "--rest")
    tid = out["txn"]["txn_id"]
    remote.on("PATCH", "transactions", lambda: set_remote(
        remote, transactions, transactions.c.txn_id == tid, status="cancelled", error="by user"))
    code, out = cli("txn", "mark", tid, "failed", "--error", "nope", "--run-id", rid, "--rest")
    assert code == 7 and out["code"] == "invalid_state" and out["status"] == "cancelled"
    (row,) = remote.rows("transactions")
    assert row["status"] == "cancelled" and row["error"] == "by user"
    # the PATCH carried the expected-state filter
    patch = [r for r in remote.requests if r.method == "PATCH"][-1]
    assert patch.url.params["status"] == 'in.("planned","awaiting_approval","submitted")'


def test_concurrent_submit_race_only_one_wins(remote):
    """Two executors pass check_submit; the second PATCH (status=in.(planned)) matches nothing."""
    rid = start()
    _, out = cli("txn", "plan", "lineup", "--payload", '{"m":3}', "--run-id", rid, "--rest")
    tid = out["txn"]["txn_id"]
    assert cli("lock", "acquire", "yahoo:roster", "--run-id", rid, "--rest")[0] == 0
    remote.on("PATCH", "transactions", lambda: set_remote(
        remote, transactions, transactions.c.txn_id == tid, status="submitted"))
    code, out = cli("txn", "mark", tid, "submitted", "--run-id", rid, "--rest")
    assert code == 7 and out["status"] == "submitted"
    # no 'submitting' command event was logged for the losing attempt
    assert not [e for e in remote.rows("agent_events") if e["kind"] == "command"]


def test_expire_overdue_never_touches_decided_approvals(remote):
    past = datetime.now(UTC) - timedelta(hours=1)
    with remote.engine.begin() as c:
        c.execute(approvals.insert(), [
            {"approval_id": "apr-old-pending", "action_type": "drop", "status": "pending",
             "requested_at": past, "expires_at": past},
            {"approval_id": "apr-old-approved", "action_type": "drop", "status": "approved",
             "decided_by": "harry", "requested_at": past, "expires_at": past},
        ])
    code, out = cli("approval", "list", "--status", "all", "--rest")
    st = {a["approval_id"]: a["status"] for a in out["approvals"]}
    assert code == 0 and st == {"apr-old-pending": "expired", "apr-old-approved": "approved"}


def test_approval_request_rest_and_no_decide_path(remote):
    rid = start()
    code, out = cli("approval", "request", "other", "do a thing", "--payload", '{"x":1}',
                    "--expires-in", 60, "--run-id", rid, "--rest")
    assert code == 0 and out["approval"]["status"] == "pending"
    # deciding stays human-only: no --rest option, no REST method
    res = runner.invoke(app, ["approval", "decide", out["approval_id"], "--approve", "--by", "x",
                              "--rest"])
    assert res.exit_code == 2
    assert not hasattr(rest_ledger.RestApprovals, "decide")
    assert remote.rows("approvals")[0]["status"] == "pending"
    assert not [r for r in remote.requests if r.method == "PATCH"
                and r.url.path.endswith("/approvals") and b'"approved"' in r.content]


# ------------------------------------------------------------------ locks


def test_locks_rest(remote):
    a, b = start(), start()
    code, out = cli("lock", "acquire", "yahoo:browser", "--ttl", 60, "--run-id", a, "--rest")
    assert code == 0 and out["lock"]["taken_over"] is False
    code, out = cli("lock", "acquire", "yahoo:browser", "--run-id", b, "--rest")
    assert code == 3 and out["code"] == "lock_held" and out["holder"]["holder_run_id"] == a
    assert cli("lock", "release", "yahoo:browser", "--run-id", b, "--rest")[0] == 3
    assert cli("lock", "renew", "yahoo:browser", "--run-id", b, "--rest")[0] == 3
    assert cli("lock", "renew", "yahoo:browser", "--ttl", 120, "--run-id", a, "--rest")[0] == 0
    # re-entrant acquire by the holder renews it
    assert cli("lock", "acquire", "yahoo:browser", "--run-id", a, "--rest")[1]["lock"]["taken_over"]
    # expired lease -> b takes it over
    set_remote(remote, locks, locks.c.resource == "yahoo:browser",
               expires_at=datetime.now(UTC) - timedelta(seconds=5))
    assert cli("lock", "show", "--rest")[1]["locks"] == []
    assert len(cli("lock", "show", "--all", "--rest")[1]["locks"]) == 1
    code, out = cli("lock", "acquire", "yahoo:browser", "--run-id", b, "--rest")
    assert code == 0 and out["lock"]["holder_run_id"] == b and out["lock"]["taken_over"] is True
    assert cli("lock", "show", "yahoo:browser", "--rest")[1]["lock"]["holder_run_id"] == b
    code, out = cli("lock", "release", "yahoo:browser", "--run-id", b, "--rest")
    assert code == 0 and out["released"] is True
    assert cli("lock", "release", "yahoo:browser", "--run-id", b, "--rest")[1]["released"] is False
    # --force is a human override: not available over REST
    code, out = cli("lock", "release", "yahoo:browser", "--force", "--rest")
    assert code == 2 and "human" in out["message"]


# ------------------------------------------------------------------ tasks


def test_tasks_rest(remote):
    code, t1 = cli("task", "add", "first thing", "--area", "infra", "--priority", 1, "--rest")
    assert code == 0 and t1["task"]["task_id"] == "T-001"
    assert cli("task", "add", "first thing", "--rest")[0] == 5
    assert cli("task", "add", "second", "--rest")[1]["task"]["task_id"] == "T-002"
    assert [t["task_id"] for t in cli("task", "list", "--rest")[1]["tasks"]] == ["T-001", "T-002"]
    code, out = cli("task", "claim", "T-001", "--owner", 'agent "a", 1', "--rest")
    assert code == 0 and out["task"]["owner"] == 'agent "a", 1'  # quoting inside or=(...)
    code, out = cli("task", "claim", "T-001", "--owner", "b", "--rest")
    assert code == 3 and out["code"] == "conflict"
    assert cli("task", "claim", "T-001", "--owner", 'agent "a", 1', "--rest")[0] == 0  # re-entrant
    assert cli("task", "done", "T-001", "--run-id", "b", "--rest")[0] == 3
    code, out = cli("task", "update", "T-002", "--status", "blocked", "--description", "d", "--rest")
    assert code == 0 and out["task"]["status"] == "blocked"
    assert cli("task", "done", "T-001", "--run-id", 'agent "a", 1', "--rest")[0] == 0
    assert cli("task", "claim", "T-001", "--owner", "c", "--rest")[0] == 7  # done
    assert [t["task_id"] for t in cli("task", "list", "--rest")[1]["tasks"]] == ["T-002"]
    assert cli("task", "show", "T-009", "--rest")[0] == 6
    assert cli("task", "update", "T-009", "--status", "done", "--rest")[0] == 6


def test_task_done_cas_against_concurrent_claim(remote):
    cli("task", "add", "contested", "--rest")
    remote.on("PATCH", "tasks", lambda: set_remote(
        remote, tasks, tasks.c.task_id == "T-001", status="in_progress", owner="other"))
    code, out = cli("task", "done", "T-001", "--rest")
    assert code == 3 and remote.rows("tasks")[0]["status"] == "in_progress"


# ------------------------------------------------------------------ errors / plumbing


def test_auth_error_maps_to_rest_auth(remote, monkeypatch):
    monkeypatch.setattr(rest_ledger, "default_client", lambda: rest.RestClient(
        base_url="https://example.supabase.co", sleep=lambda s: None,
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "no"}))))
    code, out = cli("txn", "list", "--rest")
    assert code == 1 and out["code"] == "rest_auth"


def test_filter_helpers():
    assert rest_ledger.in_(("a", 'b"c')) == 'in.("a","b\\"c")'
    assert rest_ledger.ts(datetime(2026, 10, 18, 12, 0, tzinfo=UTC)) == "2026-10-18T12:00:00.000000Z"
    with pytest.raises(ValueError):
        rest_ledger.RestStore(rest.RestClient()).patch("tasks", [], {"a": 1})


def test_local_backend_is_the_ops_modules():
    from fantasy.ops import approvals as a
    from fantasy.ops import cli as ops_cli
    from fantasy.ops import ledger as led
    from fantasy.ops import locks as lk
    from fantasy.ops import tasks as tk
    from fantasy.ops import transactions as tx

    b = ops_cli._backend(False)
    assert (b.ledger, b.locks, b.approvals, b.transactions, b.tasks) == (led, lk, a, tx, tk)
    assert not hasattr(b, "store")  # no REST client is ever built without --rest


def test_local_path_never_calls_rest(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)

    def boom():
        raise AssertionError("REST used without --rest")

    monkeypatch.setattr(rest_ledger, "default_client", boom)
    code, out = cli("run", "start", "--agent", "x", "--mode", "live")
    rid = out["run_id"]
    assert code == 0
    assert cli("txn", "plan", "add_drop", "--payload", '{"a":1}', "--run-id", rid)[0] == 0
    assert cli("lock", "acquire", "yahoo:roster", "--run-id", rid)[0] == 0
    assert cli("task", "add", "t")[0] == 0
    assert cli("run", "end", "--run-id", rid)[0] == 0
    with engine().connect() as c:
        assert c.execute(agent_runs.select()).first().status == "succeeded"
