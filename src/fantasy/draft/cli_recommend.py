"""`fantasy draft recommend|watch|simulate` — optimizer-backed commands (registered on `fantasy.draft.cli.app`)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from fantasy.cli_common import check_choice, dumps, emit, parse_punt
from fantasy.draft import store
from fantasy.draft.cli import (
    OBJECTIVES,
    DraftOpt,
    JsonOpt,
    ProjOpt,
    _context,
    _load_draft,
    _settings_for,
    app,
)
from fantasy.draft.optimizer import (
    DEFAULT_STRATEGIES,
    DraftContext,
    DraftOptimizer,
    DraftState,
    OptimizerParams,
)
from fantasy.model.lines import load_projections
from fantasy.model.valuation import ValuationParams


@app.command()
def recommend(
    draft_id: DraftOpt = None,
    projections: ProjOpt = None,
    strategy: Annotated[str | None, typer.Option("--strategy", help="Force a strategy (e.g. punt_ft); default scans")] = None,
    objective: Annotated[str, typer.Option("--objective", help="Optimizer: cat_wins | matchup. Value search: "
                                           "blend (default) | title | playoffs | rank")] = "cat_wins",
    kappa: Annotated[float, typer.Option("--kappa", help="Valuation: 0=z-scores, 1=G-scores")] = 1.0,
    punt: Annotated[str, typer.Option("--punt", help="Valuation: comma-separated categories to zero-weight")] = "",
    top: Annotated[int, typer.Option("--top", help="Recommendations to print in text mode")] = 8,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Store in draft_recommendations")] = True,
    search: Annotated[str | None, typer.Option("--search", help="none | advise (add an advisory value_search block, "
                                               "optimizer order kept) | value (value search picks the top). "
                                               "Default: see fantasy.valuesearch.search.DEFAULT_SEARCH")] = None,
    rollouts: Annotated[int | None, typer.Option("--rollouts", help="Value search: rollouts per candidate "
                                                 "(default 48; 24 with --opponents ours, which is ~10x slower)")] = None,
    search_candidates: Annotated[int, typer.Option("--search-candidates", help="Value search: top-N optimizer candidates")] = 6,
    opponents: Annotated[str, typer.Option("--opponents", help="Value search opponent model: adp | ours")] = "adp",
    value_model: Annotated[Path | None, typer.Option("--value-model", help="Value model artifact (.npz)")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Recommend my next pick given all recorded picks. Deterministic for a given state."""
    from fantasy.valuesearch.model import OBJECTIVES as VALUE_OBJECTIVES
    from fantasy.valuesearch.search import DEFAULT_SEARCH, OPPONENT_MODELS, SEARCH_MODES

    check_choice(strategy, list(DEFAULT_STRATEGIES), "--strategy", as_json)
    check_choice(search, SEARCH_MODES, "--search", as_json)
    # value-search objectives are only valid with an explicit --search value|advise; the default path keeps
    # the optimizer's objectives (cat_wins | matchup)
    check_choice(objective, OBJECTIVES + VALUE_OBJECTIVES if search in ("value", "advise") else OBJECTIVES,
                  "--objective", as_json)
    check_choice(opponents, OPPONENT_MODELS, "--opponents", as_json)
    search = search or DEFAULT_SEARCH
    value_objective = objective if objective in VALUE_OBJECTIVES else "blend"
    rollouts = rollouts or (24 if opponents == "ours" else 48)
    if objective in VALUE_OBJECTIVES:
        objective = "cat_wins"
    d = _load_draft(draft_id, as_json)
    s = _settings_for(d)
    vp = ValuationParams(kappa=kappa, punt=parse_punt(punt, s.categories, as_json))
    t0 = time.time()
    ctx, run_id = _context(s, projections, d, vp)
    picks = store.get_picks(d["draft_id"])
    state = DraftState(taken={p["overall_pick"]: p["player_id"] for p in picks}, my_slot=d["my_slot"])
    opt = DraftOptimizer(ctx, OptimizerParams(strategy=strategy, objective=objective))
    rec = opt.recommend(state, draft_id=d["draft_id"], model_run_id=run_id)
    if search != "none" and rec.get("recommendations"):
        _attach_value_search(rec, ctx, state, search, value_objective, rollouts, search_candidates, opponents,
                             value_model, projections)
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
        emit(rec, True)
        return
    typer.echo(f"Pick #{rec['for_pick']} (on the clock: #{rec.get('on_the_clock', {}).get('overall')}); "
               f"strategy {rec['strategy']['name']} — {rec['strategy']['notes']}")
    for i, r in enumerate(rec["recommendations"][:top], 1):
        typer.echo(f"{i:>2}. {r['name']:<26} {'/'.join(r['positions']):<9} score {r['score']:.3f} "
                   f"({r['marginal_value']:+.3f})  avail@next {r['prob_available_next']:.0%}  — {r['reason']}")
    vsb = rec.get("value_search")
    if vsb and vsb.get("candidates"):
        tag = "decided top pick" if vsb.get("mode") == "value" else "advisory only"
        best = vsb["candidates"][0]
        typer.echo(f"value search ({tag}, {vsb['params']['objective']}): {vsb['top_pick_name']} "
                   f"[best mean: {best['name']} P(title) {best['p_title']:.0%}, P(playoffs) {best['p_playoffs']:.0%}, "
                   f"{best['diff_vs_optimizer_top']:+.3f} ± {best['diff_se'] or 0:.3f} vs optimizer #1]")
    for w in rec["warnings"]:
        typer.echo(f"WARNING: {w}")


