"""Top-level commands: `fantasy status`, `fantasy db init`, `fantasy publish`."""

from __future__ import annotations

import json
from typing import Annotated, Any, cast

import typer
from sqlalchemy import create_engine, func, select

from fantasy.db import rest
from fantasy.db.engine import DEFAULT_SQLITE, db_url
from fantasy.db.rest import PUBLISH_UNIQUE, publish_policy, unique_clashes
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
    """Copy every table from source into target in FK order with the same per-table policy as
    `publish --rest` (`fantasy.db.rest.publish_policy`): `merge` tables are upserted by PK,
    `insert` (ledger/state) tables get ON CONFLICT DO NOTHING -- a stale local row never overwrites
    the target's (e.g. a local 'pending' approval vs. 'rejected' online) -- and `locks` are skipped."""
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
    policies: dict[str, str] = {}
    inserted: dict[str, int | None] = {}
    with src.connect() as sconn, dst.begin() as dconn:
        for table in metadata.sorted_tables:
            pol = publish_policy(table.name)
            if pol == "skip":
                continue
            policies[table.name] = pol
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
            new: int | None = 0
            for i in range(0, len(rows), chunk):
                batch = rows[i : i + chunk]
                stmt = dialect_insert(table).values(batch)
                updates = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in pk}
                if pol == "merge" and updates:
                    dconn.execute(stmt.on_conflict_do_update(index_elements=pk, set_=updates))
                    continue
                n = dconn.execute(stmt.on_conflict_do_nothing(index_elements=pk)).rowcount
                new = None if new is None or n is None or n < 0 else new + n
            counts[table.name] = len(rows)
            if pol == "insert":
                inserted[table.name] = new
    return {"rows": counts, "skipped": skipped, "policy": policies, "inserted": inserted}


# ------------------------------------------------------------------ text renderers (pure)


def _ping_text(res: dict[str, Any]) -> str:
    """`db ping --rest` text: target, HTTP status or UNREACHABLE, auth, and the hint message if any."""
    got = f"HTTP {res['status']}" if res["reachable"] else "UNREACHABLE"
    text = f"{res['target']}: {got} auth_ok={res['auth_ok']} (auth: {res['auth']})"
    if res.get("message"):
        text += "\n" + res["message"]
    return text


_SKIPPED = "SKIPPED (unique-key clash with different target row): "


def _new_rows(res: dict[str, Any], table: str) -> str:
    return f", {res['inserted'][table]} new" if res["inserted"].get(table) is not None else ""


def _publish_rest_text(res: dict[str, Any], source: str, dry_run: bool) -> str:
    """`publish --rest` text: one line per table (upsert vs insert-if-missing, rows, batches, new rows),
    then skipped rows and errors (with errors the command fails with this text as the message)."""
    def verb(k: str) -> str:
        if res["policy"].get(k) == "merge":
            return "would upsert" if dry_run else "upserted"
        return "would insert-if-missing" if dry_run else "insert-if-missing sent"

    lines = [f"{'DRY RUN ' if dry_run else ''}{source} -> {res['target']} (auth: {res['auth']})"]
    lines += [f"{k}: {verb(k)} {v} rows in {res['batches'][k]} batch(es)" + _new_rows(res, k)
              for k, v in res["rows"].items()]
    if res["skipped"]:
        lines.append(f"{_SKIPPED}{res['skipped']}")
    if res["errors"]:
        lines.append(f"ERRORS: {res['errors']}")
    return "\n".join(lines)


def _publish_sql_text(res: dict[str, Any]) -> str:
    """`publish` (SQL) text: one line per table, then skipped rows."""
    text = "\n".join(
        f"{k}: {'upserted' if res['policy'][k] == 'merge' else 'insert-if-missing'} {v} rows" + _new_rows(res, k)
        for k, v in res["rows"].items())
    if res["skipped"]:
        text += f"\n{_SKIPPED}{res['skipped']}"
    return text


def _psycopg_url(url: str) -> str:
    """postgres:// and postgresql:// -> postgresql+psycopg:// (SQLAlchemy driver); others unchanged."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


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
            text = _ping_text(res)
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
        """Copy all rows from the local sqlite DB to Supabase (skips `locks`).

        Default: SQL to FANTASY_PUBLISH_URL (laptop). --rest: HTTPS/PostgREST (cloud sessions).
        Both apply fantasy.db.rest.publish_policy: reference/model-output tables are upserted by
        PK; ledger/state tables (runs, approvals, transactions, tasks, drafts, events, decisions,
        snapshots) are insert-if-missing, never overwritten.
        """
        if source:
            src = source
        else:  # the local sqlite DB: FANTASY_DB_URL if it is sqlite, else the default file
            src = db_url() if db_url().startswith("sqlite") else f"sqlite:///{DEFAULT_SQLITE}"
        if use_rest:
            res = rest.publish_rest(src, tables=tables, dry_run=dry_run)
            text = _publish_rest_text(res, _redact(src), dry_run)
            if res["errors"]:
                raise rest.RestError(text, **{**res, "source": _redact(src)})
            emit({"source": _redact(src), **res}, json_out, text)
            return
        if not target:
            raise ValueError("set FANTASY_PUBLISH_URL or pass --target (or use --rest)")
        target = _psycopg_url(target)
        res = publish(src, target)
        emit({"source": _redact(src), "target": _redact(target), **res}, json_out, _publish_sql_text(res))
