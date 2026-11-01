"""Which actions an agent may execute on Yahoo, and under what conditions.

Rules (most restrictive wins):
- run mode `dry_run` never executes anything.
- lineup changes, draft picks, adds, drops and add/drops are allowed for a `live` run without approval (it
  must still hold the relevant lock, plan the txn, verify on Yahoo, and tell the user in the dashboard Inbox
  afterwards). User decision 2026-10-31: "you dont need approvals to do any actions ... just let me know in
  the inbox but dont wait for me to give permission"; same day: "keep trades behind approval for now".
- trades always require an *approved* human approval.
- with `AUTONOMY_ON = False` adds/drops require an approved approval again (the pre-2026-10-31 rule).
- any unknown kind still requires an *approved* human approval (conservative default).
"""

from __future__ import annotations

LIVE_ALLOWED = frozenset({"lineup", "draft_pick"})
# Needed the user's approval before 2026-10-31; executed without one (then reported) while AUTONOMY_ON.
AUTONOMOUS = frozenset({"drop", "add_drop", "add"})  # trades stay gated (user, 2026-10-31)
KNOWN_KINDS = LIVE_ALLOWED | AUTONOMOUS | {"trade"}
AUTONOMY_ON = True  # set False to go back to approval-gated adds/drops


def _no_approval_needed(kind: str) -> bool:
    return kind in LIVE_ALLOWED or (AUTONOMY_ON and kind in AUTONOMOUS)


def requires_approval(kind: str) -> bool:
    return not _no_approval_needed(kind)  # unknown kinds are treated conservatively


def check_action_allowed(
    kind: str, run_mode: str | None, approval_status: str | None = None
) -> tuple[bool, str]:
    """Return (allowed, reason). `approval_status` is the status of the linked approval, if any
    (pending | approved | rejected | expired | None)."""
    if run_mode != "live":
        return False, f"run mode is {run_mode or 'unknown'!r}; only live runs may execute actions"
    if _no_approval_needed(kind):
        return True, f"{kind} is allowed for a live run"
    label = kind if kind in KNOWN_KINDS else f"unknown action kind {kind!r}"
    if approval_status == "approved":
        return True, f"{label} approved by a human"
    if approval_status is None:
        return False, f"{label} requires human approval; none requested"
    return False, f"{label} requires human approval; approval is {approval_status}"
