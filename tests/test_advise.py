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
        "rationale": ["B fills our weakest category.", "- A is redundant behind C."],
        "why_now": {"before": "At the draft we took A: higher value and ADP.",
                    "changed": ["B's role grew (news)", {"text": "inseason-0.3.0 adds refill", "kind": "model"}],
                    "evidence": ["ΔP(title)"]},
    }
    s.update(over)
    return {k: v for k, v in s.items() if v is not None}


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
        # lenient: seeds posted before advice-0.2.0 may lack rationale/why_now (a warning, not an error)
        s, _ = advice.validate(summary, strict=False)
        assert all(m["evidence"] in advice.EVIDENCE for m in s["metrics"]), p


def test_why_validated_and_kept():
    s, w = advice.validate(sample())
    assert s["rationale"] == ["B fills our weakest category.", "- A is redundant behind C."]
    assert s["why_now"]["changed"][1] == {"text": "inseason-0.3.0 adds refill", "kind": "model"}
    assert not w
    s, _ = advice.validate(sample(rationale="One paragraph."))
    assert s["rationale"] == ["One paragraph."]
    _, w = advice.validate(sample(why_now={"before": "b", "changed": ["c"], "evidence": ["nope"]}))
    assert any("'nope'" in x for x in w)


@pytest.mark.parametrize("over", [
    {"rationale": None}, {"why_now": None}, {"rationale": None, "why_now": None},
    {"type": "add_drop", "status": "awaiting_yes", "why_now": None},
    {"type": "stream", "rationale": None},
])
def test_open_actionable_items_require_why(over):
    with pytest.raises(advice.AdviceError, match="required for an open"):
        advice.validate(sample(**over))
    _, w = advice.validate(sample(**over), strict=False)
    assert any("required for an open" in x for x in w)


@pytest.mark.parametrize("over", [
    {"type": "lineup"}, {"type": "il"}, {"status": "done"}, {"status": "approved"}, {"status": "rejected"},
])
def test_why_optional_for_lineups_il_and_decided_items(over):
    s, w = advice.validate(sample(rationale=None, why_now=None, **over))
    assert "rationale" not in s and not any("required" in x for x in w)


@pytest.mark.parametrize("bad, msg", [
    ({"rationale": []}, "rationale: expected 1-6"),
    ({"rationale": ["x"] * 7}, "rationale: expected 1-6"),
    ({"rationale": ["ok", " "]}, r"rationale\[1\]"),
    ({"rationale": ["x" * 601]}, "at most 600"),
    ({"why_now": "because"}, "why_now: expected an object"),
    ({"why_now": {"before": "b", "changed": ["c"], "after": "x"}}, "unknown keys"),
    ({"why_now": {"changed": ["c"]}}, "why_now.before"),
    ({"why_now": {"before": "b" * 801, "changed": ["c"]}}, "at most 800"),
    ({"why_now": {"before": "b", "changed": []}}, "why_now.changed"),
    ({"why_now": {"before": "b", "changed": ["c"] * 9}}, "why_now.changed"),
    ({"why_now": {"before": "b", "changed": ["c" * 401]}}, "at most 400"),
    ({"why_now": {"before": "b", "changed": [{"text": "c", "kind": "vibes"}]}}, "kind"),
    ({"why_now": {"before": "b", "changed": [{"txt": "c"}]}}, "unknown keys"),
    ({"why_now": {"before": "b", "changed": [3]}}, "string or"),
    ({"why_now": {"before": "b", "changed": ["c"], "evidence": "ΔP"}}, "why_now.evidence"),
])
def test_why_validation_rejects(bad, msg):
    with pytest.raises(advice.AdviceError, match=msg):
        advice.validate(sample(**bad))


