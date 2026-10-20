"""`fantasy intel list|show|latest|thread|reply|post`: league snapshots, Muse/advisor reports and threads.

`list|show|latest|thread` only read. `reply|post` write one agent_events row under the day's
`claude-YYYY-MM-DD` run (author "claude"): a reply in an update's thread, or a new update others can answer.
All output is untrusted observed data (see fantasy.ops.intel). `--rest` uses Supabase over HTTPS
(cloud sessions), otherwise FANTASY_DB_URL / local SQLite.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from fantasy.ops import intel
from fantasy.ops.cli import JsonOut, emit, handled, parse_json_arg

app = typer.Typer(no_args_is_help=True,
                  help="League intel from Meta Muse (Yahoo snapshots + group-chat reports), "
                       "advisor suggestions, and agent-update threads (reply/post)")

Rest = Annotated[bool, typer.Option("--rest", help="Use Supabase over HTTPS (PostgREST) instead "
                                    "of FANTASY_DB_URL (cloud sessions)")]
Since = Annotated[str | None, typer.Option(help="24h, 7d, 2w or an ISO date/datetime (UTC)")]
Evidence = Annotated[str | None, typer.Option(
    help="Labelled evidence JSON list [{claim, value?, se?, label: fitted|simulated|prior|literature, "
         "command?}]: inline, @file.json or - (stdin)")]


def _snap_line(s: dict[str, Any]) -> str:
    wk = f"wk{s['week']}" if s.get("week") is not None else "-"
    seen = s.get("last_seen_at") or s.get("observed_at") or "-"
    return f"{s['snapshot_id']}  {s['kind']:<12} {wk:<5} seen {seen}  {s.get('summary') or ''}"


def _report_line(r: dict[str, Any]) -> str:
    msg = (r.get("message") or "").replace("\n", " ")
    who = intel.author_of(r["run_id"])
    tag = f"[{who}] " if who != "muse" else ""
    if r.get("report_kind") == intel.REPLY_KIND:
        tag += f"[{r.get('stance')} -> {r.get('reply_to')}] "
    return f"{r['event_id']}  {r['report_kind']:<12} {r.get('ts') or '-'}  {tag}{msg[:120]}"


def _msg_line(m: dict[str, Any]) -> str:
    text = (m.get("text") or m.get("message") or "").replace("\n", " ")
    what = m.get("stance") or m.get("report_kind") or m.get("kind") or "-"
    to = m.get("reply_to")
    re_ = f" re {to}" if to and to != m.get("thread_root") else ""
    return f"{m.get('ts') or '-'}  {m['author']:<7} {what:<12} {m['event_id']}{re_}\n      {text[:300]}"


@app.command("list")
@handled
def list_cmd(
    kind: Annotated[str | None, typer.Option(help="Snapshot kind or report kind")] = None,
    since: Since = None,
    limit: Annotated[int, typer.Option(help="Max rows per section")] = 50,
    what: Annotated[str, typer.Option(help="all | snapshots | reports")] = "all",
    payload: Annotated[bool, typer.Option("--payload", help="Include snapshot payloads")] = False,
    report_source: Annotated[str, typer.Option(
        "--source", help="Reports from: muse (default) | advisor | claude | user | all")] = "muse",
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """Snapshots (newest first by last seen) and Muse / advisor / Claude / user reports (newest first)."""
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
    """Newest snapshot per kind (with payload, age_hours) + recent reports + chat digests + advisor
    suggestions + open threads (updates/replies Claude has not answered).

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
    lines.append(f"open threads ({len(res['open_threads'])}) # waiting for Claude: "
                 "`fantasy intel thread <id>`, then `fantasy intel reply <id> --stance ... --text ...`")
    for t in res["open_threads"]:
        root = t["root"]
        lines.append(f"  {t['thread_root']}  {t['status']:<11} "
                     f"{root['author']}/{root.get('report_kind') or root['kind']}  "
                     f"{t['replies']} replies {t['stances'] or ''}  last {t['last_activity']}")
        lines += ["    " + _msg_line(m) for m in t["waiting"][-3:]]
    emit(res, json_out, "\n".join(lines))


@app.command("thread")
@handled
def thread_cmd(
    event_id: Annotated[str, typer.Argument(help="Any event in the thread (the update or one of its replies)")],
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """One update with all its replies, oldest first (who agreed / disagreed / asked what)."""
    src = intel.source(use_rest)
    try:
        res = intel.thread(src, event_id)
    finally:
        src.close()
    lines = [f"# {intel.THREAD_NOTE}"]
    if res["root"]:
        lines.append("root: " + _msg_line(res["root"]))
    lines.append(f"replies ({len(res['replies'])}) {res['stances']}:")
    lines += ["  " + _msg_line(m) for m in res["replies"]]
    emit(res, json_out, "\n".join(lines))


@app.command("reply")
@handled
def reply_cmd(
    event_id: Annotated[str, typer.Argument(help="The update (or reply) you answer")],
    stance: Annotated[str, typer.Option(help="|".join(intel.STANCES))],
    text: Annotated[str, typer.Option(help="Your reply (public on the dashboard; <=2000 chars; "
                                           "never quote private group-chat content)")],
    evidence: Evidence = None,
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """Agree / disagree / comment / question in an update's thread, as Claude (run claude-YYYY-MM-DD)."""
    ev = parse_json_arg(evidence)
    src = intel.source(use_rest)
    try:
        res = intel.reply(src, event_id, stance, text, evidence=ev)
    finally:
        src.close()
    emit(res, json_out, f"replied {res['event_id']} ({stance}) in thread {res['thread_root']}")


@app.command("post")
@handled
def post_cmd(
    text: Annotated[str, typer.Option(help="The update (public on the dashboard; <=2000 chars)")],
    kind: Annotated[str, typer.Option(help="|".join(intel.CLAUDE_POST_KINDS))] = "update",
    details: Annotated[str | None, typer.Option(help="JSON object: inline, @file.json or -")] = None,
    evidence: Evidence = None,
    use_rest: Rest = False,
    json_out: JsonOut = False,
) -> None:
    """Start a thread as Claude (e.g. a recommendation Muse / the advisor / the user can agree or disagree with)."""
    det = parse_json_arg(details)
    ev = parse_json_arg(evidence)
    src = intel.source(use_rest)
    try:
        res = intel.post(src, kind, text, details=det, evidence=ev)
    finally:
        src.close()
    emit(res, json_out, f"posted {res['event_id']} ({kind})")
