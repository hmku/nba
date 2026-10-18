"""Publish local SQLite state to Supabase over HTTPS (PostgREST), for sessions without Postgres.

Cloud Claude Code sessions reach the internet only through an HTTPS proxy, so the SQLAlchemy/psycopg
path (`fantasy publish --target postgresql://...`) only works on the user's laptop. This module does
the same upsert through `POST {SUPABASE_URL}/rest/v1/<table>?on_conflict=<pk>` with
`Prefer: resolution=merge-duplicates`, in FK-safe order, skipping ephemeral tables.

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
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import Table, create_engine, select

from fantasy.db.schema import metadata
from fantasy.ops.common import OpsError, as_utc, row_dict

DEFAULT_SUPABASE_URL = "https://bdzpkdkhlzjzddkptjex.supabase.co"
KEY_ENVS = ("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")
BATCH = 500
RETRIES = 2  # extra attempts on 5xx / network errors (upserts are idempotent)

# Ephemeral state that must not be copied (an offline lease means nothing online).
PUBLISH_SKIP = frozenset({"locks"})
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
    if isinstance(v, datetime):
        return as_utc(v).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: to_json_value(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [to_json_value(x) for x in v]
    return v


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

    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
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
            except httpx.TransportError as exc:
                last = exc
                continue
            if resp.status_code >= 500 and attempt < RETRIES:
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
            params={"on_conflict": ",".join(pk)},
            headers={
                "Prefer": "resolution=merge-duplicates,return=minimal",
                "Content-Type": "application/json",
            },
            content=json.dumps(rows, allow_nan=False).encode(),
        )
        self.check(resp, f"upsert {table}")


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
    """Upsert local rows into Supabase via PostgREST. Returns counts, skips and per-table errors."""
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
            try:
                for i in range(0, len(payload), batch):
                    cl.upsert(t.name, payload[i : i + batch], pk)
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
