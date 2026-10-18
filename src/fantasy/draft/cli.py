"""`fantasy draft ...` — live draft tooling. All commands print JSON with --json."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from fantasy.draft import store
from fantasy.draft.names import NameResolutionError, resolve_player
from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.order import draft_order, order_for_draft, picks_for_slot
from fantasy.league.settings import LeagueSettings, load_settings
from fantasy.model.lines import load_projections
from fantasy.model.valuation import ValuationParams, run_valuation

app = typer.Typer(no_args_is_help=True, help="Live draft: state, picks, recommendations, simulation.")

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
DraftOpt = Annotated[str | None, typer.Option("--draft-id", envvar="FANTASY_DRAFT_ID", help="Default: latest non-mock draft")]
ProjOpt = Annotated[Path | None, typer.Option("--projections", help="Projections CSV (default data/processed)")]


def _emit(obj: dict, as_json: bool, text: str | None = None) -> None:
    if as_json:
        typer.echo(json.dumps(store._clean(obj), indent=2))
    else:
        typer.echo(text if text is not None else json.dumps(store._clean(obj), indent=2))


def _fail(msg: str, as_json: bool, code: int = 1, **extra: object) -> None:
    if as_json:
        typer.echo(json.dumps({"ok": False, "error": msg, **extra}))
    else:
        typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(code)


def _settings_for(draft: dict | None) -> LeagueSettings:
    s = load_settings()
    if draft:
        s = s.model_copy(update={"num_teams": draft["num_teams"], "my_slot": draft["my_slot"],
                                 "draft_order_type": draft["order_type"]})
    return s


def _load_draft(draft_id: str | None, as_json: bool) -> dict:
    d = store.get_draft(draft_id)
    if not d:
        _fail(f"draft not found: {draft_id or '(none exist)'} — run `fantasy draft init`", as_json)
    assert d is not None
    return d


def _context(
    settings: LeagueSettings, projections: Path | None, draft: dict | None = None
) -> tuple[DraftContext, str]:
    custom = ((draft or {}).get("config") or {}).get("custom_rounds")
    proj = load_projections(projections)
    vp = ValuationParams()
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
    as_json: JsonOpt = False,
) -> None:
    """Create a draft record (idempotent)."""
    s = load_settings()
    row = store.create_draft(draft_id, s, is_mock=mock, my_slot=slot)
    picks = [p.overall for p in order_for_draft(row) if p.slot == row["my_slot"]]
    _emit({"ok": True, "draft": row, "my_picks": picks}, as_json,
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
    _emit({"slot": slot, "order_type": order_type, "picks": picks}, as_json, f"slot {slot} ({order_type}): {picks}")


@app.command("set-order")
def set_order(
    rounds: Annotated[str, typer.Option("--rounds", help="Slot order per round as read on Yahoo: '1,2,..,12;12,..,1;...' "
                                        "or @file.json with [[...], ...]")],
    draft_id: DraftOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Store the exact draft order from Yahoo's draft-order page (overrides the 3RR formula)."""
    d = _load_draft(draft_id, as_json)
    if rounds.startswith("@"):
        custom = json.loads(Path(rounds[1:]).read_text())
    else:
        custom = [[int(x) for x in r.split(",") if x.strip()] for r in rounds.split(";") if r.strip()]
    try:
        draft_order(d["num_teams"], d["num_rounds"], "custom", custom)
    except ValueError as e:
        _fail(str(e), as_json)
    store.set_draft_order(d["draft_id"], custom)
    d = store.get_draft(d["draft_id"])
    mine = [p.overall for p in order_for_draft(d) if p.slot == d["my_slot"]]
    formula = picks_for_slot(d["my_slot"], d["num_teams"], d["num_rounds"], "snake_3rr")
    _emit({"ok": True, "my_picks": mine, "matches_3rr_formula": mine == formula}, as_json,
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
    _emit({"queue": rows}, as_json, "\n".join(f"{r['rank']:>3}. {r['name']} ({r['team']})" for r in rows))


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
    proj = load_projections(projections)
    values = proj.df
    try:
        pid = resolve_player(player, values)
        name = str(values.loc[pid, "name"])
    except NameResolutionError as e:
        if not force:
            _fail(str(e), as_json, code=2, candidates=e.candidates)
        pid, name = f"unknown:{player.strip().lower().replace(' ', '-')}", player.strip()
    taken = {p["overall_pick"] for p in store.get_picks(d["draft_id"])}
    n_total = d["num_teams"] * d["num_rounds"]
    ov = overall or next((k for k in range(1, n_total + 1) if k not in taken), None)
    if ov is None:
        _fail("draft is complete", as_json)
    try:
        row = store.record_pick(d, ov, pid, name, source, force=force)
    except store.PickConflict as e:
        _fail(str(e), as_json, code=2)
    _emit({"ok": True, "pick": row}, as_json, f"#{ov}: {name} ({pid}) [{row['status']}]")


@app.command()
def undo(overall: int, draft_id: DraftOpt = None, as_json: JsonOpt = False) -> None:
    """Remove a recorded pick (to correct mistakes)."""
    d = _load_draft(draft_id, as_json)
    n = store.undo_pick(d["draft_id"], overall)
    _emit({"ok": n > 0, "removed": n}, as_json, f"removed {n} pick(s)")


@app.command()
def board(draft_id: DraftOpt = None, as_json: JsonOpt = False) -> None:
    """Current draft state: picks made, on the clock, my roster and upcoming picks."""
    d = _load_draft(draft_id, as_json)
    picks = store.get_picks(d["draft_id"])
    order_ = order_for_draft(d)
    taken = {p["overall_pick"]: p for p in picks}
    current = (max(taken, default=0) + 1) if (max(taken, default=0) < len(order_)) else None
    mine = [p.overall for p in order_ if p.slot == d["my_slot"]]
    out = {
        "draft_id": d["draft_id"], "status": d["status"], "picks_made": len(picks),
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
        _emit(out, True)
        return
    clock = out["on_the_clock"]
    typer.echo(f"{d['draft_id']} [{d['status']}] picks made {len(picks)}; on the clock: {clock}")
    typer.echo("my roster: " + ", ".join(f"#{p['overall_pick']} {p['player_name']}" for p in out["my_roster"]))
    typer.echo(f"my upcoming picks: {out['my_upcoming']}")
    if out["gaps"]:
        typer.echo(f"WARNING missing picks (not yet recorded): {out['gaps']}")


@app.command()
def recommend(
    draft_id: DraftOpt = None,
    projections: ProjOpt = None,
    strategy: Annotated[str | None, typer.Option("--strategy", help="Force a strategy (e.g. punt_ft); default scans")] = None,
    objective: Annotated[str, typer.Option("--objective", help="cat_wins | matchup")] = "cat_wins",
    top: Annotated[int, typer.Option("--top", help="Recommendations to print in text mode")] = 8,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Store in draft_recommendations")] = True,
    as_json: JsonOpt = False,
) -> None:
    """Recommend my next pick given all recorded picks. Deterministic for a given state."""
    d = _load_draft(draft_id, as_json)
    s = _settings_for(d)
    t0 = time.time()
    ctx, run_id = _context(s, projections, d)
    picks = store.get_picks(d["draft_id"])
    state = DraftState(taken={p["overall_pick"]: p["player_id"] for p in picks}, my_slot=d["my_slot"])
    opt = DraftOptimizer(ctx, OptimizerParams(strategy=strategy, objective=objective))
    rec = opt.recommend(state, draft_id=d["draft_id"], model_run_id=run_id)
    rec["compute_seconds"] = round(time.time() - t0, 2)
    gaps = [p for p in range(1, (max(state.taken) if state.taken else 0)) if p not in state.taken]
    if gaps:
        rec["warnings"].append(f"Picks {gaps} not recorded — availability may be wrong; sync the board first.")
    if save:
        try:
            rec["rec_id"] = store.save_recommendation(rec)
        except Exception as e:
            rec["warnings"].append(f"could not save recommendation: {e}")
    if as_json:
        _emit(rec, True)
        return
    typer.echo(f"Pick #{rec['for_pick']} (on the clock: #{rec.get('on_the_clock', {}).get('overall')}); "
               f"strategy {rec['strategy']['name']} — {rec['strategy']['notes']}")
    for i, r in enumerate(rec["recommendations"][:top], 1):
        typer.echo(f"{i:>2}. {r['name']:<26} {'/'.join(r['positions']):<9} score {r['score']:.3f} "
                   f"({r['marginal_value']:+.3f})  avail@next {r['prob_available_next']:.0%}  — {r['reason']}")
    for w in rec["warnings"]:
        typer.echo(f"WARNING: {w}")


@app.command()
def watch(
    draft_id: DraftOpt = None,
    projections: ProjOpt = None,
    interval: Annotated[float, typer.Option("--interval")] = 3.0,
) -> None:
    """Poll for new picks (e.g. entered on the dashboard) and publish a fresh recommendation each time."""
    d = _load_draft(draft_id, False)
    s = _settings_for(d)
    ctx, run_id = _context(s, projections, d)
    opt = DraftOptimizer(ctx)
    last: tuple | None = None
    typer.echo(f"watching {d['draft_id']} every {interval}s (Ctrl-C to stop)")
    while True:
        picks = store.get_picks(d["draft_id"])
        key = tuple((p["overall_pick"], p["player_id"]) for p in picks)
        if key != last:
            state = DraftState(taken={p["overall_pick"]: p["player_id"] for p in picks}, my_slot=d["my_slot"])
            rec = opt.recommend(state, draft_id=d["draft_id"], model_run_id=run_id)
            store.save_recommendation(rec)
            top = rec["recommendations"][0]["name"] if rec["recommendations"] else "-"
            typer.echo(f"[{time.strftime('%H:%M:%S')}] {len(picks)} picks → for #{rec.get('for_pick')}: {top}")
            last = key
            if rec.get("for_pick") is None:
                typer.echo("draft complete for my slot")
                return
        time.sleep(interval)


@app.command()
def simulate(
    sims: Annotated[int, typer.Option("--sims")] = 5,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    slot: Annotated[int | None, typer.Option("--slot")] = None,
    noise: Annotated[float, typer.Option("--noise", help="Opponent ADP noise multiplier")] = 1.0,
    strategy: Annotated[str | None, typer.Option("--strategy")] = None,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Monte Carlo mock drafts: opponents draft by noisy ADP, I draft with the optimizer."""
    from fantasy.draft.simulate import simulate_drafts

    s = load_settings()
    if slot:
        s = s.model_copy(update={"my_slot": slot})
    proj = load_projections(projections)
    ctx = DraftContext(proj, s)
    res = simulate_drafts(ctx, n_sims=sims, seed=seed, noise=noise, strategy=strategy)
    if as_json:
        _emit(res, True)
        return
    typer.echo(json.dumps(store._clean(res["summary"]), indent=2))


RunOpt = Annotated[str | None, typer.Option("--run-id", envvar="FANTASY_RUN_ID", help="Agent run id")]


@app.command("prepare-pick")
def prepare_pick(
    player: Annotated[str, typer.Argument(help="Player to draft (name or player_id)")],
    overall: Annotated[int, typer.Option("--overall", help="My overall pick number")],
    rationale: Annotated[str, typer.Option("--rationale")] = "",
    draft_id: DraftOpt = None,
    run_id: RunOpt = None,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """One call before clicking Draft: log decision vs latest recommendation, plan the txn, mark it submitted.

    Exit 0 with action=CLICK → click Draft in Yahoo now. Exit 4 → NOT allowed (dry_run, no lock): do not click.
    Exit 7 → this pick was already submitted: verify on Yahoo instead of clicking again.
    """
    from fantasy.ops import ledger, transactions
    from fantasy.ops.common import DuplicateError, OpsError

    if not run_id:
        _fail("no run id (set FANTASY_RUN_ID)", as_json, code=2)
    d = _load_draft(draft_id, as_json)
    values = load_projections(projections).df
    try:
        pid = resolve_player(player, values)
    except NameResolutionError as e:
        _fail(str(e), as_json, code=2, candidates=e.candidates)
    name = str(values.loc[pid, "name"])
    rec = store.latest_recommendation(d["draft_id"]) or {}
    recs = rec.get("recommendations") or []
    top = {k: recs[0].get(k) for k in ("player_id", "name", "score")} if recs else None
    rank_in_recs = next((i for i, r in enumerate(recs) if r.get("player_id") == pid), None)
    warnings = []
    if rec.get("for_pick") not in (None, overall):
        warnings.append(f"latest recommendation was for pick {rec.get('for_pick')}, not {overall}")
    if rank_in_recs is None:
        warnings.append("chosen player is not in the latest recommendation list")
    try:
        dec = ledger.record_decision(run_id, "draft_pick", top, {"player_id": pid, "name": name, "overall": overall},
                                     rationale or None)
        payload = {"draft_id": d["draft_id"], "overall_pick": overall, "player_id": pid}
        try:
            txn = transactions.plan("draft_pick", payload, run_id=run_id, summary=f"Draft {name} at #{overall}")
        except DuplicateError as e:
            txn = e.details.get("existing") or {}
            if txn.get("status") != "planned":
                raise
        txn = transactions.mark_submitted(txn["txn_id"], run_id)
    except OpsError as e:
        if as_json:
            typer.echo(json.dumps({**e.to_dict(), "action": "DO_NOT_CLICK"}, default=str))
        else:
            typer.echo(f"DO NOT CLICK: {e.message}", err=True)
        raise typer.Exit(e.exit_code) from e
    out = {"ok": True, "action": "CLICK", "player_id": pid, "name": name, "team": _str(values.loc[pid, "team"]),
           "overall": overall, "txn_id": txn["txn_id"], "agrees_with_model": dec.get("agrees_with_model"),
           "rank_in_recommendations": None if rank_in_recs is None else rank_in_recs + 1, "warnings": warnings}
    _emit(out, as_json, f"CLICK DRAFT: {name} ({out['team']}) at #{overall}  txn={txn['txn_id']}  "
                        f"agrees_with_model={out['agrees_with_model']} {warnings or ''}")


@app.command("confirm-pick")
def confirm_pick(
    txn_id: Annotated[str, typer.Argument()],
    evidence: Annotated[str, typer.Option("--evidence", help="What on Yahoo shows the pick (text or JSON)")] = "",
    failed: Annotated[str | None, typer.Option("--failed", help="Mark failed with this reason instead")] = None,
    run_id: RunOpt = None,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """After clicking: verify (records the pick on the board) or mark failed. Never click twice."""
    from fantasy.ops import transactions
    from fantasy.ops.common import OpsError

    try:
        txn = transactions.get_txn(txn_id)
        if failed:
            txn = transactions.mark_failed(txn_id, failed, run_id=run_id)
            _emit({"ok": True, "txn": txn}, as_json, f"marked failed: {failed}")
            return
        if not evidence:
            _fail("--evidence is required (e.g. 'draft log shows pick 24 = X for my team')", as_json, code=2)
        try:
            ev = json.loads(evidence)
        except json.JSONDecodeError:
            ev = {"note": evidence}
        txn = transactions.mark_verified(txn_id, ev, run_id=run_id)
    except OpsError as e:
        typer.echo(json.dumps(e.to_dict(), default=str) if as_json else f"error: {e.message}")
        raise typer.Exit(e.exit_code) from e
    p = txn["payload"]
    d = store.get_draft(p["draft_id"])
    values = load_projections(projections).df
    name = str(values.loc[p["player_id"], "name"]) if p["player_id"] in values.index else p["player_id"]
    row = store.record_pick(d, p["overall_pick"], p["player_id"], name, "agent", force=False)
    _emit({"ok": True, "txn": txn, "pick": row}, as_json, f"verified #{p['overall_pick']} {name}")


def _str(x: object) -> str:
    return "" if x is None or x != x else str(x)
