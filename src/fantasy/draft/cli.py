"""`fantasy draft ...` — live draft tooling. All commands print JSON with --json."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from fantasy.draft import store
from fantasy.draft.names import NameResolutionError, resolve_player
from fantasy.draft.optimizer import (
    DEFAULT_STRATEGIES,
    DraftContext,
    DraftOptimizer,
    DraftState,
    OptimizerParams,
)
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


def _check_choice(value: str | None, valid: list[str] | tuple[str, ...], what: str, as_json: bool) -> None:
    """Exit 2 with the list of valid values if `value` is not one of them (None = not given)."""
    if value is not None and value not in valid:
        _fail(f"invalid {what} {value!r}; valid: {', '.join(valid)}", as_json, code=2, valid=list(valid))


def parse_punt(punt: str | None, categories: list[str], as_json: bool) -> list[str]:
    """Comma-separated category keys; exit 2 listing valid keys on anything unknown."""
    cats = [p.strip() for p in (punt or "").split(",") if p.strip()]
    bad = [c for c in cats if c not in categories]
    if bad:
        _fail(f"invalid punt categor{'y' if len(bad) == 1 else 'ies'} {', '.join(bad)}; "
              f"valid: {', '.join(categories)}", as_json, code=2, valid=list(categories))
    return cats


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
        _fail(f"draft id {draft_id!r} looks like a mock: pass --mock (a non-mock draft becomes the default "
              "for every draft command)", as_json, code=2, draft_id=draft_id)
    if not mock and not REAL_DRAFT_ID.fullmatch(draft_id) and not force:
        _fail(f"non-mock draft id {draft_id!r} must match nba.l.<digits>-<year> (e.g. nba.l.44495-2026); "
              "use --mock for rehearsals or --force", as_json, code=2, draft_id=draft_id)
    s = load_settings()
    row = store.create_draft(draft_id, s, is_mock=mock, my_slot=slot)
    picks = [p.overall for p in order_for_draft(row) if p.slot == row["my_slot"]]
    warn = []
    if bool(row["is_mock"]) != mock:
        warn.append(f"draft {draft_id} already exists with is_mock={bool(row['is_mock'])}; --mock={mock} ignored")
    _emit({"ok": True, "draft_id": draft_id, "draft": row, "my_picks": picks, "warnings": warn}, as_json,
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
    _emit({"ok": True, "draft_id": None, "slot": slot, "order_type": order_type, "picks": picks}, as_json,
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
        _fail(f"invalid --rounds: {e}", as_json, code=2, draft_id=d["draft_id"])
    store.set_draft_order(d["draft_id"], custom)
    d = store.get_draft(d["draft_id"])
    mine = [p.overall for p in order_for_draft(d) if p.slot == d["my_slot"]]
    formula = picks_for_slot(d["my_slot"], d["num_teams"], d["num_rounds"], "snake_3rr")
    _emit({"ok": True, "draft_id": d["draft_id"], "my_picks": mine, "matches_3rr_formula": mine == formula}, as_json,
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
    _emit({"ok": True, "draft_id": d["draft_id"] if d else None, "queue": rows}, as_json, "\n".join(f"{r['rank']:>3}. {r['name']} ({r['team']})" for r in rows))


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
    _emit({"ok": True, "draft_id": d["draft_id"], "pick": row}, as_json, f"#{ov}: {name} ({pid}) [{row['status']}]")


@app.command()
def undo(overall: int, draft_id: DraftOpt = None, as_json: JsonOpt = False) -> None:
    """Remove a recorded pick (to correct mistakes)."""
    d = _load_draft(draft_id, as_json)
    n = store.undo_pick(d["draft_id"], overall)
    _emit({"ok": n > 0, "draft_id": d["draft_id"], "removed": n}, as_json, f"removed {n} pick(s)")


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
    kappa: Annotated[float, typer.Option("--kappa", help="Valuation: 0=z-scores, 1=G-scores")] = 1.0,
    punt: Annotated[str, typer.Option("--punt", help="Valuation: comma-separated categories to zero-weight")] = "",
    top: Annotated[int, typer.Option("--top", help="Recommendations to print in text mode")] = 8,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Store in draft_recommendations")] = True,
    as_json: JsonOpt = False,
) -> None:
    """Recommend my next pick given all recorded picks. Deterministic for a given state."""
    _check_choice(strategy, list(DEFAULT_STRATEGIES), "--strategy", as_json)
    _check_choice(objective, OBJECTIVES, "--objective", as_json)
    d = _load_draft(draft_id, as_json)
    s = _settings_for(d)
    vp = ValuationParams(kappa=kappa, punt=parse_punt(punt, s.categories, as_json))
    t0 = time.time()
    ctx, run_id = _context(s, projections, d, vp)
    picks = store.get_picks(d["draft_id"])
    state = DraftState(taken={p["overall_pick"]: p["player_id"] for p in picks}, my_slot=d["my_slot"])
    opt = DraftOptimizer(ctx, OptimizerParams(strategy=strategy, objective=objective))
    rec = opt.recommend(state, draft_id=d["draft_id"], model_run_id=run_id)
    rec["compute_seconds"] = round(time.time() - t0, 2)
    rec["ok"] = True
    rec["valuation_params"] = {"kappa": vp.kappa, "punt": vp.punt}
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
    draft_id: Annotated[str | None, typer.Option(
        "--draft-id", envvar="FANTASY_DRAFT_ID",
        help="Use this draft's teams/slot and custom order (set-order); default: settings + formula order")] = None,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Record a model_runs row (kind draft_sim)")] = True,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Monte Carlo mock drafts: opponents draft by noisy ADP, I draft with the optimizer."""
    from fantasy.draft.simulate import save_draft_sim, simulate_drafts

    _check_choice(strategy, list(DEFAULT_STRATEGIES), "--strategy", as_json)
    d = _load_draft(draft_id, as_json) if draft_id else None
    s = _settings_for(d)
    if slot:
        s = s.model_copy(update={"my_slot": slot})
    custom = ((d or {}).get("config") or {}).get("custom_rounds")
    proj = load_projections(projections)
    ctx = DraftContext(proj, s, custom_rounds=custom)
    res = simulate_drafts(ctx, n_sims=sims, seed=seed, noise=noise, strategy=strategy)
    run_id = None
    if save:
        try:
            run_id = save_draft_sim(
                res,
                {"n_sims": sims, "seed": seed, "noise": noise, "strategy": strategy, "my_slot": s.my_slot},
                {"projections_path": proj.path, "projections_sha256": proj.sha256,
                 "draft_id": d["draft_id"] if d else None, "order_type": s.draft_order_type,
                 "custom_rounds": custom, "num_teams": s.num_teams, "my_picks": res["summary"]["my_picks"]})
        except Exception as e:  # recording must never block the answer
            print(f"warning: could not record draft_sim run: {e}", file=sys.stderr)
    res = {"ok": True, "run_id": run_id, "draft_id": d["draft_id"] if d else None,
           "order_type": s.draft_order_type, "custom_order": bool(custom), **res}
    if as_json:
        _emit(res, True)
        return
    sm = res["summary"]
    head = {k: sm[k] for k in ("n_sims", "my_slot", "optimizer", "baseline_bpa", "edge", "first_pick_counts",
                               "strategy_counts")}
    typer.echo(json.dumps(store._clean({**head, "run_id": run_id}), indent=2))


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
    """One call before clicking Draft: check the board, log decision vs latest recommendation, plan the txn,
    mark it submitted.

    Exit 0 with action=CLICK → click Draft in Yahoo now. Exit 4 → NOT allowed (dry_run, no lock, not my pick,
    pick already on the board, player already drafted): do not click. Exit 7 → this pick already has a
    submitted/verified txn: verify on Yahoo instead of clicking again. A failed/cancelled earlier attempt is
    retried automatically (next attempt number); a `planned` txn left by another run is re-owned.
    """
    from fantasy.ops import ledger, transactions
    from fantasy.ops.common import OpsError

    if not run_id:
        _fail("no run id (set FANTASY_RUN_ID)", as_json, code=2)
    d = _load_draft(draft_id, as_json)
    did = d["draft_id"]
    values = load_projections(projections).df
    try:
        pid = resolve_player(player, values)
    except NameResolutionError as e:
        _fail(str(e), as_json, code=2, candidates=e.candidates, draft_id=did)
    name = str(values.loc[pid, "name"])

    def refuse(msg: str, code: int = 4, **extra: object) -> None:
        if as_json:
            typer.echo(json.dumps(store._clean({"ok": False, "error": "not_allowed", "message": msg,
                                                "action": "DO_NOT_CLICK", "draft_id": did, **extra})))
        else:
            typer.echo(f"DO NOT CLICK: {msg}", err=True)
        raise typer.Exit(code)

    picks = store.get_picks(did)
    taken = {p["overall_pick"]: p for p in picks}
    mine = [p.overall for p in order_for_draft(d) if p.slot == d["my_slot"]]
    if overall not in mine:
        refuse(f"pick {overall} is not my pick (slot {d['my_slot']}); my picks: {mine}", my_picks=mine)
    if overall in taken:
        refuse(f"pick {overall} is already recorded on the board: {taken[overall]['player_name']}",
               pick=taken[overall])
    already = next((p for p in picks if p["player_id"] == pid), None)
    if already:
        refuse(f"{name} is already recorded as drafted at pick {already['overall_pick']}", pick=already)

    rec = store.latest_recommendation(did) or {}
    recs = rec.get("recommendations") or []
    top = {k: recs[0].get(k) for k in ("player_id", "name", "score")} if recs else None
    rank_in_recs = next((i for i, r in enumerate(recs) if r.get("player_id") == pid), None)
    warnings = []
    gaps = [k for k in range(1, max(taken, default=0)) if k not in taken]
    if gaps:
        warnings.append(f"Picks {gaps} not recorded — availability may be wrong; sync the board first.")
    if rec.get("for_pick") not in (None, overall):
        warnings.append(f"latest recommendation was for pick {rec.get('for_pick')}, not {overall}")
    if rank_in_recs is None:
        warnings.append("chosen player is not in the latest recommendation list")
    mode = None
    try:
        mode = ledger.get_run(run_id)["mode"]
        choice = {"player_id": pid, "name": name, "overall": overall, "mode": mode}
        dec = ledger.record_decision(run_id, "draft_pick", top, choice, rationale or None)
        payload = {"draft_id": did, "overall_pick": overall, "player_id": pid}
        txn, txn_note = _txn_for_pick(did, overall, payload, run_id, f"Draft {name} at #{overall}")
        txn = transactions.mark_submitted(txn["txn_id"], run_id)
    except OpsError as e:
        if as_json:
            typer.echo(json.dumps({**e.to_dict(), "action": "DO_NOT_CLICK", "draft_id": did, "run_mode": mode,
                                   "gaps": gaps, "warnings": warnings}, default=str))
        else:
            typer.echo(f"DO NOT CLICK: {e.message}", err=True)
        raise typer.Exit(e.exit_code) from e
    out = {"ok": True, "action": "CLICK", "draft_id": did, "player_id": pid, "name": name,
           "team": _str(values.loc[pid, "team"]), "overall": overall, "txn_id": txn["txn_id"],
           "attempt": transactions.attempt_of(txn), "txn_note": txn_note, "run_mode": mode,
           "agrees_with_model": dec.get("agrees_with_model"),
           "rank_in_recommendations": None if rank_in_recs is None else rank_in_recs + 1,
           "gaps": gaps, "warnings": warnings}
    _emit(out, as_json, f"CLICK DRAFT: {name} ({out['team']}) at #{overall}  txn={txn['txn_id']}  "
                        f"agrees_with_model={out['agrees_with_model']} {warnings or ''}")


