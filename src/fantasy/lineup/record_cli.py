"""`fantasy lineup record|backfill`: what Yahoo actually shows for my lineup, as `lineup_actual` model_runs rows.

Registered on `fantasy.lineup.cli.app` (imported at the bottom of that module). Schemas and readers:
`fantasy.lineup.record`, docs/research/dashboard_data.md section 6. `--rest` writes Supabase over HTTPS
(cloud sessions); otherwise FANTASY_DB_URL / local SQLite. Nothing here touches Yahoo.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer

from fantasy.cli_common import emit, fail
from fantasy.league.dates import et_date
from fantasy.league.settings import load_settings
from fantasy.lineup import record
from fantasy.lineup.cli import LEAGUE_FILE, YAHOO_DIR, app
from fantasy.lineup.players import normalize_team
from fantasy.ops.common import EXIT_USAGE, OpsError, now

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
RestOpt = Annotated[bool, typer.Option("--rest", help="Write/read Supabase over HTTPS (PostgREST) instead of "
                                       "FANTASY_DB_URL (cloud sessions)")]
LeagueOpt = Annotated[Path | None, typer.Option("--league-file", help="League state JSON (default "
                                                "data/manual/league_rosters_2026-27.json): my team, ids, names")]
ProjOpt = Annotated[Path | None, typer.Option("--projections", help="Projections CSV (player names / NBA teams)")]
NbaOpt = Annotated[Path | None, typer.Option("--nba-schedule", help="NBA games CSV (game markers; default cache)")]


class Context:
    """League state + names + NBA schedule, loaded once per command (all optional: degrade, never crash)."""

    def __init__(self, league_file: Path | None, projections: Path | None, nba_schedule: Path | None) -> None:
        lf = league_file or LEAGUE_FILE
        self.league: dict[str, Any] = json.loads(lf.read_text()) if lf.exists() else {}
        self.my_team = int(self.league.get("my_team") or load_settings().my_slot)
        t = (self.league.get("teams") or {}).get(str(self.my_team))
        self.team: dict[str, Any] = t if isinstance(t, dict) else ({"players": t} if isinstance(t, list) else {})
        self.names: dict[str, str] = {}
        self.nba_teams: dict[str, str | None] = {}
        try:
            from fantasy.model.lines import load_projections

            df = load_projections(projections).df
            self.names = {str(k): str(v) for k, v in df["name"].items()}
            if "team" in df.columns:
                self.nba_teams = {str(k): normalize_team(v) for k, v in df["team"].items()}
        except (OSError, ValueError, KeyError):
            pass
        try:
            from fantasy.sim.schedule import load_nba_schedule

            self.nba = load_nba_schedule(nba_schedule)
        except (OSError, ValueError, KeyError):
            self.nba = None

    def roster(self, capture_players: list[dict[str, Any]] | None = None) -> record.Roster:
        return record.Roster.build(self.team, capture_players, self.names)

    def games(self, roster: record.Roster, day: str) -> dict[str, dict[str, str]] | None:
        if self.nba is None:
            return None
        teams = {pid: normalize_team(p.get("nba_team")) or self.nba_teams.get(pid) for pid, p in roster.players.items()}
        return record.game_markers(self.nba, teams, [day])

    def roster_size(self, roster: record.Roster) -> int | None:
        return len(self.team.get("players") or roster.players) or None


def build_actual(ctx: Context, day: str, raw_slots: Any, *, source: str, observed_at: str | None, how: str | None,
                 txn_id: str | None = None, capture_file: str | None = None,
                 capture_players: list[dict[str, Any]] | None = None,
                 params: dict[str, Any] | None = None) -> dict[str, Any]:
    roster = ctx.roster(capture_players)
    slots, warnings = record.normalise_slots(raw_slots, roster)
    summary = record.actual_summary(day, slots, source=source, team=ctx.my_team, observed_at=observed_at, how=how,
                                    txn_id=txn_id, capture_file=capture_file, games=ctx.games(roster, day),
                                    roster_size=ctx.roster_size(roster), warnings=warnings)
    return record.actual_row(summary, params)


def capture_actual(ctx: Context, capture: dict[str, Any], capture_file: str, *, day: str | None = None,
                   how: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """The `lineup_actual` row for my team in a roster capture (date = the capture's ET date)."""
    team = record.capture_team(capture, ctx.my_team, ctx.team.get("yahoo_team_id"))
    if team is None:
        raise record.RecordError(f"team {ctx.my_team} not found in {capture_file}")
    cap_day = capture.get("capture_date")
    if not cap_day:
        d = et_date(capture.get("captured_at"))
        cap_day = d.isoformat() if d else None
    if day and cap_day and day != cap_day:
        raise record.RecordError(f"--date {day} but the capture is from {cap_day} (ET): its slots are that day's")
    day = day or cap_day
    if not day:
        raise record.RecordError(f"{capture_file}: no captured_at/capture_date; pass --date")
    players = [p for p in team.get("players") or [] if isinstance(p, dict)]
    raw = [{"slot": p.get("slot"), "player_id": p.get("player_id") or p.get("name")} for p in players]
    return build_actual(ctx, day, raw, source="yahoo_capture", observed_at=capture.get("captured_at"),
                        how=how or f"roster capture {capture_file} (scripts/yahoo/capture_rosters.js, read-only)",
                        capture_file=capture_file, capture_players=players, params=params)


def _save(row: dict[str, Any], use_rest: bool) -> dict[str, Any]:
    st = record.store(use_rest)
    try:
        return record.save(row, st)
    finally:
        st.close()


def _brief(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    s = row["summary"]
    return {"run_id": row["run_id"], "date": s["date"], "source": s["source"], "txn_id": s.get("txn_id"),
            "players": len(s["slots"]), "complete": s.get("complete"), "warnings": s.get("warnings", []), **extra}


@app.command("record")
def record_cmd(
    day: Annotated[str, typer.Option("--date", help="Lineup date YYYY-MM-DD (ET); with --from-capture default "
                                     "= the capture's date")] = "",
    slots: Annotated[str, typer.Option("--slots", help="Slots read from Yahoo: inline JSON or @file; "
                                       "{player_id|yahoo id|name: slot}, {slot: player} or [{slot, player_id}]")] = "",
    from_capture: Annotated[Path | None, typer.Option("--from-capture", help="Roster capture (my team's slots)")] = None,
    source: Annotated[str, typer.Option("--source", help="yahoo_verified (server re-read after a change) | "
                                        "yahoo_capture (default: verified for --slots, capture for --from-capture)")] = "",
    txn: Annotated[str | None, typer.Option("--txn", help="Lineup txn this verifies")] = None,
    how: Annotated[str | None, typer.Option("--how", help="How it was read, e.g. 'fetch /nba/44495/11/team?date=D "
                                            "no-store, 10:42 ET'")] = None,
    observed_at: Annotated[str | None, typer.Option("--observed-at", help="ISO time of the read (default now)")] = None,
    league_file: LeagueOpt = None,
    projections: ProjOpt = None,
    nba_schedule: NbaOpt = None,
    use_rest: RestOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Record what Yahoo actually shows for my lineup on a date (kind lineup_actual; idempotent)."""
    if bool(slots) == bool(from_capture):
        fail("pass exactly one of --slots and --from-capture", as_json, EXIT_USAGE)
    if source and source not in record.SOURCES:
        fail(f"--source must be one of {list(record.SOURCES)}", as_json, EXIT_USAGE)
    ctx = Context(league_file, projections, nba_schedule)
    params = {"command": "lineup record", "source": source or None}
    try:
        if from_capture is not None:
            cap = json.loads(from_capture.read_text())
            row = capture_actual(ctx, cap, str(from_capture), day=day or None, how=how, params=params)
            if source:
                row = _with_source(row, source, txn)
        else:
            if not day:
                fail("--date is required with --slots", as_json, EXIT_USAGE)
            date.fromisoformat(day)
            src = source or "yahoo_verified"
            if src == "yahoo_verified" and not (txn or how):
                fail("yahoo_verified needs --txn or --how (what confirms it on Yahoo?)", as_json, EXIT_USAGE)
            raw = json.loads(Path(slots[1:]).read_text()) if slots.startswith("@") else json.loads(slots)
            row = build_actual(ctx, day, raw, source=src, observed_at=observed_at or now().isoformat(), how=how,
                               txn_id=txn, params=params)
    except (record.RecordError, ValueError, OSError) as e:
        fail(str(e), as_json, EXIT_USAGE)
    try:
        res = _save(row, use_rest)
    except OpsError as e:
        fail(e.message, as_json, e.exit_code, code=e.code)
    out = _brief(row, inserted=res["inserted"], store=res["store"])
    emit({"ok": True, **out}, as_json, f"{'recorded' if res['inserted'] else 'unchanged'}: {row['run_id']} "
         f"({out['players']} players, {out['source']})" + "".join(f"\nwarning: {w}" for w in out["warnings"]))


def _with_source(row: dict[str, Any], source: str, txn: str | None) -> dict[str, Any]:
    """A capture-based row re-labelled (e.g. a capture taken as the verification read of a lineup txn)."""
    s = dict(row["summary"])
    s["source"] = source
    s["txn_id"] = txn or s.get("txn_id")
    return record.actual_row(s, {"command": "lineup record", "source": source})


@app.command("backfill")
def backfill_cmd(
    start: Annotated[str, typer.Option("--from", help="First date YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--to", help="Last date YYYY-MM-DD")],
    captures: Annotated[list[Path] | None, typer.Option("--capture", help="Roster capture(s) to record (default: "
                                                        "data/raw/yahoo/league_rosters_<date>.json in range)")] = None,
    no_txns: Annotated[bool, typer.Option("--no-txns", help="Skip the verified lineup txns in the ledger")] = False,
    write: Annotated[bool, typer.Option("--write", help="Insert the rows (default: dry run, print them)")] = False,
    league_file: LeagueOpt = None,
    projections: ProjOpt = None,
    nba_schedule: NbaOpt = None,
    use_rest: RestOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Seed lineup_actual rows for past dates from what exists: verified lineup txns (their evidence slot map) and
    roster captures. Dry run unless --write; idempotent (re-running inserts nothing new)."""
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        fail("--from/--to: expected YYYY-MM-DD", as_json, EXIT_USAGE)
    ctx = Context(league_file, projections, nba_schedule)
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    params = {"command": "lineup backfill", "from": start, "to": end}
    files = captures if captures else [p for p in sorted(YAHOO_DIR.glob("league_rosters_*.json"))
                                      if d0.isoformat() <= p.stem.removeprefix("league_rosters_")[:10] <= d1.isoformat()]
    for f in files:
        try:
            cap = json.loads(f.read_text())
            row = capture_actual(ctx, cap, str(f), params=params)
        except (record.RecordError, ValueError, OSError) as e:
            skipped.append({"capture": str(f), "reason": str(e)})
            continue
        if d0.isoformat() <= row["summary"]["date"] <= d1.isoformat():
            rows.append(row)
        else:
            skipped.append({"capture": str(f), "reason": f"date {row['summary']['date']} outside range"})
    if not no_txns:
        from fantasy.ops.cli import _backend

        try:
            txns = _backend(use_rest).transactions.list_txns(kind="lineup", limit=1000)
        except OpsError as e:
            fail(e.message, as_json, e.exit_code, code=e.code)
        # by lineup date, then time: a txn without a slot map is replayed on the latest state known for its date
        for t in sorted(txns, key=lambda t: (record.txn_date(t) or "", str(t.get("created_at")))):
            got = _txn_row(ctx, t, d0, d1, params, rows)
            if isinstance(got, dict) and "run_id" in got:
                rows.append(got)
            elif got is not None:
                skipped.append(got)
    results = []
    for row in rows:
        res = _save(row, use_rest) if write else {"inserted": None, "store": "dry-run"}
        results.append(_brief(row, inserted=res["inserted"], store=res["store"],
                              slots={r["player_id"] or r["name"]: r["slot"] for r in row["summary"]["slots"]}))
    out = {"ok": True, "written": write, "rows": results, "skipped": skipped}
    text = "\n".join(f"{'+' if r['inserted'] else ('=' if write else '?')} {r['run_id']} {r['date']} {r['source']} "
                     f"txn={r['txn_id'] or '-'} players={r['players']} complete={r['complete']}" for r in results)
    text += "".join(f"\nskipped: {s}" for s in skipped)
    text += "" if write else "\n(dry run; --write to insert)"
    emit(out, as_json, text)


def _base_state(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    """The newest known lineup for a date <= `day` (Yahoo carries an edited lineup forward to later, unedited days)."""
    prior = [r for r in rows if r["summary"]["date"] <= day and r["summary"].get("complete") is not False]
    return prior[-1] if prior else None


def _txn_row(ctx: Context, t: dict[str, Any], d0: date, d1: date, params: dict[str, Any],
             known: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A lineup_actual row for a verified txn in range, a skip record, or None (out of range).

    The txn's verification evidence slot map is used when present; otherwise the verified `changes` are replayed on
    the latest known full lineup for a date <= the txn's (`known`, built in date order), and the row says so in `how`
    / `derived_from`."""
    day = record.txn_date(t)
    tid = t.get("txn_id")
    if day is None:
        return {"txn_id": tid, "reason": "payload has no date"}
    if not d0.isoformat() <= day <= d1.isoformat():
        return None
    if t.get("status") != "verified":
        return {"txn_id": tid, "date": day, "reason": f"status {t.get('status')} (only verified)"}
    v: dict[str, Any] = t.get("verification") or {}
    ev: dict[str, Any] = v["evidence"] if isinstance(v.get("evidence"), dict) else {}
    how = f"txn {tid} verified by a server re-read" + (f" of {ev['page']}" if ev.get("page") else "")
    raw: Any = record.txn_slots(t)
    derived = None
    try:
        if raw is None:
            base = _base_state(known, day)
            changes = record.txn_changes(t)
            if base is None or not changes:
                return {"txn_id": tid, "date": day, "reason": "verification evidence has no slot map and there is "
                        + ("no earlier full lineup to replay its changes on" if base is None else "no changes list")}
            state = {s["player_id"]: s["slot"] for s in base["summary"]["slots"] if s.get("player_id")}
            raw = record.apply_changes(state, changes, ctx.roster())
            derived = {"base_run_id": base["run_id"], "base_date": base["summary"]["date"], "changes": len(changes)}
            how += (f"; slots derived: {base['summary']['date']} lineup ({base['run_id']}) + this txn's "
                    f"{len(changes)} verified change(s)")
        row = build_actual(ctx, day, raw, source="yahoo_verified", observed_at=v.get("verified_at"), how=how,
                           txn_id=tid, params=params)
    except record.RecordError as e:
        return {"txn_id": tid, "date": day, "reason": str(e)}
    if derived:
        row = record.actual_row({**row["summary"], "derived_from": derived}, params)
    return row