def test_post_requires_why_only_for_new_content(tmp_path):
    old = sample(rationale=None, why_now=None)
    # an item posted before advice-0.2.0 (inserted directly, as the old writer did)
    full = {"schema": "advice/1", "advice_id": "old-1", "version": 1, "supersedes": None, **old,
            "status_history": [{"status": "idea", "at": "2026-10-20T00:00:00", "by": "post"}]}
    advice.store().insert(advice._row(full, {"command": "post"}, {}))
    # unchanged re-post of the old item: no-op with a warning, not an error
    again = advice.post(old, advice.store(), advice_id="old-1")
    assert again["unchanged"] and any("required for an open" in w for w in again["warnings"])
    # a status change on it warns too
    st = advice.set_status(advice.store(), "old-1", "awaiting_yes", note="asked")
    assert st["warnings"] and "rationale and why_now missing" in st["warnings"][0]
    # changed content without the why is refused (CLI: usage error)
    with pytest.raises(advice.AdviceError, match="required for an open"):
        advice.post({**old, "headline": "new"}, advice.store(), advice_id="old-1")
    p = write(tmp_path, "new-item.json", sample(why_now=None))
    res = runner.invoke(app, ["advise", "post", "--file", str(p), "--json"])
    assert res.exit_code == 2 and json.loads(res.output)["code"] == "usage"
    # adding the why appends a version
    ok = advice.post(sample(), advice.store(), advice_id="old-1")
    assert not ok["unchanged"] and ok["version"] == 3
    assert rows()[-1]["summary"]["why_now"]["before"].startswith("At the draft")


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


@pytest.mark.parametrize("bad, msg", [
    ({"week": True}, "week"), ({"week": 0}, "week"), ({"headline": "a\nb"}, "headline: one line"),
    ({"title": "  "}, "title"), ({"metrics": {"label": "x"}}, "metrics: expected a list"),
    ({"metrics": [{"label": "x", "value": 1, "evidence": "prior", "delta": "yes"}]}, "delta"),
    ({"metrics": [{"label": "x", "value": 1, "evidence": "prior", "why": 1}]}, "unknown keys"),
    ({"risks": ["ok", " "]}, "risks"), ({"scenario_columns": ["a", ""]}, "scenario_columns"),
    ({"links": [{"label": "x"}]}, r"links\[0\]\.path"), ({"advice_id": "Bad Id"}, "advice_id"),
])
def test_validation_rejects_more(bad, msg):
    with pytest.raises(advice.AdviceError, match=msg):
        advice.validate(sample(**bad))


def test_validation_warnings_and_lineup_without_metrics():
    s, w = advice.validate(sample(type="lineup", metrics=[], scenario_columns=["name", "nope"], extra=1))
    assert s["metrics"] == [] and s["extra"] == 1
    assert any("unknown keys" in x for x in w) and any("'nope'" in x for x in w)
    assert not any("no metrics" in x for x in w)  # lineups may carry no metrics
    assert any("no metrics" in x for x in advice.validate(sample(metrics=[]))[1])


def test_slug_and_advice_id_normalisation():
    assert advice.slug("Trade: Ware + VanVleet!") == "trade-ware-vanvleet"
    assert advice.slug("!!!") == "advice" and len(advice.slug("x" * 200)) == 80
    res = advice.post(sample(), advice.store(), advice_id="My Trade #1")
    assert res["advice_id"] == "my-trade-1"


def test_latest_per_item_groups_rows_without_advice_id_by_run_id():
    rows = [{"run_id": "r1", "summary": {"version": 1}, "created_at": "2026-10-20T01:00:00"},
            {"run_id": "r2", "summary": None, "created_at": "2026-10-20T02:00:00"},
            {"run_id": "r3", "summary": {"advice_id": "a", "version": 2}, "created_at": "2026-10-20T00:00:00"},
            {"run_id": "r4", "summary": {"advice_id": "a", "version": 1}, "created_at": "2026-10-20T03:00:00"}]
    out = advice.latest_per_item(rows)
    assert [r["run_id"] for r in out] == ["r2", "r1", "r3"]  # newest head first; version beats created_at
    head = out[-1]
    assert head["versions"] == 2 and head["first_created_at"] == "2026-10-20T03:00:00"


# ------------------------------------------------------------------ concise limits (advice-0.3.0)


