"""Render rows of a (local) database as Postgres INSERT statements, per table policy
(`fantasy.db.rest.publish_policy`, the same as `fantasy publish` and `publish --rest`):
  merge  -> INSERT ... ON CONFLICT (<pk>) DO UPDATE SET <non-pk cols> = excluded.<col>
  insert -> INSERT ... ON CONFLICT (<pk>) DO NOTHING   (ledger/state tables: never overwritten)
  skip   -> nothing (`locks`)

Used to publish to Supabase when only SQL access (e.g. the Supabase MCP) is available:
  python -m fantasy.db.sqldump --source sqlite:///var/fantasy.db --tables players model_runs ... --out dir/

Same semantics as the SQL `fantasy publish`, except that unique-key clashes with *different* target
rows cannot be pre-checked offline: such a statement fails in the target instead of being skipped.
A stale local ledger/state row (e.g. a 'pending' approval already 'rejected' online) is left alone;
change those rows one at a time with `fantasy ... --rest` instead.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select

from fantasy.db.rest import publish_policy
from fantasy.db.schema import metadata


def literal(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        return repr(v) if v == v else "NULL"
    if isinstance(v, datetime):
        return "'" + v.isoformat() + "'"
    if isinstance(v, dict | list):
        return "'" + json.dumps(v).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def dump(source: str, tables: list[str], chunk_chars: int = 90_000) -> list[str]:
    eng = create_engine(source)
    chunks: list[str] = []
    for name in tables:
        policy = publish_policy(name)
        if policy == "skip":
            continue
        t = metadata.tables[name]
        cols = [c.name for c in t.columns]
        pk = [c.name for c in t.primary_key.columns]
        with eng.connect() as conn:
            rows = conn.execute(select(t)).mappings().all()
        buf: list[str] = []
        size = 0
        for r in rows:
            vals = "(" + ",".join(literal(r[c]) for c in cols) + ")"
            buf.append(vals)
            size += len(vals)
            if size > chunk_chars:
                chunks.append(_stmt(name, cols, pk, buf, policy))
                buf, size = [], 0
        if buf:
            chunks.append(_stmt(name, cols, pk, buf, policy))
    return chunks


def _stmt(table: str, cols: list[str], pk: list[str], rows: list[str], policy: str = "insert") -> str:
    rest = [c for c in cols if c not in pk]
    if policy == "merge" and rest:
        action = "do update set " + ", ".join(f"{c} = excluded.{c}" for c in rest)
    else:
        action = "do nothing"
    return (f"insert into {table} ({','.join(cols)}) values\n" + ",\n".join(rows)
            + f"\non conflict ({','.join(pk)}) {action};")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--tables", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(dump(a.source, a.tables)):
        (out / f"{i:03d}.sql").write_text(c)
        print(out / f"{i:03d}.sql", len(c))
