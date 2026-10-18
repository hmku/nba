"""Test helper: point the ops layer at a fresh database.

Default: a temporary SQLite file. Set FANTASY_TEST_PG_URL (a *throwaway* Postgres with the
schema applied) to run the same tests against Postgres; all tables are truncated first.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import text

from fantasy.db.schema import metadata


def use_test_db(tmp_path: Path, monkeypatch) -> str:
    pg = os.environ.get("FANTASY_TEST_PG_URL")
    if not pg:
        url = f"sqlite:///{tmp_path / 'ops.db'}"
        monkeypatch.setenv("FANTASY_DB_URL", url)
        return url
    monkeypatch.setenv("FANTASY_DB_URL", pg)
    from fantasy.ops.common import engine

    names = ", ".join(t.name for t in metadata.sorted_tables)
    with engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {names} CASCADE"))
    return pg
