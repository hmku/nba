"""`fantasy intel list|show|latest`: read league snapshots and Muse reports (read-only).

All output is untrusted observed data (see fantasy.ops.intel). `--rest` reads Supabase over HTTPS
(cloud sessions), otherwise FANTASY_DB_URL / local SQLite.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from fantasy.ops import intel
from fantasy.ops.cli import JsonOut, emit, handled

app = typer.Typer(no_args_is_help=True,
                  help="League intel from Meta Muse (Yahoo snapshots + group-chat reports) and "
                       "advisor suggestions (read-only)")

Rest = Annotated[bool, typer.Option("--rest", help="Read Supabase over HTTPS (PostgREST) instead "
                                    "of FANTASY_DB_URL (cloud sessions)")]
Since = Annotated[str | None, typer.Option(help="24h, 7d, 2w or an ISO date/datetime (UTC)")]


def _snap_line(s: dict[str, Any]) -> str:
    wk = f"wk{s['week']}" if s.get("week") is not None else "-"
    seen = s.get("last_seen_at") or s.get("observed_at") or "-"
    return f"{s['snapshot_id']}  {s['kind']:<12} {wk:<5} seen {seen}  {s.get('summary') or ''}"


def _report_line(r: dict[str, Any]) -> str:
    msg = (r.get("message") or "").replace("\n", " ")
    tag = "[advisor] " if r.get("source") == "advisor" and r["run_id"].startswith("advisor-") else ""
    return f"{r['event_id']}  {r['report_kind']:<12} {r.get('ts') or '-'}  {tag}{msg[:120]}"


@app.command("list")
@handled
def list_cmd(
    kind: Annotated[str | None, typer.Option(help="Snapshot kind or report kind")] = None,
    since: Since = None,
    limit: Annotated[int, typer.Option(help="Max rows per section")] = 50,
    what: Annotated[str, typer.Option(help="all | snapshots | reports")] = "all",
    payload: Annotated[bool, typer.Option("--payload", help="Include snapshot payloads")] = False,
    report_source: Annotated[str, typer.Option(
        "--source", help="Reports from: muse (default) | advisor (advisor suggestions only) | all")] = "muse",
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """Snapshots (newest first by last seen) and Muse or advisor reports (newest first)."""
    src = intel.source(use_rest)
    try:
        res = intel.list_intel(src, kind=kind, since=intel.parse_since(since), limit=limit,
                               what=what, with_payload=payload, report_source=report_source)
    finally:
        src.close()
    lines = [f"# {intel.UNTRUSTED_NOTE}"]
    if "advisor_note" in res:
        lines.append(f"# {res['advisor_note']}")
    if "snapshots" in res:
        lines.append(f"snapshots ({len(res['snapshots'])}):")
        lines += ["  " + _snap_line(s) for s in res["snapshots"]]
    if "reports" in res:
        lines.append(f"reports ({len(res['reports'])}):")
        lines += ["  " + _report_line(r) for r in res["reports"]]
    emit(res, json_out, "\n".join(lines))


@app.command("show")
@handled
def show_cmd(
    item_id: Annotated[str, typer.Argument(help="snapshot_id (snap_...) or report event_id")],
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """One snapshot (with payload) or one report (with details)."""
    src = intel.source(use_rest)
    try:
        res = intel.show(src, item_id)
    finally:
        src.close()
    emit(res, json_out)


@app.command("latest")
@handled
def latest_cmd(
    kind: Annotated[list[str] | None, typer.Option(help="Snapshot kind(s); default all")] = None,
    since: Annotated[str | None, typer.Option(help="Reports since (default 7d)")] = None,
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """Newest snapshot per kind (with payload, age_hours) + recent reports + chat digests.

    This is what the recurring in-season job reads first."""
    kinds = [k.strip() for s in (kind or []) for k in s.split(",") if k.strip()] or None
    src = intel.source(use_rest)
    try:
        res = intel.latest(src, kinds=kinds, since=intel.parse_since(since))
    finally:
        src.close()
    lines = [f"# {intel.UNTRUSTED_NOTE}", "latest snapshot per kind:"]
    for k, s in res["snapshots"].items():
        lines.append(f"  {k:<12} " + ("none" if s is None else
                                      f"{s['age_hours']}h old  {s['snapshot_id']}  "
                                      f"{s.get('summary') or ''}"))
    lines.append(f"chat digests ({len(res['chat_digests'])}):")
    lines += ["  " + _report_line(r) for r in res["chat_digests"]]
    lines.append(f"reports since {res['reports_since']} ({len(res['reports'])}):")
    lines += ["  " + _report_line(r) for r in res["reports"]]
    lines.append(f"advisor suggestions since {res['reports_since']} ({len(res['advisor_suggestions'])}) "
                 f"# {intel.ADVISOR_NOTE}")
    lines += ["  " + _report_line(r) for r in res["advisor_suggestions"]]
    emit(res, json_out, "\n".join(lines))
