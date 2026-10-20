"""`fantasy advise post|status|list`: advice rows in model_runs (temp SQLite; REST via a fake PostgREST)."""

import json
from pathlib import Path

import httpx
import pytest
from fake_postgrest import FakePostgrest
from sqlalchemy import select
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db import rest
from fantasy.db.schema import model_runs
from fantasy.ops import advice
from fantasy.ops.common import engine
from fantasy.ops.testing import use_test_db

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)


def sample(**over):
    s = {
        "type": "trade",
        "title": "A for B",
        "status": "idea",
        "headline": "Swap A for B.",
        "verdict": "Do it if the refill lands.",
        "requires_user_yes": True,
        "metrics": [{"label": "ΔP(title)", "value": 8.6, "se": 0.6, "unit": "pp", "delta": True,
                     "evidence": "simulated"}],
        "scenarios": [{"name": "base", "ΔP(title)": "+8.6"}],
        "risks": ["health"],
        "links": [{"label": "note", "path": "docs/research/x.md"}],
    }
    s.update(over)
    return s


def write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


def rows():
    with engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(select(model_runs).order_by(model_runs.c.created_at))]


def test_post_writes_versioned_model_run(tmp_path):
    p = write(tmp_path, "2026-10-20-ab.json", sample())
    res = runner.invoke(app, ["advise", "post", "--file", str(p), "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["ok"] and out["advice_id"] == "2026-10-20-ab" and out["version"] == 1
    [r] = rows()
    assert r["run_id"] == out["run_id"] and r["kind"] == "advice"
    assert r["model_version"] == advice.ADVICE_VERSION
    s = r["summary"]
    assert s["schema"] == "advice/1" and s["supersedes"] is None
    assert s["status_history"][0]["status"] == "idea"
    assert r["inputs"]["content_sha256"] and r["params"]["command"] == "post"


def test_repost_identical_is_noop_and_edit_appends(tmp_path):
    p = write(tmp_path, "item.json", sample())
    first = advice.post(sample(), advice.store(), source_file=str(p))
    again = advice.post(sample(), advice.store(), source_file=str(p))
    assert again["unchanged"] and again["run_id"] == first["run_id"]
    edited = advice.post(sample(headline="Swap A for B, revised."), advice.store(), source_file=str(p))
    assert not edited["unchanged"] and edited["version"] == 2
    assert len(rows()) == 2


def test_status_appends_version_and_repost_does_not_revert(tmp_path):
    first = advice.post(sample(), advice.store(), advice_id="item-1")
    res = runner.invoke(app, ["advise", "status", first["run_id"], "awaiting_yes", "--note", "asked", "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["version"] == 2 and out["previous_run_id"] == first["run_id"] and out["status"] == "awaiting_yes"
    latest = rows()[-1]["summary"]
    assert latest["supersedes"] == first["run_id"]
    assert [h["status"] for h in latest["status_history"]] == ["idea", "awaiting_yes"]
    # the earlier row is untouched (append-only)
    assert rows()[0]["summary"]["status"] == "idea"
    # re-posting the original file (status idea) keeps the newer status, with a warning
    again = advice.post(sample(), advice.store(), advice_id="item-1")
    assert again["unchanged"] and again["status"] == "awaiting_yes" and again["warnings"]
    # same status again is a no-op
    assert advice.set_status(advice.store(), "item-1", "awaiting_yes")["unchanged"]


def test_approved_needs_evidence_of_the_users_yes():
    advice.post(sample(), advice.store(), advice_id="item-2")
    res = runner.invoke(app, ["advise", "status", "item-2", "approved", "--json"])
    assert res.exit_code == 2 and json.loads(res.output)["code"] == "usage"
    ok = advice.set_status(advice.store(), "item-2", "approved", approval_id="apr-1")
    assert ok["status"] == "approved" and rows()[-1]["summary"]["approval_id"] == "apr-1"


def test_status_unknown_ref_and_bad_status():
    res = runner.invoke(app, ["advise", "status", "nope", "done", "--json"])
    assert res.exit_code != 0 and json.loads(res.output)["code"] == "not_found"
    advice.post(sample(), advice.store(), advice_id="item-3")
    res = runner.invoke(app, ["advise", "status", "item-3", "maybe", "--json"])
    assert res.exit_code == 2


@pytest.mark.parametrize("bad, msg", [
    ({"type": "swap"}, "type"),
    ({"status": "pending"}, "status"),
    ({"verdict": "a\nb\nc\nd"}, "verdict"),
    ({"requires_user_yes": "yes"}, "requires_user_yes"),
    ({"metrics": [{"label": "x", "value": 1, "evidence": "vibes"}]}, "evidence"),
    ({"metrics": [{"label": "x", "value": 1, "se": -1, "evidence": "fitted"}]}, "se"),
    ({"metrics": [{"label": "x", "value": None, "evidence": "fitted"}]}, "value"),
    ({"scenarios": [{"name": "a", "cell": [1]}]}, "scalars"),
    ({"for_date": "Oct 21"}, "for_date"),
    ({"links": ["docs/x.md"]}, "links"),
])
def test_validation_rejects(bad, msg):
    with pytest.raises(advice.AdviceError, match=msg):
        advice.validate(sample(**bad))


def test_cli_post_reports_validation_error(tmp_path):
    p = write(tmp_path, "bad.json", sample(type="swap"))
    res = runner.invoke(app, ["advise", "post", "--file", str(p), "--json"])
    assert res.exit_code == 2 and json.loads(res.output)["code"] == "usage"


def test_list_latest_per_item_and_filters():
    a = advice.post(sample(title="A"), advice.store(), advice_id="item-a")
    advice.post(sample(title="B", status="done", requires_user_yes=False), advice.store(), advice_id="item-b")
    advice.set_status(advice.store(), a["run_id"], "rejected", note="user said no")
    res = runner.invoke(app, ["advise", "list", "--json"])
    items = json.loads(res.output)["items"]
    assert {i["advice_id"]: i["status"] for i in items} == {"item-a": "rejected", "item-b": "done"}
    assert next(i for i in items if i["advice_id"] == "item-a")["versions"] == 2
    assert json.loads(runner.invoke(app, ["advise", "list", "--open", "--json"]).output)["count"] == 0
    assert json.loads(runner.invoke(app, ["advise", "list", "--all-versions", "--json"]).output)["count"] == 3


def test_seed_files_validate():
    for p in sorted((REPO / "data" / "advice").glob("*.json")):
        summary, params, inputs = advice.load_file(p)
        s, _ = advice.validate(summary)
        assert all(m["evidence"] in advice.EVIDENCE for m in s["metrics"]), p


def test_rest_store_round_trip(tmp_path):
    fake = FakePostgrest(f"sqlite:///{tmp_path / 'remote.db'}")
    cl = rest.RestClient(base_url="https://example.supabase.co", key="sb_secret_TEST",
                         transport=httpx.MockTransport(fake), sleep=lambda s: None)
    st = advice.store(use_rest=True, client=cl)
    first = advice.post(sample(), st, advice_id="item-r")
    assert advice.post(sample(), st, advice_id="item-r")["unchanged"]
    advice.set_status(st, "item-r", "awaiting_yes")
    [item] = advice.list_advice(st)
    assert item["status"] == "awaiting_yes" and item["version"] == 2
    assert item["first_created_at"] is not None and first["run_id"] != item["run_id"]
    assert all(r.headers["apikey"] == "sb_secret_TEST" for r in fake.requests)
