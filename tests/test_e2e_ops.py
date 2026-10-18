"""E2E ops invariants through the public CLI (.claude/skills/agent-run/SKILL.md): lock exclusivity across
runs, stale-run reaping, and the approval-policy matrix for drop/add_drop/trade/lineup/draft_pick in
dry_run vs live."""

from __future__ import annotations

import json
import time

import pytest
from e2e_helpers import cli


@pytest.fixture(autouse=True)
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)


def start(mode="dry_run", agent="e2e"):
    code, out = cli("run", "start", "--agent", agent, "--mode", mode, "--json")
    assert code == 0 and out["run"]["status"] == "running"
    return out["run_id"]


def test_lock_exclusive_across_runs():
    a, b = start("live", "a"), start("live", "b")
    res = "draft:mock-lock"
    code, out = cli("lock", "acquire", res, "--ttl", 600, "--run-id", a, "--json")
    assert code == 0 and out["lock"]["holder_run_id"] == a
    assert cli("lock", "acquire", res, "--ttl", 600, "--run-id", a, "--json")[0] == 0  # re-entrant
    code, out = cli("lock", "acquire", res, "--run-id", b, "--json")
    assert code == 3 and out["ok"] is False and out["error"] == "lock_held"
    assert cli("lock", "renew", res, "--run-id", b, "--json")[0] in (3, 4, 7)
    assert cli("lock", "release", res, "--run-id", b, "--json")[0] == 3
    code, out = cli("lock", "show", res, "--json")
    assert out["lock"]["holder_run_id"] == a
    code, end = cli("run", "end", "--run-id", a, "--json")
    assert code == 0 and end["released_locks"] == [res]
    code, out = cli("lock", "acquire", res, "--run-id", b, "--json")
    assert code == 0 and out["lock"]["holder_run_id"] == b


def test_stale_runs_are_reaped_and_cannot_heartbeat():
    old = start()
    time.sleep(1.6)
    fresh = start()
    code, out = cli("run", "reap", "--stale-minutes", 0.02, "--json")  # 1.2 s
    assert code == 0 and out["reaped"] == [old]
    code, show = cli("run", "show", old, "--json")
    assert show["run"]["status"] == "abandoned"
    assert cli("run", "heartbeat", "--run-id", old, "--json")[0] == 7
    assert cli("run", "heartbeat", "--run-id", fresh, "--json")[0] == 0
    code, st = cli("status", "--json")
    assert old not in {r["run_id"] for r in st["status"]["runs"]["active"]}
    assert fresh in {r["run_id"] for r in st["status"]["runs"]["active"]}


PAYLOADS = {
    "drop": {"player_id": "player-100"},
    "add_drop": {"add": "player-200", "drop": "player-100"},
    "trade": {"give": ["player-001"], "get": ["player-002"]},
    "lineup": {"date": "2026-10-21", "starters": ["player-001"]},
    "draft_pick": {"draft_id": "mock-pol", "overall_pick": 1, "player_id": "player-003"},
}
LOCK_FOR = {"draft_pick": "draft:mock-pol"}  # everything else: yahoo:roster


@pytest.mark.parametrize("mode", ["dry_run", "live"])
@pytest.mark.parametrize("kind", list(PAYLOADS))
def test_policy_matrix(kind, mode):
    rid = start(mode)
    assert cli("lock", "acquire", LOCK_FOR.get(kind, "yahoo:roster"), "--run-id", rid, "--json")[0] == 0
    code, out = cli("txn", "plan", kind, "--payload", json.dumps(PAYLOADS[kind]), "--run-id", rid, "--json")
    assert code == 0, out
    txn = out["txn"]
    needs_approval = kind in ("drop", "add_drop", "trade")
    assert (txn["status"] == "awaiting_approval") is needs_approval
    assert bool(txn["approval_id"]) is needs_approval
    # idempotent: planning the same thing again is a duplicate with the existing record
    code, dup = cli("txn", "plan", kind, "--payload", json.dumps(PAYLOADS[kind]), "--run-id", rid, "--json")
    assert code == 5 and dup["existing"]["txn_id"] == txn["txn_id"]

    code, chk = cli("txn", "check", txn["txn_id"], "--run-id", rid, "--json")
    allowed_now = mode == "live" and not needs_approval
    assert code == (0 if allowed_now else 4), chk
    if not allowed_now:
        assert cli("txn", "mark", txn["txn_id"], "submitted", "--run-id", rid, "--json")[0] == 4
    if needs_approval:
        # human approves (tests may decide; agents never do) -> live passes, dry_run still refuses
        code, _ = cli("approval", "decide", txn["approval_id"], "--approve", "--by", "e2e-human", "--json")
        assert code == 0
        code, chk = cli("txn", "check", txn["txn_id"], "--run-id", rid, "--json")
        assert code == (0 if mode == "live" else 4)
        assert cli("approval", "decide", txn["approval_id"], "--reject", "--by", "x", "--json")[0] == 7


def test_rejected_approval_blocks_live_add_drop():
    rid = start("live")
    cli("lock", "acquire", "yahoo:roster", "--run-id", rid, "--json")
    _, out = cli("txn", "plan", "add_drop", "--payload", json.dumps(PAYLOADS["add_drop"]), "--run-id", rid, "--json")
    aid = out["txn"]["approval_id"]
    code, show = cli("approval", "show", aid, "--wait", 0.2, "--json")
    assert code == 8 and show["error"] == "still_pending"
    assert cli("approval", "decide", aid, "--reject", "--by", "e2e-human", "--json")[0] == 0
    code, chk = cli("txn", "check", out["txn"]["txn_id"], "--run-id", rid, "--json")
    assert code == 4 and "rejected" in chk["message"]


def test_live_without_lock_refuses_lineup():
    rid = start("live")
    _, out = cli("txn", "plan", "lineup", "--payload", json.dumps(PAYLOADS["lineup"]), "--run-id", rid, "--json")
    code, chk = cli("txn", "check", out["txn"]["txn_id"], "--run-id", rid, "--json")
    assert code == 4 and "yahoo:roster" in chk["message"]
