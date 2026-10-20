"""Ops ledger over PostgREST: single-row reads/writes straight to Supabase (`--rest`).

Cloud sessions cannot reach Postgres, only HTTPS. Instead of keeping a local SQLite copy and
republishing whole tables (which can clobber newer online rows), `fantasy run|lock|approval|txn|task
... --rest` uses the classes below: every write is one INSERT, or one PATCH/DELETE whose filter
includes the expected current state (compare-and-set), e.g.

    PATCH /rest/v1/transactions?txn_id=eq.X&status=in.(planned,awaiting_approval)
          Prefer: return=representation   -> [] means someone changed it first -> InvalidStateError

so a concurrent change (the user deciding on the dashboard, another session) makes the call fail with
the same error code / exit code the local SQLAlchemy path uses, instead of overwriting it.

Lost responses: the client never re-sends a write whose response was lost (it may have committed);
it raises `rest.RestOutcomeUnknownError` and `RestStore` resolves it. An INSERT is re-read by its
primary key (ours are generated, so a matching row is our own write). A PATCH/DELETE is re-issued
once, which is safe because every one here is conditional (CAS: an already-applied write now
matches 0 rows) or sets absolute values; the 0-row paths then re-read the row and accept it when it
already holds exactly our write (`_transition`, `end_run`, `done`), so the outcome is idempotent.

The business rules are not duplicated: kinds, statuses, the txn state machine (`TRANSITIONS`),
idempotency keys, default scopes, required locks, policy and `agrees_with_model` all come from the
local ops modules. Only the storage calls differ. Method names and signatures mirror the module
functions (`ledger.start_run` -> `RestLedger.start_run`, ...) so the CLI can pick either backend.

Deliberately missing: `approvals.decide` (human-only: dashboard or a human typing
`fantasy approval decide`), `locks.force_release` (human override), `ledger.reap_stale_runs`.

Auth is whatever `fantasy.db.rest.RestClient` sends (env key, or none and the session's network
secret injects it). Keys are never printed.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import DateTime

from fantasy.db import rest
from fantasy.db.schema import metadata
from fantasy.ops import approvals as _approvals
from fantasy.ops import ledger as _ledger
from fantasy.ops import locks as _locks
from fantasy.ops import policy
from fantasy.ops import tasks as _tasks
from fantasy.ops import transactions as _txns
from fantasy.ops.common import (
    ConflictError,
    DuplicateError,
    InvalidStateError,
    LockHeldError,
    NotAllowedError,
    NotFoundError,
    as_utc,
    canonical_json,
    git_sha,
    new_id,
    now,
)

Filters = list[tuple[str, str]]


# ------------------------------------------------------------------ PostgREST filter helpers


def ts(value: datetime) -> str:
    """Timestamp literal for a filter (no '+', which would need escaping in a query string)."""
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def q(value: Any) -> str:
    """Double-quote a value for use inside in.(...) / or=(...) lists."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _same_ts(a: Any, b: datetime) -> bool:
    """Timestamp read back == the one we wrote (identifies our own write after a lost response)."""
    return isinstance(a, datetime) and as_utc(a) == as_utc(b)


def eq(value: Any) -> str:
    return f"eq.{value}"


def in_(values: tuple[str, ...] | list[str]) -> str:
    return "in.(" + ",".join(q(v) for v in values) + ")"


class Conflict409(rest.RestError):
    """INSERT hit a unique/PK constraint (PostgREST 409). Internal: callers translate it."""

    code = "rest_conflict"


def default_client() -> rest.RestClient:
    return rest.default_client()  # module-level hook: tests monkeypatch it


