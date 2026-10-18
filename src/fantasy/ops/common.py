"""Shared helpers for the ops layer: engine access, clock, ids, errors, serialisation.

All ops modules use SQLAlchemy Core over the tables in `fantasy.db.schema` and behave the same on
SQLite (tests/offline) and Postgres (Supabase). Timestamps are produced in Python (UTC) rather
than by the database so both backends compare them identically.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine

from fantasy.db.engine import db_url, get_engine

# ------------------------------------------------------------------ exit codes (CLI contract)

EXIT_OK = 0
EXIT_ERROR = 1  # unexpected / generic failure
EXIT_USAGE = 2  # bad arguments (also Typer's own usage errors)
EXIT_CONFLICT = 3  # resource held by someone else (lock held, task claimed)
EXIT_NOT_ALLOWED = 4  # policy refusal: approval required / dry_run / approval not granted
EXIT_DUPLICATE = 5  # duplicate transaction (existing record returned)
EXIT_NOT_FOUND = 6  # unknown id
EXIT_INVALID_STATE = 7  # invalid state transition (txn, approval already decided, run not running)


class OpsError(Exception):
    """Refusal or failure with a stable machine-readable code and CLI exit code."""

    code = "error"
    exit_code = EXIT_ERROR

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message, **jsonable(self.details)}


class ConflictError(OpsError):
    code = "conflict"
    exit_code = EXIT_CONFLICT


class LockHeldError(ConflictError):
    code = "lock_held"


class NotAllowedError(OpsError):
    code = "not_allowed"
    exit_code = EXIT_NOT_ALLOWED


class DuplicateError(OpsError):
    code = "duplicate"
    exit_code = EXIT_DUPLICATE


class NotFoundError(OpsError):
    code = "not_found"
    exit_code = EXIT_NOT_FOUND


class InvalidStateError(OpsError):
    code = "invalid_state"
    exit_code = EXIT_INVALID_STATE


# ------------------------------------------------------------------ engine / clock / ids


def engine(url: str | None = None) -> Engine:
    # Key the cached engine by the resolved URL so a changed FANTASY_DB_URL (tests) takes effect.
    return get_engine(url or db_url())


def now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes (stored as UTC); normalise everything to aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ------------------------------------------------------------------ serialisation


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def row_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row._mapping)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = as_utc(v)
    return d


def age_seconds(ts: datetime | None, ref: datetime | None = None) -> float | None:
    if ts is None:
        return None
    return round(((ref or now()) - as_utc(ts)).total_seconds(), 1)
