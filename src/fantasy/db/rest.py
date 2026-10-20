"""Publish local SQLite state to Supabase over HTTPS (PostgREST), for sessions without Postgres.

Cloud Claude Code sessions reach the internet only through an HTTPS proxy, so the SQLAlchemy/psycopg
path (`fantasy publish --target postgresql://...`) only works on the user's laptop. This module does
the same through `POST {SUPABASE_URL}/rest/v1/<table>?on_conflict=<pk>`, in FK-safe order, skipping
ephemeral tables, with a per-table policy (`publish_policy`): `resolution=merge-duplicates` (upsert)
for reference/model-output tables, `resolution=ignore-duplicates` (insert missing rows only) for
ledger/state tables, so a stale local copy can never overwrite newer online state. Single-row ledger
writes (CAS on status) live in `fantasy.ops.rest_ledger`.

Auth: SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_ROLE_KEY) is sent as the `apikey` header (plus
`Authorization: Bearer` when it is a legacy JWT). With no key in the environment no auth header
is sent: the session's network layer may inject it ("network secret"). The key is never printed.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import Table, create_engine, select

from fantasy.db.schema import metadata
from fantasy.ops.common import OpsError, jsonable, row_dict

DEFAULT_SUPABASE_URL = "https://bdzpkdkhlzjzddkptjex.supabase.co"
KEY_ENVS = ("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")
BATCH = 500
RETRIES = 2  # extra attempts (see RestClient.request for which failures are retried)
# Methods whose repetition cannot change server state; anything else is retried only when the
# request provably never reached the server (connection not established), unless the caller
# declares the write idempotent (bulk upsert / insert-ignore-duplicates in `publish --rest`).
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)
# Gateway answers after the request was forwarded: the write may or may not have been applied.
AMBIGUOUS_STATUSES = frozenset({502, 504})

# Ephemeral state that must not be copied (an offline lease means nothing online).
PUBLISH_SKIP = frozenset({"locks"})
# Per-table publish policy (docs/runbooks/agent_ops.md section 5b):
#   merge  -> upsert, `resolution=merge-duplicates`: the publishing copy owns these rows (reference
#             data, model outputs, the draft board), so republishing refreshes them.
#   insert -> `resolution=ignore-duplicates`: only rows missing online are inserted, an existing row
#             is NEVER overwritten. For ledger/state tables whose rows change after creation online
#             (the user approves on the dashboard, another session marks a txn, a run ends), and for
#             immutable logs. Change these rows one at a time with `fantasy ... --rest`.
#   Any table not listed in PUBLISH_MERGE (incl. tables added later) defaults to insert.
PUBLISH_MERGE = frozenset({
    "players", "league_settings", "data_snapshots",  # reference data, refreshed by rebuilds
    "model_runs", "projections", "player_values",  # model outputs (versioned by run_id)
    "draft_picks", "draft_recommendations",  # draft board as recorded by the drafting session
})
# Documentation of the insert-only set (the default); kept explicit so reviews see each choice.
PUBLISH_INSERT_ONLY = frozenset({
    "agent_runs",  # status/heartbeat/ended_at change after creation
    "approvals",  # pending -> approved/rejected/expired (dashboard)
    "transactions",  # state machine
    "tasks",  # claim/update/done
    "drafts",  # status pending -> live -> complete, config (order) updates
    "agent_events", "decisions",  # immutable logs
    "league_snapshots",  # written server-side by the Muse API (last_seen_at bumps)
})


def publish_policy(table: str) -> str:
    """'skip' | 'merge' | 'insert' (see PUBLISH_MERGE)."""
    if table in PUBLISH_SKIP:
        return "skip"
    return "merge" if table in PUBLISH_MERGE else "insert"
# Natural unique keys besides the PK: rows that would collide with a *different* target row
# are skipped (reported), never overwritten -- e.g. offline task T-013 vs. online T-013.
PUBLISH_UNIQUE = {"tasks": "title", "transactions": "idempotency_key"}

BLOCKED_HELP = (
    "The session's network policy blocked {host} (proxy answered: {err}). Allow the host: "
    "cloud environment menu -> Edit -> Network access -> add {host} under Allowed domains "
    "(keep 'Allow package managers' ticked), then start a new session."
)
AUTH_HELP = (
    "Supabase rejected the request (HTTP {status}): no/invalid API key. Configure one of:\n"
    "  - cloud: a Network secret for host bdzpkdkhlzjzddkptjex.supabase.co injecting header "
    "`apikey: <sb_secret_... key>` (new sessions pick it up), or\n"
    "  - env var SUPABASE_SECRET_KEY=<sb_secret_... key> (Supabase -> Project Settings -> API Keys).\n"
    "Never paste the key into chat. See docs/USER_TODO.md."
)


class RestError(OpsError):
    code = "rest_error"


class RestAuthError(RestError):
    code = "rest_auth"


class RestUnreachableError(RestError):
    code = "rest_unreachable"


class RestOutcomeUnknownError(RestUnreachableError):
    """A non-idempotent write was sent but its response was lost (read timeout, dropped
    connection, 502/504): it may or may not have been applied. Never retry it blindly; re-read
    the target row and decide (`fantasy.ops.rest_ledger` does this for every ledger write)."""

    code = "rest_outcome_unknown"


def unique_clashes(
    rows: list[dict[str, Any]], pkcol: str, uniq: str, existing: dict[Any, Any]
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Split rows into (keep, skipped_pks) given the target's {pk: unique_value}."""
    by_uniq = {v: k for k, v in existing.items()}
    keep, skipped = [], []
    for r in rows:
        clash_pk = by_uniq.get(r[uniq], r[pkcol]) != r[pkcol]
        clash_uniq = r[pkcol] in existing and existing[r[pkcol]] != r[uniq]
        if clash_pk or clash_uniq:
            skipped.append(r[pkcol])
        else:
            keep.append(r)
    return keep, skipped


