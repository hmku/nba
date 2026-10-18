"""Schema drift + SQL-only publish path (tech-debt F7/F15).

* `fantasy.db.ddl.render()` is snapshotted in tests/fixtures/schema_ddl.sql: a schema.py change fails here
  until the snapshot is regenerated (`python -m fantasy.db.ddl > tests/fixtures/schema_ddl.sql`) AND a
  new supabase/migrations/*.sql is added (checked: every table/column appears in some migration).
* `fantasy.db.sqldump` emits upserts (stale rows get updated), skips `locks`, and round-trips through SQLite.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import create_engine, insert, select, text

from fantasy.db import sqldump
from fantasy.db.ddl import render
from fantasy.db.schema import metadata, players

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
