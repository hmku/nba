"""NBA game days are US/Eastern calendar dates: the one ET conversion shared by league sync and the lineup planner."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def et_date(ts: object) -> date | None:
    """US/Eastern calendar date of an ISO timestamp (`Z` suffix allowed; a naive timestamp is taken as UTC).
    None if empty or not ISO. A bare ISO date counts as midnight UTC, i.e. the previous ET day."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET).date()
