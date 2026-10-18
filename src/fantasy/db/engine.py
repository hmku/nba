"""Database engine factory.

FANTASY_DB_URL selects the backend:
- unset            -> sqlite:///var/fantasy.db (local dev / offline)
- sqlite:///...    -> tests, mocks
- postgresql+psycopg://...  -> Supabase (production system of record; use the pooler URL)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine

from fantasy.db.schema import metadata

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE = REPO_ROOT / "var" / "fantasy.db"


def db_url() -> str:
    url = os.environ.get("FANTASY_DB_URL")
    if url:
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url
    DEFAULT_SQLITE.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE}"


def get_engine(url: str | None = None) -> Engine:
    return _engine(url or db_url())


@lru_cache(maxsize=8)
def _engine(url: str) -> Engine:
    engine = create_engine(url, future=True, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        metadata.create_all(engine)  # sqlite is self-initialising; postgres uses migrations
    return engine
