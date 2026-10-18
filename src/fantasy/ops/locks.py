"""Lease-based mutual exclusion on the `locks` table.

acquire() is atomic on both SQLite and Postgres without SELECT ... FOR UPDATE:
  1. INSERT the row (primary key = resource). Success -> acquired.
  2. On unique violation, conditional UPDATE ... WHERE resource = :r AND
     (expires_at < :now OR holder_run_id = :me). rowcount == 1 -> took over an expired lease
     (or re-entrantly renewed our own); rowcount == 0 -> held by someone else.
Each step runs in its own transaction (a failed INSERT aborts a Postgres transaction).

Standard resources:
  yahoo:roster   -- any roster-changing Yahoo transaction (add/drop/trade/lineup)
  yahoo:browser  -- exclusive driving of the Yahoo browser session
  draft:<id>     -- making picks in a given draft
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from fantasy.db.schema import locks
from fantasy.ops.common import (
    InvalidStateError,
    LockHeldError,
    as_utc,
    engine,
    now,
    row_dict,
)

YAHOO_ROSTER = "yahoo:roster"
YAHOO_BROWSER = "yahoo:browser"
DEFAULT_TTL = 300


def draft_resource(draft_id: str) -> str:
    return f"draft:{draft_id}"


def get_lock(resource: str) -> dict[str, Any] | None:
    with engine().connect() as conn:
        row = conn.execute(select(locks).where(locks.c.resource == resource)).first()
    d = row_dict(row)
    if d is not None:
        d["expired"] = d["expires_at"] is not None and d["expires_at"] < now()
    return d


def list_locks(include_expired: bool = False) -> list[dict[str, Any]]:
    with engine().connect() as conn:
        rows = [row_dict(r) for r in conn.execute(select(locks).order_by(locks.c.resource))]
    t = now()
    out = []
    for d in rows:
        d["expired"] = d["expires_at"] is not None and d["expires_at"] < t
        if include_expired or not d["expired"]:
            out.append(d)
    return out


def _try_insert(resource: str, run_id: str, ttl: float, purpose: str | None) -> bool:
    t = now()
    try:
        with engine().begin() as conn:
            conn.execute(
                locks.insert().values(
                    resource=resource,
                    holder_run_id=run_id,
                    purpose=purpose,
                    acquired_at=t,
                    expires_at=t + timedelta(seconds=ttl),
                )
            )
        return True
    except IntegrityError:
        return False


def _try_takeover(resource: str, run_id: str, ttl: float, purpose: str | None) -> bool:
    t = now()
    with engine().begin() as conn:
        res = conn.execute(
            update(locks)
            .where(
                and_(
                    locks.c.resource == resource,
                    or_(locks.c.expires_at < t, locks.c.holder_run_id == run_id),
                )
            )
            .values(
                holder_run_id=run_id,
                purpose=purpose,
                acquired_at=t,
                expires_at=t + timedelta(seconds=ttl),
            )
        )
    return res.rowcount == 1


def acquire(
    resource: str, run_id: str, ttl_seconds: float = DEFAULT_TTL, purpose: str | None = None
) -> dict[str, Any]:
    """Acquire (or re-entrantly renew) a lease. Raises LockHeldError if someone else holds it."""
    for _ in range(5):
        try:
            if _try_insert(resource, run_id, ttl_seconds, purpose):
                return get_lock(resource) | {"taken_over": False}
            if _try_takeover(resource, run_id, ttl_seconds, purpose):
                return get_lock(resource) | {"taken_over": True}
        except OperationalError:  # sqlite "database is locked" under contention: retry
            continue
        current = get_lock(resource)
        if current is not None:
            raise LockHeldError(
                f"{resource} is held by {current['holder_run_id']} until "
                f"{current['expires_at'].isoformat()}",
                resource=resource,
                holder=current,
            )
        # row vanished between INSERT and UPDATE (released concurrently) -> retry
    current = get_lock(resource)
    raise LockHeldError(f"could not acquire {resource}", resource=resource, holder=current)


def renew(resource: str, run_id: str, ttl_seconds: float = DEFAULT_TTL) -> dict[str, Any]:
    """Extend a lease we hold (even if just expired, provided nobody took it over)."""
    with engine().begin() as conn:
        res = conn.execute(
            update(locks)
            .where(and_(locks.c.resource == resource, locks.c.holder_run_id == run_id))
            .values(expires_at=now() + timedelta(seconds=ttl_seconds))
        )
    if res.rowcount != 1:
        current = get_lock(resource)
        if current is None:
            raise InvalidStateError(f"{resource} is not locked", resource=resource)
        raise LockHeldError(
            f"{resource} is held by {current['holder_run_id']}", resource=resource, holder=current
        )
    return get_lock(resource)


def release(resource: str, run_id: str) -> bool:
    """Release a lock. Only the holder may release. Returns False if it was not locked at all."""
    with engine().begin() as conn:
        res = conn.execute(
            delete(locks).where(and_(locks.c.resource == resource, locks.c.holder_run_id == run_id))
        )
    if res.rowcount == 1:
        return True
    current = get_lock(resource)
    if current is None:
        return False
    raise LockHeldError(
        f"{resource} is held by {current['holder_run_id']}, not {run_id}",
        resource=resource,
        holder=current,
    )


def release_all(run_id: str) -> list[str]:
    """Release every lock (expired or not) held by run_id. Returns the released resources."""
    with engine().begin() as conn:
        held = [
            r[0]
            for r in conn.execute(
                select(locks.c.resource).where(locks.c.holder_run_id == run_id)
            )
        ]
        if held:
            conn.execute(
                delete(locks).where(
                    and_(locks.c.holder_run_id == run_id, locks.c.resource.in_(held))
                )
            )
    return sorted(held)


def force_release(resource: str) -> bool:
    """Human/operator override: remove a lock regardless of holder."""
    with engine().begin() as conn:
        return conn.execute(delete(locks).where(locks.c.resource == resource)).rowcount == 1


def holds(resource: str, run_id: str) -> bool:
    """True if run_id holds an unexpired lease on resource."""
    lk = get_lock(resource)
    return bool(lk and lk["holder_run_id"] == run_id and as_utc(lk["expires_at"]) >= now())


@contextmanager
def lock(
    resource: str, run_id: str, ttl_seconds: float = DEFAULT_TTL, purpose: str | None = None
) -> Iterator[dict[str, Any]]:
    """`with lock("yahoo:roster", run_id): ...` -- acquire, yield, always release."""
    info = acquire(resource, run_id, ttl_seconds, purpose)
    try:
        yield info
    finally:
        try:
            release(resource, run_id)
        except LockHeldError:
            pass  # our lease expired and was taken over; nothing to release
