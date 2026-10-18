"""Human approvals for risky actions (drops, add/drops, trades).

An agent requests an approval, then polls `check`/`wait`. A human decides via the dashboard
(server route) or `fantasy approval decide`. Pending approvals past `expires_at` become `expired`
(lazily, whenever approvals are read).
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, select, update

from fantasy.db.schema import approvals
from fantasy.ops.common import (
    InvalidStateError,
    NotFoundError,
    engine,
    new_id,
    now,
    row_dict,
)

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired")
DEFAULT_EXPIRES_IN = 6 * 3600  # seconds


def expire_overdue() -> int:
    t = now()
    with engine().begin() as conn:
        res = conn.execute(
            update(approvals)
            .where(
                and_(
                    approvals.c.status == "pending",
                    approvals.c.expires_at.is_not(None),
                    approvals.c.expires_at < t,
                )
            )
            .values(status="expired", decided_at=t, note="expired without a decision")
        )
    return res.rowcount


def request_approval(
    run_id: str | None,
    action_type: str,
    summary: str,
    payload: Any = None,
    expires_in: float | None = DEFAULT_EXPIRES_IN,
) -> str:
    aid = new_id("apr-")
    t = now()
    with engine().begin() as conn:
        conn.execute(
            approvals.insert().values(
                approval_id=aid,
                run_id=run_id,
                action_type=action_type,
                summary=summary,
                payload=payload,
                status="pending",
                requested_at=t,
                expires_at=t + timedelta(seconds=expires_in) if expires_in else None,
            )
        )
    return aid


def get_approval(approval_id: str) -> dict[str, Any]:
    expire_overdue()
    with engine().connect() as conn:
        row = conn.execute(select(approvals).where(approvals.c.approval_id == approval_id)).first()
    if row is None:
        raise NotFoundError(f"approval {approval_id} not found", approval_id=approval_id)
    return row_dict(row)


def list_approvals(status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
    expire_overdue()
    q = select(approvals).order_by(approvals.c.requested_at.desc()).limit(limit)
    if status:
        q = q.where(approvals.c.status == status)
    with engine().connect() as conn:
        return [row_dict(r) for r in conn.execute(q)]


def list_pending() -> list[dict[str, Any]]:
    return list_approvals("pending")


def decide(
    approval_id: str, approved: bool, decided_by: str, note: str | None = None
) -> dict[str, Any]:
    """Approve or reject a pending approval. Refuses if already decided or expired."""
    expire_overdue()
    with engine().begin() as conn:
        res = conn.execute(
            update(approvals)
            .where(and_(approvals.c.approval_id == approval_id, approvals.c.status == "pending"))
            .values(
                status="approved" if approved else "rejected",
                decided_by=decided_by,
                note=note,
                decided_at=now(),
            )
        )
    if res.rowcount != 1:
        cur = get_approval(approval_id)  # raises NotFound
        raise InvalidStateError(
            f"approval {approval_id} is already {cur['status']}",
            approval_id=approval_id,
            status=cur["status"],
        )
    return get_approval(approval_id)


def check(approval_id: str) -> str:
    """Current status: pending | approved | rejected | expired."""
    return get_approval(approval_id)["status"]


def wait(approval_id: str, timeout: float = 0, poll: float = 5.0) -> str:
    """Poll until the approval leaves `pending` or `timeout` seconds pass. Returns the status."""
    deadline = time.monotonic() + timeout
    while True:
        status = check(approval_id)
        if status != "pending" or time.monotonic() >= deadline:
            return status
        time.sleep(min(poll, max(0.0, deadline - time.monotonic())))
