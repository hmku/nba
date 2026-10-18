"""Which actions an agent may execute on Yahoo, and under what conditions.

Rules (most restrictive wins):
- run mode `dry_run` never executes anything.
- drop, add_drop, trade (and `add`, plus any unknown kind) require an *approved* human approval.
- lineup changes and draft picks are allowed for a `live` run without approval
  (a live draft run is the authorisation; it must still hold the relevant lock).
"""

from __future__ import annotations

APPROVAL_REQUIRED = frozenset({"drop", "add_drop", "trade", "add"})
LIVE_ALLOWED = frozenset({"lineup", "draft_pick"})
KNOWN_KINDS = APPROVAL_REQUIRED | LIVE_ALLOWED


def requires_approval(kind: str) -> bool:
    return kind not in LIVE_ALLOWED  # unknown kinds are treated conservatively


def check_action_allowed(
    kind: str, run_mode: str | None, approval_status: str | None = None
) -> tuple[bool, str]:
    """Return (allowed, reason). `approval_status` is the status of the linked approval, if any
    (pending | approved | rejected | expired | None)."""
    if run_mode != "live":
        return False, f"run mode is {run_mode or 'unknown'!r}; only live runs may execute actions"
    if kind in LIVE_ALLOWED:
        return True, f"{kind} is allowed for a live run"
    label = kind if kind in KNOWN_KINDS else f"unknown action kind {kind!r}"
    if approval_status == "approved":
        return True, f"{label} approved by a human"
    if approval_status is None:
        return False, f"{label} requires human approval; none requested"
    return False, f"{label} requires human approval; approval is {approval_status}"
