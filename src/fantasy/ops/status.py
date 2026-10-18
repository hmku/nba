"""Top-level commands: `fantasy status`, `fantasy db init`, `fantasy publish`."""

from __future__ import annotations

import json
from typing import Annotated, Any, cast

import typer
from sqlalchemy import create_engine, func, select

from fantasy.db import rest
from fantasy.db.engine import DEFAULT_SQLITE, db_url
from fantasy.db.rest import PUBLISH_SKIP, PUBLISH_UNIQUE, unique_clashes
from fantasy.db.schema import drafts, metadata, model_runs
from fantasy.ops import approvals, ledger, locks, tasks, transactions
from fantasy.ops.cli import JsonOut, emit, handled
from fantasy.ops.common import age_seconds, engine, jsonable, now, row_dict

MIGRATIONS_DIR = "supabase/migrations"


def _redact(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def collect_status(stale_minutes: float = ledger.DEFAULT_STALE_MINUTES) -> dict[str, Any]:
    """Everything an agent (or human) needs to orient itself, as one JSON-able dict."""
    t = now()
    running = ledger.list_runs(status="running", limit=200)
    stale_ids = {r["run_id"] for r in ledger.stale_runs(stale_minutes)}
    for r in running:
        r["stale"] = r["run_id"] in stale_ids
        r["heartbeat_age_s"] = age_seconds(r["heartbeat_at"] or r["started_at"], t)

    open_tasks = tasks.list_tasks(open_only=True)
    by_priority: dict[str, list[dict[str, Any]]] = {}
    for tk in open_tasks:
        by_priority.setdefault(f"P{tk['priority']}", []).append(
            {k: tk[k] for k in ("task_id", "title", "status", "area", "owner")}
        )

    with engine().connect() as conn:
        latest = (
            select(model_runs.c.kind, func.max(model_runs.c.created_at).label("created_at"))
            .group_by(model_runs.c.kind)
            .subquery()
        )
        mr = conn.execute(
            select(model_runs)
            .join(
                latest,
                (model_runs.c.kind == latest.c.kind)
                & (model_runs.c.created_at == latest.c.created_at),
            )
            .order_by(model_runs.c.kind)
        )
        freshness = []
        for mrow in mr:
            d = row_dict(mrow)
            freshness.append(
                {
                    "kind": d["kind"],
                    "run_id": d["run_id"],
                    "model_version": d["model_version"],
                    "created_at": d["created_at"],
                    "age_hours": round(cast(float, age_seconds(d["created_at"], t)) / 3600, 2)
                    if d["created_at"]
                    else None,
                }
            )
        draft_row = conn.execute(
            select(drafts)
            .where(drafts.c.status.in_(("live", "pending")))
            .order_by((drafts.c.status == "live").desc(), drafts.c.updated_at.desc())
            .limit(1)
        ).first()

    return {
        "as_of": t,
        "db": _redact(db_url()),
        "runs": {
            "active": [r for r in running if not r["stale"]],
            "stale": [r for r in running if r["stale"]],
            "stale_minutes": stale_minutes,
        },
        "locks": locks.list_locks(),
        "approvals_pending": approvals.list_pending(),
        "txns_needing_reconciliation": transactions.needs_reconciliation(),
        "txns_awaiting_approval": transactions.list_txns(status="awaiting_approval"),
        "tasks_open": by_priority,
        "model_freshness": freshness,
        "active_draft": row_dict(draft_row),
    }


def _render_text(s: dict[str, Any]) -> str:
    lines = [f"status as of {s['as_of'].isoformat()}  db={s['db']}"]
    lines.append(f"runs: {len(s['runs']['active'])} active, {len(s['runs']['stale'])} stale")
    for r in s["runs"]["active"] + s["runs"]["stale"]:
        flag = " STALE" if r["stale"] else ""
        lines.append(f"  {r['run_id']} {r['agent']} [{r['mode']}] hb {r['heartbeat_age_s']}s{flag}")
    lines.append(f"locks: {len(s['locks'])}")
    for lk in s["locks"]:
        lines.append(f"  {lk['resource']} <- {lk['holder_run_id']} until {lk['expires_at']}")
    lines.append(f"pending approvals: {len(s['approvals_pending'])}")
    for a in s["approvals_pending"]:
        lines.append(f"  {a['approval_id']} {a['action_type']}: {a['summary']}")
    rec = s["txns_needing_reconciliation"]
    lines.append(f"txns needing reconciliation: {len(rec)}" + ("  <-- CHECK YAHOO" if rec else ""))
    for x in rec:
        lines.append(f"  {x['txn_id']} {x['kind']} {json.dumps(jsonable(x['payload']))}")
    lines.append("open tasks:")
    for p, items in sorted(s["tasks_open"].items()):
        for tk in items:
            lines.append(f"  {p} {tk['task_id']} [{tk['status']}] {tk['title']}")
    lines.append("model freshness:")
    for m in s["model_freshness"]:
        lines.append(f"  {m['kind']}: {m['run_id']} ({m['age_hours']}h old)")
    d = s["active_draft"]
    lines.append(f"draft: {d['draft_id']} {d['status']}" if d else "draft: none active")
    return "\n".join(lines)


# ------------------------------------------------------------------ publish


def publish(source_url: str, target_url: str, chunk: int = 500) -> dict[str, Any]:
    """Upsert every row of every table from source into target (FK order, PK upsert)."""
    if source_url == target_url:
        raise ValueError("source and target are the same database")
    src = create_engine(source_url, future=True)
    dst = create_engine(target_url, future=True)
    if dst.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif dst.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert  # type: ignore[assignment]

        metadata.create_all(dst)
    else:
        raise ValueError(f"unsupported target dialect {dst.dialect.name}")
    counts: dict[str, int] = {}
    skipped: dict[str, list[str]] = {}
    with src.connect() as sconn, dst.begin() as dconn:
        for table in metadata.sorted_tables:
            if table.name in PUBLISH_SKIP:
                continue
            rows = [row_dict(r) for r in sconn.execute(select(table))]  # tz-aware UTC
            pk = [c.name for c in table.primary_key.columns]
            uniq = PUBLISH_UNIQUE.get(table.name)
            if uniq and rows:
                (pkcol,) = pk
                existing = {
                    r[0]: r[1] for r in dconn.execute(select(table.c[pkcol], table.c[uniq]))
                }
                rows, skipped_pks = unique_clashes(rows, pkcol, uniq, existing)
                if skipped_pks:
                    skipped[table.name] = skipped_pks
            for i in range(0, len(rows), chunk):
                batch = rows[i : i + chunk]
                stmt = dialect_insert(table).values(batch)
                updates = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in pk}
                stmt = (
                    stmt.on_conflict_do_update(index_elements=pk, set_=updates)
                    if updates
                    else stmt.on_conflict_do_nothing(index_elements=pk)
                )
                dconn.execute(stmt)
            counts[table.name] = len(rows)
    return {"rows": counts, "skipped": skipped}


