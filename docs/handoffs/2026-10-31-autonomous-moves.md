# Handoff 2026-10-31 - autonomous adds/drops/trades (main session, local)

## What was accomplished
- User decision: no approvals for any roster action; act, then report in the dashboard Inbox.
- `src/fantasy/ops/policy.py`: `AUTONOMOUS = {add, drop, add_drop, trade}` allowed in live runs while `AUTONOMY_ON = True`
  (set False to restore approval gating). Unknown kinds still need approval; `dry_run` still never executes.
- Tests: new `approval_gated` fixture (tests/conftest.py) keeps the old approval path covered; new autonomy tests.
- Context updated: CLAUDE.md safety rules, skills daily-routine (§4 is now EXECUTE + REPORT), in-season-ops, league-loop,
  agent-run, yahoo-browser; inseason CLI notes; intel thread/advisor notes; STATUS.md; memory (approve-counts → autonomous-moves).
- Scheduled tasks fantasy-tipoff / -late / -weekend prompts updated. **fantasy-daily prompt update was blocked by the
  permission classifier**; its old wording defers to the skill, which no longer requires a yes, so behaviour is consistent.

## Decisions & rationale
- Guardrails kept: live run + locks + txn ledger + independent Yahoo verification; only tool-recommended moves (`helps` /
  beats 2 SE); no league chat, trade notes or settings changes. Reporting = advice card status `done` + `fantasy intel post`.
- Push notifications only fire on `approvals` INSERT, so autonomous moves do not ping the phone (Inbox only).

## Outstanding
- Pending ask from 93b7050 (Shead into open spot, deadline Sun 21:00 ET): next scheduled run executes it under the new policy if still `helps`.
- Optional: push notification for executed moves (webhook on advice `done`).
