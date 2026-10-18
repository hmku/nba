"""`fantasy draft ...` — live draft tooling. All commands print JSON with --json.

This module holds the Typer app, shared options/helpers and the board commands (init, order, set-order,
queue, pick, undo, board). Model commands (recommend, watch, simulate) live in `cli_recommend`, the
transactional pick flow (prepare-pick, confirm-pick) in `cli_pick`; both register on `app` when imported
at the bottom of this file, in this order, so `fantasy draft --help` is unchanged."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Annotated

import typer

from fantasy.cli_common import emit, fail
from fantasy.draft import store
from fantasy.draft.names import NameResolutionError, resolve_player
from fantasy.draft.optimizer import DraftContext
from fantasy.draft.order import draft_order, order_for_draft, picks_for_slot
from fantasy.league.settings import LeagueSettings, load_settings
from fantasy.model.lines import load_projections
from fantasy.model.valuation import ValuationParams, run_valuation

app = typer.Typer(no_args_is_help=True, help="Live draft: state, picks, recommendations, simulation.")

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
DraftOpt = Annotated[str | None, typer.Option("--draft-id", envvar="FANTASY_DRAFT_ID", help="Default: latest non-mock draft")]
ProjOpt = Annotated[Path | None, typer.Option("--projections", help="Projections CSV (default data/processed)")]

REAL_DRAFT_ID = re.compile(r"nba\.l\.\d+-\d{4}")
OBJECTIVES = ("cat_wins", "matchup")


def _settings_for(draft: dict | None) -> LeagueSettings:
    s = load_settings()
    if draft:
        s = s.model_copy(update={"num_teams": draft["num_teams"], "my_slot": draft["my_slot"],
                                 "draft_order_type": draft["order_type"]})
    return s


def _load_draft(draft_id: str | None, as_json: bool) -> dict:
    d = store.get_draft(draft_id)
    if not d:
        fail(f"draft not found: {draft_id or '(none exist)'} — run `fantasy draft init`", as_json, code="not_found")
    assert d is not None
    return d


def _context(
    settings: LeagueSettings, projections: Path | None, draft: dict | None = None,
    vp: ValuationParams | None = None,
) -> tuple[DraftContext, str]:
    custom = ((draft or {}).get("config") or {}).get("custom_rounds")
    proj = load_projections(projections)
    vp = vp or ValuationParams()
    run_id = None
    try:
        from dataclasses import asdict

        params = {**asdict(vp), "categories": settings.categories}
        run_id = store.find_valuation_run(proj.sha256, params)
        if run_id is None:
            run = run_valuation(proj, settings, vp)
            run_id = store.save_valuation(run, proj.df)
            ctx = DraftContext(proj, settings, vp, values=run.values, custom_rounds=custom)
            return ctx, run_id
    except Exception as e:  # DB trouble must never block a live pick
        print(f"warning: could not record valuation run: {e}", file=sys.stderr)
    return DraftContext(proj, settings, vp, custom_rounds=custom), run_id or "unsaved"


@app.command()
def init(
    draft_id: Annotated[str, typer.Option("--draft-id", help="e.g. nba.l.44495-2026 or mock-001")],
    mock: Annotated[bool, typer.Option("--mock")] = False,
    slot: Annotated[int | None, typer.Option("--slot", help="My draft slot (default from settings)")] = None,
    force: Annotated[bool, typer.Option("--force", help="Allow a non-mock id not shaped like nba.l.<league>-<year>")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Create a draft record (idempotent).

    Non-mock drafts become the default for every draft command, so: ids starting with `mock` need
    --mock (exit 2), and non-mock ids must look like `nba.l.<digits>-<year>` unless --force."""
    if not mock and draft_id.lower().startswith("mock"):
        fail(f"draft id {draft_id!r} looks like a mock: pass --mock (a non-mock draft becomes the default "
             "for every draft command)", as_json, exit_code=2, draft_id=draft_id)
    if not mock and not REAL_DRAFT_ID.fullmatch(draft_id) and not force:
        fail(f"non-mock draft id {draft_id!r} must match nba.l.<digits>-<year> (e.g. nba.l.44495-2026); "
             "use --mock for rehearsals or --force", as_json, exit_code=2, draft_id=draft_id)
    s = load_settings()
    row = store.create_draft(draft_id, s, is_mock=mock, my_slot=slot)
    picks = [p.overall for p in order_for_draft(row) if p.slot == row["my_slot"]]
    warn = []
    if bool(row["is_mock"]) != mock:
        warn.append(f"draft {draft_id} already exists with is_mock={bool(row['is_mock'])}; --mock={mock} ignored")
    emit({"ok": True, "draft_id": draft_id, "draft": row, "my_picks": picks, "warnings": warn}, as_json,
         f"draft {draft_id} ({row['order_type']}, {row['num_teams']} teams × {row['num_rounds']} rounds); my picks: {picks}")