class RestStore:
    """Thin typed wrapper: JSON <-> row dicts (aware UTC datetimes), one request per call."""

    def __init__(self, client: rest.RestClient | None = None) -> None:
        self.client = client or default_client()

    # -- (de)serialisation
    @staticmethod
    def _decode(table: str, row: dict[str, Any]) -> dict[str, Any]:
        t = metadata.tables[table]
        out = dict(row)
        for c in t.columns:
            v = out.get(c.name)
            if isinstance(c.type, DateTime) and isinstance(v, str):
                out[c.name] = as_utc(datetime.fromisoformat(v.replace("Z", "+00:00")))
        return out

    @staticmethod
    def _encode(row: dict[str, Any]) -> bytes:
        return json.dumps(
            {k: rest.to_json_value(v) for k, v in row.items()}, allow_nan=False
        ).encode()

    def _rows(self, table: str, resp: Any) -> list[dict[str, Any]]:
        if not resp.content:
            return []
        data = resp.json()
        if isinstance(data, dict):
            data = [data]
        return [self._decode(table, r) for r in data]

    # -- verbs
    def select(
        self, table: str, filters: Filters | None = None, order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [("select", "*"), *(filters or [])]
        if order:
            params.append(("order", order))
        if limit is not None:
            params.append(("limit", limit))
        resp = self.client.request("GET", table, params=params)
        self.client.check(resp, f"read {table}")
        return self._rows(table, resp)

    def one(self, table: str, filters: Filters) -> dict[str, Any] | None:
        rows = self.select(table, filters, limit=1)
        return rows[0] if rows else None

    @staticmethod
    def same_values(row: dict[str, Any], values: dict[str, Any]) -> bool:
        """Does `row` (as read back) hold `values` (as we wrote them)? Datetimes and `*_at` keys
        inside JSON objects are ignored (server precision/format may differ; ids identify us)."""
        def norm(v: Any) -> Any:
            if isinstance(v, dict):
                return {k: norm(x) for k, x in v.items() if not str(k).endswith("_at")}
            return json.loads(json.dumps(rest.to_json_value(v)))
        return all(
            k in row and norm(row[k]) == norm(v)
            for k, v in values.items() if not isinstance(v, datetime)
        )

    def _pk_filters(self, table: str, row: dict[str, Any]) -> Filters:
        return [(c.name, eq(row[c.name])) for c in metadata.tables[table].primary_key.columns]

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """Plain INSERT (no upsert). Raises Conflict409 on a unique/PK clash. If the response is
        lost, the row is re-read by primary key: present with our values -> our insert landed."""
        try:
            resp = self.client.request(
                "POST",
                table,
                headers={"Prefer": "return=representation", "Content-Type": "application/json"},
                content=self._encode(row),
            )
        except rest.RestOutcomeUnknownError:
            got = self.one(table, self._pk_filters(table, row))
            if got is not None and self.same_values(got, row):
                return got
            raise
        if resp.status_code == 409:
            raise Conflict409(f"insert {table}: conflict", status=409)
        self.client.check(resp, f"insert {table}")
        rows = self._rows(table, resp)
        return rows[0] if rows else row

    def patch(self, table: str, filters: Filters, values: dict[str, Any]) -> list[dict[str, Any]]:
        """Conditional UPDATE; returns the rows that matched (empty = precondition failed)."""
        if not filters:
            raise ValueError("refusing an unfiltered PATCH")
        kw: dict[str, Any] = {
            "params": list(filters),
            "headers": {"Prefer": "return=representation", "Content-Type": "application/json"},
            "content": self._encode(values),
        }
        try:
            resp = self.client.request("PATCH", table, **kw)
        except rest.RestOutcomeUnknownError:
            # Conditional/absolute: re-issuing matches 0 rows if the first one applied, and the
            # caller's 0-row path re-reads (see module docstring). A second loss propagates.
            resp = self.client.request("PATCH", table, **kw)
        self.client.check(resp, f"update {table}")
        return self._rows(table, resp)

    def delete(self, table: str, filters: Filters) -> list[dict[str, Any]]:
        if not filters:
            raise ValueError("refusing an unfiltered DELETE")
        kw: dict[str, Any] = {"params": list(filters), "headers": {"Prefer": "return=representation"}}
        try:
            resp = self.client.request("DELETE", table, **kw)
        except rest.RestOutcomeUnknownError:  # same filter again: deletes nothing if it applied
            resp = self.client.request("DELETE", table, **kw)
        self.client.check(resp, f"delete {table}")
        return self._rows(table, resp)


# ------------------------------------------------------------------ runs, events, decisions


class RestLedger:
    RUN_MODES = _ledger.RUN_MODES
    EVENT_KINDS = _ledger.EVENT_KINDS
    DECISION_KINDS = _ledger.DECISION_KINDS
    DEFAULT_STALE_MINUTES = _ledger.DEFAULT_STALE_MINUTES

    def __init__(self, store: RestStore, locks: RestLocks) -> None:
        self.s = store
        self.locks = locks

    def start_run(self, agent: str, task: str | None = None, mode: str = "dry_run",
                  session_ref: str | None = None, run_id: str | None = None) -> str:
        if mode not in _ledger.RUN_MODES:
            raise ValueError(f"mode must be one of {_ledger.RUN_MODES}, got {mode!r}")
        rid = run_id or new_id("run-")
        t = now()
        self.s.insert("agent_runs", {
            "run_id": rid, "agent": agent, "task": task, "status": "running", "mode": mode,
            "git_sha": git_sha(), "session_ref": session_ref, "started_at": t, "heartbeat_at": t,
        })
        return rid

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.s.one("agent_runs", [("run_id", eq(run_id))])
        if row is None:
            raise NotFoundError(f"run {run_id} not found", run_id=run_id)
        return row

    def require_running(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] != "running":
            raise InvalidStateError(
                f"run {run_id} is {run['status']}, not running", run_id=run_id, status=run["status"]
            )
        return run

    def heartbeat(self, run_id: str) -> dict[str, Any]:
        rows = self.s.patch("agent_runs", [("run_id", eq(run_id)), ("status", "eq.running")],
                            {"heartbeat_at": now()})
        if len(rows) != 1:
            self.require_running(run_id)
        return self.get_run(run_id)

    def end_run(self, run_id: str, status: str = "succeeded",
                summary: str | None = None) -> dict[str, Any]:
        if status not in _ledger.RUN_END_STATUSES:
            raise ValueError(f"status must be one of {_ledger.RUN_END_STATUSES}, got {status!r}")
        t = now()
        rows = self.s.patch(
            "agent_runs",
            [("run_id", eq(run_id)), ("status", in_(("running", "abandoned")))],
            {"status": status, "summary": summary, "ended_at": t, "heartbeat_at": t},
        )
        if len(rows) != 1:
            run = self.get_run(run_id)
            if run["status"] == status and _same_ts(run["ended_at"], t):
                # our own write applied but its response was lost: finish the job idempotently
                return {**run, "released_locks": self.locks.release_all(run_id)}
            raise InvalidStateError(
                f"run {run_id} already ended ({run['status']})", run_id=run_id, status=run["status"]
            )
        released = self.locks.release_all(run_id)
        if released:
            self.log_event(run_id, "note", f"run ended; released locks: {', '.join(released)}",
                           {"released_locks": released})
        return {**self.get_run(run_id), "released_locks": released}

    def list_runs(self, status: str | None = None, agent: str | None = None,
                  limit: int = 50) -> list[dict]:
        f: Filters = []
        if status:
            f.append(("status", eq(status)))
        if agent:
            f.append(("agent", eq(agent)))
        return self.s.select("agent_runs", f, order="started_at.desc", limit=limit)

    def log_event(self, run_id: str, kind: str, message: str | None = None,
                  payload: Any = None) -> str:
        if kind not in _ledger.EVENT_KINDS:
            raise ValueError(f"event kind must be one of {_ledger.EVENT_KINDS}, got {kind!r}")
        eid = new_id("evt-")
        self.s.insert("agent_events", {"event_id": eid, "run_id": run_id, "ts": now(), "kind": kind,
                                       "message": message, "payload": payload})
        return eid

    def list_events(self, run_id: str, limit: int = 200) -> list[dict]:
        return self.s.select("agent_events", [("run_id", eq(run_id))], order="ts.asc", limit=limit)

    def record_decision(self, run_id: str | None, kind: str, model_recommendation: Any,
                        choice: Any, rationale: str | None = None,
                        agrees: bool | None = None) -> dict[str, Any]:
        if kind not in _ledger.DECISION_KINDS:
            raise ValueError(f"decision kind must be one of {_ledger.DECISION_KINDS}, got {kind!r}")
        agree = agrees if agrees is not None else _ledger.agrees_with_model(model_recommendation,
                                                                           choice)
        did = new_id("dec-")
        self.s.insert("decisions", {
            "decision_id": did, "run_id": run_id, "kind": kind,
            "model_recommendation": model_recommendation, "choice": choice,
            "agrees_with_model": agree, "rationale": rationale, "created_at": now(),
        })
        if run_id:
            self.log_event(
                run_id, "decision",
                f"{kind}: {'agrees with' if agree else 'overrides' if agree is False else 'no'} model",
                {"decision_id": did, "choice": choice, "agrees_with_model": agree},
            )
        return {"decision_id": did, "run_id": run_id, "kind": kind, "agrees_with_model": agree}

    def list_decisions(self, run_id: str) -> list[dict]:
        return self.s.select("decisions", [("run_id", eq(run_id))], order="created_at.asc")


# ------------------------------------------------------------------ locks


class RestLocks:
    DEFAULT_TTL = _locks.DEFAULT_TTL
    draft_resource = staticmethod(_locks.draft_resource)

    def __init__(self, store: RestStore) -> None:
        self.s = store

    def get_lock(self, resource: str) -> dict[str, Any] | None:
        d = self.s.one("locks", [("resource", eq(resource))])
        if d is not None:
            d["expired"] = d["expires_at"] is not None and d["expires_at"] < now()
        return d

    def _must_lock(self, resource: str) -> dict[str, Any]:
        lk = self.get_lock(resource)
        if lk is None:  # released between our write and the read-back
            raise InvalidStateError(f"{resource} is not locked", resource=resource)
        return lk

    def list_locks(self, include_expired: bool = False) -> list[dict[str, Any]]:
        t = now()
        out = []
        for d in self.s.select("locks", order="resource.asc"):
            d["expired"] = d["expires_at"] is not None and d["expires_at"] < t
            if include_expired or not d["expired"]:
                out.append(d)
        return out

    def _try_insert(self, resource: str, run_id: str, ttl: float, purpose: str | None) -> bool:
        t = now()
        try:
            self.s.insert("locks", {"resource": resource, "holder_run_id": run_id,
                                    "purpose": purpose, "acquired_at": t,
                                    "expires_at": t + timedelta(seconds=ttl)})
            return True
        except Conflict409:
            return False

    def _try_takeover(self, resource: str, run_id: str, ttl: float, purpose: str | None) -> bool:
        t = now()
        rows = self.s.patch(
            "locks",
            [("resource", eq(resource)),
             ("or", f"(expires_at.lt.{ts(t)},holder_run_id.eq.{q(run_id)})")],
            {"holder_run_id": run_id, "purpose": purpose, "acquired_at": t,
             "expires_at": t + timedelta(seconds=ttl)},
        )
        return len(rows) == 1

    def acquire(self, resource: str, run_id: str, ttl_seconds: float = _locks.DEFAULT_TTL,
                purpose: str | None = None) -> dict[str, Any]:
        for _ in range(5):
            if self._try_insert(resource, run_id, ttl_seconds, purpose):
                return self._must_lock(resource) | {"taken_over": False}
            if self._try_takeover(resource, run_id, ttl_seconds, purpose):
                return self._must_lock(resource) | {"taken_over": True}
            current = self.get_lock(resource)
            if current is not None:
                raise LockHeldError(
                    f"{resource} is held by {current['holder_run_id']} until "
                    f"{current['expires_at'].isoformat()}",
                    resource=resource, holder=current,
                )
        current = self.get_lock(resource)
        raise LockHeldError(f"could not acquire {resource}", resource=resource, holder=current)

    def renew(self, resource: str, run_id: str,
              ttl_seconds: float = _locks.DEFAULT_TTL) -> dict[str, Any]:
        rows = self.s.patch("locks", [("resource", eq(resource)), ("holder_run_id", eq(run_id))],
                            {"expires_at": now() + timedelta(seconds=ttl_seconds)})
        if len(rows) != 1:
            current = self.get_lock(resource)
            if current is None:
                raise InvalidStateError(f"{resource} is not locked", resource=resource)
            raise LockHeldError(f"{resource} is held by {current['holder_run_id']}",
                                resource=resource, holder=current)
        return self._must_lock(resource)

    def release(self, resource: str, run_id: str) -> bool:
        rows = self.s.delete("locks", [("resource", eq(resource)), ("holder_run_id", eq(run_id))])
        if len(rows) == 1:
            return True
        current = self.get_lock(resource)
        if current is None:
            return False
        raise LockHeldError(f"{resource} is held by {current['holder_run_id']}, not {run_id}",
                            resource=resource, holder=current)

    def release_all(self, run_id: str) -> list[str]:
        rows = self.s.delete("locks", [("holder_run_id", eq(run_id))])
        return sorted(r["resource"] for r in rows)

    def holds(self, resource: str, run_id: str) -> bool:
        lk = self.get_lock(resource)
        return bool(lk and lk["holder_run_id"] == run_id and as_utc(lk["expires_at"]) >= now())


# ------------------------------------------------------------------ approvals (no `decide`)


class RestApprovals:
    APPROVAL_STATUSES = _approvals.APPROVAL_STATUSES
    DEFAULT_EXPIRES_IN = _approvals.DEFAULT_EXPIRES_IN

    def __init__(self, store: RestStore, sleep=time.sleep) -> None:
        self.s = store
        self.sleep = sleep

    def expire_overdue(self) -> int:
        """pending & past expires_at -> expired. Conditional on `pending`: never touches a decision."""
        t = now()
        rows = self.s.patch(
            "approvals", [("status", "eq.pending"), ("expires_at", f"lt.{ts(t)}")],
            {"status": "expired", "decided_at": t, "note": "expired without a decision"},
        )
        return len(rows)

    def request_approval(self, run_id: str | None, action_type: str, summary: str,
                         payload: Any = None,
                         expires_in: float | None = _approvals.DEFAULT_EXPIRES_IN) -> str:
        aid = new_id("apr-")
        t = now()
        self.s.insert("approvals", {
            "approval_id": aid, "run_id": run_id, "action_type": action_type, "summary": summary,
            "payload": payload, "status": "pending", "requested_at": t,
            "expires_at": t + timedelta(seconds=expires_in) if expires_in else None,
        })
        return aid

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        self.expire_overdue()
        row = self.s.one("approvals", [("approval_id", eq(approval_id))])
        if row is None:
            raise NotFoundError(f"approval {approval_id} not found", approval_id=approval_id)
        return row

    def list_approvals(self, status: str | None = "pending", limit: int = 100) -> list[dict]:
        self.expire_overdue()
        f: Filters = [("status", eq(status))] if status else []
        return self.s.select("approvals", f, order="requested_at.desc", limit=limit)

    def list_pending(self) -> list[dict[str, Any]]:
        return self.list_approvals("pending")

    def check(self, approval_id: str) -> str:
        return self.get_approval(approval_id)["status"]

    def wait(self, approval_id: str, timeout: float = 0, poll: float = 5.0) -> str:
        deadline = time.monotonic() + timeout
        while True:
            status = self.check(approval_id)
            if status != "pending" or time.monotonic() >= deadline:
                return status
            self.sleep(min(poll, max(0.0, deadline - time.monotonic())))


# ------------------------------------------------------------------ transactions


class RestTransactions:
    TXN_KINDS = _txns.TXN_KINDS
    TXN_STATUSES = _txns.TXN_STATUSES
    TRANSITIONS = _txns.TRANSITIONS

    def __init__(self, store: RestStore, ledger: RestLedger, approvals: RestApprovals,
                 locks: RestLocks) -> None:
        self.s = store
        self.ledger = ledger
        self.approvals = approvals
        self.locks = locks

    def get_txn(self, txn_id: str) -> dict[str, Any]:
        row = self.s.one("transactions", [("txn_id", eq(txn_id))])
        if row is None:
            raise NotFoundError(f"transaction {txn_id} not found", txn_id=txn_id)
        return row

    def get_by_key(self, key: str) -> dict[str, Any] | None:
        return self.s.one("transactions", [("idempotency_key", eq(key))])

    def _dup(self, existing: dict[str, Any]) -> DuplicateError:
        return DuplicateError(
            f"transaction already planned ({existing['txn_id']}, {existing['status']})",
            existing=existing,
        )

    def plan(self, kind: str, payload: dict[str, Any], run_id: str | None = None,
             scope: str | None = None, attempt: int = 1, summary: str | None = None,
             approval_expires_in: float | None = _approvals.DEFAULT_EXPIRES_IN) -> dict[str, Any]:
        if kind not in _txns.TXN_KINDS:
            raise ValueError(f"kind must be one of {_txns.TXN_KINDS}, got {kind!r}")
        scope = scope or _txns.default_scope(kind, payload)
        key = _txns.idempotency_key(kind, payload, scope, attempt)
        existing = self.get_by_key(key)
        if existing is not None:
            raise self._dup(existing)
        txn_id = new_id("txn-")
        t = now()
        try:
            self.s.insert("transactions", {
                "txn_id": txn_id, "idempotency_key": key, "run_id": run_id, "kind": kind,
                "payload": payload, "status": "planned",
                "verification": {"scope": scope, "attempt": attempt},
                "created_at": t, "updated_at": t,
            })
        except Conflict409 as exc:  # lost a race with an identical plan
            existing = self.get_by_key(key)
            if existing is None:
                raise
            raise self._dup(existing) from exc
        except rest.RestOutcomeUnknownError:  # store.insert found no row of ours by txn_id
            existing = self.get_by_key(key)
            if existing is None or existing["txn_id"] != txn_id:
                raise
        # (A lost INSERT response that did commit is resolved by RestStore.insert: we continue
        # with our own txn_id and create the approval below, so it is never left without one.)
        if policy.requires_approval(kind):
            approval_id = self.approvals.request_approval(
                run_id, kind, summary or f"{kind}: {canonical_json(payload)}",
                {"txn_id": txn_id, "kind": kind, "payload": payload}, approval_expires_in,
            )
            self._transition(txn_id, "planned", "awaiting_approval", approval_id=approval_id)
        if run_id:
            self.ledger.log_event(run_id, "note", f"planned {kind} transaction {txn_id}",
                                  {"txn_id": txn_id})
        return self.get_txn(txn_id)

    def _transition(self, txn_id: str, frm: str | tuple[str, ...], to: str, **values: Any) -> None:
        """Compare-and-set on status: PATCH ...&status=in.(<expected>)."""
        froms = (frm,) if isinstance(frm, str) else frm
        for f in froms:
            if to not in _txns.TRANSITIONS[f]:
                raise InvalidStateError(f"invalid transition {f} -> {to}", txn_id=txn_id)
        t = now()
        rows = self.s.patch("transactions", [("txn_id", eq(txn_id)), ("status", in_(froms))],
                            {"status": to, "updated_at": t, **values})
        if len(rows) != 1:
            cur = self.get_txn(txn_id)
            # updated_at == our t identifies *this* write (a genuine repeat call still fails)
            if (cur["status"] == to and _same_ts(cur["updated_at"], t)
                    and self.s.same_values(cur, values)):
                return  # our own write already applied (its response was lost): idempotent
            raise InvalidStateError(
                f"invalid transition {cur['status']} -> {to}",
                txn_id=txn_id, status=cur["status"],
                allowed=sorted(_txns.TRANSITIONS[cur["status"]]),
            )

    @staticmethod
    def _merge_verification(txn: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        return {**(txn.get("verification") or {}), **extra}

    def check_submit(self, txn_id: str, run_id: str) -> dict[str, Any]:
        txn = self.get_txn(txn_id)
        if txn["status"] not in ("planned", "awaiting_approval"):
            raise InvalidStateError(
                f"cannot submit a {txn['status']} transaction"
                + (" -- reconcile against Yahoo first" if txn["status"] == "submitted" else ""),
                txn_id=txn_id, status=txn["status"],
            )
        run = self.ledger.require_running(run_id)
        approval_status = self.approvals.check(txn["approval_id"]) if txn["approval_id"] else None
        allowed, reason = policy.check_action_allowed(txn["kind"], run["mode"], approval_status)
        if not allowed:
            raise NotAllowedError(reason, txn_id=txn_id, approval_id=txn["approval_id"],
                                  approval_status=approval_status)
        resource = _txns.required_lock(txn)
        if not self.locks.holds(resource, run_id):
            raise NotAllowedError(f"run {run_id} must hold lock {resource} to submit",
                                  txn_id=txn_id, resource=resource,
                                  holder=self.locks.get_lock(resource))
        return txn

    def mark_submitted(self, txn_id: str, run_id: str) -> dict[str, Any]:
        txn = self.check_submit(txn_id, run_id)
        self._transition(txn_id, txn["status"], "submitted", verification=self._merge_verification(
            txn, {"submitted_by": run_id, "submitted_at": now().isoformat()}))
        self.ledger.log_event(run_id, "command", f"submitting {txn['kind']} {txn_id}",
                              {"txn_id": txn_id})
        return self.get_txn(txn_id)

    def mark_verified(self, txn_id: str, evidence: dict[str, Any],
                      run_id: str | None = None) -> dict[str, Any]:
        if not evidence:
            raise ValueError("verification evidence is required (what in Yahoo confirms it?)")
        txn = self.get_txn(txn_id)
        self._transition(txn_id, "submitted", "verified", verification=self._merge_verification(
            txn, {"evidence": evidence, "verified_at": now().isoformat(), "verified_by": run_id}))
        if run_id:
            self.ledger.log_event(run_id, "verification", f"verified {txn_id}",
                                  {"evidence": evidence})
        return self.get_txn(txn_id)

    def mark_failed(self, txn_id: str, error: str, evidence: dict[str, Any] | None = None,
                    run_id: str | None = None) -> dict[str, Any]:
        txn = self.get_txn(txn_id)
        extra: dict[str, Any] = {"failed_at": now().isoformat()}
        if evidence:
            extra["evidence"] = evidence
        self._transition(txn_id, ("planned", "awaiting_approval", "submitted"), "failed",
                         error=error, verification=self._merge_verification(txn, extra))
        if run_id:
            self.ledger.log_event(run_id, "error", f"transaction {txn_id} failed: {error}",
                                  evidence)
        return self.get_txn(txn_id)

    def cancel(self, txn_id: str, reason: str | None = None) -> dict[str, Any]:
        self._transition(txn_id, ("planned", "awaiting_approval"), "cancelled", error=reason)
        return self.get_txn(txn_id)

    def mark(self, txn_id: str, status: str, run_id: str | None = None,
             evidence: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
        if status == "submitted":
            if not run_id:
                raise InvalidStateError("submitting requires --run-id", txn_id=txn_id)
            return self.mark_submitted(txn_id, run_id)
        if status == "verified":
            return self.mark_verified(txn_id, evidence or {}, run_id)
        if status == "failed":
            return self.mark_failed(txn_id, error or "unspecified", evidence, run_id)
        if status == "cancelled":
            return self.cancel(txn_id, error)
        if status in ("planned", "awaiting_approval"):
            cur = self.get_txn(txn_id)
            self._transition(txn_id, cur["status"], status)
            return self.get_txn(txn_id)
        raise ValueError(f"status must be one of {_txns.TXN_STATUSES}, got {status!r}")

    def list_txns(self, status: str | None = None, kind: str | None = None,
                  limit: int = 100) -> list[dict[str, Any]]:
        f: Filters = []
        if status:
            f.append(("status", eq(status)))
        if kind:
            f.append(("kind", eq(kind)))
        return self.s.select("transactions", f, order="created_at.desc", limit=limit)

    def needs_reconciliation(self, older_than_seconds: float = 0) -> list[dict[str, Any]]:
        cutoff = now() - timedelta(seconds=older_than_seconds)
        return self.s.select(
            "transactions", [("status", "eq.submitted"), ("updated_at", f"lte.{ts(cutoff)}")],
            order="updated_at.asc",
        )


# ------------------------------------------------------------------ tasks


class RestTasks:
    TASK_STATUSES = _tasks.TASK_STATUSES
    TASK_AREAS = _tasks.TASK_AREAS

    def __init__(self, store: RestStore) -> None:
        self.s = store

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self.s.one("tasks", [("task_id", eq(task_id))])
        if row is None:
            raise NotFoundError(f"task {task_id} not found", task_id=task_id)
        return row

    def find_by_title(self, title: str) -> dict[str, Any] | None:
        return self.s.one("tasks", [("title", eq(title))])

    def _next_id(self) -> str:
        best = 0
        for r in self.s.select("tasks"):
            m = _tasks._ID_RE.match(r.get("task_id") or "")
            if m:
                best = max(best, int(m.group(1)))
        return f"T-{best + 1:03d}"

    def add(self, title: str, description: str | None = None, area: str | None = None,
            priority: int = 2, status: str = "todo") -> dict[str, Any]:
        if status not in _tasks.TASK_STATUSES:
            raise ValueError(f"status must be one of {_tasks.TASK_STATUSES}")
        if not 0 <= priority <= 3:
            raise ValueError("priority must be 0..3")
        for _ in range(10):
            existing = self.find_by_title(title)
            if existing:
                raise DuplicateError(f"task with this title exists ({existing['task_id']})",
                                     existing=existing)
            t = now()
            tid = self._next_id()
            try:
                self.s.insert("tasks", {
                    "task_id": tid, "title": title, "description": description, "area": area,
                    "priority": priority, "status": status, "created_at": t, "updated_at": t,
                })
            except Conflict409:
                continue
            return self.get_task(tid)
        raise ConflictError("could not allocate a task id")

    def list_tasks(self, status: str | list[str] | None = None, area: str | None = None,
                   open_only: bool = False) -> list[dict[str, Any]]:
        f: Filters = []
        if isinstance(status, str):
            f.append(("status", eq(status)))
        elif status:
            f.append(("status", in_(list(status))))
        if open_only:
            f.append(("status", "neq.done"))
        if area:
            f.append(("area", eq(area)))
        return self.s.select("tasks", f, order="priority.asc,task_id.asc")

    def claim(self, task_id: str, owner: str) -> dict[str, Any]:
        rows = self.s.patch(
            "tasks",
            [("task_id", eq(task_id)), ("status", "neq.done"),
             ("or", f"(status.neq.in_progress,owner.is.null,owner.eq.{q(owner)})")],
            {"owner": owner, "status": "in_progress", "updated_at": now()},
        )
        if len(rows) != 1:
            cur = self.get_task(task_id)
            if cur["status"] == "done":
                raise InvalidStateError(f"task {task_id} is done", task=cur)
            raise ConflictError(f"task {task_id} is claimed by {cur['owner']}", task=cur)
        return self.get_task(task_id)

    def update_task(self, task_id: str, status: str | None = None, owner: str | None = None,
                    priority: int | None = None, description: str | None = None,
                    area: str | None = None, release: bool = False) -> dict[str, Any]:
        values: dict[str, Any] = {"updated_at": now()}
        if status is not None:
            if status not in _tasks.TASK_STATUSES:
                raise ValueError(f"status must be one of {_tasks.TASK_STATUSES}")
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
        rows = self.s.patch("tasks", [("task_id", eq(task_id))], values)
        if len(rows) != 1:
            raise NotFoundError(f"task {task_id} not found", task_id=task_id)
        return self.get_task(task_id)

    def done(self, task_id: str, owner: str | None = None) -> dict[str, Any]:
        cur = self.get_task(task_id)
        if owner and cur["owner"] and cur["owner"] != owner and cur["status"] == "in_progress":
            raise ConflictError(f"task {task_id} is claimed by {cur['owner']}", task=cur)
        # CAS: only finish it if nobody claimed/changed it since we looked
        f: Filters = [("task_id", eq(task_id)), ("status", eq(cur["status"]))]
        f.append(("owner", "is.null") if cur["owner"] is None else ("owner", eq(cur["owner"])))
        t = now()
        rows = self.s.patch("tasks", f, {"status": "done", "updated_at": t})
        if len(rows) != 1:
            now_ = self.get_task(task_id)
            if now_["status"] == "done" and _same_ts(now_["updated_at"], t):
                return now_  # our own write already applied (its response was lost)
            raise ConflictError(f"task {task_id} changed concurrently "
                                f"({now_['status']}, owner {now_['owner']})", task=now_)
        return self.get_task(task_id)


# ------------------------------------------------------------------ backend bundle


def backend(client: rest.RestClient | None = None) -> SimpleNamespace:
    """Objects with the same method names as the ops modules, backed by PostgREST."""
    store = RestStore(client)
    lk = RestLocks(store)
    led = RestLedger(store, lk)
    apr = RestApprovals(store)
    return SimpleNamespace(
        rest=True,
        store=store,
        ledger=led,
        locks=lk,
        approvals=apr,
        transactions=RestTransactions(store, led, apr, lk),
        tasks=RestTasks(store),
    )


def local_backend() -> SimpleNamespace:
    """The SQLAlchemy ops modules themselves (unchanged behaviour)."""
    return SimpleNamespace(rest=False, ledger=_ledger, locks=_locks, approvals=_approvals,
                           transactions=_txns, tasks=_tasks)


__all__ = [
    "Conflict409", "RestApprovals", "RestLedger", "RestLocks", "RestStore", "RestTasks",
    "RestTransactions", "backend", "default_client", "local_backend",
]