def _txn_for_pick(draft_id: str, overall: int, payload: dict, run_id: str, summary: str) -> tuple[dict, str]:
    """The txn to submit for (draft_id, overall).

    submitted/verified exists → InvalidStateError (exit 7: never click twice). A planned txn for the same
    player is reused (re-owned if another run, e.g. a dry run, left it); planned txns for other players are
    cancelled. Otherwise plan a new txn with the next attempt number (automatic retry after failed/cancelled).
    """
    from fantasy.ops import transactions
    from fantasy.ops.common import InvalidStateError

    existing = transactions.draft_pick_txns(draft_id, overall)
    done = [t for t in existing if t["status"] in ("submitted", "verified")]
    if done:
        t = done[-1]
        raise InvalidStateError(
            f"pick {overall} already has a {t['status']} transaction {t['txn_id']}: verify on Yahoo and "
            "reconcile with `fantasy draft confirm-pick`, never click again", existing=t)
    reuse, note = None, ""
    for t in existing:
        if t["status"] not in ("planned", "awaiting_approval"):
            continue
        if reuse is None and t["payload"] == payload:
            if t["run_id"] == run_id:
                reuse, note = t, "reused"
            else:
                reuse, note = transactions.reassign(t["txn_id"], run_id), f"re-owned from {t['run_id']}"
        else:
            transactions.cancel(t["txn_id"], f"superseded by prepare-pick in run {run_id}")
    if reuse is not None:
        return reuse, note
    attempt = max((transactions.attempt_of(t) for t in existing), default=0) + 1
    txn = transactions.plan("draft_pick", payload, run_id=run_id, attempt=attempt, summary=summary)
    return txn, ("new" if attempt == 1 else f"retry (attempt {attempt})")


