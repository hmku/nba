"""`fantasy intel list|show|latest`: league snapshots + Muse reports, on SQLite and via REST (mocked)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db import rest
from fantasy.db.schema import agent_events, agent_runs
from fantasy.ops import intel
from fantasy.ops.common import engine
from fantasy.ops.testing import use_test_db

runner = CliRunner()
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

# Shared cross-language vector: web/tests/agent.test.mjs asserts the same hash/ids for this payload.
VECTOR = {
    "team_name": "Tip-Top Jokers",
    "players": [{"name": "Nikola Jokić", "status": "healthy", "positions": ["C"], "slot": "C"}],
    "adds_used_this_week": 2,
}
VECTOR_SHA = "c1f53ea67bc02cfc29ee4401af2f4a1aedcbd1c6bbad43be6652e91d0e920ebe"


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)


def my_team(adds=1, status="healthy"):
    return {
        "team_name": "Tip-Top Jokers",
        "players": [
            {"name": "Nikola Jokic", "positions": ["C"], "status": status, "slot": "C"},
            {"name": "Tyrese Haliburton", "positions": ["PG"], "status": "healthy", "slot": "PG"},
        ],
        "adds_used_this_week": adds,
    }


def matchup(week=3, pts=(410, 388)):
    return {"week": week, "opponent": "Team Thomas",
            "categories": {"pts": {"me": pts[0], "opp": pts[1]}, "fg_pct": {"me": ".481", "opp": ".466"}},
            "games_remaining": {"me": 14, "opp": 11}}


def add_report(event_id, kind, message, ts, details=None, run_id="muse-2026-11-02"):
    with engine().begin() as c:
        if not c.execute(agent_runs.select().where(agent_runs.c.run_id == run_id)).first():
            c.execute(agent_runs.insert().values(run_id=run_id, agent="muse", status="running",
                                                 mode="dry_run", started_at=ts))
        c.execute(agent_events.insert().values(
            event_id=event_id, run_id=run_id, ts=ts, kind="observation", message=message,
            payload={"report_kind": kind, "source": "messenger", "details": details,
                     "reported_by": "muse"}))


def seed():
    a = intel.record_snapshot("my_team", my_team(adds=1), observed_at=T0 - timedelta(days=2),
                              reported_by="muse", summary="old roster")
    b = intel.record_snapshot("my_team", my_team(adds=2, status="O"), observed_at=T0 - timedelta(days=1),
                              reported_by="muse", summary="Jokic out")
    m = intel.record_snapshot("matchup", matchup(), week=3, observed_at=T0 - timedelta(hours=3),
                              reported_by="muse", source_url="https://basketball.fantasysports.yahoo.com/nba/44495/matchup")
    add_report("muse-ev-1", "chat_digest", "Quiet day; trade rumours about Team A.", T0 - timedelta(hours=5),
               {"period_start": "2026-11-01T18:00", "period_end": "2026-11-02T07:00",
                "excerpts": [{"author": "Thomas", "text": "anyone want Trae?"}]})
    add_report("muse-ev-2", "trade", "Team A traded X for Y.", T0 - timedelta(hours=4))
    add_report("muse-ev-3", "injury_news", "Old news", T0 - timedelta(days=30))
    # a non-report event in a muse run and a report-like event outside muse runs are both ignored
    with engine().begin() as c:
        c.execute(agent_events.insert().values(event_id="muse-ev-x", run_id="muse-2026-11-02", ts=T0,
                                               kind="note", message="heartbeat", payload={"x": 1}))
        c.execute(agent_runs.insert().values(run_id="draft-1", agent="draft", status="running",
                                             mode="dry_run", started_at=T0))
        c.execute(agent_events.insert().values(event_id="ev-other", run_id="draft-1", ts=T0,
                                               kind="observation", message="x",
                                               payload={"report_kind": "trade"}))
    return a, b, m


def cli(*args):
    res = runner.invoke(app, ["intel", *args, "--json"])
    return res.exit_code, json.loads(res.stdout)


def test_hash_vector_matches_web():
    assert intel.payload_sha256(VECTOR) == VECTOR_SHA
    assert intel.snapshot_id("my_team", 3, VECTOR_SHA) == "snap_44d6ddba65e3f6156f83d427715a6dfa"
    assert intel.snapshot_id("my_team", None, VECTOR_SHA) == "snap_0467e6a2854e20fdb7a9a39403b6ff33"
    # key order does not matter
    assert intel.payload_sha256(dict(reversed(list(VECTOR.items())))) == VECTOR_SHA


def test_record_dedupes_and_bumps_last_seen():
    first = intel.record_snapshot("my_team", my_team(), observed_at=T0 - timedelta(days=1))
    again = intel.record_snapshot("my_team", my_team(), observed_at=T0)
    assert first["status"] == "stored" and again["status"] == "duplicate"
    assert first["snapshot_id"] == again["snapshot_id"]
    s = intel.SqlSource().snapshot(first["snapshot_id"])
    assert s["last_seen_at"] == T0.isoformat()
    assert s["observed_at"] == (T0 - timedelta(days=1)).isoformat()
    # same payload in another week is a different snapshot
    assert intel.record_snapshot("my_team", my_team(), week=4)["status"] == "stored"
    with pytest.raises(ValueError):
        intel.record_snapshot("bogus", my_team())


def test_cli_list_and_filters():
    a, b, m = seed()
    code, out = cli("list")
    assert code == 0 and out["ok"] and out["source"] == "sql"
    ids = [s["snapshot_id"] for s in out["snapshots"]]
    assert ids == [m["snapshot_id"], b["snapshot_id"], a["snapshot_id"]]  # newest seen first
    assert "payload" not in out["snapshots"][0]
    assert [r["event_id"] for r in out["reports"]] == ["muse-ev-2", "muse-ev-1", "muse-ev-3"]
    assert "never" in out["note"]

    code, out = cli("list", "--kind", "my_team", "--payload")
    assert [s["kind"] for s in out["snapshots"]] == ["my_team", "my_team"]
    assert out["snapshots"][0]["payload"]["players"][0]["status"] == "O"
    assert out["reports"] == []  # my_team is not a report kind

    code, out = cli("list", "--kind", "chat_digest")
    assert out["snapshots"] == [] and [r["event_id"] for r in out["reports"]] == ["muse-ev-1"]
    assert out["reports"][0]["details"]["excerpts"][0]["author"] == "Thomas"

    code, out = cli("list", "--since", (T0 - timedelta(days=3)).isoformat(), "--what", "reports")
    assert "snapshots" not in out
    assert [r["event_id"] for r in out["reports"]] == ["muse-ev-2", "muse-ev-1"]

    code, out = cli("list", "--kind", "nope")
    assert code == 2 and out["code"] == "usage"
    code, out = cli("list", "--since", "yesterday-ish")
    assert code == 2 and out["code"] == "usage"


def test_cli_show():
    _, b, _ = seed()
    code, out = cli("show", b["snapshot_id"])
    assert code == 0 and out["type"] == "snapshot"
    assert out["snapshot"]["payload"]["adds_used_this_week"] == 2
    code, out = cli("show", "muse-ev-2")
    assert code == 0 and out["type"] == "report" and out["report"]["report_kind"] == "trade"
    code, out = cli("show", "snap_doesnotexist")
    assert code == 6 and out["code"] == "not_found"
    code, out = cli("show", "muse-ev-x")  # not a report
    assert code == 6


def test_latest_sql(monkeypatch):
    _, b, m = seed()
    monkeypatch.setattr(intel, "now", lambda: T0)
    code, out = cli("latest")
    assert code == 0
    snaps = out["snapshots"]
    assert set(snaps) == set(intel.SNAPSHOT_KINDS)
    assert snaps["my_team"]["snapshot_id"] == b["snapshot_id"]
    assert snaps["my_team"]["age_hours"] == 24.0
    assert snaps["matchup"]["payload"]["categories"]["pts"] == {"me": 410, "opp": 388}
    assert snaps["standings"] is None and "standings" in out["missing_kinds"]
    assert [r["event_id"] for r in out["chat_digests"]] == ["muse-ev-1"]
    assert [r["event_id"] for r in out["reports"]] == ["muse-ev-2"]  # 7d window, digests separate

    code, out = cli("latest", "--kind", "matchup,my_team", "--since", "60d")
    assert list(out["snapshots"]) == ["matchup", "my_team"]
    assert [r["event_id"] for r in out["reports"]] == ["muse-ev-2", "muse-ev-3"]


def test_latest_prefers_reconfirmed_snapshot():
    a = intel.record_snapshot("my_team", my_team(adds=1), observed_at=T0 - timedelta(days=2))
    intel.record_snapshot("my_team", my_team(adds=2), observed_at=T0 - timedelta(days=1))
    intel.record_snapshot("my_team", my_team(adds=1), observed_at=T0)  # roster A seen again
    out = intel.latest(intel.SqlSource(), kinds=["my_team"], at=T0)
    assert out["snapshots"]["my_team"]["snapshot_id"] == a["snapshot_id"]
    assert out["snapshots"]["my_team"]["age_hours"] == 0


def test_text_output():
    seed()
    res = runner.invoke(app, ["intel", "latest"])
    assert res.exit_code == 0 and "my_team" in res.stdout and "untrusted" in res.stdout


def test_parse_since():
    assert intel.parse_since("24h", at=T0) == T0 - timedelta(hours=24)
    assert intel.parse_since("2w", at=T0) == T0 - timedelta(weeks=2)
    assert intel.parse_since("2026-11-01") == datetime(2026, 11, 1, tzinfo=UTC)
    assert intel.parse_since(None) is None
    with pytest.raises(ValueError):
        intel.parse_since("soon")


# ------------------------------------------------------------------ REST (mocked PostgREST)


class FakeRest:
    def __init__(self, tables):
        self.tables = tables
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        table = req.url.path.rsplit("/", 1)[1]
        rows = list(self.tables.get(table, []))
        p = req.url.params
        for col in ("kind", "snapshot_id", "event_id"):
            if col in p:
                rows = [r for r in rows if r.get(col) == p[col].removeprefix("eq.")]
        if "payload->>report_kind" in p:
            want = p["payload->>report_kind"]
            rows = [r for r in rows if want == "not.is.null" or r["payload"]["report_kind"] == want.removeprefix("eq.")]
        if "limit" in p:
            rows = rows[: int(p["limit"])]
        return httpx.Response(200, json=rows)


def test_rest_source_queries_and_auth_header(monkeypatch):
    snap = {"snapshot_id": "snap_1", "ts": "2026-11-02T09:00:00+00:00", "reported_by": "muse",
            "kind": "matchup", "week": 3, "observed_at": "2026-11-02T09:00:00Z", "source_url": None,
            "payload": matchup(), "payload_sha256": "abc", "summary": "wk3", "last_seen_at": None}
    ev = {"event_id": "muse-ev-9", "run_id": "muse-2026-11-02", "ts": "2026-11-02T08:00:00+00:00",
          "kind": "observation", "message": "digest",
          "payload": {"report_kind": "chat_digest", "details": {"excerpts": []}, "source": "messenger"}}
    fake = FakeRest({"league_snapshots": [snap], "agent_events": [ev]})
    cl = rest.RestClient(base_url="https://example.supabase.co", key="sb_secret_TEST",
                         transport=httpx.MockTransport(fake), sleep=lambda s: None)
    src = intel.source(use_rest=True, client=cl)
    out = intel.latest(src, kinds=["matchup", "standings"], at=T0)
    assert out["source"] == "rest"
    assert out["snapshots"]["matchup"]["observed_at"] == "2026-11-02T09:00:00+00:00"
    assert out["snapshots"]["matchup"]["age_hours"] == 3.0
    assert out["snapshots"]["standings"] is None
    assert [r["event_id"] for r in out["chat_digests"]] == ["muse-ev-9"]
    first = fake.requests[0]
    assert first.headers["apikey"] == "sb_secret_TEST"
    assert first.url.params["kind"] == "eq.matchup" and first.url.params["limit"] == "1"
    ev_req = [r for r in fake.requests if r.url.path.endswith("/agent_events")][0]
    assert ev_req.url.params["run_id"] == "like.muse-*"
    lst = intel.list_intel(src, since=T0 - timedelta(days=1))
    q = [r for r in fake.requests if r.url.path.endswith("/league_snapshots")][-1].url.params
    assert q["or"].startswith("(observed_at.gte.2026-11-01T12:00:00+00:00")
    assert lst["snapshots"][0]["snapshot_id"] == "snap_1"
    assert intel.show(src, "muse-ev-9")["report"]["report_kind"] == "chat_digest"


def test_rest_auth_error_is_reported():
    cl = rest.RestClient(base_url="https://example.supabase.co",
                         transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})),
                         sleep=lambda s: None)
    with pytest.raises(rest.RestAuthError):
        intel.list_intel(intel.source(True, cl))


def test_parse_since_edges():
    assert intel.parse_since(" 90m ", at=T0) == T0 - timedelta(minutes=90)
    assert intel.parse_since("1.5H", at=T0) == T0 - timedelta(hours=1.5)
    assert intel.parse_since("   ") is None
    assert intel.parse_since("2026-11-01T05:00:00Z") == datetime(2026, 11, 1, 5, tzinfo=UTC)
    with pytest.raises(ValueError, match="--since"):
        intel.parse_since("5y")


def test_list_intel_argument_checks():
    src = intel.source()
    for kw, msg in (({"what": "both"}, "--what"), ({"limit": 0}, "--limit"), ({"limit": 1001}, "--limit"),
                    ({"report_source": "x"}, "--source"),
                    ({"kind": "advisor_suggestion"}, "invalid kind")):  # advisor kind on the muse stream
        with pytest.raises(ValueError, match=msg):
            intel.list_intel(src, **kw)
    out = intel.list_intel(src, kind="advisor_suggestion", report_source="advisor")
    assert out["snapshots"] == [] and out["reports"] == [] and "advisor_note" in out