def _attach_value_search(rec: dict, ctx: DraftContext, state: DraftState, mode: str, objective: str, rollouts: int,
                         n_candidates: int, opponents: str, model_path: Path | None, projections: Path | None) -> None:
    """Add the `value_search` block; with mode "value" also move its pick to the top. Never raises: a
    value-search failure must not block a live pick (it becomes a warning)."""
    try:
        from fantasy.valuesearch.features import Featurizer
        from fantasy.valuesearch.model import ValueModel
        from fantasy.valuesearch.search import SearchParams, ValueSearch

        proj = load_projections(projections)
        vs = ValueSearch(ctx, Featurizer(ctx, proj.df), ValueModel.load(model_path))
        cands = [r["player_id"] for r in rec["recommendations"]]
        res = vs.run(state.taken, state.my_slot, cands,
                     SearchParams(n_candidates=n_candidates, rollouts=rollouts, opponents=opponents, objective=objective))
    except Exception as e:  # noqa: BLE001
        rec["warnings"].append(f"value search failed ({type(e).__name__}: {e}); optimizer ranking unchanged")
        return
    res["mode"] = mode
    res["advisory"] = mode != "value"
    if mode == "value" and res["top_pick"] != cands[0]:
        recs = rec["recommendations"]
        j = next(i for i, r in enumerate(recs) if r["player_id"] == res["top_pick"])
        recs.insert(0, recs.pop(j))
        recs[0]["reason"] = f"value search: {res['decision_rule']}; " + recs[0]["reason"]
    rec["value_search"] = res


WATCH_MAX_BACKOFF = 30.0  # seconds between retries while the DB keeps failing


def _watch_warn(msg: str) -> None:
    print(f"warning: [{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _short(e: BaseException) -> str:
    """One-line, length-capped error text for logs (DB driver messages can span many lines)."""
    return f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"


@app.command()
def watch(
    draft_id: DraftOpt = None,
    projections: ProjOpt = None,
    interval: Annotated[float, typer.Option("--interval")] = 3.0,
    max_iterations: Annotated[int, typer.Option("--max-iterations", help="Stop after N polls (0 = run until "
                                                "my last pick; for tests)")] = 0,
) -> None:
    """Poll for new picks (e.g. entered on the dashboard) and publish a fresh recommendation each time.

    Survives transient DB errors: a failed read or save is logged to stderr and retried with exponential
    backoff (capped at 30 s) without exiting; a recommendation that could not be saved is re-saved on the
    next successful poll. An optimizer error is logged and the board change is skipped (the loop keeps
    polling; run `fantasy draft recommend` by hand)."""
    from sqlalchemy.exc import SQLAlchemyError

    db_errors = (SQLAlchemyError, OSError)
    d = _load_draft(draft_id, False)
    s = _settings_for(d)
    ctx, run_id = _context(s, projections, d)
    opt = DraftOptimizer(ctx)
    last: tuple | None = None
    unsaved: dict | None = None  # computed recommendation whose save failed
    failures = 0
    polls = 0
    typer.echo(f"watching {d['draft_id']} every {interval}s (Ctrl-C to stop)")
    while True:
        polls += 1
        try:
            picks = store.get_picks(d["draft_id"])
            key = tuple((p["overall_pick"], p["player_id"]) for p in picks)
            if key != last:
                unsaved = None
                state = DraftState(taken={p["overall_pick"]: p["player_id"] for p in picks}, my_slot=d["my_slot"])
                try:
                    rec = opt.recommend(state, draft_id=d["draft_id"], model_run_id=run_id)
                except db_errors:
                    raise
                except Exception as e:  # noqa: BLE001 — keep watching; the agent can still run `draft recommend`
                    _watch_warn(f"recommendation failed for {len(picks)} picks ({_short(e)}); waiting for the next "
                                "board change")
                    last = key
                    rec = None
                if rec is not None:
                    last = key
                    unsaved = rec
                    top = rec["recommendations"][0]["name"] if rec["recommendations"] else "-"
                    typer.echo(f"[{time.strftime('%H:%M:%S')}] {len(picks)} picks → for #{rec.get('for_pick')}: {top}")
            if unsaved is not None:
                store.save_recommendation(unsaved)
                done = unsaved.get("for_pick") is None
                unsaved = None
                if done:
                    typer.echo("draft complete for my slot")
                    return
            if failures:
                _watch_warn(f"DB recovered after {failures} failed poll(s)")
            failures = 0
            wait = interval
        except db_errors as e:
            failures += 1
            wait = min(max(interval, 1.0) * 2 ** (failures - 1), WATCH_MAX_BACKOFF)
            what = "saving the recommendation" if unsaved is not None else "reading picks"
            _watch_warn(f"DB error {what} ({_short(e)}); retry {failures} in {wait:.0f}s — still watching")
        if max_iterations and polls >= max_iterations:
            typer.echo(f"stopping after {polls} polls (--max-iterations)")
            return
        time.sleep(wait)


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

    check_choice(strategy, list(DEFAULT_STRATEGIES), "--strategy", as_json)
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
        emit(res, True)
        return
    sm = res["summary"]
    head = {k: sm[k] for k in ("n_sims", "my_slot", "optimizer", "baseline_bpa", "edge", "first_pick_counts",
                               "strategy_counts")}
    typer.echo(dumps({**head, "run_id": run_id}))
