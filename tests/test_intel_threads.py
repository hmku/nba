"""Threads on agent updates: `fantasy intel reply|thread|post` and `latest` open threads (SQLite + mocked REST)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db import rest
from fantasy.db.schema import agent_events, agent_runs
from fantasy.ops import intel
from fantasy.ops.common import NotAllowedError, NotFoundError, engine
from fantasy.ops.testing import use_test_db

runner = CliRunner()
T0 = datetime(2026, 10, 20, 16, 0, tzinfo=UTC)  # 12:00 ET


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)


def _event(event_id, run_id, agent, ts, payload, kind="observation", message=None):
    with engine().begin() as c:
        if not c.execute(agent_runs.select().where(agent_runs.c.run_id == run_id)).first():
            c.execute(agent_runs.insert().values(run_id=run_id, agent=agent, status="running",
                                                 mode="dry_run", started_at=ts))
        c.execute(agent_events.insert().values(event_id=event_id, run_id=run_id, ts=ts, kind=kind,
                                               message=message or event_id, payload=payload))


def _reply(event_id, run_id, agent, ts, to, root, stance="agree"):
    _event(event_id, run_id, agent, ts, {"report_kind": "reply", "reply_to": to, "thread_root": root,
                                         "stance": stance, "text": f"{event_id} {stance}"}, kind="note")


def seed():
    _event("muse-ev-trade", "muse-2026-10-20", "muse", T0 - timedelta(hours=6),
           {"report_kind": "trade_offer", "source": "yahoo", "reported_by": "muse",
            "details": {"from_team": "Yeet", "to_team": "Tip-Top", "from_gives": ["Jamal Murray"],
                        "to_gives": ["Jalen Duren", "Fred VanVleet"], "status": "proposed"}},
           message="Jesse (Yeet) offered Murray for Duren + VanVleet")
    _event("muse-ev-digest", "muse-2026-10-20", "muse", T0 - timedelta(hours=5),
           {"report_kind": "chat_digest", "details": {"excerpts": []}})
    _event("muse-ev-injury", "muse-2026-10-20", "muse", T0 - timedelta(hours=4),
           {"report_kind": "injury_news", "reported_by": "muse"})
    _event("muse-ev-pick", "muse-2026-10-19", "muse", T0 - timedelta(hours=20), {"report_kind": "observed_pick"})
    _event("evt-decision", "run-daily", "daily-routine", T0 - timedelta(hours=3), {"choice": "Duren"},
           kind="decision", message="Started Duren over Ware")
    _reply("advisor-ev-r1", "advisor-2026-10-20", "advisor", T0 - timedelta(hours=2), "muse-ev-trade",
           "muse-ev-trade", "disagree")


# ------------------------------------------------------------------ reply / thread


def test_reply_writes_under_daily_claude_run_and_threads_under_root():
    seed()
    src = intel.source()
    ev = [{"claim": "ΔP(playoffs) of the offer", "value": -0.031, "se": 0.008, "label": "simulated",
           "command": "fantasy inseason trade ..."}]
    out = intel.reply(src, "advisor-ev-r1", "agree", "  Agree: it costs us blocks.  ", evidence=ev, at=T0)
    assert out["status"] == "logged" and out["run_id"] == "claude-2026-10-20"
    assert out["thread_root"] == "muse-ev-trade" and out["event_id"].startswith("claude-ev-")
    with engine().connect() as c:
        run = c.execute(agent_runs.select().where(agent_runs.c.run_id == "claude-2026-10-20")).first()
        row = c.execute(agent_events.select().where(agent_events.c.event_id == out["event_id"])).first()
    assert run.agent == "claude" and run.status == "succeeded"  # a log container: never stuck/reaped
    assert row.kind == "note" and row.message == "Agree: it costs us blocks."
    assert row.payload == {"report_kind": "reply", "reply_to": "advisor-ev-r1", "thread_root": "muse-ev-trade",
                           "stance": "agree", "text": "Agree: it costs us blocks.", "evidence": ev,
                           "author": "claude", "reported_by": "claude", "source": "claude"}
    # a second reply the same day reuses the run
    intel.reply(src, "muse-ev-trade", "question", "Is Murray's hamstring fine?", at=T0 + timedelta(minutes=5))
    th = intel.thread(src, "advisor-ev-r1")  # any event in the thread finds the whole thread
    assert th["thread_root"] == "muse-ev-trade"
    assert th["root"]["author"] == "muse" and th["root"]["details"]["from_team"] == "Yeet"
    assert [(r["author"], r["stance"]) for r in th["replies"]] == [
        ("advisor", "disagree"), ("claude", "agree"), ("claude", "question")]
    assert th["replies"][1]["reply_to"] == "advisor-ev-r1"
    assert th["stances"] == {"disagree": 1, "agree": 1, "question": 1}
    assert th["participants"] == ["advisor", "claude", "muse"]


def test_reply_validation_and_privacy():
    seed()
    src = intel.source()
    with pytest.raises(NotFoundError):
        intel.reply(src, "nope", "agree", "x")
    with pytest.raises(NotAllowedError):
        intel.reply(src, "muse-ev-digest", "comment", "x")  # group-chat content: replies are public
    for bad in [dict(stance="maybe", text="x"), dict(stance="agree", text="  "),
                dict(stance="agree", text="x" * 2001),
                dict(stance="agree", text="x", evidence=[{"claim": "c", "label": "vibes"}]),
                dict(stance="agree", text="x", evidence={"claim": "c"}),
                dict(stance="agree", text="x", evidence=[{"claim": "c", "label": "prior", "value": float("nan")}]),
                dict(stance="agree", text="x", evidence=[{"claim": "c", "label": "prior"}] * 11)]:
        with pytest.raises(ValueError):
            intel.reply(src, "muse-ev-trade", **bad)
    # Claude can answer its own decision events too
    assert intel.reply(src, "evt-decision", "comment", "Duren had 3 games.")["thread_root"] == "evt-decision"


def test_post_starts_a_thread():
    src = intel.source()
    out = intel.post(src, "recommendation", "Decline the Murray offer.", details={"advice_id": "x"},
                     evidence=[{"claim": "ΔP(title)", "value": -0.4, "se": 0.3, "label": "simulated"}], at=T0)
    rep = intel.show(src, out["event_id"])["report"]
    assert rep["report_kind"] == "recommendation" and rep["source"] == "claude" and rep["reported_by"] == "claude"
    with pytest.raises(ValueError):
        intel.post(src, "chat_digest", "x")
    with pytest.raises(ValueError):
        intel.post(src, "update", "x", details=["not", "an", "object"])
    lst = intel.list_intel(src, report_source="claude", what="reports")
    assert [r["event_id"] for r in lst["reports"]] == [out["event_id"]]


# ------------------------------------------------------------------ open threads


def test_latest_open_threads_track_what_claude_has_not_answered():
    seed()
    src = intel.source()
    out = intel.latest(src, at=T0)
    ot = {t["thread_root"]: t for t in out["open_threads"]}
    # trade: Muse root + advisor reply, Claude silent -> new_replies; injury: unreplied
    assert set(ot) == {"muse-ev-trade", "muse-ev-injury"}
    assert ot["muse-ev-trade"]["status"] == "new_replies"
    assert [m["event_id"] for m in ot["muse-ev-trade"]["waiting"]] == ["muse-ev-trade", "advisor-ev-r1"]
    assert ot["muse-ev-injury"]["status"] == "unreplied"
    assert out["open_threads"][0]["thread_root"] == "muse-ev-trade"  # newest activity first
    assert "advisor-ev-r1" not in {r["event_id"] for r in out["reports"]}  # replies are not reports
    # Claude answers both -> nothing open
    intel.reply(src, "advisor-ev-r1", "agree", "agreed", at=T0)
    intel.reply(src, "muse-ev-injury", "comment", "noted, no action", at=T0)
    assert intel.latest(src, at=T0 + timedelta(minutes=1))["open_threads"] == []
    # the user then disagrees -> the thread reopens with just the new message waiting
    _reply("user-ev-1", "user-2026-10-20", "user", T0 + timedelta(minutes=10), "muse-ev-trade",
           "muse-ev-trade", "disagree")
    again = intel.latest(src, at=T0 + timedelta(minutes=11))["open_threads"]
    assert [(t["thread_root"], [m["event_id"] for m in t["waiting"]]) for t in again] == [
        ("muse-ev-trade", ["user-ev-1"])]
    assert again[0]["waiting"][0]["author"] == "user"


def test_open_threads_include_replies_to_old_or_claude_roots():
    seed()
    # a reply today to Claude's decision makes that thread open; a Claude post nobody answered is not
    _reply("advisor-ev-r2", "advisor-2026-10-20", "advisor", T0 - timedelta(hours=1), "evt-decision",
           "evt-decision", "question")
    src = intel.source()
    intel.post(src, "update", "Lineup set.", at=T0 - timedelta(minutes=30))
    ot = {t["thread_root"]: t for t in intel.open_threads(src, at=T0)}
    assert ot["evt-decision"]["root"]["author"] == "claude"
    assert [m["event_id"] for m in ot["evt-decision"]["waiting"]] == ["advisor-ev-r2"]
    assert all(t["root"]["author"] != "claude" or t["replies"] for t in ot.values())
    assert "muse-ev-digest" not in ot and "muse-ev-pick" not in ot


# ------------------------------------------------------------------ CLI


def test_cli_reply_thread_post_latest():
    seed()
    res = runner.invoke(app, ["intel", "reply", "muse-ev-trade", "--stance", "disagree", "--text",
                              "Murray's FT% helps but we lose 2 cats.", "--evidence",
                              '[{"claim": "cats lost", "value": 2, "label": "simulated"}]', "--json"])
    assert res.exit_code == 0, res.stdout
    out = json.loads(res.stdout)
    assert out["ok"] and out["thread_root"] == "muse-ev-trade"
    res = runner.invoke(app, ["intel", "thread", "muse-ev-trade"])
    assert res.exit_code == 0 and "disagree" in res.stdout and "advisor" in res.stdout
    res = runner.invoke(app, ["intel", "post", "--kind", "update", "--text", "Lineup set", "--json"])
    assert res.exit_code == 0 and json.loads(res.stdout)["event_id"].startswith("claude-ev-")
    # pin the window: the default (last 7 days from now) drops the 2026-10-20 seed once that date is old
    res = runner.invoke(app, ["intel", "latest", "--since", "2026-10-19"])
    assert res.exit_code == 0 and "open threads" in res.stdout and "muse-ev-injury" in res.stdout
    res = runner.invoke(app, ["intel", "reply", "muse-ev-digest", "--stance", "agree", "--text", "x", "--json"])
    assert res.exit_code == 4 and json.loads(res.stdout)["error"] == "not_allowed"
    res = runner.invoke(app, ["intel", "reply", "missing", "--stance", "agree", "--text", "x", "--json"])
    assert res.exit_code == 6
    res = runner.invoke(app, ["intel", "reply", "muse-ev-trade", "--stance", "meh", "--text", "x", "--json"])
    assert res.exit_code == 2


# ------------------------------------------------------------------ REST (mocked PostgREST)


class FakeRest:
    """Enough PostgREST for threads: eq/in/gte filters on columns and payload->>keys, inserts."""

    def __init__(self, tables):
        self.tables = tables
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        table = req.url.path.rsplit("/", 1)[1]
        if req.method == "POST":
            rows = json.loads(req.content)
            pk = req.url.params["on_conflict"]
            have = {r[pk] for r in self.tables.setdefault(table, [])}
            new = [r for r in rows if r[pk] not in have]
            self.tables[table] += new
            return httpx.Response(201, json=[{pk: r[pk]} for r in new])
        rows = list(self.tables.get(table, []))

        def val(r, col):
            return (r.get("payload") or {}).get(col.split("->>")[1]) if "->>" in col else r.get(col)

        for col, f in req.url.params.multi_items():
            if col in ("select", "order", "limit"):
                continue
            op, _, arg = f.partition(".")
            if col == "run_id" and op == "like":
                rows = [r for r in rows if r["run_id"].startswith(arg.rstrip("*"))]
            elif op == "eq":
                rows = [r for r in rows if val(r, col) == arg]
            elif op == "in":
                want = {x.strip('"') for x in arg.strip("()").split(",")}
                rows = [r for r in rows if val(r, col) in want]
            elif op == "gte":
                rows = [r for r in rows if (val(r, col) or "") >= arg]
            elif f == "not.is.null":
                rows = [r for r in rows if val(r, col) is not None]
        rows.sort(key=lambda r: r.get("ts") or "", reverse="desc" in req.url.params.get("order", ""))
        if "limit" in req.url.params:
            rows = rows[: int(req.url.params["limit"])]
        return httpx.Response(200, json=rows)


def test_rest_reply_thread_and_open_threads():
    trade = {"event_id": "muse-ev-trade", "run_id": "muse-2026-10-20", "ts": "2026-10-20T10:00:00+00:00",
             "kind": "observation", "message": "offer", "payload": {"report_kind": "trade_offer"}}
    digest = {"event_id": "muse-ev-d", "run_id": "muse-2026-10-20", "ts": "2026-10-20T09:00:00+00:00",
              "kind": "observation", "message": "chat", "payload": {"report_kind": "chat_digest"}}
    adv = {"event_id": "advisor-ev-r1", "run_id": "advisor-2026-10-20", "ts": "2026-10-20T11:00:00+00:00",
           "kind": "note", "message": "no", "payload": {"report_kind": "reply", "reply_to": "muse-ev-trade",
                                                         "thread_root": "muse-ev-trade", "stance": "disagree",
                                                         "text": "no"}}
    fake = FakeRest({"agent_events": [trade, digest, adv], "agent_runs": []})
    cl = rest.RestClient(base_url="https://example.supabase.co", key="sb_secret_TEST",
                         transport=httpx.MockTransport(fake), sleep=lambda s: None)
    src = intel.source(use_rest=True, client=cl)
    ot = intel.open_threads(src, at=T0)
    assert [(t["thread_root"], t["status"]) for t in ot] == [("muse-ev-trade", "new_replies")]
    q = [r for r in fake.requests if "payload->>thread_root" in r.url.params][0].url.params
    assert q["payload->>thread_root"] == 'in.("muse-ev-trade")' and q["payload->>report_kind"] == "eq.reply"
    out = intel.reply(src, "advisor-ev-r1", "agree", "fair", at=T0)
    posts = [r for r in fake.requests if r.method == "POST"]
    assert [p.url.path.rsplit("/", 1)[1] for p in posts] == ["agent_runs", "agent_events"]
    assert all(p.headers["Prefer"].startswith("resolution=ignore-duplicates") for p in posts)
    run = fake.tables["agent_runs"][0]
    assert run["run_id"] == "claude-2026-10-20" and run["status"] == "succeeded"
    assert run["started_at"] == "2026-10-20T16:00:00+00:00"  # datetimes serialised for PostgREST
    th = intel.thread(src, out["event_id"])
    assert [r["author"] for r in th["replies"]] == ["advisor", "claude"]
    assert intel.open_threads(src, at=T0 + timedelta(minutes=1)) == []
    with pytest.raises(NotAllowedError):
        intel.reply(src, "muse-ev-d", "agree", "x")


def test_user_comment_on_a_decision_card_is_an_open_thread_root():
    """The dashboard Inbox comment (no reply_to: the decision may have no thread yet) starts a thread Claude sees."""
    _event("user-ev-c1", "user-2026-10-20", "user", T0 - timedelta(minutes=5),
           {"report_kind": "comment", "text": "why drop Bailey?", "stance": "question",
            "details": {"advice_id": "2026-10-21-champagnie-claim"}, "author": "user"}, kind="note",
           message="why drop Bailey?")
    src = intel.source()
    ot = {t["thread_root"]: t for t in intel.open_threads(src, at=T0)}
    assert ot["user-ev-c1"]["status"] == "unreplied"
    assert ot["user-ev-c1"]["root"]["author"] == "user"
    assert ot["user-ev-c1"]["root"]["details"] == {"advice_id": "2026-10-21-champagnie-claim"}
    listed = intel.list_intel(src, report_source="user", what="reports")["reports"]
    assert [r["report_kind"] for r in listed] == ["comment"]
    intel.reply(src, "user-ev-c1", "comment", "Bailey is the weakest bench slot.", at=T0)
    assert "user-ev-c1" not in {t["thread_root"] for t in intel.open_threads(src, at=T0 + timedelta(minutes=1))}
