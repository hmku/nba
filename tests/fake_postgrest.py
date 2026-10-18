"""In-process fake of the PostgREST subset we use, backed by a real SQLite DB with our schema.

Plug into `httpx.MockTransport(FakePostgrest(url))`. Supports GET/POST/PATCH/DELETE on
/rest/v1/<table> with filters `col=op.value` (eq, neq, lt, lte, gt, gte, is.null, in.(...)),
`or=(col.op.value,...)`, `order`, `limit`, `offset`, `select`, `on_conflict` +
`Prefer: resolution=merge-duplicates|ignore-duplicates` and `return=representation|minimal`.
Unique/PK violations answer 409 like PostgREST. Constraints therefore behave like the real DB.

`before` hooks let a test change the "remote" DB right before a matching request, to simulate a
concurrent writer (the dashboard, another session) between our read and our conditional write.
No network: nothing here opens a socket.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    and_,
    create_engine,
    delete,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from fantasy.db.schema import metadata

_SPLIT = re.compile(r',(?=(?:[^"]*"[^"]*")*[^"]*$)')  # commas outside double quotes


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def _naive_utc(d: datetime) -> datetime:
    return (d if d.tzinfo is None else d.astimezone(UTC)).replace(tzinfo=None)


class FakePostgrest:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url, future=True)
        metadata.create_all(self.engine)
        self.requests: list[httpx.Request] = []
        self.before: list[tuple[Callable[[httpx.Request], bool], Callable[[], None]]] = []

    # -- helpers for tests
    def on(self, method: str, table: str, action: Callable[[], None]) -> None:
        """Run `action` once, right before the next `method` request on `table`."""
        self.before.append((lambda r: r.method == method and r.url.path.endswith("/" + table),
                            action))

    def rows(self, table: str) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(select(metadata.tables[table]))]

    def methods(self, table: str | None = None) -> list[str]:
        return [r.method for r in self.requests
                if table is None or r.url.path.endswith("/" + table)]

    # -- value conversion
    @staticmethod
    def _conv(col, v: Any) -> Any:
        if v is None:
            return None
        t = col.type
        if isinstance(t, DateTime):
            return _naive_utc(datetime.fromisoformat(str(v).replace("Z", "+00:00")))
        if isinstance(t, Boolean):
            return v if isinstance(v, bool) else str(v).lower() == "true"
        if isinstance(t, Integer) and not isinstance(v, bool):
            return int(v)
        if isinstance(t, Float):
            return float(v)
        return v

    @staticmethod
    def _out(row: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in row.items():
            if isinstance(v, datetime):
                v = (v.replace(tzinfo=UTC) if v.tzinfo is None else v).isoformat()
            out[k] = v
        return out

    def _cond(self, table, col_name: str, expr: str):
        col = table.c[col_name]
        op, _, val = expr.partition(".")
        if op == "is":
            return col.is_(None) if val == "null" else col.is_(val == "true")
        if op == "in":
            items = [_unquote(x) for x in _SPLIT.split(val.strip("()")) if x != ""]
            return col.in_([self._conv(col, x) for x in items])
        v = self._conv(col, _unquote(val))
        return {"eq": col == v, "neq": col != v, "lt": col < v, "lte": col <= v,
                "gt": col > v, "gte": col >= v}[op]

    def _where(self, table, params: list[tuple[str, str]]):
        conds = []
        for k, v in params:
            if k in ("select", "order", "limit", "offset", "on_conflict"):
                continue
            if k == "or":
                parts = []
                for item in _SPLIT.split(v.strip()[1:-1]):
                    c, _, e = item.partition(".")
                    parts.append(self._cond(table, c, e))
                conds.append(or_(*parts))
            else:
                conds.append(self._cond(table, k, v))
        return and_(*conds) if conds else None

    # -- the transport handler
    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        for i, (match, action) in enumerate(self.before):
            if match(req):
                self.before.pop(i)
                action()
                break
        name = req.url.path.rsplit("/", 1)[-1]
        table = metadata.tables.get(name)
        if table is None:
            return httpx.Response(404, json={"message": f"relation {name} does not exist"})
        params = list(req.url.params.multi_items())
        prefer = req.headers.get("prefer", "")
        ret = "return=representation" in prefer
        where = self._where(table, params)
        pk = [c for c in table.primary_key.columns]
        try:
            with self.engine.begin() as conn:
                if req.method == "GET":
                    q = select(table)
                    if where is not None:
                        q = q.where(where)
                    p = dict(params)
                    for part in (p.get("order") or "").split(","):
                        if part:
                            c, _, d = part.partition(".")
                            q = q.order_by(table.c[c].desc() if d.startswith("desc")
                                           else table.c[c].asc())
                    if "limit" in p:
                        q = q.limit(int(p["limit"]))
                    if "offset" in p:
                        q = q.offset(int(p["offset"]))
                    rows = [dict(r._mapping) for r in conn.execute(q)]
                    return httpx.Response(200, json=[self._select(r, p) for r in rows])
                if req.method == "POST":
                    body = json.loads(req.content)
                    rows = body if isinstance(body, list) else [body]
                    rows = [{k: self._conv(table.c[k], v) for k, v in r.items()} for r in rows]
                    p = dict(params)
                    inserted = []
                    for r in rows:
                        if "on_conflict" in p and "resolution=merge-duplicates" in prefer:
                            stmt = sqlite_insert(table).values(r)
                            stmt = stmt.on_conflict_do_update(
                                index_elements=pk,
                                set_={k: stmt.excluded[k] for k in r if k not in {c.name for c in pk}},
                            )
                            conn.execute(stmt)
                            inserted.append(r)
                        elif "on_conflict" in p and "resolution=ignore-duplicates" in prefer:
                            stmt = sqlite_insert(table).values(r).on_conflict_do_nothing(
                                index_elements=pk)
                            if conn.execute(stmt).rowcount == 1:
                                inserted.append(r)
                        else:
                            conn.execute(insert(table).values(r))
                            inserted.append(r)
                    if ret:
                        return httpx.Response(201, json=[self._select(r, p) for r in inserted])
                    return httpx.Response(201)
                if req.method in ("PATCH", "DELETE"):
                    if where is None:
                        return httpx.Response(400, json={"message": "filter required"})
                    before = [dict(r._mapping) for r in conn.execute(select(table).where(where))]
                    keys = [tuple(r[c.name] for c in pk) for r in before]
                    if req.method == "DELETE":
                        conn.execute(delete(table).where(where))
                        after = before
                    else:
                        vals = {k: self._conv(table.c[k], v)
                                for k, v in json.loads(req.content).items()}
                        conn.execute(update(table).where(where).values(**vals))
                        after = []
                        for key in keys:
                            cond = and_(*[c == v for c, v in zip(pk, key, strict=True)])
                            after += [dict(r._mapping)
                                      for r in conn.execute(select(table).where(cond))]
                    if not ret:
                        return httpx.Response(204)
                    return httpx.Response(200, json=[self._out(r) for r in after])
        except IntegrityError as exc:
            return httpx.Response(409, json={"code": "23505", "message": str(exc.orig)[:200]})
        return httpx.Response(405)

    def _select(self, row: dict[str, Any], p: dict[str, str]) -> dict[str, Any]:
        cols = p.get("select", "*")
        out = self._out(row)
        if cols == "*":
            return out
        return {c: out[c] for c in cols.split(",")}