# ------------------------------------------------------------------ registration


def register(app: typer.Typer) -> None:
    @app.command("status")
    @handled
    def status_cmd(
        stale_minutes: Annotated[float, typer.Option()] = ledger.DEFAULT_STALE_MINUTES,
        json_out: JsonOut = False,
    ) -> None:
        """One-shot overview: runs, locks, approvals, reconciliation, tasks, freshness, draft."""
        s = collect_status(stale_minutes)
        emit({"status": s}, json_out, _render_text(s))

    db_app = typer.Typer(no_args_is_help=True, help="Database utilities")

    @db_app.command("init")
    @handled
    def db_init(json_out: JsonOut = False) -> None:
        """Create tables (sqlite) or print how to apply Supabase migrations (postgres)."""
        url = db_url()
        eng = engine(url)
        if eng.dialect.name == "sqlite":
            metadata.create_all(eng)
            emit({"dialect": "sqlite", "created": True, "db": url}, json_out,
                 f"sqlite tables ready at {url}")
            return
        msg = (
            f"Postgres ({_redact(url)}): schema is managed by migrations in {MIGRATIONS_DIR}/.\n"
            "Apply them in order, e.g. `supabase db push`, or the Supabase MCP apply_migration, or\n"
            f"  for f in {MIGRATIONS_DIR}/*.sql; do psql \"$FANTASY_DB_URL\" -f \"$f\"; done"
        )
        emit({"dialect": eng.dialect.name, "created": False, "migrations_dir": MIGRATIONS_DIR},
             json_out, msg)

    @db_app.command("ping")
    @handled
    def db_ping(
        use_rest: Annotated[bool, typer.Option("--rest", help="Check Supabase over HTTPS "
                                                "(PostgREST) instead of FANTASY_DB_URL")] = False,
        json_out: JsonOut = False,
    ) -> None:
        """Quick connectivity/auth check. Exit 0 = reachable and authorised."""
        if use_rest:
            res = rest.ping()
            got = f"HTTP {res['status']}" if res["reachable"] else "UNREACHABLE"
            text = f"{res['target']}: {got} auth_ok={res['auth_ok']} (auth: {res['auth']})"
            if res.get("message"):
                text += "\n" + res["message"]
            if not res["auth_ok"]:
                details = {k: v for k, v in res.items() if k != "message"}
                err = (rest.RestUnreachableError if not res["reachable"]
                       else rest.RestAuthError if res["status"] in (401, 403)
                       else rest.RestError)
                raise err(text, ping=details)
            emit({"ping": res}, json_out, text)
            return
        url = db_url()
        with engine(url).connect() as conn:
            conn.execute(select(1))
        emit({"ping": {"db": _redact(url), "auth_ok": True}}, json_out, f"ok: {_redact(url)}")

    app.add_typer(db_app, name="db")

    @app.command("publish")
    @handled
    def publish_cmd(
        source: Annotated[str | None, typer.Option(help="Source DB URL (default: FANTASY_DB_URL"
                                                   " or local sqlite)")] = None,
        target: Annotated[str | None, typer.Option(envvar="FANTASY_PUBLISH_URL",
                                                   help="Target Postgres URL")] = None,
        use_rest: Annotated[bool, typer.Option("--rest", help="Publish over HTTPS to Supabase "
                                                "PostgREST (SUPABASE_URL) instead of --target")]
        = False,
        tables: Annotated[list[str] | None, typer.Option(help="--rest: only these tables "
                                                          "(repeat or comma-separate)")] = None,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="--rest: count rows/batches, "
                                              "send nothing")] = False,
        json_out: JsonOut = False,
    ) -> None:
        """Copy all rows from the local sqlite DB to Supabase (upsert by PK; skips `locks`).

        Default: SQL to FANTASY_PUBLISH_URL (laptop). --rest: HTTPS/PostgREST (cloud sessions);
        ledger/state tables (runs, approvals, transactions, tasks, drafts, events, decisions) are
        insert-if-missing there, never overwritten (fantasy.db.rest.publish_policy).
        """
        if source:
            src = source
        else:  # the local sqlite DB: FANTASY_DB_URL if it is sqlite, else the default file
            src = db_url() if db_url().startswith("sqlite") else f"sqlite:///{DEFAULT_SQLITE}"
        if use_rest:
            res = rest.publish_rest(src, tables=tables, dry_run=dry_run)
            def verb(k: str) -> str:
                if res["policy"].get(k) == "merge":
                    return "would upsert" if dry_run else "upserted"
                return "would insert-if-missing" if dry_run else "insert-if-missing sent"

            lines = [f"{'DRY RUN ' if dry_run else ''}{_redact(src)} -> {res['target']}"
                     f" (auth: {res['auth']})"]
            lines += [f"{k}: {verb(k)} {v} rows in {res['batches'][k]} batch(es)"
                      + (f", {res['inserted'][k]} new" if res["inserted"].get(k) is not None else "")
                      for k, v in res["rows"].items()]
            if res["skipped"]:
                lines.append(f"SKIPPED (unique-key clash with different target row): "
                             f"{res['skipped']}")
            if res["errors"]:
                lines.append(f"ERRORS: {res['errors']}")
                raise rest.RestError("\n".join(lines), **{**res, "source": _redact(src)})
            emit({"source": _redact(src), **res}, json_out, "\n".join(lines))
            return
        if not target:
            raise ValueError("set FANTASY_PUBLISH_URL or pass --target (or use --rest)")
        for prefix in ("postgres://", "postgresql://"):
            if target.startswith(prefix):
                target = "postgresql+psycopg://" + target[len(prefix):]
        res = publish(src, target)
        text = "\n".join(f"{k}: {v}" for k, v in res["rows"].items())
        if res["skipped"]:
            text += f"\nSKIPPED (unique-key clash with different target row): {res['skipped']}"
        emit({"source": _redact(src), "target": _redact(target), **res}, json_out, text)
