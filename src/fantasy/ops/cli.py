"""Ops CLI: `fantasy run|lock|approval|txn|task ...`.

Every command accepts `--json`: stdout is then a single JSON object, always with an "ok" key.
Exit codes (see fantasy.ops.common): 0 ok, 1 error, 2 usage, 3 conflict (lock held / task
claimed), 4 not allowed (approval required, dry_run), 5 duplicate txn, 6 not found,
7 invalid state transition, 8 still pending (`approval show --wait` timed out).
Run ids come from `--run-id` or the FANTASY_RUN_ID environment variable.
`--rest` (run/lock/approval/txn/task, except `run reap`, `lock release --force`, `approval decide`
and `task seed`) talks to Supabase over PostgREST instead of FANTASY_DB_URL (fantasy.ops.rest_ledger).
"""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, NoReturn

import typer

from fantasy.ops import approvals, ledger, locks, seed_tasks, tasks, transactions
from fantasy.ops.common import EXIT_STILL_PENDING, EXIT_USAGE, OpsError, jsonable

RunId = Annotated[
    str | None, typer.Option("--run-id", envvar="FANTASY_RUN_ID", help="Agent run id")
]
JsonOut = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output")]
UseRest = Annotated[bool, typer.Option(
    "--rest", help="Read/write Supabase directly over HTTPS (PostgREST), one row per call, "
    "with compare-and-set state transitions (cloud sessions; no local DB involved)")]

# Storage backend for the commands below: the SQLAlchemy ops modules (default, unchanged) or their
# PostgREST mirror in fantasy.ops.rest_ledger (`--rest`). Same method names, same errors/exit codes.
_LOCAL = SimpleNamespace(
    ledger=ledger, locks=locks, approvals=approvals, transactions=transactions, tasks=tasks
)


def _backend(use_rest: bool) -> SimpleNamespace:
    if not use_rest:
        return _LOCAL
    from fantasy.ops import rest_ledger  # local import: keeps httpx off the default path

    return rest_ledger.backend()


# ------------------------------------------------------------------ helpers


def parse_json_arg(value: str | None) -> Any:
    """Accept inline JSON, `@path/to/file.json`, or `-` for stdin."""
    if value is None or value == "":
        return None
    if value == "-":
        value = sys.stdin.read()
    elif value.startswith("@"):
        value = Path(value[1:]).read_text()
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid JSON: {exc}") from exc


def emit(data: dict[str, Any], as_json: bool, text: str | None = None) -> None:
    if as_json:
        typer.echo(json.dumps(jsonable({"ok": True, **data}), sort_keys=True))
    else:
        typer.echo(text if text is not None else json.dumps(jsonable(data), indent=2))


class _UsageError(OpsError):
    code = "usage"
    exit_code = EXIT_USAGE


class _StillPendingError(OpsError):
    code = "still_pending"
    exit_code = EXIT_STILL_PENDING


def fail(err: OpsError, as_json: bool) -> NoReturn:
    """Every ops failure: `err.to_dict()` (sorted keys) on stdout with --json, else `error [<code>]: ...`."""
    if as_json:
        typer.echo(json.dumps(err.to_dict(), sort_keys=True, default=str))
    else:
        typer.echo(f"error [{err.code}]: {err.message}", err=True)
        if "existing" in err.details:
            typer.echo(json.dumps(jsonable(err.details["existing"]), indent=2))
    raise typer.Exit(err.exit_code)


