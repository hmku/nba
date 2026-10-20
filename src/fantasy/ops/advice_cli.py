"""`fantasy advise post|status|list`: recommendations for the dashboard's Moves view.

Stored as append-only `model_runs` rows (kind 'advice'); see fantasy.ops.advice and
docs/research/dashboard_data.md section 4. `--rest` writes/reads Supabase over HTTPS (cloud
sessions); otherwise FANTASY_DB_URL / local SQLite (then `fantasy publish` copies the rows up).
Nothing here touches Yahoo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from fantasy.db.engine import REPO_ROOT
from fantasy.ops import advice
from fantasy.ops.cli import JsonOut, emit, handled

app = typer.Typer(no_args_is_help=True,
                  help="Recommendations (trades, add/drops, streams, lineups) for the dashboard's Moves view")

Rest = Annotated[bool, typer.Option("--rest", help="Write/read Supabase over HTTPS (PostgREST) instead "
                                    "of FANTASY_DB_URL (cloud sessions)")]


def _close(st: Any) -> None:
    close = getattr(st, "close", None)
    if close is not None:
        close()


def _rel(path: Path) -> str:
    p = path.resolve()
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _line(r: dict[str, Any]) -> str:
    yes = " [needs yes]" if r.get("requires_user_yes") and r.get("status") in ("idea", "awaiting_yes") else ""
    return (f"{r['run_id']}  {r.get('advice_id')} v{r.get('version')}  {r.get('type'):<8} "
            f"{r.get('status'):<12} {r.get('title')}{yes}")


@app.command("post")
@handled
def post_cmd(
    file: Annotated[Path, typer.Option("--file", "-f", help="Advice JSON (schema advice/1)")],
    advice_id: Annotated[str | None, typer.Option(
        help="Stable item id (default: the file's advice_id, else the file name stem)")] = None,
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """Validate an advice file and record it (new item, or a new version of an existing one)."""
    summary, params, inputs = advice.load_file(file)
    st = advice.store(use_rest)
    try:
        res = advice.post(summary, st, params=params, inputs=inputs, source_file=_rel(file),
                          advice_id=advice_id)
    finally:
        _close(st)
    text = ("unchanged (identical to the latest version): " if res["unchanged"] else "posted: ") + _line(res)
    for w in res["warnings"]:
        text += f"\nwarning: {w}"
    emit(res, json_out, text)


@app.command("status")
@handled
def status_cmd(
    ref: Annotated[str, typer.Argument(help="advice_id or any run_id of the item")],
    status: Annotated[str, typer.Argument(help=" | ".join(advice.STATUSES))],
    note: Annotated[str | None, typer.Option(help="Why (e.g. 'user said yes in chat 10:05 ET')")] = None,
    approval_id: Annotated[str | None, typer.Option(help="approvals row that carries the user's decision")] = None,
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """Record a status change as a new version (rows are never edited in place)."""
    st = advice.store(use_rest)
    try:
        res = advice.set_status(st, ref, status, note=note, approval_id=approval_id)
    finally:
        _close(st)
    emit(res, json_out, ("unchanged: " if res["unchanged"] else "status recorded: ") + _line(res))


@app.command("list")
@handled
def list_cmd(
    status: Annotated[str | None, typer.Option(help="Only this status")] = None,
    open_only: Annotated[bool, typer.Option("--open", help="Hide done/rejected/expired")] = False,
    all_versions: Annotated[bool, typer.Option("--all-versions", help="Every version, not just the latest")] = False,
    limit: Annotated[int, typer.Option(help="Max items")] = 100,
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """Latest version of each advice item, newest first."""
    st = advice.store(use_rest)
    try:
        items = advice.list_advice(st, status=status, all_versions=all_versions, open_only=open_only, limit=limit)
    finally:
        _close(st)
    emit({"items": items, "count": len(items)}, json_out,
         "\n".join(_line(r) for r in items) if items else "no advice recorded")
