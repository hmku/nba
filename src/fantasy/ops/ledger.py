"""Agent run ledger: runs, heartbeats, events and decisions.

Lifecycle: start_run -> (log_event / record_decision / heartbeat)* -> end_run.
end_run releases every lock the run still holds (reported as `released_locks`).
A run whose heartbeat is older than `stale_minutes` is considered stale; `reap_stale_runs`
marks such runs `abandoned` (it does not release their locks -- locks expire by TTL).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update

from fantasy.db.schema import agent_events, agent_runs, decisions
from fantasy.ops.common import (
    InvalidStateError,
    NotFoundError,
    canonical_json,
    engine,
    git_sha,
    new_id,
    now,
    row_dict,
)

RUN_MODES = ("dry_run", "live")
RUN_END_STATUSES = ("succeeded", "failed", "abandoned")
EVENT_KINDS = (
    "observation",
    "command",
    "model_output",
    "decision",
    "browser_action",
    "verification",
    "error",
    "note",
)
DECISION_KINDS = ("draft_pick", "lineup", "add_drop", "trade", "other")
DEFAULT_STALE_MINUTES = 15


def start_run(
    agent: str,
    task: str | None = None,
    mode: str = "dry_run",
    session_ref: str | None = None,
    run_id: str | None = None,
) -> str:
    if mode not in RUN_MODES:
        raise ValueError(f"mode must be one of {RUN_MODES}, got {mode!r}")
    rid = run_id or new_id("run-")
    t = now()
    with engine().begin() as conn:
        conn.execute(
            agent_runs.insert().values(
                run_id=rid,
                agent=agent,
                task=task,
                status="running",
                mode=mode,
                git_sha=git_sha(),
                session_ref=session_ref,
                started_at=t,
                heartbeat_at=t,
            )
        )
    return rid


def get_run(run_id: str) -> dict[str, Any]:
    with engine().connect() as conn:
        row = conn.execute(select(agent_runs).where(agent_runs.c.run_id == run_id)).first()
    if row is None:
        raise NotFoundError(f"run {run_id} not found", run_id=run_id)
    return row_dict(row)


def require_running(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if run["status"] != "running":
        raise InvalidStateError(
            f"run {run_id} is {run['status']}, not running", run_id=run_id, status=run["status"]
        )
    return run


def heartbeat(run_id: str) -> dict[str, Any]:
    with engine().begin() as conn:
        res = conn.execute(
            update(agent_runs)
            .where(and_(agent_runs.c.run_id == run_id, agent_runs.c.status == "running"))
            .values(heartbeat_at=now())
        )
    if res.rowcount != 1:
        require_running(run_id)  # raises the precise error (not found / not running)
    return get_run(run_id)


def end_run(run_id: str, status: str = "succeeded", summary: str | None = None) -> dict[str, Any]:
    """End a run and release all locks it holds. Returns the run plus `released_locks`."""
    if status not in RUN_END_STATUSES:
        raise ValueError(f"status must be one of {RUN_END_STATUSES}, got {status!r}")
    t = now()
    with engine().begin() as conn:
        res = conn.execute(
            update(agent_runs)
            .where(
                and_(
                    agent_runs.c.run_id == run_id,
                    agent_runs.c.status.in_(("running", "abandoned")),
                )
            )
            .values(status=status, summary=summary, ended_at=t, heartbeat_at=t)
        )
    if res.rowcount != 1:
        run = get_run(run_id)
        raise InvalidStateError(
            f"run {run_id} already ended ({run['status']})", run_id=run_id, status=run["status"]
        )
    from fantasy.ops import locks  # local import: locks must not depend on the ledger

    released = locks.release_all(run_id)
    if released:
        log_event(run_id, "note", f"run ended; released locks: {', '.join(released)}",
                  {"released_locks": released})
    return {**get_run(run_id), "released_locks": released}


def list_runs(status: str | None = None, agent: str | None = None, limit: int = 50) -> list[dict]:
    q = select(agent_runs).order_by(agent_runs.c.started_at.desc()).limit(limit)
    if status:
        q = q.where(agent_runs.c.status == status)
    if agent:
        q = q.where(agent_runs.c.agent == agent)
    with engine().connect() as conn:
        return [row_dict(r) for r in conn.execute(q)]


def _stale_clause(stale_minutes: float):
    cutoff = now() - timedelta(minutes=stale_minutes)
    hb = func.coalesce(agent_runs.c.heartbeat_at, agent_runs.c.started_at)
    return and_(agent_runs.c.status == "running", or_(hb.is_(None), hb < cutoff))


def stale_runs(stale_minutes: float = DEFAULT_STALE_MINUTES) -> list[dict]:
    with engine().connect() as conn:
        rows = conn.execute(select(agent_runs).where(_stale_clause(stale_minutes)))
        return [row_dict(r) for r in rows]


def reap_stale_runs(stale_minutes: float = DEFAULT_STALE_MINUTES) -> list[str]:
    """Mark stale running runs as abandoned. Returns the reaped run ids."""
    reaped: list[str] = []
    for run in stale_runs(stale_minutes):
        with engine().begin() as conn:
            res = conn.execute(
                update(agent_runs)
                .where(and_(agent_runs.c.run_id == run["run_id"], _stale_clause(stale_minutes)))
                .values(status="abandoned", ended_at=now())
            )
        if res.rowcount == 1:
            reaped.append(run["run_id"])
            log_event(
                run["run_id"],
                "note",
                f"run marked abandoned: no heartbeat for > {stale_minutes} min",
                {"reaped": True},
            )
    return reaped


# ------------------------------------------------------------------ events & decisions


def log_event(
    run_id: str, kind: str, message: str | None = None, payload: Any = None
) -> str:
    if kind not in EVENT_KINDS:
        raise ValueError(f"event kind must be one of {EVENT_KINDS}, got {kind!r}")
    eid = new_id("evt-")
    with engine().begin() as conn:
        conn.execute(
            agent_events.insert().values(
                event_id=eid, run_id=run_id, ts=now(), kind=kind, message=message, payload=payload
            )
        )
    return eid


def list_events(run_id: str, limit: int = 200) -> list[dict]:
    q = (
        select(agent_events)
        .where(agent_events.c.run_id == run_id)
        .order_by(agent_events.c.ts)
        .limit(limit)
    )
    with engine().connect() as conn:
        return [row_dict(r) for r in conn.execute(q)]


_IDENTITY_KEYS = ("player_id", "id", "action", "choice", "name")


def _identity(value: Any) -> str:
    if isinstance(value, dict):
        for k in _IDENTITY_KEYS:
            if k in value:
                return canonical_json(value[k])
    return canonical_json(value)


def agrees_with_model(model_recommendation: Any, choice: Any) -> bool | None:
    """Did the agent follow the model?

    - None if there was no model recommendation.
    - If the recommendation is a list (ranked candidates) the top candidate counts.
    - Dicts are compared by their identity key (player_id, id, action, choice, name) when present,
      otherwise by canonical JSON.
    """
    if model_recommendation is None or choice is None:
        return None
    rec = model_recommendation
    if isinstance(rec, list):
        if not rec:
            return None
        rec = rec[0]
    return _identity(rec) == _identity(choice)


def record_decision(
    run_id: str | None,
    kind: str,
    model_recommendation: Any,
    choice: Any,
    rationale: str | None = None,
    agrees: bool | None = None,
) -> dict[str, Any]:
    if kind not in DECISION_KINDS:
        raise ValueError(f"decision kind must be one of {DECISION_KINDS}, got {kind!r}")
    agree = agrees if agrees is not None else agrees_with_model(model_recommendation, choice)
    did = new_id("dec-")
    with engine().begin() as conn:
        conn.execute(
            decisions.insert().values(
                decision_id=did,
                run_id=run_id,
                kind=kind,
                model_recommendation=model_recommendation,
                choice=choice,
                agrees_with_model=agree,
                rationale=rationale,
                created_at=now(),
            )
        )
    if run_id:
        log_event(
            run_id,
            "decision",
            f"{kind}: {'agrees with' if agree else 'overrides' if agree is False else 'no'} model",
            {"decision_id": did, "choice": choice, "agrees_with_model": agree},
        )
    return {"decision_id": did, "run_id": run_id, "kind": kind, "agrees_with_model": agree}


def list_decisions(run_id: str) -> list[dict]:
    q = select(decisions).where(decisions.c.run_id == run_id).order_by(decisions.c.created_at)
    with engine().connect() as conn:
        return [row_dict(r) for r in conn.execute(q)]
