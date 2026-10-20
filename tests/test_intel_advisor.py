"""Advisor suggestions in `fantasy intel`: surfaced separately from Muse intel (SQLite and mocked REST)."""

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

SUGGESTION = {
    "category": "add_drop", "title": "Stream a C Thursday", "summary": "No C plays Thursday.",
    "evidence": [{"claim": "P(win week) +2pp", "value": 0.02, "se": 0.006, "label": "simulated"}],
    "proposed_change": None, "confidence": "medium", "urgency": "high", "links": [],
}


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)


def _event(event_id, run_id, agent, kind, message, ts, payload_extra=None):
    with engine().begin() as c:
        if not c.execute(agent_runs.select().where(agent_runs.c.run_id == run_id)).first():
            c.execute(agent_runs.insert().values(run_id=run_id, agent=agent, status="running",
                                                 mode="dry_run", started_at=ts))
        payload = {"report_kind": kind, **(payload_extra or {})}
        c.execute(agent_events.insert().values(event_id=event_id, run_id=run_id, ts=ts,
                                               kind="observation", message=message, payload=payload))


def seed():
    _event("muse-ev-1", "muse-2026-11-02", "muse", "trade", "Team A traded X for Y.",
           T0 - timedelta(hours=4), {"source": "messenger", "reported_by": "muse"})
    _event("advisor-ev-1", "advisor-2026-11-02", "advisor", "advisor_suggestion",
           "[add_drop] Stream a C Thursday", T0 - timedelta(hours=2),
           {"source": "advisor", "reported_by": "advisor", "details": SUGGESTION})
    _event("advisor-ev-old", "advisor-2026-10-01", "advisor", "advisor_suggestion", "[model] old",
           T0 - timedelta(days=30), {"source": "advisor", "details": {**SUGGESTION, "category": "model"}})
    # a muse-run event that claims to be from the advisor stays a Muse report (identity = run, not payload)
    _event("muse-ev-fake", "muse-2026-11-02", "muse", "other", "I am the advisor",
           T0 - timedelta(hours=1), {"source": "advisor", "reported_by": "advisor"})


def test_latest_has_separate_advisor_section():
    seed()
    out = intel.latest(intel.source(), at=T0)
    assert [r["event_id"] for r in out["advisor_suggestions"]] == ["advisor-ev-1"]  # old one outside 7d
    adv = out["advisor_suggestions"][0]
    assert adv["source"] == "advisor" and adv["reported_by"] == "advisor"
    assert adv["details"]["evidence"][0]["label"] == "simulated"
    assert {r["event_id"] for r in out["reports"]} == {"muse-ev-1", "muse-ev-fake"}
    fake = next(r for r in out["reports"] if r["event_id"] == "muse-ev-fake")
    assert fake["reported_by"] == "advisor"  # what the payload claims ...
    assert fake["run_id"].startswith("muse-")  # ... but it is filed (and listed) as Muse intel
    assert "advisor_note" in out


def test_list_by_source():
    seed()
    src = intel.source()
    muse = intel.list_intel(src)
    assert {r["event_id"] for r in muse["reports"]} == {"muse-ev-1", "muse-ev-fake"}
    adv = intel.list_intel(src, report_source="advisor")
    assert [r["event_id"] for r in adv["reports"]] == ["advisor-ev-1", "advisor-ev-old"]
    assert adv["snapshots"] == [] and "advisor_note" in adv
    both = intel.list_intel(src, report_source="all", what="reports")
    assert [r["event_id"] for r in both["reports"]] == ["muse-ev-fake", "advisor-ev-1", "muse-ev-1",
                                                       "advisor-ev-old"]
    only = intel.list_intel(src, report_source="all", kind="advisor_suggestion")
    assert {r["source"] for r in only["reports"]} == {"advisor"}
    with pytest.raises(ValueError):
        intel.list_intel(src, kind="advisor_suggestion")  # not a Muse report kind
    with pytest.raises(ValueError):
        intel.list_intel(src, report_source="claude")


def test_cli_source_advisor_and_show():
    seed()
    res = runner.invoke(app, ["intel", "list", "--source", "advisor", "--json"])
    assert res.exit_code == 0, res.stdout
    out = json.loads(res.stdout)
    assert out["report_source"] == "advisor"
    assert [r["event_id"] for r in out["reports"]] == ["advisor-ev-1", "advisor-ev-old"]
    res = runner.invoke(app, ["intel", "show", "advisor-ev-1", "--json"])
    shown = json.loads(res.stdout)
    assert shown["report"]["source"] == "advisor" and shown["note"] == intel.ADVISOR_NOTE
    res = runner.invoke(app, ["intel", "latest"])
    assert res.exit_code == 0
    assert "advisor suggestions" in res.stdout and "[advisor] [add_drop] Stream a C" in res.stdout


def test_rest_advisor_query():
    ev = {"event_id": "advisor-ev-9", "run_id": "advisor-2026-11-02", "ts": "2026-11-02T08:00:00+00:00",
          "kind": "observation", "message": "[trade] x",
          "payload": {"report_kind": "advisor_suggestion", "source": "advisor", "details": SUGGESTION}}
    muse = {"event_id": "muse-ev-9", "run_id": "muse-2026-11-02", "ts": "2026-11-02T07:00:00+00:00",
            "kind": "observation", "message": "m", "payload": {"report_kind": "trade"}}
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        p = req.url.params
        rows = [ev, muse] if req.url.path.endswith("/agent_events") else []
        if "run_id" in p:  # PostgREST like.<prefix>*
            prefix = p["run_id"].removeprefix("like.").rstrip("*")
            rows = [r for r in rows if r["run_id"].startswith(prefix)]
        return httpx.Response(200, json=rows)

    cl = rest.RestClient(base_url="https://example.supabase.co", key="sb_secret_TEST",
                         transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out = intel.latest(intel.source(True, cl), kinds=["matchup"], at=T0)
    assert [r["event_id"] for r in out["advisor_suggestions"]] == ["advisor-ev-9"]
    adv_req = [r for r in seen if r.url.params.get("run_id") == "like.advisor-*"]
    assert adv_req and adv_req[0].url.params["payload->>report_kind"] == "eq.advisor_suggestion"
    assert all(r["source"] != "advisor" for r in out["reports"])
