"""Idempotent ledger of Yahoo transactions (draft picks, adds/drops, lineup changes, trades).

State machine:

    planned ──► awaiting_approval ──► submitted ──► verified
       │  └──────────────┬──────────────┘   └─────► failed
       └─► cancelled ◄───┘ (also planned/awaiting_approval ─► failed)

- `plan` derives a deterministic idempotency key = sha256(kind, canonical payload, scope, attempt).
  Planning the same thing twice returns/raises the existing record (DuplicateError) instead of
  creating a second transaction. To deliberately retry after `failed`, plan with attempt=2.
- If policy requires approval, `plan` files an approval request and the txn starts in
  `awaiting_approval`.
- `mark_submitted` must be called immediately BEFORE executing the action in Yahoo. It requires a
  live run, policy clearance and the resource lock (`yahoo:roster`, or `draft:<id>` for picks).
- A txn left in `submitted` (agent crashed mid-action) must never be blindly re-executed:
  `needs_reconciliation()` lists them; the agent re-checks Yahoo and marks verified or failed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError

from fantasy.db.schema import transactions
from fantasy.ops import approvals, ledger, locks, policy
from fantasy.ops.common import (
    DuplicateError,
    InvalidStateError,
    NotAllowedError,
    NotFoundError,
    canonical_json,
    engine,
    new_id,
    now,
    row_dict,
    sha256_hex,
)

TXN_KINDS = ("draft_pick", "add", "drop", "add_drop", "lineup", "trade")
TXN_STATUSES = ("planned", "awaiting_approval", "submitted", "verified", "failed", "cancelled")
TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"awaiting_approval", "submitted", "cancelled", "failed"}),
    "awaiting_approval": frozenset({"planned", "submitted", "cancelled", "failed"}),
    "submitted": frozenset({"verified", "failed"}),
    "verified": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
TERMINAL = frozenset({"verified", "failed", "cancelled"})


def default_scope(kind: str, payload: dict[str, Any]) -> str:
    """Draft picks are scoped to (draft, pick); everything else to the UTC date."""
    if kind == "draft_pick" and "draft_id" in payload:
        return f"{payload['draft_id']}#{payload.get('overall_pick', '')}"
    return now().date().isoformat()


def idempotency_key(kind: str, payload: Any, scope: str, attempt: int = 1) -> str:
    return sha256_hex(
        canonical_json({"kind": kind, "payload": payload, "scope": scope, "attempt": attempt})
    )


def required_lock(txn: dict[str, Any]) -> str:
    payload = txn.get("payload") or {}
    if txn["kind"] == "draft_pick":
        if not payload.get("draft_id"):
            raise InvalidStateError("draft_pick payload needs draft_id", txn_id=txn["txn_id"])
        return locks.draft_resource(payload["draft_id"])
    return locks.YAHOO_ROSTER


def get_txn(txn_id: str) -> dict[str, Any]:
    with engine().connect() as conn:
        row = conn.execute(select(transactions).where(transactions.c.txn_id == txn_id)).first()
    if row is None:
        raise NotFoundError(f"transaction {txn_id} not found", txn_id=txn_id)
    return row_dict(row)


def get_by_key(key: str) -> dict[str, Any] | None:
    with engine().connect() as conn:
        row = conn.execute(
            select(transactions).where(transactions.c.idempotency_key == key)
        ).first()
    return row_dict(row)


def plan(
    kind: str,
    payload: dict[str, Any],
    run_id: str | None = None,
    scope: str | None = None,
    attempt: int = 1,
    summary: str | None = None,
    approval_expires_in: float | None = approvals.DEFAULT_EXPIRES_IN,
) -> dict[str, Any]:
    """Plan a transaction. Raises DuplicateError (with `existing`) if already planned."""
    if kind not in TXN_KINDS:
        raise ValueError(f"kind must be one of {TXN_KINDS}, got {kind!r}")
    scope = scope or default_scope(kind, payload)
    key = idempotency_key(kind, payload, scope, attempt)
    existing = get_by_key(key)
    if existing is not None:
        raise DuplicateError(
            f"transaction already planned ({existing['txn_id']}, {existing['status']})",
            existing=existing,
        )
    txn_id = new_id("txn-")
    status, approval_id = "planned", None
    t = now()
    try:
        with engine().begin() as conn:
            conn.execute(
                transactions.insert().values(
                    txn_id=txn_id,
                    idempotency_key=key,
                    run_id=run_id,
                    kind=kind,
                    payload=payload,
                    status=status,
                    verification={"scope": scope, "attempt": attempt},
                    created_at=t,
                    updated_at=t,
                )
            )
    except IntegrityError as exc:
        existing = get_by_key(key)
        if existing is None:
            raise
        raise DuplicateError(
            f"transaction already planned ({existing['txn_id']}, {existing['status']})",
            existing=existing,
        ) from exc
    if policy.requires_approval(kind):
        approval_id = approvals.request_approval(
            run_id,
            kind,
            summary or f"{kind}: {canonical_json(payload)}",
            {"txn_id": txn_id, "kind": kind, "payload": payload},
            approval_expires_in,
        )
        _transition(txn_id, "planned", "awaiting_approval", approval_id=approval_id)
    if run_id:
        ledger.log_event(run_id, "note", f"planned {kind} transaction {txn_id}", {"txn_id": txn_id})
    return get_txn(txn_id)


def _transition(txn_id: str, frm: str | tuple[str, ...], to: str, **values: Any) -> None:
    """Atomic compare-and-set on status."""
    froms = (frm,) if isinstance(frm, str) else frm
    for f in froms:
        if to not in TRANSITIONS[f]:
            raise InvalidStateError(f"invalid transition {f} -> {to}", txn_id=txn_id)
    with engine().begin() as conn:
        res = conn.execute(
            update(transactions)
            .where(and_(transactions.c.txn_id == txn_id, transactions.c.status.in_(froms)))
            .values(status=to, updated_at=now(), **values)
        )
    if res.rowcount != 1:
        cur = get_txn(txn_id)
        raise InvalidStateError(
            f"invalid transition {cur['status']} -> {to}",
            txn_id=txn_id,
            status=cur["status"],
            allowed=sorted(TRANSITIONS[cur["status"]]),
        )


def _merge_verification(txn: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    return {**(txn.get("verification") or {}), **extra}


def check_submit(txn_id: str, run_id: str) -> dict[str, Any]:
    """Every precondition for submitting, without changing state. Raises on refusal."""
    txn = get_txn(txn_id)
    if txn["status"] not in ("planned", "awaiting_approval"):
        raise InvalidStateError(
            f"cannot submit a {txn['status']} transaction"
            + (" -- reconcile against Yahoo first" if txn["status"] == "submitted" else ""),
            txn_id=txn_id,
            status=txn["status"],
        )
    run = ledger.require_running(run_id)
    approval_status = approvals.check(txn["approval_id"]) if txn["approval_id"] else None
    allowed, reason = policy.check_action_allowed(txn["kind"], run["mode"], approval_status)
    if not allowed:
        raise NotAllowedError(
            reason, txn_id=txn_id, approval_id=txn["approval_id"], approval_status=approval_status
        )
    resource = required_lock(txn)
    if not locks.holds(resource, run_id):
        raise NotAllowedError(
            f"run {run_id} must hold lock {resource} to submit",
            txn_id=txn_id,
            resource=resource,
            holder=locks.get_lock(resource),
        )
    return txn


def mark_submitted(txn_id: str, run_id: str) -> dict[str, Any]:
    txn = check_submit(txn_id, run_id)
    _transition(
        txn_id,
        txn["status"],
        "submitted",
        verification=_merge_verification(
            txn, {"submitted_by": run_id, "submitted_at": now().isoformat()}
        ),
    )
    ledger.log_event(run_id, "command", f"submitting {txn['kind']} {txn_id}", {"txn_id": txn_id})
    return get_txn(txn_id)


def mark_verified(
    txn_id: str, evidence: dict[str, Any], run_id: str | None = None
) -> dict[str, Any]:
    if not evidence:
        raise ValueError("verification evidence is required (what in Yahoo confirms it?)")
    txn = get_txn(txn_id)
    _transition(
        txn_id,
        "submitted",
        "verified",
        verification=_merge_verification(
            txn, {"evidence": evidence, "verified_at": now().isoformat(), "verified_by": run_id}
        ),
    )
    if run_id:
        ledger.log_event(run_id, "verification", f"verified {txn_id}", {"evidence": evidence})
    return get_txn(txn_id)


def mark_failed(
    txn_id: str, error: str, evidence: dict[str, Any] | None = None, run_id: str | None = None
) -> dict[str, Any]:
    txn = get_txn(txn_id)
    extra: dict[str, Any] = {"failed_at": now().isoformat()}
    if evidence:
        extra["evidence"] = evidence
    _transition(
        txn_id,
        ("planned", "awaiting_approval", "submitted"),
        "failed",
        error=error,
        verification=_merge_verification(txn, extra),
    )
    if run_id:
        ledger.log_event(run_id, "error", f"transaction {txn_id} failed: {error}", evidence)
    return get_txn(txn_id)


def cancel(txn_id: str, reason: str | None = None) -> dict[str, Any]:
    _transition(txn_id, ("planned", "awaiting_approval"), "cancelled", error=reason)
    return get_txn(txn_id)


def mark(
    txn_id: str,
    status: str,
    run_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Generic entry point used by the CLI."""
    if status == "submitted":
        if not run_id:
            raise InvalidStateError("submitting requires --run-id", txn_id=txn_id)
        return mark_submitted(txn_id, run_id)
    if status == "verified":
        return mark_verified(txn_id, evidence or {}, run_id)
    if status == "failed":
        return mark_failed(txn_id, error or "unspecified", evidence, run_id)
    if status == "cancelled":
        return cancel(txn_id, error)
    if status in ("planned", "awaiting_approval"):
        cur = get_txn(txn_id)
        _transition(txn_id, cur["status"], status)
        return get_txn(txn_id)
    raise ValueError(f"status must be one of {TXN_STATUSES}, got {status!r}")


