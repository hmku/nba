"""Persistent project backlog on the `tasks` table (ids T-001, T-002, ...)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from fantasy.db.schema import tasks
from fantasy.ops.common import (
    ConflictError,
    DuplicateError,
    InvalidStateError,
    NotFoundError,
    engine,
    now,
    row_dict,
)

TASK_STATUSES = ("todo", "in_progress", "blocked", "done")
TASK_AREAS = ("data", "model", "draft", "web", "infra", "agent", "season")
_ID_RE = re.compile(r"^T-(\d+)$")


def _next_id(conn) -> str:
    best = 0
    for (tid,) in conn.execute(select(tasks.c.task_id)):
        m = _ID_RE.match(tid or "")
        if m:
            best = max(best, int(m.group(1)))
    return f"T-{best + 1:03d}"


def get_task(task_id: str) -> dict[str, Any]:
    with engine().connect() as conn:
        row = conn.execute(select(tasks).where(tasks.c.task_id == task_id)).first()
    if row is None:
        raise NotFoundError(f"task {task_id} not found", task_id=task_id)
    return row_dict(row)


def find_by_title(title: str) -> dict[str, Any] | None:
    with engine().connect() as conn:
        return row_dict(conn.execute(select(tasks).where(tasks.c.title == title)).first())


def add(
    title: str,
    description: str | None = None,
    area: str | None = None,
    priority: int = 2,
    status: str = "todo",
) -> dict[str, Any]:
    """Create a task with the next T-NNN id. Raises DuplicateError if the title exists."""
    if status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {TASK_STATUSES}")
    if not 0 <= priority <= 3:
        raise ValueError("priority must be 0..3")
    for _ in range(10):
        existing = find_by_title(title)
        if existing:
            raise DuplicateError(f"task with this title exists ({existing['task_id']})",
                                 existing=existing)
        t = now()
        try:
            with engine().begin() as conn:
                tid = _next_id(conn)
                conn.execute(
                    tasks.insert().values(
                        task_id=tid,
                        title=title,
                        description=description,
                        area=area,
                        priority=priority,
                        status=status,
                        created_at=t,
                        updated_at=t,
                    )
                )
            return get_task(tid)
        except IntegrityError:
            continue  # id race (or title race -> caught by the check above on retry)
    raise ConflictError("could not allocate a task id")


def list_tasks(
    status: str | list[str] | None = None, area: str | None = None, open_only: bool = False
) -> list[dict[str, Any]]:
    q = select(tasks).order_by(tasks.c.priority, tasks.c.task_id)
    if isinstance(status, str):
        q = q.where(tasks.c.status == status)
    elif status:
        q = q.where(tasks.c.status.in_(status))
    if open_only:
        q = q.where(tasks.c.status != "done")
    if area:
        q = q.where(tasks.c.area == area)
    with engine().connect() as conn:
        return [row_dict(r) for r in conn.execute(q)]


def claim(task_id: str, owner: str) -> dict[str, Any]:
    """Atomically take a task: fails if another owner has it in_progress, or it's done."""
    with engine().begin() as conn:
        res = conn.execute(
            update(tasks)
            .where(
                and_(
                    tasks.c.task_id == task_id,
                    tasks.c.status != "done",
                    or_(
                        tasks.c.status != "in_progress",
                        tasks.c.owner.is_(None),
                        tasks.c.owner == owner,
                    ),
                )
            )
            .values(owner=owner, status="in_progress", updated_at=now())
        )
    if res.rowcount != 1:
        cur = get_task(task_id)
        if cur["status"] == "done":
            raise InvalidStateError(f"task {task_id} is done", task=cur)
        raise ConflictError(f"task {task_id} is claimed by {cur['owner']}", task=cur)
    return get_task(task_id)


def update_task(
    task_id: str,
    status: str | None = None,
    owner: str | None = None,
    priority: int | None = None,
    description: str | None = None,
    area: str | None = None,
    release: bool = False,
) -> dict[str, Any]:
    values: dict[str, Any] = {"updated_at": now()}
    if status is not None:
        if status not in TASK_STATUSES:
            raise ValueError(f"status must be one of {TASK_STATUSES}")
        values["status"] = status
    if owner is not None:
        values["owner"] = owner
    if release:
        values["owner"] = None
    if priority is not None:
        values["priority"] = priority
    if description is not None:
        values["description"] = description
    if area is not None:
        values["area"] = area
    with engine().begin() as conn:
        res = conn.execute(update(tasks).where(tasks.c.task_id == task_id).values(**values))
    if res.rowcount != 1:
        raise NotFoundError(f"task {task_id} not found", task_id=task_id)
    return get_task(task_id)


def done(task_id: str, owner: str | None = None) -> dict[str, Any]:
    cur = get_task(task_id)
    if owner and cur["owner"] and cur["owner"] != owner and cur["status"] == "in_progress":
        raise ConflictError(f"task {task_id} is claimed by {cur['owner']}", task=cur)
    return update_task(task_id, status="done")
