"""`fantasy review week`: deterministic retrospective of a completed (or in-progress) H2H week -- final score, the
day-by-day score, per-category over/under-performance vs projections (z from the fitted noise model), games volume,
top over/under-performers and the week's decisions/missed moves. Never touches Yahoo.
Schema: docs/research/dashboard_data.md section 8 (`week_review/1`); code `fantasy.inseason.weekreview`."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from fantasy.cli_common import emit, fail
from fantasy.inseason import weekreview as wr
from fantasy.league.dates import ET
from fantasy.ops.common import EXIT_NOT_FOUND, EXIT_USAGE, OpsError

app = typer.Typer(no_args_is_help=True, help="Retrospectives: what happened in a fantasy week and why (read-only).")

STATE = wr.MANUAL / "league_rosters_2026-27.json"


def _today_et() -> date:
    return datetime.now(ET).date()


def _text(r: dict[str, Any]) -> str:
    s = r["summary"]
    lines = [s["headline"], *[f"  - {b}" for b in s["bullets"]], "",
             f"{'cat':<6} {'me':>8} {'opp':>8}  win | me: act  proj  o/u    z   | opp: act  proj  o/u    z"]
    for c in r["categories"]:
        ratio = c["cat"] in ("fg_pct", "ft_pct")

        def f(v: Any, ratio: bool = ratio) -> str:
            if v is None:
                return "-"
            return f"{v:.3f}" if ratio else f"{v:g}"
        lines.append(f"{c['label']:<6} {f(c['final_me']):>8} {f(c['final_opp']):>8}  {c['winner']:<3} | "
                     f"{f(c['me_actual']):>7} {f(c['me_proj_given_games']):>6} {f(c['me_over_under']):>6} "
                     f"{c['me_z'] if c['me_z'] is not None else '-':>5} | "
                     f"{f(c['opp_actual']):>7} {f(c['opp_proj_given_games']):>6} {f(c['opp_over_under']):>6} "
                     f"{c['opp_z'] if c['opp_z'] is not None else '-':>5}")
    for side in ("me", "opp"):
        t = r["teams"][side]
        lines.append(f"{side}: over " + ", ".join(f"{p['name']} {p['score']:+.2f}" for p in t["top_over"])
                     + " | under " + ", ".join(f"{p['name']} {p['score']:+.2f}" for p in t["top_under"]))
    ps = r["projection_source"]
    lines.append(f"projections: {ps.get('kind')} {ps.get('path')} {ps.get('commit') or ''} "
                 f"(data as of {ps.get('data_as_of') or '?'})")
    lines += [f"warning: {w}" for w in r["warnings"]]
    return "\n".join(lines)


@app.command("week")
def week_cmd(
    week: Annotated[int | None, typer.Option("--week", help="Fantasy week (default: the most recently completed)")]
    = None,
    team: Annotated[int | None, typer.Option("--team", help="YAHOO team id to review (default: mine, 11)")] = None,
    projections: Annotated[Path | None, typer.Option("--projections", help="Per-game projections CSV to use instead "
                                                     "of the version committed before week start")] = None,
    box_scores: Annotated[Path | None, typer.Option("--box-scores", help="ESPN player box scores CSV(.gz)")] = None,
    captures_dir: Annotated[Path | None, typer.Option("--captures-dir", help="Dir with matchup_*.json and "
                                                      "league_rosters_*.json (default data/raw/yahoo)")] = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON")] = None,
    no_ledger: Annotated[bool, typer.Option("--no-ledger", help="Skip the ops-ledger / lineup_plan reads")] = False,
    save: Annotated[bool, typer.Option("--save", help="Write the model_runs row (kind week_review; idempotent per "
                                       "week and content)")] = False,
    use_rest: Annotated[bool, typer.Option("--rest", help="Read the ledger and write over Supabase HTTPS (cloud)")]
    = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output")] = False,
) -> None:
    """Week retrospective: score, per-category over/under vs projection (z), games volume, players, decisions."""
    from fantasy.data.provenance import sha256_file
    from fantasy.league.settings import load_settings
    from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH, load_nba_schedule, load_schedule_json

    sp = schedule or LEAGUE_SCHEDULE_PATH
    if not sp.exists():
        fail(f"league schedule {sp} not found", as_json, EXIT_NOT_FOUND, code="not_found")
    ms = load_schedule_json(sp)
    cal = ms.calendar or []
    today = _today_et()
    wk = week if week is not None else wr.default_week(cal, today)
    fw = next((w for w in cal if w.week == wk), None)
    if wk is None or fw is None:
        fail(f"week {wk} not in the league schedule (no completed week yet?)", as_json, EXIT_USAGE)
    assert fw is not None and wk is not None
    warnings: list[str] = []
    if fw.end >= today:
        warnings.append(f"week {wk} ends {fw.end}: not complete yet (today {today} ET)")

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    slot_to_yahoo = {int(k): int(v["yahoo_team_id"]) for k, v in (state.get("teams") or {}).items()
                     if isinstance(v, dict) and v.get("yahoo_team_id") is not None}
    yahoo_to_slot = {y: s for s, y in slot_to_yahoo.items()}
    my_default_slot = int(state.get("my_team") or load_settings().my_slot)
    my_yahoo = team if team is not None else slot_to_yahoo.get(my_default_slot, 11)
    my_slot = yahoo_to_slot.get(my_yahoo)
    if my_slot is None:
        fail(f"Yahoo team {my_yahoo} has no draft slot in {STATE}", as_json, EXIT_USAGE)
    assert my_slot is not None
    opp_slot = ms.opponent(my_slot, wk)
    opp_yahoo = slot_to_yahoo.get(opp_slot) if opp_slot is not None else None
    if opp_yahoo is None:
        fail(f"no week-{wk} opponent for team {my_slot} in {sp}", as_json, EXIT_NOT_FOUND, code="not_found")
    assert opp_yahoo is not None
    names = {int(v["yahoo_team_id"]): v.get("name") for v in (state.get("teams") or {}).values()
             if isinstance(v, dict) and v.get("yahoo_team_id") is not None}

    cdir = captures_dir or wr.YAHOO_RAW
    snap_paths = sorted(cdir.glob("league_rosters_2*.json")) + (
        sorted(wr.MANUAL.glob("league_rosters_2026-27*.json")) if captures_dir is None else [])
    snaps, snap_files = wr.load_snapshots(snap_paths)

    def week_of(d: date) -> int | None:
        return next((w.week for w in cal if w.start <= d <= w.end), None)

    captures, w = wr.load_matchup_captures(sorted(cdir.glob("matchup_*.json")), wk, my_yahoo, week_of)
    warnings += w
    box_path = box_scores or wr.BOXSCORES
    if not box_path.exists():
        fail(f"box scores {box_path} not found (`fantasy data fetch-boxscores`?)", as_json, EXIT_NOT_FOUND,
             code="not_found")
    box = wr.load_box_scores(box_path, fw.start, fw.end)
    try:
        proj, proj_src = wr.projections_as_of(fw.start, projections)
    except FileNotFoundError as e:
        fail(str(e), as_json, EXIT_NOT_FOUND, code="not_found")
    if not proj_src.get("as_of_week_start", True):
        warnings.append(f"projections: {proj_src.get('note')}")

    nba = load_nba_schedule()
    if nba is not None:
        played = {(t, d) for d, v, h in nba.games for t in (v, h)}
        nba_src = nba.source
    else:
        played = {(str(r["team"]), r["game_date"]) for _, r in box.iterrows()}
        nba_src = "box scores (no cached NBA schedule: future days of the week have no games)"
        warnings.append("NBA schedule not cached: week-start expectation uses box-score team games only")

    def team_games(nba_team: str, d: date) -> bool:
        return (nba_team, d) in played

    ledger: dict[str, Any] | None = None
    pwin: list[dict[str, Any]] = []
    plan_games: dict[str, Any] | None = None
    if not no_ledger:
        ledger = wr.ledger_window(fw.start, fw.end, use_rest=use_rest)
        try:
            from fantasy.lineup import record

            st = record.store(use_rest)
            try:
                rows = st.rows("lineup_plan", limit=500)
            finally:
                st.close()
            pwin = wr.pwin_trajectory(rows, wk, my_slot)
            first = [r for r in rows if (r.get("summary") or {}).get("week") == wk
                     and (r.get("summary") or {}).get("date") == fw.start.isoformat()]
            if first:
                wt = (min(first, key=lambda r: str(r.get("created_at")))["summary"].get("week_totals") or {})
                plan_games = {"me": wt.get("planned_starts"), "opp": wt.get("opponent_planned_starts")}
        except Exception as e:  # noqa: BLE001 -- P(win) is optional context
            warnings.append(f"lineup_plan rows not read ({type(e).__name__}: {e}): no P(win) trajectory")

    slots = load_settings().roster_slots
    start_slots = sum(n for s, n in slots.items() if s not in wr.INACTIVE_SLOTS)
    inp = wr.ReviewInputs(week=wk, start=fw.start, end=fw.end, my_yahoo=my_yahoo, opp_yahoo=opp_yahoo,
                          my_slot=my_slot, opp_slot=opp_slot, names=names, snapshots=snaps, box=box, proj=proj,
                          proj_source=proj_src, team_games=team_games, start_slots=start_slots, captures=captures,
                          ledger=ledger, pwin=pwin, plan_games=plan_games, warnings=warnings, today=today)
    review = wr.build_review(inp)
    inputs = {"box_scores": {"file": wr._rel(box_path), "sha256": sha256_file(box_path)},
              "projections": proj_src, "nba_schedule": nba_src,
              "league_schedule": {"file": wr._rel(sp), "sha256": sha256_file(sp)},
              "matchup_captures": [{"file": c["file"], "sha256": c["sha256"]} for c in captures],
              "lineup_captures": snap_files}
    params = {"command": "review week", "week": wk, "team": my_yahoo, "start_slots": start_slots,
              "ledger": not no_ledger, "rest": use_rest}
    row = wr.review_row(review, inputs=inputs, params=params)
    out: dict[str, Any] = {"ok": True, "run_id": row["run_id"], "review": review}
    text = _text(review)
    if save:
        from fantasy.lineup import record

        try:
            st = record.store(use_rest)
            try:
                out["saved"] = wr.save(row, st)
            finally:
                st.close()
        except OpsError as e:
            fail(e.message, as_json, e.exit_code, code=e.code)
        text += f"\n{'saved' if out['saved']['inserted'] else 'unchanged'}: {row['run_id']} ({out['saved']['store']})"
    emit(out, as_json, text)