def list_txns(
    status: str | None = None, kind: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    q = select(transactions).order_by(transactions.c.created_at.desc()).limit(limit)
    if status:
        q = q.where(transactions.c.status == status)
    if kind:
        q = q.where(transactions.c.kind == kind)
    with engine().connect() as conn:
        return [row_dict(r) for r in conn.execute(q)]


def draft_pick_txns(draft_id: str, overall: int) -> list[dict[str, Any]]:
    """All draft_pick transactions (any player, any attempt) for one pick, oldest first."""
    q = (
        select(transactions)
        .where(transactions.c.kind == "draft_pick")
        .order_by(transactions.c.created_at)
    )
    with engine().connect() as conn:
        rows = [row_dict(r) for r in conn.execute(q)]
    return [
        r for r in rows
        if (r["payload"] or {}).get("draft_id") == draft_id
        and int((r["payload"] or {}).get("overall_pick") or -1) == int(overall)
    ]


def attempt_of(txn: dict[str, Any]) -> int:
    return int((txn.get("verification") or {}).get("attempt") or 1)


def reassign(txn_id: str, run_id: str) -> dict[str, Any]:
    """Re-own a still-`planned` txn (e.g. left behind by a dry run) to `run_id`."""
    with engine().begin() as conn:
        res = conn.execute(
            update(transactions)
            .where(and_(transactions.c.txn_id == txn_id,
                        transactions.c.status.in_(("planned", "awaiting_approval"))))
            .values(run_id=run_id, updated_at=now())
        )
    if res.rowcount != 1:
        cur = get_txn(txn_id)
        raise InvalidStateError(
            f"cannot re-own a {cur['status']} transaction", txn_id=txn_id, status=cur["status"]
        )
    ledger.log_event(run_id, "note", f"re-owned planned transaction {txn_id}", {"txn_id": txn_id})
    return get_txn(txn_id)


def needs_reconciliation(older_than_seconds: float = 0) -> list[dict[str, Any]]:
    """Transactions stuck in `submitted`. Re-check Yahoo before doing anything with them."""
    cutoff = now() - timedelta(seconds=older_than_seconds)
    q = (
        select(transactions)
        .where(and_(transactions.c.status == "submitted", transactions.c.updated_at <= cutoff))
        .order_by(transactions.c.updated_at)
    )
    with engine().connect() as conn:
        return [row_dict(r) for r in conn.execute(q)]
