"""Schema drift + SQL-only publish path (tech-debt F7/F15).

* `fantasy.db.ddl.render()` is snapshotted in tests/fixtures/schema_ddl.sql: a schema.py change fails here
  until the snapshot is regenerated (`python -m fantasy.db.ddl > tests/fixtures/schema_ddl.sql`) AND a
  new supabase/migrations/*.sql is added (checked: every table/column appears in some migration).
* `fantasy.db.sqldump` and the SQL `fantasy publish` follow `publish_policy`: upserts for merge tables (stale
  rows get updated), ON CONFLICT DO NOTHING for ledger/state tables (online decisions win), skip `locks`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, insert, select, text

from fantasy.db import sqldump
from fantasy.db.ddl import render
from fantasy.db.schema import approvals, locks, metadata, players
from fantasy.ops.status import publish

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))


def test_ddl_matches_snapshot():
    snap = (ROOT / "tests" / "fixtures" / "schema_ddl.sql").read_text()
    assert render().rstrip() == snap.rstrip(), ("schema.py changed: add a supabase migration and regenerate the snapshot with "
                              "`python -m fantasy.db.ddl > tests/fixtures/schema_ddl.sql`")


def test_every_table_and_column_is_in_a_migration():
    sql = "\n".join(p.read_text() for p in MIGRATIONS).lower()
    for t in metadata.sorted_tables:
        assert re.search(rf"create table (if not exists )?{t.name}\b", sql), f"no migration creates {t.name}"
        for c in t.columns:
            assert re.search(rf"\b{c.name}\b", sql), f"{t.name}.{c.name} missing from supabase/migrations"


def test_every_table_has_a_primary_key():  # sqldump / publish upsert by PK
    assert all(len(t.primary_key.columns) for t in metadata.sorted_tables)


def _sqlite(path: Path):
    eng = create_engine(f"sqlite:///{path}")
    metadata.create_all(eng)
    return eng


def _apply(eng, stmts: list[str]) -> None:
    with eng.begin() as conn:
        for s in stmts:
            conn.execute(text(s.replace("::jsonb", "")))  # Postgres cast; SQLite stores the JSON text


def test_sqldump_upserts_and_skips_locks(tmp_path):
    src = _sqlite(tmp_path / "src.db")
    row = {"player_id": "p1", "name": "Old Name", "team": "AAA", "positions": ["PG"], "status": "healthy"}
    with src.begin() as conn:
        conn.execute(insert(players).values(**row))
    stmts = sqldump.dump(str(src.url), ["players", "locks"])
    assert len(stmts) == 1 and "on conflict (player_id) do update set" in stmts[0] and "locks" not in stmts[0]
    tgt = _sqlite(tmp_path / "tgt.db")
    _apply(tgt, stmts)
    with src.begin() as conn:
        conn.execute(players.update().values(name="New Name", team="BBB"))
    _apply(tgt, sqldump.dump(str(src.url), ["players"]))  # re-publish: the stale row is updated
    with tgt.connect() as conn:
        got = conn.execute(select(players.c.name, players.c.team)).all()
    assert got == [("New Name", "BBB")]


def test_literal_quoting():
    assert sqldump.literal("O'Neal") == "'O''Neal'"
    assert sqldump.literal(float("nan")) == "NULL" and sqldump.literal(None) == "NULL"
    assert sqldump.literal(True) == "TRUE" and sqldump.literal({"a": "b'c"}) == "'{\"a\": \"b''c\"}'::jsonb"


T0 = datetime(2026, 10, 19, 12, tzinfo=UTC)


def _approval(status: str, note: str | None = None) -> dict:
    return {"approval_id": "apr-1", "run_id": None, "action_type": "add_drop", "summary": "add X",
            "payload": {"a": 1}, "status": status, "requested_at": T0, "note": note,
            "decided_at": None if status == "pending" else T0}


def _stale_local_and_decided_target(tmp_path):
    src, tgt = _sqlite(tmp_path / "src.db"), _sqlite(tmp_path / "tgt.db")
    with src.begin() as c:
        c.execute(insert(approvals).values(**_approval("pending")))
        c.execute(insert(approvals).values(**{**_approval("pending"), "approval_id": "apr-2"}))
        c.execute(insert(players).values(player_id="p1", name="New", team="B", positions=["PG"]))
        c.execute(insert(locks).values(resource="yahoo:roster", holder_run_id="r", acquired_at=T0,
                                       expires_at=T0))
    with tgt.begin() as c:
        c.execute(insert(approvals).values(**_approval("rejected", "no")))  # the user decided online
        c.execute(insert(players).values(player_id="p1", name="Old", team="A", positions=["PG"]))
    return src, tgt


def _target_state(tgt):
    with tgt.connect() as c:
        return (sorted(c.execute(select(approvals.c.approval_id, approvals.c.status)).all()),
                c.execute(select(players.c.name)).scalar_one(),
                c.execute(select(locks.c.resource)).all())


def test_sql_publish_never_overwrites_ledger_rows(tmp_path):
    src, tgt = _stale_local_and_decided_target(tmp_path)
    res = publish(str(src.url), str(tgt.url))
    assert res["policy"]["approvals"] == "insert" and res["policy"]["players"] == "merge"
    assert "locks" not in res["policy"] and res["inserted"]["approvals"] == 1  # only apr-2 is new
    assert _target_state(tgt) == ([("apr-1", "rejected"), ("apr-2", "pending")], "New", [])


def test_sqldump_never_overwrites_ledger_rows(tmp_path):
    src, tgt = _stale_local_and_decided_target(tmp_path)
    stmts = sqldump.dump(str(src.url), ["approvals", "players", "locks"])
    assert any("insert into approvals" in s and "on conflict (approval_id) do nothing" in s for s in stmts)
    _apply(tgt, stmts)
    assert _target_state(tgt) == ([("apr-1", "rejected"), ("apr-2", "pending")], "New", [])
