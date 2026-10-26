"""Web Push tables (dashboard notifications): server-owned, private, never published from a local copy."""

from __future__ import annotations

import re
from pathlib import Path

from fantasy.db.rest import publish_policy, resolve_tables
from fantasy.db.schema import metadata

ROOT = Path(__file__).resolve().parents[1]
PUSH_TABLES = ("push_subscriptions", "push_sent")


def test_push_tables_are_never_published():
    names = {t.name for t in resolve_tables(None)}
    for t in PUSH_TABLES:
        assert t in metadata.tables
        assert publish_policy(t) == "skip" and t not in names


def test_push_migration_enables_rls_without_anon_policies():
    sql = "\n".join(p.read_text() for p in sorted((ROOT / "supabase" / "migrations").glob("*.sql"))).lower()
    for t in PUSH_TABLES:
        assert f"alter table {t} enable row level security" in sql
        assert not re.search(rf"create policy[^;]*\bon {t}\b", sql), f"{t} must have no RLS policy (service role only)"
        assert f"revoke all on {t} from anon, authenticated" in sql
