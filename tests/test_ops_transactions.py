import pytest

from fantasy.ops import approvals, ledger, locks, transactions
from fantasy.ops.common import DuplicateError, InvalidStateError, NotAllowedError
from fantasy.ops.testing import use_test_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)


@pytest.fixture
def live_run():
    return ledger.start_run("tester", "txn tests", mode="live")


LINEUP = {"date": "2026-10-21", "moves": [{"player_id": "a", "slot": "PG"}]}


def test_idempotent_plan(live_run):
    t1 = transactions.plan("lineup", LINEUP, live_run)
    assert t1["status"] == "planned"
    # same payload with keys in another order -> same key
    reordered = {"moves": [{"slot": "PG", "player_id": "a"}], "date": "2026-10-21"}
    with pytest.raises(DuplicateError) as ei:
        transactions.plan("lineup", reordered, live_run)
    assert ei.value.exit_code == 5
    assert ei.value.details["existing"]["txn_id"] == t1["txn_id"]
    # different scope or attempt -> new txn (scope must differ from the default = today's UTC date: never a real date,
    # or the test fails on that day -- it did on 2026-10-22, T-019)
    t2 = transactions.plan("lineup", LINEUP, live_run, scope="not-a-date-scope")
    t3 = transactions.plan("lineup", LINEUP, live_run, attempt=2)
    assert len({t1["txn_id"], t2["txn_id"], t3["txn_id"]}) == 3


def test_draft_pick_scope_and_lock(live_run):
    payload = {"draft_id": "d1", "overall_pick": 7, "player_id": "nikola-jokic"}
    t = transactions.plan("draft_pick", payload, live_run)
    assert t["verification"]["scope"] == "d1#7"
    assert t["status"] == "planned"  # no approval needed
    # roster lock is not the right lock for a draft pick
    locks.acquire(locks.YAHOO_ROSTER, live_run)
    with pytest.raises(NotAllowedError):
        transactions.mark_submitted(t["txn_id"], live_run)
    locks.acquire("draft:d1", live_run)
    sub = transactions.mark_submitted(t["txn_id"], live_run)
    assert sub["status"] == "submitted"
    assert sub["verification"]["submitted_by"] == live_run


def test_submit_requires_lock(live_run):
    t = transactions.plan("lineup", LINEUP, live_run)
    with pytest.raises(NotAllowedError, match="lock"):
        transactions.mark_submitted(t["txn_id"], live_run)
    other = ledger.start_run("other", mode="live")
    locks.acquire(locks.YAHOO_ROSTER, other)
    with pytest.raises(NotAllowedError):
        transactions.mark_submitted(t["txn_id"], live_run)
    locks.release(locks.YAHOO_ROSTER, other)
    locks.acquire(locks.YAHOO_ROSTER, live_run)
    assert transactions.mark_submitted(t["txn_id"], live_run)["status"] == "submitted"


def test_dry_run_never_submits():
    run = ledger.start_run("tester", mode="dry_run")
    t = transactions.plan("lineup", LINEUP, run)
    locks.acquire(locks.YAHOO_ROSTER, run)
    with pytest.raises(NotAllowedError, match="dry_run"):
        transactions.mark_submitted(t["txn_id"], run)


def test_drop_needs_approval(live_run):
    t = transactions.plan("drop", {"player_id": "x"}, live_run)
    assert t["status"] == "awaiting_approval" and t["approval_id"]
    locks.acquire(locks.YAHOO_ROSTER, live_run)
    with pytest.raises(NotAllowedError) as ei:
        transactions.mark_submitted(t["txn_id"], live_run)
    assert ei.value.exit_code == 4
    approvals.decide(t["approval_id"], True, "harry")
    assert transactions.mark_submitted(t["txn_id"], live_run)["status"] == "submitted"


def test_rejected_approval_blocks(live_run):
    t = transactions.plan("trade", {"give": ["a"], "get": ["b"]}, live_run)
    approvals.decide(t["approval_id"], False, "harry", "no")
    locks.acquire(locks.YAHOO_ROSTER, live_run)
    with pytest.raises(NotAllowedError, match="rejected"):
        transactions.mark_submitted(t["txn_id"], live_run)
    assert transactions.cancel(t["txn_id"], "rejected")["status"] == "cancelled"


def test_state_machine_and_reconciliation(live_run):
    t = transactions.plan("lineup", LINEUP, live_run)
    tid = t["txn_id"]
    with pytest.raises(InvalidStateError):
        transactions.mark_verified(tid, {"x": 1})  # planned -> verified not allowed
    locks.acquire(locks.YAHOO_ROSTER, live_run)
    transactions.mark_submitted(tid, live_run)
    assert [x["txn_id"] for x in transactions.needs_reconciliation()] == [tid]
    with pytest.raises(InvalidStateError, match="reconcile"):
        transactions.mark_submitted(tid, live_run)  # never blindly retried
    with pytest.raises(InvalidStateError):
        transactions.cancel(tid)  # submitted -> cancelled not allowed
    with pytest.raises(ValueError):
        transactions.mark_verified(tid, {})  # evidence required
    v = transactions.mark_verified(tid, {"yahoo_lineup": "ok"}, live_run)
    assert v["status"] == "verified" and v["verification"]["evidence"] == {"yahoo_lineup": "ok"}
    assert transactions.needs_reconciliation() == []
    for to in ("failed", "cancelled", "planned", "submitted"):
        with pytest.raises(InvalidStateError):
            transactions.mark(tid, to, run_id=live_run, error="x")


def test_failed_then_retry_with_attempt(live_run):
    t = transactions.plan("lineup", LINEUP, live_run)
    transactions.mark_failed(t["txn_id"], "yahoo error")
    with pytest.raises(DuplicateError):
        transactions.plan("lineup", LINEUP, live_run)
    assert transactions.plan("lineup", LINEUP, live_run, attempt=2)["status"] == "planned"


def test_transition_table_is_closed():
    for frm, tos in transactions.TRANSITIONS.items():
        assert frm in transactions.TXN_STATUSES
        assert tos <= set(transactions.TXN_STATUSES)
    for term in transactions.TERMINAL:
        assert not transactions.TRANSITIONS[term]