def handled(fn: Callable) -> Callable:
    """Translate OpsError / ValueError into JSON + exit code."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        as_json = bool(kwargs.get("json_out"))
        try:
            return fn(*args, **kwargs)
        except OpsError as err:
            fail(err, as_json)
        except ValueError as exc:
            fail(_UsageError(str(exc)), as_json)
        except (typer.Exit, typer.Abort, KeyboardInterrupt):
            raise
        except Exception as exc:
            if not as_json:
                raise
            fail(OpsError(f"{type(exc).__name__}: {exc}"), as_json)

    return wrapper


def _need_run(run_id: str | None) -> str:
    if not run_id:
        raise ValueError("a run id is required: pass --run-id or set FANTASY_RUN_ID")
    return run_id


def _fmt_row(d: dict[str, Any], keys: list[str]) -> str:
    return "  ".join(str(jsonable(d.get(k)) if d.get(k) is not None else "-") for k in keys)


# ------------------------------------------------------------------ run


run_app = typer.Typer(no_args_is_help=True, help="Agent run ledger (start, events, decisions, end)")


@run_app.command("start")
@handled
def run_start(
    agent: Annotated[str, typer.Option(help="Agent name, e.g. draft-manager")],
    task: Annotated[str | None, typer.Option(help="What this run is doing")] = None,
    mode: Annotated[str, typer.Option(help="dry_run | live")] = "dry_run",
    session_ref: Annotated[str | None, typer.Option(help="Claude Code session URL/id")] = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Start a run. Prints the run id (export it as FANTASY_RUN_ID)."""
    b = _backend(use_rest)
    rid = b.ledger.start_run(agent, task, mode, session_ref)
    emit({"run_id": rid, "run": b.ledger.get_run(rid)}, json_out, rid)