@app.command()
def order(
    slot: Annotated[int, typer.Option("--slot")] = 1,
    teams: Annotated[int, typer.Option("--teams")] = 12,
    rounds: Annotated[int, typer.Option("--rounds")] = 13,
    order_type: Annotated[str, typer.Option("--type")] = "snake_3rr",
    as_json: JsonOpt = False,
) -> None:
    """Show pick numbers for a slot under an order type (snake | snake_3rr)."""
    picks = picks_for_slot(slot, teams, rounds, order_type)
    emit({"ok": True, "draft_id": None, "slot": slot, "order_type": order_type, "picks": picks}, as_json,
         f"slot {slot} ({order_type}): {picks}")


@app.command("set-order")
def set_order(
    rounds: Annotated[str, typer.Option("--rounds", help="Slot order per round as read on Yahoo: '1,2,..,12;12,..,1;...' "
                                        "or @file.json with [[...], ...]")],
    draft_id: DraftOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Store the exact draft order from Yahoo's draft-order page (overrides the 3RR formula)."""
    d = _load_draft(draft_id, as_json)
    try:
        if rounds.startswith("@"):
            custom = json.loads(Path(rounds[1:]).read_text())
        else:
            custom = [[int(x) for x in r.split(",") if x.strip()] for r in rounds.split(";") if r.strip()]
        draft_order(d["num_teams"], d["num_rounds"], "custom", custom)
    except (ValueError, TypeError, OSError) as e:
        fail(f"invalid --rounds: {e}", as_json, exit_code=2, draft_id=d["draft_id"])
    store.set_draft_order(d["draft_id"], custom)
    d = store.get_draft(d["draft_id"])
    mine = [p.overall for p in order_for_draft(d) if p.slot == d["my_slot"]]
    formula = picks_for_slot(d["my_slot"], d["num_teams"], d["num_rounds"], "snake_3rr")
    emit({"ok": True, "draft_id": d["draft_id"], "my_picks": mine, "matches_3rr_formula": mine == formula}, as_json,
         f"order saved; my picks: {mine} ({'matches' if mine == formula else 'DIFFERS FROM'} 3RR formula)")


@app.command()
def queue(
    top: Annotated[int, typer.Option("--top")] = 60,
    draft_id: DraftOpt = None,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Ranked list of available players for Yahoo's Pre-Draft Rankings / draft queue (autopick safety net)."""
    d = store.get_draft(draft_id)
    s = _settings_for(d)
    proj = load_projections(projections)
    from fantasy.model.valuation import compute_values

    values, _, _ = compute_values(proj, s)
    taken = {p["player_id"] for p in store.get_picks(d["draft_id"])} if d else set()
    v = values[~values.index.isin(taken)].head(top)
    rows = [{"rank": i, "player_id": pid, "name": r["name"], "team": r["team"], "positions": list(r["positions"])}
            for i, (pid, r) in enumerate(v.iterrows(), 1)]
    emit({"ok": True, "draft_id": d["draft_id"] if d else None, "queue": rows}, as_json, "\n".join(f"{r['rank']:>3}. {r['name']} ({r['team']})" for r in rows))


@app.command()
def pick(
    player: Annotated[str, typer.Argument(help="Player name as shown on Yahoo, or player_id")],
    overall: Annotated[int | None, typer.Option("--overall", help="Overall pick number (default: next open pick)")] = None,
    source: Annotated[str, typer.Option("--source", help="yahoo_sync|browser|manual|agent")] = "manual",
    force: Annotated[bool, typer.Option("--force", help="Overwrite a conflicting pick")] = False,
    draft_id: DraftOpt = None,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Record an observed pick. Idempotent; refuses conflicts unless --force (exit 2)."""
    d = _load_draft(draft_id, as_json)
    n_total = d["num_teams"] * d["num_rounds"]
    if overall is not None and not 1 <= overall <= n_total:
        fail(f"invalid --overall {overall}; valid: 1..{n_total}", as_json, exit_code=2, draft_id=d["draft_id"],
             valid_range=[1, n_total])
    proj = load_projections(projections)
    values = proj.df
    try:
        pid = resolve_player(player, values)
        name = str(values.loc[pid, "name"])
    except NameResolutionError as e:
        if not force:
            fail(str(e), as_json, exit_code=2, code="unresolved_player", candidates=e.candidates)
        pid, name = f"unknown:{player.strip().lower().replace(' ', '-')}", player.strip()
    taken = {p["overall_pick"] for p in store.get_picks(d["draft_id"])}
    ov = overall if overall is not None else next((k for k in range(1, n_total + 1) if k not in taken), None)
    if ov is None:
        fail("draft is complete", as_json, code="draft_complete", draft_id=d["draft_id"])
    try:
        row = store.record_pick(d, ov, pid, name, source, force=force)
    except store.PickConflict as e:
        fail(str(e), as_json, exit_code=2, code="conflict")
    emit({"ok": True, "draft_id": d["draft_id"], "pick": row}, as_json, f"#{ov}: {name} ({pid}) [{row['status']}]")


@app.command()
def undo(overall: int, draft_id: DraftOpt = None, as_json: JsonOpt = False) -> None:
    """Remove a recorded pick (to correct mistakes)."""
    d = _load_draft(draft_id, as_json)
    n = store.undo_pick(d["draft_id"], overall)
    emit({"ok": n > 0, "draft_id": d["draft_id"], "removed": n}, as_json, f"removed {n} pick(s)")


@app.command()
def board(draft_id: DraftOpt = None, as_json: JsonOpt = False) -> None:
    """Current draft state: picks made, on the clock, my roster and upcoming picks."""
    d = _load_draft(draft_id, as_json)
    picks = store.get_picks(d["draft_id"])
    try:  # heal rows recorded before the lifecycle fix (status stayed 'live' on a full board); never block
        d["status"] = store.sync_draft_status(d["draft_id"]) or d["status"]
    except Exception as e:  # noqa: BLE001
        print(f"warning: could not sync draft status: {e}", file=sys.stderr)
    order_ = order_for_draft(d)
    taken = {p["overall_pick"]: p for p in picks}
    current = (max(taken, default=0) + 1) if (max(taken, default=0) < len(order_)) else None
    mine = [p.overall for p in order_ if p.slot == d["my_slot"]]
    out = {
        "ok": True, "draft_id": d["draft_id"], "status": d["status"], "picks_made": len(picks),
        "on_the_clock": None if current is None else {"overall": current, "slot": order_[current - 1].slot,
                                                      "round": order_[current - 1].round,
                                                      "is_me": order_[current - 1].slot == d["my_slot"]},
        "my_slot": d["my_slot"], "my_picks": mine,
        "my_upcoming": [p for p in mine if p not in taken],
        "my_roster": [taken[p] for p in mine if p in taken],
        "recent_picks": picks[-12:],
        "gaps": [p for p in range(1, (max(taken) if taken else 0)) if p not in taken],
    }
    if as_json:
        emit(out, True)
        return
    clock = out["on_the_clock"]
    typer.echo(f"{d['draft_id']} [{d['status']}] picks made {len(picks)}; on the clock: {clock}")
    typer.echo("my roster: " + ", ".join(f"#{p['overall_pick']} {p['player_name']}" for p in out["my_roster"]))
    typer.echo(f"my upcoming picks: {out['my_upcoming']}")
    if out["gaps"]:
        typer.echo(f"WARNING missing picks (not yet recorded): {out['gaps']}")


# Register the remaining commands on `app` (order = help order). Imported last: they import this module.
from fantasy.draft import cli_recommend  # noqa: E402,F401,I001  (side effect: commands)
from fantasy.draft import cli_pick  # noqa: E402,F401,I001
