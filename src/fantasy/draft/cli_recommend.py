"""`fantasy draft recommend|watch|simulate` — optimizer-backed commands (registered on `fantasy.draft.cli.app`)."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from fantasy.cli_common import EXIT_USAGE, check_choice, dumps, emit, fail, parse_punt
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
    behind_clock,
)
from fantasy.draft.optimizer import (
    DEFAULT_STRATEGIES,
    FILLER_MODES,
    DraftContext,
    DraftOptimizer,
    DraftState,
    OptimizerParams,
)
from fantasy.draft.order import order_for_draft
from fantasy.draft.policy import (
    DEFAULT_EPS,
    DEFAULT_MARGIN,
    DEFAULT_POLICY,
    POLICIES,
    PolicyParams,
    apply_policy,
)
from fantasy.model.lines import load_projections
from fantasy.model.valuation import ValuationParams

# Version of the recommend composition (optimizer -> policy -> value search) and its defaults.
# 0.2.0 (2026-10-19, user-approved): default = --search value --availability sim --room mixed
#       (docs/research/draft_policy.md); payload gains `recommend_policy` (which policy produced the pick).
RECOMMEND_VERSION = "recommend-0.2.0"
DEFAULT_AVAILABILITY = OptimizerParams.availability  # "sim"
DEFAULT_ROOM = OptimizerParams.room  # "mixed"


@dataclass
class RecommendConfig:
    """Everything that decides a recommendation besides the board. `draft recommend` and `draft watch` build
    the same config from the same defaults, so watch precomputes exactly what recommend would return."""

    strategy: str | None = None
    objective: str = "cat_wins"  # optimizer objective
    value_objective: str = "blend"
    search: str = "value"  # none | advise | value (default: fantasy.valuesearch.search.DEFAULT_SEARCH)
    rollouts: int = 48
    search_candidates: int = 6
    opponents: str = "adp"
    value_model: Path | None = None
    policy: str = DEFAULT_POLICY
    policy_margin: float = DEFAULT_MARGIN
    policy_eps: float = DEFAULT_EPS
    availability: str = DEFAULT_AVAILABILITY
    room: str = DEFAULT_ROOM
    filler: str = "market"
    refine: int = 0

    def optimizer_params(self) -> OptimizerParams:
        return OptimizerParams(strategy=self.strategy, objective=self.objective, availability=self.availability,
                               room=self.room, filler=self.filler, refine_passes=self.refine)

    def is_default_policy(self) -> bool:
        from fantasy.valuesearch.search import DEFAULT_SEARCH

        return (self.search, self.availability, self.room, self.policy) == (
            DEFAULT_SEARCH, DEFAULT_AVAILABILITY, DEFAULT_ROOM, DEFAULT_POLICY)


class Recommender:
    """Optimizer -> --policy -> value search for one draft context. Caches the value-search engine (projections,
    featurizer, value model) across calls; every step is deterministic for a given board (the room simulation
    is seeded by the current pick, the value search by (seed, current pick)), so recommend and watch agree."""

    def __init__(self, ctx: DraftContext, cfg: RecommendConfig, projections: Path | None = None) -> None:
        self.ctx, self.cfg, self.projections = ctx, cfg, projections
        self.opt = DraftOptimizer(ctx, cfg.optimizer_params())
        self._vs = None

    def value_search(self):  # noqa: ANN201 — ValueSearch, imported lazily
        if self._vs is None:
            from fantasy.valuesearch.features import Featurizer
            from fantasy.valuesearch.model import ValueModel
            from fantasy.valuesearch.search import ValueSearch

            proj = load_projections(self.projections)
            self._vs = ValueSearch(self.ctx, Featurizer(self.ctx, proj.df), ValueModel.load(self.cfg.value_model))
        return self._vs

    def recommend(self, state: DraftState, draft_id: str, model_run_id: str | None) -> dict:
        cfg = self.cfg
        rec = self.opt.recommend(state, draft_id=draft_id, model_run_id=model_run_id)
        # the policy decides the top pick (and records BPA / optimizer #1 side by side); value search, if any,
        # then advises relative to (or, with "value", re-ranks) that order
        apply_policy(self.ctx, rec, state, PolicyParams(name=cfg.policy, margin=cfg.policy_margin, eps=cfg.policy_eps),
                     self.opt.params)
        status = "off"
        if cfg.search != "none" and rec.get("recommendations"):
            status = _attach_value_search(rec, self, state)
        vsb = rec.get("value_search") or {}
        decided_by = ("value_search" if cfg.search == "value" and status == "ok" and not vsb.get("agrees_with_optimizer")
                      else f"policy:{cfg.policy}" if cfg.policy != "optimizer" else "optimizer")
        rec["recommend_policy"] = {
            "version": RECOMMEND_VERSION,
            "search": cfg.search, "value_search_status": status,
            "availability": cfg.availability, "room": cfg.room if cfg.availability == "sim" else None,
            "room_sims": self.opt.params.room_sims if cfg.availability == "sim" else None,
            "policy": cfg.policy, "objective": cfg.objective,
            "value_objective": cfg.value_objective if cfg.search != "none" else None,
            "rollouts": cfg.rollouts if cfg.search != "none" else None,
            "search_candidates": cfg.search_candidates if cfg.search != "none" else None,
            "opponents": cfg.opponents if cfg.search != "none" else None,
            "value_model_version": vsb.get("model_version"),
            "is_default": cfg.is_default_policy(),
            "top_pick_decided_by": decided_by if rec.get("recommendations") else None,
            "fallback": ("value search failed: optimizer ranking used" if status == "failed" else None),
            "evidence": "docs/research/draft_policy.md",
        }
        return rec


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
    search: Annotated[str | None, typer.Option("--search", help="value (default: value search picks the top among "
                                               "the optimizer's candidates) | advise (advisory value_search block, "
                                               "optimizer order kept) | none. Default: "
                                               "fantasy.valuesearch.search.DEFAULT_SEARCH; with a non-optimizer "
                                               "--policy the default is advise")] = None,
    rollouts: Annotated[int | None, typer.Option("--rollouts", help="Value search: rollouts per candidate "
                                                 "(default 48; 24 with --opponents ours, which is ~10x slower)")] = None,
    search_candidates: Annotated[int, typer.Option("--search-candidates", help="Value search: top-N optimizer candidates")] = 6,
    opponents: Annotated[str, typer.Option("--opponents", help="Value search opponent model: adp | ours")] = "adp",
    value_model: Annotated[Path | None, typer.Option("--value-model", help="Value model artifact (.npz)")] = None,
    policy: Annotated[str, typer.Option("--policy", help="Decision rule for the top pick: optimizer (default) | bpa | "
                                        "hybrid | bpa_need | opt_valavail | opt_sd50 (docs/research/draft_policy.md)")]
    = DEFAULT_POLICY,
    policy_margin: Annotated[float, typer.Option("--policy-margin", help="hybrid: expected category wins the "
                                                 "optimizer #1 must gain over BPA")] = DEFAULT_MARGIN,
    policy_eps: Annotated[float, typer.Option("--policy-eps", help="bpa_need: value window for the need tiebreak")]
    = DEFAULT_EPS,
    availability: Annotated[str, typer.Option("--availability", help="Optimizer availability model: sim (default: "
                                              "Monte Carlo of --room from the board) | adp (analytic Normal(ADP), "
                                              "the pre-2026-10-19 default)")]
    = DEFAULT_AVAILABILITY,
    room: Annotated[str, typer.Option("--room", help="--availability sim: room model mixed (3 sharp + 8 ADP "
                                      "drafters, default) | adp | sym (docs/research/draft_policy.md)")] = DEFAULT_ROOM,
    filler: Annotated[str, typer.Option("--filler", help="Research option: rollout placeholder for later picks: "
                                        "market (default) | board | available (docs/research/optimizer_placeholders.md)")]
    = "market",
    refine: Annotated[int, typer.Option("--refine", help="Research option: complement-aware refinement passes "
                                        "(default 0)")] = 0,
    as_json: JsonOpt = False,
) -> None:
    """Recommend my next pick given all recorded picks. Deterministic for a given state."""
    from fantasy.draft.room_sim import ROOMS
    from fantasy.valuesearch.model import OBJECTIVES as VALUE_OBJECTIVES
    from fantasy.valuesearch.search import DEFAULT_SEARCH, OPPONENT_MODELS, SEARCH_MODES

    check_choice(strategy, list(DEFAULT_STRATEGIES), "--strategy", as_json)
    check_choice(search, SEARCH_MODES, "--search", as_json)
    check_choice(policy, POLICIES, "--policy", as_json)
    if search == "value" and policy != "optimizer":
        fail("--search value re-ranks the optimizer's candidates; use it with --policy optimizer", as_json,
             EXIT_USAGE, valid=["optimizer"])
    # default search: DEFAULT_SEARCH ("value"); a non-optimizer --policy decides the top itself, so the value
    # search only advises there
    search = search or (DEFAULT_SEARCH if policy == "optimizer" else "advise")
    # value-search objectives are only valid when a value search runs; the optimizer keeps cat_wins | matchup
    check_choice(objective, OBJECTIVES + VALUE_OBJECTIVES if search in ("value", "advise") else OBJECTIVES,
                 "--objective", as_json)
    check_choice(opponents, OPPONENT_MODELS, "--opponents", as_json)
    check_choice(availability, ["adp", "sim"], "--availability", as_json)
    check_choice(room, list(ROOMS), "--room", as_json)
    check_choice(filler, list(FILLER_MODES), "--filler", as_json)
    if not 0 <= refine <= 5:
        fail("--refine must be between 0 and 5", as_json, EXIT_USAGE)
    cfg = RecommendConfig(
        strategy=strategy, objective="cat_wins" if objective in VALUE_OBJECTIVES else objective,
        value_objective=objective if objective in VALUE_OBJECTIVES else "blend", search=search,
        rollouts=rollouts or (24 if opponents == "ours" else 48), search_candidates=search_candidates,
        opponents=opponents, value_model=value_model, policy=policy, policy_margin=policy_margin,
        policy_eps=policy_eps, availability=availability, room=room, filler=filler, refine=refine)
    d = _load_draft(draft_id, as_json)
    s = _settings_for(d)
    vp = ValuationParams(kappa=kappa, punt=parse_punt(punt, s.categories, as_json))
    t0 = time.time()
    ctx, run_id = _context(s, projections, d, vp)
    picks = store.get_picks(d["draft_id"])
    state = DraftState(taken={p["overall_pick"]: p["player_id"] for p in picks}, my_slot=d["my_slot"])
    rec = Recommender(ctx, cfg, projections).recommend(state, d["draft_id"], run_id)
    rec["compute_seconds"] = round(time.time() - t0, 2)
    rec["ok"] = True
    rec["valuation_params"] = {"kappa": vp.kappa, "punt": vp.punt}
    gaps = [p for p in range(1, (max(state.taken) if state.taken else 0)) if p not in state.taken]
    if gaps and not any("not recorded" in w for w in rec["warnings"]):  # the optimizer usually warned already
        rec["warnings"].append(f"Picks {gaps} not recorded — availability may be wrong; sync the board first.")
    rec["behind"] = behind_clock(state.taken, rec.get("for_pick"))
    if rec["behind"] and rec["behind"] != gaps:
        rec["warnings"].append(f"Board behind my pick #{rec['for_pick']}: picks {rec['behind']} are not on the "
                               "board yet; all must be recorded before prepare-pick.")
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
    rp = rec.get("recommend_policy") or {}
    typer.echo(f"policy: search={rp.get('search')} ({rp.get('value_search_status')}), availability="
               f"{rec.get('availability')}, decided by {rp.get('top_pick_decided_by')}"
               + ("" if rp.get("is_default") else " [non-default]"))
    if rp.get("value_search_status") == "failed":
        typer.echo("!!! VALUE SEARCH FAILED - ranking below is the optimizer fallback; tell the user before picking "
                   "(see WARNING lines) !!!")
    fb = rec.get("filler")
    if fb:
        steps = fb["convergence"]["steps"]
        typer.echo(f"research filler={fb['mode']} refine={fb['refine_passes']}"
                   + (f"; last pass max score change {steps[-1]['max_abs_score_change']:.3f}" if steps else ""))
    pol = rec.get("policy") or {}
    if pol.get("top_pick"):
        typer.echo(f"policy {pol['name']}: {pol['top_pick_name']} — {pol['rule']} "
                   f"[BPA: {pol['bpa_top']['name']}; optimizer #1: {pol['optimizer_top']['name']}]")
    for i, r in enumerate(rec["recommendations"][:top], 1):
        if r.get("score") is None:  # policy pick outside the optimizer's scored candidates
            typer.echo(f"{i:>2}. {r['name']:<26} {'/'.join(r['positions']):<9} (not scored)  — {r['reason']}")
            continue
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


def _attach_value_search(rec: dict, rcm: Recommender, state: DraftState) -> str:
    """Add the `value_search` block; with search "value" also move its pick to the top. Returns "ok" or
    "failed". Never raises: a value-search failure (missing/incompatible model, search error) must not block a
    live pick - it becomes a warning and the optimizer/policy ranking stands."""
    from fantasy.valuesearch.search import SearchParams

    cfg = rcm.cfg
    cands = [r["player_id"] for r in rec["recommendations"]]
    try:
        res = rcm.value_search().run(state.taken, state.my_slot, cands,
                                     SearchParams(n_candidates=cfg.search_candidates, rollouts=cfg.rollouts,
                                                  opponents=cfg.opponents, objective=cfg.value_objective))
    except Exception as e:  # noqa: BLE001
        rec["warnings"].append(f"value search failed ({type(e).__name__}: {e}); optimizer ranking unchanged "
                               "(fallback: --search none)")
        return "failed"
    res["mode"] = cfg.search
    res["advisory"] = cfg.search != "value"
    if cfg.search == "value" and res["top_pick"] != cands[0]:
        recs = rec["recommendations"]
        j = next(i for i, r in enumerate(recs) if r["player_id"] == res["top_pick"])
        recs.insert(0, recs.pop(j))
        recs[0]["reason"] = f"value search: {res['decision_rule']}; " + recs[0]["reason"]
    rec["value_search"] = res
    return "ok"


def _order_key(d: dict) -> tuple:
    """What `watch` must recompute on besides picks: the per-pick slot order and my slot."""
    slots = (d.get("config") or {}).get("order_slots") or [p.slot for p in order_for_draft(d)]
    return (tuple(slots), d["my_slot"])


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
    search: Annotated[str | None, typer.Option("--search", help="As `draft recommend --search` (default value)")]
    = None,
    availability: Annotated[str, typer.Option("--availability", help="As `draft recommend` (default sim)")]
    = DEFAULT_AVAILABILITY,
    room: Annotated[str, typer.Option("--room", help="As `draft recommend` (default mixed)")] = DEFAULT_ROOM,
    value_model: Annotated[Path | None, typer.Option("--value-model", help="Value model artifact (.npz)")] = None,
) -> None:
    """Poll for new picks (e.g. entered on the dashboard) and publish a fresh recommendation each time.

    Survives transient DB errors: a failed read or save is logged to stderr and retried with exponential
    backoff (capped at 30 s) without exiting; a recommendation that could not be saved is re-saved on the
    next successful poll. An optimizer error is logged and the board change is skipped (the loop keeps
    polling; run `fantasy draft recommend` by hand).

    Uses the same policy and defaults as `draft recommend` (value search + sim/mixed availability), so the
    recommendation it saves for a board is the one `draft recommend` would return (both are deterministic per
    board); it recomputes only when the board or the draft order changes."""
    from sqlalchemy.exc import SQLAlchemyError

    from fantasy.draft.room_sim import ROOMS
    from fantasy.valuesearch.search import DEFAULT_SEARCH, SEARCH_MODES

    check_choice(search, SEARCH_MODES, "--search", False)
    check_choice(availability, ["adp", "sim"], "--availability", False)
    check_choice(room, list(ROOMS), "--room", False)
    cfg = RecommendConfig(search=search or DEFAULT_SEARCH, availability=availability, room=room,
                          value_model=value_model)

    db_errors = (SQLAlchemyError, OSError)
    d = _load_draft(draft_id, False)
    order_key = _order_key(d)
    ctx, run_id = _context(_settings_for(d), projections, d)
    rcm = Recommender(ctx, cfg, projections)
    last: tuple | None = None
    unsaved: dict | None = None  # computed recommendation whose save failed
    bad_order_key: tuple | None = None  # a new order whose context rebuild failed (warned once, retried)
    failures = 0
    polls = 0
    typer.echo(f"watching {d['draft_id']} every {interval}s (Ctrl-C to stop); policy search={cfg.search} "
               f"availability={cfg.availability}" + (f":{cfg.room}" if cfg.availability == "sim" else ""))
    while True:
        polls += 1
        try:
            # re-read the draft row every poll: `set-order` mid-draft changes my picks (and the context)
            fresh = store.get_draft(d["draft_id"])
            if fresh is not None and _order_key(fresh) == order_key:
                bad_order_key = None  # order reverted to the one the current context was built for
            elif fresh is not None:
                new_key = _order_key(fresh)
                unsaved = None  # a recommendation computed under the old order must not be published
                try:
                    ctx, run_id = _context(_settings_for(fresh), projections, fresh)
                    rcm = Recommender(ctx, cfg, projections)
                except db_errors:
                    raise
                except Exception as e:  # noqa: BLE001 — keep watching; the rebuild is retried every poll
                    if new_key != bad_order_key:
                        _watch_warn(f"draft order changed but rebuilding the context failed ({_short(e)}); "
                                    "not recommending until it succeeds — still watching")
                    bad_order_key = new_key
                else:
                    d, order_key, bad_order_key, last = fresh, new_key, None, None
                    typer.echo(f"[{time.strftime('%H:%M:%S')}] draft order changed (my slot {d['my_slot']}); "
                               "context rebuilt")
            if bad_order_key is None:  # skip while the new order's context cannot be built
                picks = store.get_picks(d["draft_id"])
                key = (order_key, tuple((p["overall_pick"], p["player_id"]) for p in picks))
                if key != last:
                    unsaved = None
                    state = DraftState(taken={p["overall_pick"]: p["player_id"] for p in picks}, my_slot=d["my_slot"])
                    try:
                        rec = rcm.recommend(state, d["draft_id"], run_id)
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
    policy: Annotated[str, typer.Option("--policy", help="My decision rule (as `draft recommend --policy`); "
                                        "the BPA baseline is always replayed")] = DEFAULT_POLICY,
    policy_margin: Annotated[float, typer.Option("--policy-margin")] = DEFAULT_MARGIN,
    policy_eps: Annotated[float, typer.Option("--policy-eps")] = DEFAULT_EPS,
    as_json: JsonOpt = False,
) -> None:
    """Monte Carlo mock drafts: opponents draft by noisy ADP, I draft with the optimizer (or --policy)."""
    from fantasy.draft.simulate import save_draft_sim, simulate_drafts

    check_choice(strategy, list(DEFAULT_STRATEGIES), "--strategy", as_json)
    check_choice(policy, POLICIES, "--policy", as_json)
    d = _load_draft(draft_id, as_json) if draft_id else None
    s = _settings_for(d)
    if slot:
        s = s.model_copy(update={"my_slot": slot})
    custom = ((d or {}).get("config") or {}).get("custom_rounds")
    proj = load_projections(projections)
    ctx = DraftContext(proj, s, custom_rounds=custom)
    res = simulate_drafts(ctx, n_sims=sims, seed=seed, noise=noise, strategy=strategy,
                          policy=PolicyParams(name=policy, margin=policy_margin, eps=policy_eps))
    run_id = None
    if save:
        try:
            run_id = save_draft_sim(
                res,
                {"n_sims": sims, "seed": seed, "noise": noise, "strategy": strategy, "my_slot": s.my_slot,
                 "policy": policy},
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
