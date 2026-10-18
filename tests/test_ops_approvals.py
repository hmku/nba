import time
from datetime import timedelta

import pytest
from sqlalchemy import update

from fantasy.db.schema import approvals as approvals_t
from fantasy.ops import approvals, policy
from fantasy.ops.common import InvalidStateError, NotFoundError, engine, now
from fantasy.ops.testing import use_test_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "kind,mode,approval,allowed",
    [
        # dry_run never executes
        ("lineup", "dry_run", None, False),
        ("draft_pick", "dry_run", None, False),
        ("drop", "dry_run", "approved", False),
        ("trade", "dry_run", "approved", False),
        (None, None, None, False),
        # live: lineup / draft picks allowed without approval
        ("lineup", "live", None, True),
        ("draft_pick", "live", None, True),
        # live: risky actions need an approved approval
        ("drop", "live", None, False),
        ("drop", "live", "pending", False),
        ("drop", "live", "rejected", False),
        ("drop", "live", "expired", False),
        ("drop", "live", "approved", True),
        ("add_drop", "live", None, False),
        ("add_drop", "live", "approved", True),
        ("trade", "live", "pending", False),
        ("trade", "live", "approved", True),
        ("add", "live", None, False),
        ("mystery", "live", None, False),
        ("mystery", "live", "approved", True),
    ],
)
def test_policy_matrix(kind, mode, approval, allowed):
    ok, reason = policy.check_action_allowed(kind, mode, approval)
    assert ok is allowed
    assert isinstance(reason, str) and reason


def test_requires_approval():
    assert policy.requires_approval("drop")
    assert policy.requires_approval("add_drop")
    assert policy.requires_approval("trade")
    assert not policy.requires_approval("lineup")
    assert not policy.requires_approval("draft_pick")


def test_request_decide_flow():
    aid = approvals.request_approval(None, "drop", "drop X for Y", {"drop": "x"}, 3600)
    assert approvals.check(aid) == "pending"
    assert [a["approval_id"] for a in approvals.list_pending()] == [aid]
    a = approvals.decide(aid, True, "harry", "fine")
    assert a["status"] == "approved" and a["decided_by"] == "harry" and a["decided_at"]
    assert approvals.list_pending() == []
    with pytest.raises(InvalidStateError):
        approvals.decide(aid, False, "harry")
    with pytest.raises(NotFoundError):
        approvals.decide("apr-nope", True, "harry")


def test_expiry():
    aid = approvals.request_approval(None, "trade", "trade", None, 3600)
    with engine().begin() as conn:
        conn.execute(
            update(approvals_t)
            .where(approvals_t.c.approval_id == aid)
            .values(expires_at=now() - timedelta(seconds=1))
        )
    assert approvals.check(aid) == "expired"
    with pytest.raises(InvalidStateError):
        approvals.decide(aid, True, "harry")


def test_wait_returns_on_timeout():
    aid = approvals.request_approval(None, "drop", "x")
    t0 = time.monotonic()
    assert approvals.wait(aid, timeout=0.2, poll=0.05) == "pending"
    assert time.monotonic() - t0 < 2