@app.command("confirm-pick")
def confirm_pick(
    txn_id: Annotated[str, typer.Argument()],
    evidence: Annotated[str, typer.Option("--evidence", help="What on Yahoo shows the pick (text or JSON)")] = "",
    failed: Annotated[str | None, typer.Option("--failed", help="Mark failed with this reason instead")] = None,
    run_id: RunOpt = None,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """After clicking: verify (records the pick on the board) or mark failed. Never click twice.

    Idempotent: on a txn that is already `verified` (e.g. via `txn mark verified`) it just records the
    pick on the board and exits 0."""
    from fantasy.ops import transactions
    from fantasy.ops.common import OpsError

    already_verified = False
    try:
        txn = transactions.get_txn(txn_id)
        if txn["kind"] != "draft_pick":
            _fail(f"{txn_id} is a {txn['kind']} transaction, not draft_pick; use `fantasy txn mark`", as_json, code=2)
        did = (txn["payload"] or {}).get("draft_id")
        if failed:
            txn = transactions.mark_failed(txn_id, failed, run_id=run_id)
            _emit({"ok": True, "draft_id": did, "txn": txn}, as_json, f"marked failed: {failed}")
            return
        if txn["status"] == "verified":
            already_verified = True
        else:
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
    if d is None:
        _fail(f"draft {p['draft_id']} not found", as_json, code=6)
    values = load_projections(projections).df
    name = str(values.loc[p["player_id"], "name"]) if p["player_id"] in values.index else p["player_id"]
    try:
        row = store.record_pick(d, p["overall_pick"], p["player_id"], name, "agent", force=False)
    except store.PickConflict as e:
        _fail(f"txn verified but the board disagrees: {e} — fix with `draft undo`/`draft pick`", as_json,
              code=2, draft_id=d["draft_id"], txn=txn)
    _emit({"ok": True, "draft_id": d["draft_id"], "txn": txn, "pick": row, "already_verified": already_verified},
          as_json, f"verified #{p['overall_pick']} {name}" + (" (txn was already verified)" if already_verified else ""))


def _str(x: object) -> str:
    return "" if x is None or x != x else str(x)