@pytest.mark.parametrize("over, msg", [
    ({"title": "t" * 71}, "title 71 chars > 70"),
    ({"headline": "h" * 121}, "headline 121 chars > 120"),
    ({"verdict": "v" * 201}, "verdict 201 chars > 200"),
    ({"verdict": "One. Two. Three."}, "verdict 3 sentences > 2"),
    ({"verdict": "One.\nTwo\nThree"}, "verdict 3 sentences > 2"),
    ({"rationale": ["a", "b", "c", "d", "e"]}, "rationale 5 items > 4"),
    ({"rationale": ["r" * 161]}, r"rationale\[0\] 161 chars > 160"),
    ({"why_now": {"before": "b" * 201, "changed": ["c"]}}, "why_now.before 201 chars > 200"),
    ({"why_now": {"before": "b", "changed": ["1", "2", "3", "4"]}}, "why_now.changed 4 items > 3"),
    ({"why_now": {"before": "b", "changed": [{"text": "c" * 141}]}}, r"why_now.changed\[0\] 141 chars > 140"),
    ({"risks": ["r"] * 5}, "risks 5 items > 4"),
    ({"risks": ["r" * 121]}, r"risks\[0\] 121 chars > 120"),
    ({"metrics": [{"label": f"m{i}", "value": 1, "evidence": "prior"} for i in range(7)]}, "metrics 7 > 6"),
    ({"metrics": [{"label": f"m{i}", "value": 1, "evidence": "prior", "key": True} for i in range(4)]},
     "flagged key: true 4 > 3"),
])
def test_concise_limits_strict_error_lenient_warning(over, msg):
    with pytest.raises(advice.AdviceError, match=msg):
        advice.validate(sample(**over))
    _, w = advice.validate(sample(**over), strict=False)
    assert any("too long for the Inbox card" in x for x in w)


def test_concise_error_lists_everything_to_shorten():
    with pytest.raises(advice.AdviceError) as e:
        advice.validate(sample(title="t" * 80, risks=["r" * 130]))
    assert "title 80 chars > 70" in str(e.value) and "risks[0] 130 chars > 120" in str(e.value)


def test_concise_sample_and_key_flag_kept():
    m = [{"label": "Title", "value": 1.8, "se": 0.3, "unit": "pp", "evidence": "simulated", "key": True},
         {"label": "x", "value": 1, "evidence": "prior", "key": False}]
    s, w = advice.validate(sample(metrics=m, why_now=None, status="done"))
    assert s["metrics"][0]["key"] is True and s["metrics"][1]["key"] is False and not w
    with pytest.raises(advice.AdviceError, match="key: expected true/false"):
        advice.validate(sample(metrics=[{**m[0], "key": "yes"}]))
    assert advice.sentences("Up 8.6 pp. Do it! Now?") == ["Up 8.6 pp.", "Do it!", "Now?"]


def test_post_refuses_long_new_content_but_old_long_rows_still_load(tmp_path):
    long = sample(verdict="One. Two. Three.")
    # an item posted before advice-0.3.0 (inserted directly): an unchanged re-post only warns
    full = {"schema": "advice/1", "advice_id": "long-1", "version": 1, "supersedes": None, **long,
            "status_history": [{"status": "idea", "at": "2026-10-20T00:00:00", "by": "post"}]}
    advice.store().insert(advice._row(full, {"command": "post"}, {}))
    again = advice.post(long, advice.store(), advice_id="long-1")
    assert again["unchanged"] and any("too long" in w for w in again["warnings"])
    st = advice.set_status(advice.store(), "long-1", "awaiting_yes", note="asked")
    assert any("too long" in w for w in st["warnings"])
    with pytest.raises(advice.AdviceError, match="verdict 3 sentences"):
        advice.post({**long, "headline": "changed"}, advice.store(), advice_id="long-1")
    p = write(tmp_path, "long-new.json", sample(title="t" * 90))
    res = runner.invoke(app, ["advise", "post", "--file", str(p), "--json"])
    out = json.loads(res.output)
    assert res.exit_code == 2 and out["code"] == "usage" and "title 90 chars > 70" in json.dumps(out)
