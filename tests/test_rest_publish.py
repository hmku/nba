"""`fantasy publish --rest` / `fantasy db ping --rest`: PostgREST upsert over httpx (mocked)."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db import rest
from fantasy.db.engine import db_url
from fantasy.db.schema import (
    agent_runs,
    draft_picks,
    drafts,
    metadata,
    model_runs,
    player_values,
    players,
    projections,
    tasks,
)
from fantasy.ops.common import engine
from fantasy.ops.testing import use_test_db

SECRET = "sb_secret_TESTKEY_do_not_leak_123"
JWT = "eyJhbGciOiJIUzI1NiJ9.payload.sig"
T0 = datetime(2026, 10, 18, 12, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)
    for k in (*rest.KEY_ENVS, "SUPABASE_URL"):
        monkeypatch.delenv(k, raising=False)


class Recorder:
    """MockTransport handler: records requests; GETs answer from `online`, POSTs with `status`."""

    def __init__(self, status=201, online=None, statuses=None):
        self.requests: list[httpx.Request] = []
        self.status = status
        self.statuses = list(statuses or [])  # consumed first, per request
        self.online = online or {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.statuses:
            st = self.statuses.pop(0)
            if isinstance(st, Exception):
                raise st
            return httpx.Response(st, json={"message": "err"})
        table = req.url.path.rsplit("/", 1)[1]
        if req.method == "GET":
            off = int(req.url.params.get("offset", 0))
            lim = int(req.url.params.get("limit", 1000))
            return httpx.Response(200, json=self.online.get(table, [])[off : off + lim])
        return httpx.Response(self.status)

    def posts(self, table=None):
        return [
            r for r in self.requests
            if r.method == "POST" and (table is None or r.url.path.endswith("/" + table))
        ]


def client(rec, key=None):
    return rest.RestClient(
        base_url="https://example.supabase.co", key=key,
        transport=httpx.MockTransport(rec), sleep=lambda s: None,
    )


def seed():
    with engine().begin() as c:
        c.execute(model_runs.insert().values(
            run_id="m1", kind="valuation", model_version="v1", created_at=T0,
            params={"punt": ["ft_pct"], "kappa": 1.5}))
        c.execute(players.insert(), [
            {"player_id": f"p{i}", "name": f"P {i}", "positions": ["PG", "SG"], "updated_at": T0}
            for i in range(1203)
        ])
        c.execute(player_values.insert(), [
            {"run_id": "m1", "player_id": "p1", "rank": 1, "value": float("nan"),
             "cat_values": {"pts": 1.2}},
        ])
        c.execute(projections.insert().values(run_id="m1", player_id="p1", season="2026-27"))
        c.execute(drafts.insert().values(
            draft_id="d1", status="live", num_teams=12, num_rounds=13, my_slot=1,
            order_type="snake_3rr"))
        c.execute(draft_picks.insert().values(
            draft_id="d1", overall_pick=1, round=1, team_slot=1, player_id="p1"))
        c.execute(agent_runs.insert().values(run_id="r1", agent="a", status="running",
                                             started_at=T0))
        c.execute(tasks.insert(), [
            {"task_id": "T-001", "title": "one", "status": "todo"},
            {"task_id": "T-002", "title": "two", "status": "todo"},
            {"task_id": "T-003", "title": "three", "status": "todo"},
        ])


def test_batching_order_headers_and_serialisation():
    seed()
    rec = Recorder(online={"tasks": [{"task_id": "T-001", "title": "one"}]})
    res = rest.publish_rest(db_url(), client=client(rec, SECRET))
    # batching: 1203 players -> 3 POSTs of 500/500/203
    sizes = [len(json.loads(r.content)) for r in rec.posts("players")]
    assert sizes == [500, 500, 203] and res["batches"]["players"] == 3
    assert res["rows"]["players"] == 1203
    # FK-safe order: parents posted before children, locks never
    order = [r.url.path.rsplit("/", 1)[1] for r in rec.posts()]
    first = {t: order.index(t) for t in set(order)}
    assert first["model_runs"] < first["player_values"] and first["model_runs"] < first["projections"]
    assert first["drafts"] < first["draft_picks"]
    assert "locks" not in order and "locks" not in res["rows"]
    assert res["order"] == [t.name for t in metadata.sorted_tables if t.name != "locks"]
    # headers
    req = rec.posts("players")[0]
    assert req.headers["apikey"] == SECRET and "authorization" not in req.headers
    assert req.headers["prefer"] == "resolution=merge-duplicates,return=minimal"
    assert req.url.params["on_conflict"] == "player_id"
    # serialisation: ISO datetimes, JSON columns as JSON, NaN -> null
    mr = json.loads(rec.posts("model_runs")[0].content)[0]
    assert mr["created_at"] == "2026-10-18T12:30:00+00:00"
    assert mr["params"] == {"punt": ["ft_pct"], "kappa": 1.5}
    pv = json.loads(rec.posts("player_values")[0].content)[0]
    assert pv["value"] is None and pv["cat_values"] == {"pts": 1.2}
    assert json.loads(rec.posts("players")[0].content)[0]["positions"] == ["PG", "SG"]


def test_composite_pk_on_conflict():
    seed()
    rec = Recorder()
    rest.publish_rest(db_url(), client=client(rec),
                      tables=["model_runs,player_values", "projections", "drafts", "draft_picks"])
    oc = {r.url.path.rsplit("/", 1)[1]: r.url.params["on_conflict"] for r in rec.posts()}
    assert oc == {
        "model_runs": "run_id", "player_values": "run_id,player_id",
        "projections": "run_id,player_id", "drafts": "draft_id",
        "draft_picks": "draft_id,overall_pick",
    }


def test_jwt_key_gets_bearer_and_no_key_sends_no_auth():
    assert rest.auth_headers(JWT) == {"apikey": JWT, "Authorization": f"Bearer {JWT}"}
    assert rest.auth_headers(SECRET) == {"apikey": SECRET}
    assert rest.auth_headers(None) == {}
    seed()
    rec = Recorder()
    rest.publish_rest(db_url(), client=client(rec), tables=["drafts"])
    h = rec.posts()[0].headers
    assert "apikey" not in h and "authorization" not in h


def test_unique_clash_skipped_and_reported():
    seed()
    online = {"tasks": [
        {"task_id": "T-001", "title": "one"},          # same row: fine
        {"task_id": "T-009", "title": "two"},          # same title, different PK -> skip T-002
        {"task_id": "T-003", "title": "other"},        # same PK, different title -> skip T-003
    ]}
    rec = Recorder(online=online)
    res = rest.publish_rest(db_url(), client=client(rec), tables=["tasks"])
    assert res["skipped"] == {"tasks": ["T-002", "T-003"]}
    sent = json.loads(rec.posts("tasks")[0].content)
    assert [r["task_id"] for r in sent] == ["T-001"]


def test_dry_run_sends_nothing():
    seed()
    rec = Recorder()
    res = rest.publish_rest(db_url(), client=client(rec), dry_run=True)
    assert rec.requests == []
    assert res["dry_run"] and res["rows"]["players"] == 1203 and res["batches"]["players"] == 3


def test_401_raises_auth_error_without_leaking_key():
    seed()
    rec = Recorder(status=401)
    with pytest.raises(rest.RestAuthError) as ei:
        rest.publish_rest(db_url(), client=client(rec, SECRET), tables=["drafts"])
    assert "Network secret" in ei.value.message and SECRET not in ei.value.message


def test_retry_on_5xx_and_network_then_409_reported():
    seed()
    rec = Recorder(statuses=[503, httpx.ConnectError("boom")])  # then success
    res = rest.publish_rest(db_url(), client=client(rec), tables=["drafts"])
    assert res["rows"]["drafts"] == 1 and not res["errors"] and len(rec.requests) == 3
    rec = Recorder(statuses=[503, 503, 503])
    res = rest.publish_rest(db_url(), client=client(rec), tables=["drafts"])
    assert "HTTP 503" in res["errors"]["drafts"] and len(rec.requests) == 3
    rec = Recorder(status=409)
    res = rest.publish_rest(db_url(), client=client(rec), tables=["drafts", "draft_picks"])
    assert set(res["errors"]) == {"drafts", "draft_picks"} and res["rows"]["drafts"] == 0


def test_bad_tables():
    with pytest.raises(ValueError, match="unknown"):
        rest.resolve_tables(["nope"])
    with pytest.raises(ValueError, match="ephemeral"):
        rest.resolve_tables(["locks"])


# ------------------------------------------------------------------ CLI


@pytest.fixture()
def mock_http(monkeypatch):
    """Route every RestClient created by the CLI through a Recorder."""
    holder = {"rec": Recorder()}
    orig = rest.RestClient.__init__

    def init(self, *a, **kw):
        orig(self, *a, **kw)
        self.transport = httpx.MockTransport(holder["rec"])
        self.sleep = lambda s: None

    monkeypatch.setattr(rest.RestClient, "__init__", init)
    return holder


def run(*args):
    res = CliRunner().invoke(app, list(args))
    return res.exit_code, res.stdout + (res.stderr if res.stderr_bytes is not None else "")


def test_cli_publish_rest_dry_run_and_live(mock_http, monkeypatch):
    seed()
    monkeypatch.setenv("SUPABASE_SECRET_KEY", SECRET)
    code, out = run("publish", "--rest", "--dry-run", "--json")
    assert code == 0 and SECRET not in out
    d = json.loads(out)
    assert d["ok"] and d["dry_run"] and d["auth"] == "SUPABASE_SECRET_KEY"
    assert mock_http["rec"].requests == []
    code, out = run("publish", "--rest", "--tables", "drafts", "--source", db_url())
    assert code == 0 and "drafts: insert-if-missing sent 1 rows" in out and SECRET not in out
    assert mock_http["rec"].posts()[0].headers["apikey"] == SECRET


def test_cli_publish_rest_401(mock_http, monkeypatch):
    seed()
    monkeypatch.setenv("SUPABASE_SECRET_KEY", SECRET)
    mock_http["rec"] = Recorder(status=401)
    code, out = run("publish", "--rest", "--tables", "drafts", "--json")
    d = json.loads(out)
    assert code == 1 and d["error"] == "rest_auth" and "Network secret" in d["message"]
    assert SECRET not in out


def test_cli_ping(mock_http, monkeypatch):
    mock_http["rec"] = Recorder(statuses=[401])
    code, out = run("db", "ping", "--rest", "--json")
    d = json.loads(out)
    assert code == 1 and d["error"] == "rest_auth" and d["ping"]["status"] == 401
    assert d["ping"]["auth"].startswith("none sent")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", JWT)
    mock_http["rec"] = Recorder()
    code, out = run("db", "ping", "--rest", "--json")
    d = json.loads(out)
    assert code == 0 and d["ping"]["auth_ok"] and JWT not in out
    req = mock_http["rec"].requests[0]
    assert req.method == "GET" and req.url.path == "/rest/v1/tasks"
    assert req.url.params["select"] == "task_id" and req.url.params["limit"] == "1"
    assert req.headers["authorization"] == f"Bearer {JWT}"
    code, out = run("db", "ping", "--json")  # SQL path against local sqlite
    assert code == 0


def test_proxy_denial_not_retried():
    rec = Recorder(statuses=[httpx.ProxyError("403 Forbidden")])
    res = rest.ping(client(rec))
    assert res["reachable"] is False and res["status"] is None and len(rec.requests) == 1
    assert "Allowed domains" in res["message"] and "example.supabase.co" in res["message"]
    seed()
    rec = Recorder(statuses=[httpx.ProxyError("403 Forbidden")])
    with pytest.raises(rest.RestUnreachableError):
        rest.publish_rest(db_url(), client=client(rec), tables=["drafts"])
    assert len(rec.requests) == 1


# ------------------------------------------------------------------ per-table publish policy


def _fake_client(tmp_path):
    from fake_postgrest import FakePostgrest

    fake = FakePostgrest(f"sqlite:///{tmp_path / 'remote.db'}")
    return fake, rest.RestClient(base_url="https://example.supabase.co",
                                 transport=httpx.MockTransport(fake), sleep=lambda s: None)


def test_stale_local_pending_approval_never_overwrites_remote_approved(tmp_path):
    """The user approved on the dashboard; a cloud job republishes its stale 'pending' copy."""
    from fantasy.db.schema import approvals, transactions

    fake, cl = _fake_client(tmp_path)
    row = {"approval_id": "apr-1", "run_id": None, "action_type": "add_drop", "summary": "s",
           "status": "pending", "requested_at": T0}
    txn = {"txn_id": "txn-1", "idempotency_key": "k1", "kind": "add_drop", "status": "awaiting_approval",
           "approval_id": "apr-1", "created_at": T0}
    with fake.engine.begin() as c:  # remote: already decided / advanced
        c.execute(approvals.insert().values(**{**row, "status": "approved", "decided_by": "harry"}))
        c.execute(transactions.insert().values(**{**txn, "status": "submitted"}))
    with engine().begin() as c:  # local: stale copy + one row that is new
        c.execute(approvals.insert().values(**row))
        c.execute(approvals.insert().values(**{**row, "approval_id": "apr-2"}))
        c.execute(transactions.insert().values(**txn))
    res = rest.publish_rest(db_url(), client=cl, tables=["approvals", "transactions"])
    assert res["errors"] == {} and res["policy"] == {"approvals": "insert", "transactions": "insert"}
    remote = {r["approval_id"]: r for r in fake.rows("approvals")}
    assert remote["apr-1"]["status"] == "approved" and remote["apr-1"]["decided_by"] == "harry"
    assert remote["apr-2"]["status"] == "pending"  # missing row inserted
    assert fake.rows("transactions")[0]["status"] == "submitted"
    assert res["inserted"] == {"approvals": 1, "transactions": 0}
    req = [r for r in fake.requests if r.method == "POST" and r.url.path.endswith("/approvals")][0]
    assert req.headers["prefer"] == "resolution=ignore-duplicates,return=representation"


def test_merge_tables_still_upsert_through_fake(tmp_path):
    fake, cl = _fake_client(tmp_path)
    with fake.engine.begin() as c:
        c.execute(players.insert().values(player_id="p1", name="Old", updated_at=T0))
    with engine().begin() as c:
        c.execute(players.insert().values(player_id="p1", name="New", updated_at=T0))
    res = rest.publish_rest(db_url(), client=cl, tables=["players"])
    assert res["policy"] == {"players": "merge"} and fake.rows("players")[0]["name"] == "New"


def test_publish_policy_covers_every_table():
    for t in metadata.tables:
        assert rest.publish_policy(t) in {"skip", "merge", "insert"}
    state = {"agent_runs", "approvals", "transactions", "tasks", "drafts", "agent_events", "decisions"}
    assert {t for t in state if rest.publish_policy(t) != "insert"} == set()
    assert rest.publish_policy("locks") == "skip"
    assert rest.publish_policy("some_future_table") == "insert"  # safe default
    assert not (rest.PUBLISH_MERGE & rest.PUBLISH_INSERT_ONLY)