def to_json_value(v: Any) -> Any:
    """Row value -> JSON: datetimes as ISO UTC, NaN/inf as null, JSON columns passed through."""
    return jsonable(v, finite=True)


def api_key() -> tuple[str | None, str | None]:
    """(key, env var name) or (None, None). The key itself must never be printed."""
    for name in KEY_ENVS:
        val = os.environ.get(name, "").strip()
        if val:
            return val, name
    return None, None


def auth_headers(key: str | None) -> dict[str, str]:
    if not key:
        return {}
    h = {"apikey": key}
    if key.startswith("eyJ"):  # legacy JWT (anon/service_role); sb_secret_ keys are apikey-only
        h["Authorization"] = f"Bearer {key}"
    return h


@dataclass
class RestClient:
    base_url: str = DEFAULT_SUPABASE_URL
    key: str | None = None
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    timeout: float = 30.0
    _client: httpx.Client | None = field(default=None, init=False, repr=False)

    def client(self) -> httpx.Client:
        if self._client is None:
            # trust_env (default) -> HTTPS_PROXY and SSL_CERT_FILE are respected.
            self._client = httpx.Client(
                base_url=self.base_url.rstrip("/") + "/rest/v1/",
                headers=auth_headers(self.key),
                timeout=self.timeout,
                transport=self.transport,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def _scrub(self, text: str) -> str:
        return text.replace(self.key, "***") if self.key else text

    def request(self, method: str, path: str, *, idempotent: bool | None = None,
                **kw: Any) -> httpx.Response:
        """Send with bounded retries. Safe methods (and writes the caller marks `idempotent`) are
        retried on any transport error and on 5xx. Other writes (POST/PATCH/DELETE) are retried
        only on connection-establishment errors -- the request never left -- because a write whose
        response was lost may have committed; that raises RestOutcomeUnknownError instead."""
        retry_all = method.upper() in SAFE_METHODS if idempotent is None else idempotent
        last: Exception | None = None
        for attempt in range(RETRIES + 1):
            if attempt:
                self.sleep(0.5 * 2 ** (attempt - 1))
            try:
                resp = self.client().request(method, path, **kw)
            except httpx.ProxyError as exc:  # policy denial: do not retry
                host = httpx.URL(self.base_url).host
                raise RestUnreachableError(
                    self._scrub(BLOCKED_HELP.format(host=host, err=exc)), host=host
                ) from exc
            except CONNECT_ERRORS as exc:
                last = exc
                continue
            except httpx.TransportError as exc:
                if not retry_all:
                    raise RestOutcomeUnknownError(self._scrub(
                        f"{method} {path}: response lost ({type(exc).__name__}: {exc}); the "
                        "write may or may not have been applied -- re-read before retrying"
                    ), method=method, table=path) from exc
                last = exc
                continue
            if resp.status_code >= 500:
                if not retry_all:
                    if resp.status_code in AMBIGUOUS_STATUSES:
                        raise RestOutcomeUnknownError(self._scrub(
                            f"{method} {path}: HTTP {resp.status_code} from the gateway; the write "
                            "may or may not have been applied -- re-read before retrying"
                        ), method=method, table=path, status=resp.status_code)
                    return resp
                if attempt < RETRIES:
                    continue
            return resp
        raise RestUnreachableError(
            self._scrub(f"network error talking to Supabase: {type(last).__name__}: {last}")
        )

    def check(self, resp: httpx.Response, what: str) -> None:
        if resp.status_code in (401, 403):
            raise RestAuthError(AUTH_HELP.format(status=resp.status_code), status=resp.status_code)
        if resp.status_code >= 400:
            raise RestError(
                self._scrub(f"{what}: HTTP {resp.status_code}: {resp.text[:500]}"),
                status=resp.status_code,
            )

    def select_all(self, table: str, cols: list[str], page: int = 1000) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = {"select": ",".join(cols), "order": cols[0], "limit": page, "offset": offset}
            resp = self.request("GET", table, params=params)
            self.check(resp, f"read {table}")
            chunk = resp.json()
            out.extend(chunk)
            if len(chunk) < page:
                return out
            offset += page

    def upsert(self, table: str, rows: list[dict[str, Any]], pk: list[str]) -> None:
        resp = self.request(
            "POST",
            table,
            idempotent=True,  # merge-duplicates: re-sending the same rows is a no-op
            params={"on_conflict": ",".join(pk)},
            headers={
                "Prefer": "resolution=merge-duplicates,return=minimal",
                "Content-Type": "application/json",
            },
            content=json.dumps(rows, allow_nan=False).encode(),
        )
        self.check(resp, f"upsert {table}")

    def insert_missing(self, table: str, rows: list[dict[str, Any]], pk: list[str]) -> int | None:
        """INSERT ... ON CONFLICT (pk) DO NOTHING. Returns how many rows were new (PostgREST
        returns only inserted rows with return=representation), or None if the server sent no body."""
        resp = self.request(
            "POST",
            table,
            idempotent=True,  # ignore-duplicates: re-sending inserts nothing new (count may be low)
            params={"on_conflict": ",".join(pk), "select": ",".join(pk)},
            headers={
                "Prefer": "resolution=ignore-duplicates,return=representation",
                "Content-Type": "application/json",
            },
            content=json.dumps(rows, allow_nan=False).encode(),
        )
        self.check(resp, f"insert {table}")
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            return None
        return len(body) if isinstance(body, list) else None


def resolve_tables(names: Iterable[str] | None) -> list[Table]:
    """FK-safe order (metadata.sorted_tables), minus PUBLISH_SKIP, optionally filtered."""
    wanted: set[str] | None = None
    if names:
        wanted = {n.strip() for s in names for n in s.split(",") if n.strip()}
        unknown = wanted - set(metadata.tables)
        if unknown:
            raise ValueError(f"unknown tables: {sorted(unknown)}; valid: {sorted(metadata.tables)}")
        skipped = wanted & PUBLISH_SKIP
        if skipped:
            raise ValueError(f"tables never published (ephemeral): {sorted(skipped)}")
    return [
        t
        for t in metadata.sorted_tables
        if t.name not in PUBLISH_SKIP and (wanted is None or t.name in wanted)
    ]


def read_rows(source_url: str, tables: list[Table]) -> dict[str, list[dict[str, Any]]]:
    eng = create_engine(source_url, future=True)
    try:
        with eng.connect() as conn:
            return {t.name: [row_dict(r) for r in conn.execute(select(t))] for t in tables}
    finally:
        eng.dispose()


def publish_rest(
    source_url: str,
    *,
    tables: Iterable[str] | None = None,
    dry_run: bool = False,
    client: RestClient | None = None,
    batch: int = BATCH,
) -> dict[str, Any]:
    """Publish local rows to Supabase via PostgREST: upsert `merge` tables, insert only missing rows
    of `insert` tables (never overwriting online state). Returns counts, skips, policy, errors."""
    tbls = resolve_tables(tables)
    data = read_rows(source_url, tbls)
    key, key_env = api_key()
    cl = client or RestClient(base_url=os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL), key=key)
    res: dict[str, Any] = {
        "target": cl.base_url,
        "auth": key_env or "none sent (relying on injected network secret)",
        "dry_run": dry_run,
        "order": [t.name for t in tbls],
        "rows": {},
        "batches": {},
        "skipped": {},
        "errors": {},
        "policy": {t.name: publish_policy(t.name) for t in tbls},
        "inserted": {},  # insert-only tables: rows that were new online (None = not reported)
    }
    try:
        for t in tbls:
            pk = [c.name for c in t.primary_key.columns]
            rows = data[t.name]
            uniq = PUBLISH_UNIQUE.get(t.name)
            if uniq and rows and not dry_run:
                (pkcol,) = pk
                existing = {r[pkcol]: r[uniq] for r in cl.select_all(t.name, [pkcol, uniq])}
                rows, skipped = unique_clashes(rows, pkcol, uniq, existing)
                if skipped:
                    res["skipped"][t.name] = skipped
            payload = [{k: to_json_value(v) for k, v in r.items()} for r in rows]
            nb = math.ceil(len(payload) / batch)
            res["rows"][t.name] = len(payload)
            res["batches"][t.name] = nb
            if dry_run:
                continue
            merge = publish_policy(t.name) == "merge"
            new_rows: int | None = 0
            try:
                for i in range(0, len(payload), batch):
                    if merge:
                        cl.upsert(t.name, payload[i : i + batch], pk)
                        continue
                    n = cl.insert_missing(t.name, payload[i : i + batch], pk)
                    new_rows = None if n is None or new_rows is None else new_rows + n
                if not merge:
                    res["inserted"][t.name] = new_rows
            except (RestAuthError, RestUnreachableError):
                raise
            except RestError as exc:  # e.g. 409 unique violation: report, carry on
                res["errors"][t.name] = exc.message
                res["rows"][t.name] = i  # rows upserted before the failing batch
    finally:
        if client is None:
            cl.close()
    return res


def ping(client: RestClient | None = None) -> dict[str, Any]:
    """GET rest/v1/tasks?select=task_id&limit=1 -> reachability and auth status (never raises)."""
    key, key_env = api_key()
    cl = client or RestClient(base_url=os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL), key=key)
    out: dict[str, Any] = {
        "target": cl.base_url,
        "auth": key_env or "none sent (relying on injected network secret)",
    }
    try:
        resp = cl.request("GET", "tasks", params={"select": "task_id", "limit": 1})
    except RestUnreachableError as exc:
        return {**out, "status": None, "reachable": False, "auth_ok": False,
                "message": exc.message}
    finally:
        if client is None:
            cl.close()
    out |= {
        "status": resp.status_code,
        "reachable": True,
        "auth_ok": resp.status_code == 200,
    }
    if resp.status_code in (401, 403):
        out["message"] = AUTH_HELP.format(status=resp.status_code)
    elif resp.status_code != 200:
        out["message"] = cl._scrub(resp.text[:300])
    return out