@run_app.command("event")
@handled
def run_event(
    kind: Annotated[str, typer.Argument(help="|".join(ledger.EVENT_KINDS))],
    message: Annotated[str, typer.Argument()] = "",
    payload: Annotated[str | None, typer.Option(help="JSON, @file or -")] = None,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Log an event on a run."""
    b = _backend(use_rest)
    eid = b.ledger.log_event(_need_run(run_id), kind, message or None, parse_json_arg(payload))
    emit({"event_id": eid}, json_out, eid)


@run_app.command("decision")
@handled
def run_decision(
    kind: Annotated[str, typer.Argument(help="|".join(ledger.DECISION_KINDS))],
    choice: Annotated[str, typer.Option(help="JSON of what was chosen")],
    model: Annotated[str | None, typer.Option(help="JSON of the model recommendation")] = None,
    rationale: Annotated[str | None, typer.Option()] = None,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Record a decision; agrees_with_model is computed."""
    b = _backend(use_rest)
    res = b.ledger.record_decision(
        _need_run(run_id), kind, parse_json_arg(model), parse_json_arg(choice), rationale
    )
    emit(res, json_out, f"{res['decision_id']} agrees_with_model={res['agrees_with_model']}")


@run_app.command("heartbeat")
@handled
def run_heartbeat(
    run_id: RunId = None, use_rest: UseRest = False, json_out: JsonOut = False
) -> None:
    """Refresh the run's heartbeat (do this at least every few minutes)."""
    b = _backend(use_rest)
    run = b.ledger.heartbeat(_need_run(run_id))
    emit({"run": run}, json_out, f"{run['run_id']} heartbeat {run['heartbeat_at'].isoformat()}")


@run_app.command("end")
@handled
def run_end(
    status: Annotated[str, typer.Option(help="succeeded | failed | abandoned")] = "succeeded",
    summary: Annotated[str | None, typer.Option()] = None,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """End a run and release every lock it still holds (listed as `released_locks`)."""
    b = _backend(use_rest)
    run = b.ledger.end_run(_need_run(run_id), status, summary)
    released = run.pop("released_locks", [])
    emit({"run": run, "released_locks": released}, json_out,
         f"{run['run_id']} {run['status']}" + (f"; released {', '.join(released)}" if released else ""))


@run_app.command("list")
@handled
def run_list(
    status: Annotated[str | None, typer.Option()] = None,
    agent: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option()] = 20,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """List recent runs."""
    b = _backend(use_rest)
    runs = b.ledger.list_runs(status, agent, limit)
    keys = ["run_id", "agent", "status", "mode", "started_at", "heartbeat_at"]
    emit({"runs": runs}, json_out, "\n".join(_fmt_row(r, keys) for r in runs) or "(no runs)")


@run_app.command("show")
@handled
def run_show(
    run_id_arg: Annotated[str | None, typer.Argument(metavar="RUN_ID")] = None,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Show a run with its events and decisions."""
    b = _backend(use_rest)
    rid = _need_run(run_id_arg or run_id)
    data = {
        "run": b.ledger.get_run(rid),
        "events": b.ledger.list_events(rid),
        "decisions": b.ledger.list_decisions(rid),
    }
    emit(data, json_out)


@run_app.command("reap")
@handled
def run_reap(
    stale_minutes: Annotated[float, typer.Option()] = ledger.DEFAULT_STALE_MINUTES,
    json_out: JsonOut = False,
) -> None:
    """Mark runs with no heartbeat for > N minutes as abandoned."""
    reaped = ledger.reap_stale_runs(stale_minutes)
    emit({"reaped": reaped}, json_out, "\n".join(reaped) or "(none stale)")


# ------------------------------------------------------------------ lock


lock_app = typer.Typer(no_args_is_help=True, help="Lease locks: yahoo:roster, yahoo:browser, draft:<id>")


@lock_app.command("acquire")
@handled
def lock_acquire(
    resource: Annotated[str, typer.Argument()],
    ttl: Annotated[float, typer.Option(help="Lease seconds")] = locks.DEFAULT_TTL,
    purpose: Annotated[str | None, typer.Option()] = None,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Acquire (or renew, if already ours) a lock. Exit 3 if held by another run."""
    b = _backend(use_rest)
    info = b.locks.acquire(resource, _need_run(run_id), ttl, purpose)
    emit({"lock": info}, json_out, f"acquired {resource} until {info['expires_at'].isoformat()}")


@lock_app.command("renew")
@handled
def lock_renew(
    resource: Annotated[str, typer.Argument()],
    ttl: Annotated[float, typer.Option()] = locks.DEFAULT_TTL,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Extend a lock we hold."""
    b = _backend(use_rest)
    info = b.locks.renew(resource, _need_run(run_id), ttl)
    emit({"lock": info}, json_out, f"renewed {resource} until {info['expires_at'].isoformat()}")


@lock_app.command("release")
@handled
def lock_release(
    resource: Annotated[str, typer.Argument()],
    force: Annotated[bool, typer.Option(help="Operator override: ignore holder")] = False,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Release a lock (holder only, unless --force). Exit 3 if held by another run."""
    if force and use_rest:
        raise ValueError("--force is a human override and is not available with --rest")
    b = _backend(use_rest)
    released = (
        b.locks.force_release(resource) if force else b.locks.release(resource, _need_run(run_id))
    )
    emit({"resource": resource, "released": released}, json_out,
         f"{'released' if released else 'was not locked'}: {resource}")


@lock_app.command("show")
@handled
def lock_show(
    resource: Annotated[str | None, typer.Argument()] = None,
    all_: Annotated[bool, typer.Option("--all", help="Include expired leases")] = False,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Show one lock or all held locks."""
    b = _backend(use_rest)
    if resource:
        lk = b.locks.get_lock(resource)
        emit({"lock": lk}, json_out, json.dumps(jsonable(lk), indent=2) if lk else "(unlocked)")
        return
    lks = b.locks.list_locks(include_expired=all_)
    keys = ["resource", "holder_run_id", "expires_at", "expired", "purpose"]
    emit({"locks": lks}, json_out, "\n".join(_fmt_row(r, keys) for r in lks) or "(no locks)")


# ------------------------------------------------------------------ approval


approval_app = typer.Typer(no_args_is_help=True, help="Human approvals for risky actions")


@approval_app.command("request")
@handled
def approval_request(
    action_type: Annotated[str, typer.Argument(help="drop | add_drop | trade | other")],
    summary: Annotated[str, typer.Argument()],
    payload: Annotated[str | None, typer.Option(help="JSON, @file or -")] = None,
    expires_in: Annotated[float, typer.Option(help="Seconds")] = approvals.DEFAULT_EXPIRES_IN,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Request a human approval. Prints the approval id."""
    b = _backend(use_rest)
    aid = b.approvals.request_approval(
        run_id, action_type, summary, parse_json_arg(payload), expires_in
    )
    emit({"approval_id": aid, "approval": b.approvals.get_approval(aid)}, json_out, aid)


@approval_app.command("list")
@handled
def approval_list(
    status: Annotated[str, typer.Option(help="pending|approved|rejected|expired|all")] = "pending",
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """List approvals (default: pending)."""
    b = _backend(use_rest)
    rows = b.approvals.list_approvals(None if status == "all" else status)
    keys = ["approval_id", "status", "action_type", "summary", "expires_at"]
    emit({"approvals": rows}, json_out, "\n".join(_fmt_row(r, keys) for r in rows) or "(none)")


@approval_app.command("show")
@handled
def approval_show(
    approval_id: Annotated[str, typer.Argument()],
    wait: Annotated[float, typer.Option(help="Poll up to N seconds while pending")] = 0,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Show an approval (optionally waiting for a decision).

    With --wait N: exit 8 (`still_pending`) if the approval is still pending after N seconds."""
    b = _backend(use_rest)
    if wait > 0:
        b.approvals.wait(approval_id, timeout=wait, poll=min(5.0, wait))
    a = b.approvals.get_approval(approval_id)
    if wait > 0 and a["status"] == "pending":
        if not json_out:
            typer.echo(f"still pending after {wait:g}s: {approval_id}", err=True)
            raise typer.Exit(EXIT_STILL_PENDING)
        fail(_StillPendingError(f"still pending after {wait:g}s", approval=a), json_out)
    emit({"approval": a}, json_out)


@approval_app.command("decide")
@handled
def approval_decide(
    approval_id: Annotated[str, typer.Argument()],
    approve: Annotated[bool, typer.Option("--approve/--reject")],
    by: Annotated[str, typer.Option("--by", help="Who decided (human name)")],
    note: Annotated[str | None, typer.Option()] = None,
    json_out: JsonOut = False,
) -> None:
    """Approve or reject. Exit 7 if not pending (already decided / expired)."""
    a = approvals.decide(approval_id, approve, by, note)
    emit({"approval": a}, json_out, f"{approval_id} {a['status']}")


# ------------------------------------------------------------------ txn


txn_app = typer.Typer(no_args_is_help=True, help="Idempotent Yahoo transaction ledger")


@txn_app.command("plan")
@handled
def txn_plan(
    kind: Annotated[str, typer.Argument(help="|".join(transactions.TXN_KINDS))],
    payload: Annotated[str, typer.Option(help="JSON, @file or -")],
    scope: Annotated[str | None, typer.Option(help="Idempotency scope (default: UTC date, "
                                              "or draft_id#pick)")] = None,
    attempt: Annotated[int, typer.Option(help="Bump to deliberately retry a failed txn")] = 1,
    summary: Annotated[str | None, typer.Option(help="Text shown on the approval")] = None,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Plan a transaction. Exit 5 (with the existing record) if already planned."""
    b = _backend(use_rest)
    txn = b.transactions.plan(kind, parse_json_arg(payload), run_id, scope, attempt, summary)
    emit({"txn": txn}, json_out, f"{txn['txn_id']} {txn['status']}"
         + (f" approval={txn['approval_id']}" if txn["approval_id"] else ""))


@txn_app.command("mark")
@handled
def txn_mark(
    txn_id: Annotated[str, typer.Argument()],
    status: Annotated[str, typer.Argument(help="submitted|verified|failed|cancelled")],
    evidence: Annotated[str | None, typer.Option(help="JSON verification evidence")] = None,
    error: Annotated[str | None, typer.Option(help="Failure / cancel reason")] = None,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Advance a transaction. `submitted` needs a live run, policy clearance and the lock
    (exit 4 otherwise); invalid transitions exit 7."""
    b = _backend(use_rest)
    txn = b.transactions.mark(txn_id, status, run_id, parse_json_arg(evidence), error)
    emit({"txn": txn}, json_out, f"{txn['txn_id']} {txn['status']}")


@txn_app.command("check")
@handled
def txn_check(
    txn_id: Annotated[str, typer.Argument()],
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Would `mark submitted` succeed right now? (no state change)"""
    b = _backend(use_rest)
    txn = b.transactions.check_submit(txn_id, _need_run(run_id))
    emit({"txn": txn, "allowed": True}, json_out, "allowed")


@txn_app.command("list")
@handled
def txn_list(
    status: Annotated[str | None, typer.Option()] = None,
    kind: Annotated[str | None, typer.Option()] = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """List transactions."""
    b = _backend(use_rest)
    rows = b.transactions.list_txns(status, kind)
    keys = ["txn_id", "kind", "status", "approval_id", "updated_at"]
    emit({"txns": rows}, json_out, "\n".join(_fmt_row(r, keys) for r in rows) or "(none)")


@txn_app.command("show")
@handled
def txn_show(
    txn_id: Annotated[str, typer.Argument()], use_rest: UseRest = False, json_out: JsonOut = False
) -> None:
    """Show one transaction."""
    b = _backend(use_rest)
    emit({"txn": b.transactions.get_txn(txn_id)}, json_out)


@txn_app.command("reconcile")
@handled
def txn_reconcile(use_rest: UseRest = False, json_out: JsonOut = False) -> None:
    """List transactions stuck in `submitted`. Check Yahoo, then mark verified or failed.
    NEVER re-submit them blindly."""
    b = _backend(use_rest)
    rows = b.transactions.needs_reconciliation()
    keys = ["txn_id", "kind", "updated_at", "payload"]
    emit({"txns": rows}, json_out,
         "\n".join(_fmt_row(r, keys) for r in rows) or "(nothing to reconcile)")


# ------------------------------------------------------------------ task


task_app = typer.Typer(no_args_is_help=True, help="Project backlog (T-NNN)")


@task_app.command("add")
@handled
def task_add(
    title: Annotated[str, typer.Argument()],
    description: Annotated[str | None, typer.Option()] = None,
    area: Annotated[str | None, typer.Option(help="|".join(tasks.TASK_AREAS))] = None,
    priority: Annotated[int, typer.Option(help="0 urgent .. 3 someday")] = 2,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Add a task (exit 5 if the title exists)."""
    b = _backend(use_rest)
    t = b.tasks.add(title, description, area, priority)
    emit({"task": t}, json_out, t["task_id"])


@task_app.command("list")
@handled
def task_list(
    status: Annotated[str | None, typer.Option(help="todo|in_progress|blocked|done|open|all")]
    = "open",
    area: Annotated[str | None, typer.Option()] = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """List tasks by priority (default: not done)."""
    b = _backend(use_rest)
    if status in (None, "all"):
        rows = b.tasks.list_tasks(area=area)
    elif status == "open":
        rows = b.tasks.list_tasks(area=area, open_only=True)
    else:
        rows = b.tasks.list_tasks(status, area)
    keys = ["task_id", "priority", "status", "area", "owner", "title"]
    emit({"tasks": rows}, json_out, "\n".join(_fmt_row(r, keys) for r in rows) or "(none)")


@task_app.command("show")
@handled
def task_show(
    task_id: Annotated[str, typer.Argument()], use_rest: UseRest = False, json_out: JsonOut = False
) -> None:
    """Show a task."""
    b = _backend(use_rest)
    emit({"task": b.tasks.get_task(task_id)}, json_out)


@task_app.command("claim")
@handled
def task_claim(
    task_id: Annotated[str, typer.Argument()],
    owner: Annotated[str | None, typer.Option(help="Defaults to the run id")] = None,
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Claim a task (exit 3 if another owner has it in progress)."""
    b = _backend(use_rest)
    t = b.tasks.claim(task_id, owner or _need_run(run_id))
    emit({"task": t}, json_out, f"{task_id} claimed by {t['owner']}")


@task_app.command("update")
@handled
def task_update(
    task_id: Annotated[str, typer.Argument()],
    status: Annotated[str | None, typer.Option()] = None,
    priority: Annotated[int | None, typer.Option()] = None,
    description: Annotated[str | None, typer.Option()] = None,
    owner: Annotated[str | None, typer.Option()] = None,
    release: Annotated[bool, typer.Option(help="Clear the owner")] = False,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Update status/priority/description/owner."""
    b = _backend(use_rest)
    t = b.tasks.update_task(task_id, status, owner, priority, description, release=release)
    emit({"task": t}, json_out, f"{task_id} {t['status']}")


@task_app.command("done")
@handled
def task_done(
    task_id: Annotated[str, typer.Argument()],
    run_id: RunId = None,
    use_rest: UseRest = False,
    json_out: JsonOut = False,
) -> None:
    """Mark a task done (exit 3 if another owner has it in progress)."""
    b = _backend(use_rest)
    t = b.tasks.done(task_id, run_id)
    emit({"task": t}, json_out, f"{task_id} done")


@task_app.command("seed")
@handled
def task_seed(json_out: JsonOut = False) -> None:
    """Insert the initial backlog (idempotent by title)."""
    res = seed_tasks.seed()
    emit(res, json_out, f"created {len(res['created'])}, existing {len(res['existing'])}")
