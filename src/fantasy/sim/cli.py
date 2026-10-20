"""`fantasy sim ...` — league-aware season simulation. All commands print JSON with --json."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from fantasy.cli_common import emit, fail
from fantasy.draft import store
from fantasy.draft.names import NameResolutionError, resolve_player
from fantasy.league.settings import LeagueSettings, load_settings
from fantasy.model.lines import load_projections
from fantasy.ops.common import EXIT_USAGE
from fantasy.sim import SIM_VERSION
from fantasy.sim.context import SimContext, build_context
from fantasy.sim.engine import SimParams
from fantasy.sim.league import (
    LeagueState,
    complete_rosters,
    from_draft,
    from_json,
    league_json_from_draft,
    parse_team_names,
    remaining_order,
)
from fantasy.sim.report import matchup_report, paired_delta, season_summary
from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH

app = typer.Typer(no_args_is_help=True, help="Season simulator: P(playoffs)/P(title), matchups, roster changes.")

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
DraftOpt = Annotated[str | None, typer.Option("--draft-id", envvar="FANTASY_DRAFT_ID",
                                              help="Rosters from this draft (default: latest non-mock draft)")]
LeagueOpt = Annotated[Path | None, typer.Option("--league-file", help="Rosters JSON (post-draft Yahoo sync) instead of a draft")]
SimsOpt = Annotated[int, typer.Option("--sims")]
SeedOpt = Annotated[int, typer.Option("--seed")]
SchedOpt = Annotated[Path | None, typer.Option(
    "--schedule", help="Matchup schedule JSON (Yahoo); default data/manual/league_schedule.json if present, else an "
                       "assumed round robin")]
NbaOpt = Annotated[Path | None, typer.Option("--nba-schedule", help="NBA games CSV (default data/raw/bbref_schedule)")]
CompleteOpt = Annotated[bool, typer.Option("--complete-rosters", help="Mid-draft: fill remaining picks with expected picks")]
MyPlanOpt = Annotated[str, typer.Option("--my-plan", help="How to complete MY roster: market | optimizer")]
TeamOpt = Annotated[int | None, typer.Option("--team", help="Team id to report on (default: my slot)")]
ProjOpt = Annotated[Path | None, typer.Option("--projections", help="Projections CSV (default data/processed)")]
Week1Opt = Annotated[str | None, typer.Option("--week1-end", help="Last day of fantasy week 1 (YYYY-MM-DD; long week 1)")]
MergeOpt = Annotated[bool, typer.Option("--merge-allstar/--no-merge-allstar", help="All-Star break weeks form one matchup")]
POTeamsOpt = Annotated[int | None, typer.Option("--playoff-teams")]
POWeeksOpt = Annotated[str | None, typer.Option("--playoff-weeks", help="e.g. 18,19,20")]
StreamOpt = Annotated[bool, typer.Option("--streaming/--no-streaming", help="Model streaming adds")]
SaveOpt = Annotated[bool, typer.Option("--save/--no-save", help="Record a model_runs row")]
ILOpt = Annotated[bool, typer.Option(
    "--il-replacement/--no-il-replacement",
    help="Replace players in long injury spells with replacement-level players (IL slots; default on since season-sim-0.6.0, priors: "
         "docs/research/il_replacement.md)")]
ILDaysOpt = Annotated[float, typer.Option("--il-min-spell-days", help="Shortest spell (days) that triggers an IL move (prior 7)")]



def _settings(draft: dict | None, playoff_teams: int | None, playoff_weeks: str | None) -> LeagueSettings:
    s = load_settings()
    upd: dict = {}
    if draft:
        upd.update({"num_teams": draft["num_teams"], "my_slot": draft["my_slot"], "draft_order_type": draft["order_type"]})
    if playoff_teams:
        upd["playoff_teams"] = playoff_teams
    if playoff_weeks:
        upd["playoff_weeks"] = [int(x) for x in playoff_weeks.split(",") if x.strip()]
    return s.model_copy(update=upd) if upd else s


class _Setup:
    def __init__(self, league: LeagueState, ctx: SimContext, draft: dict | None, picks: list[dict]):
        self.league, self.ctx, self.draft, self.picks = league, ctx, draft, picks


def _setup(
    as_json: bool, draft_id: str | None, league_file: Path | None, complete: bool, my_plan: str, seed: int,
    schedule: Path | None, nba_schedule: Path | None, projections: Path | None, week1_end: str | None,
    merge_allstar: bool, playoff_teams: int | None, playoff_weeks: str | None,
) -> _Setup:
    draft, picks = None, []
    if league_file:
        league = from_json(league_file)
    else:
        draft = store.get_draft(draft_id)
        if not draft:
            fail(f"draft not found: {draft_id or '(none exist)'} — pass --draft-id or --league-file", as_json, code="not_found")
        assert draft is not None
        picks = store.get_picks(draft["draft_id"])
        league = from_draft(draft, picks)
    settings = _settings(draft, playoff_teams, playoff_weeks)
    if league_file:
        settings = settings.model_copy(update={"num_teams": len(league.rosters)})
    proj = load_projections(projections)
    if schedule is None and LEAGUE_SCHEDULE_PATH.exists():
        schedule = LEAGUE_SCHEDULE_PATH
    try:
        ctx = build_context(proj, settings, league.team_ids, schedule_file=schedule, nba_schedule_file=nba_schedule,
                            week1_end=date.fromisoformat(week1_end) if week1_end else None,
                            merge_allstar=merge_allstar, team_names=league.names)
    except ValueError as e:
        fail(str(e), as_json, exit_code=EXIT_USAGE)
    full = settings.num_rounds
    short = {t: n for t, n in league.roster_sizes().items() if n < full}
    if draft and complete and short:
        plan = None
        if my_plan == "optimizer":
            plan = _optimizer_plan(draft, picks, ctx)
        league = complete_rosters(league, remaining_order(draft, picks), ctx.values, ctx.market, settings,
                                  seed=seed, my_plan=plan)
        league.warnings.append(
            f"{sum(len(v) for v in league.projected.values())} roster spots filled with EXPECTED picks "
            f"(others: noisy market order; mine: {my_plan}) — not real players yet.")
    elif short:
        league.warnings.append(f"Partial rosters {short} (full = {full}); results compare incomplete teams. "
                               "Use --complete-rosters mid-draft.")
    return _Setup(league, ctx, draft, picks)


def _optimizer_plan(draft: dict, picks: list[dict], ctx: SimContext) -> list[str]:
    from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams

    custom = (draft.get("config") or {}).get("custom_rounds")
    dctx = DraftContext(ctx.proj, ctx.settings, values=ctx.values, custom_rounds=custom)
    taken = {int(p["overall_pick"]): p["player_id"] for p in picks if p.get("player_id")}
    rec = DraftOptimizer(dctx, OptimizerParams(strategy="balanced", availability="adp")).recommend(
        DraftState(taken=taken, my_slot=int(draft["my_slot"])), draft_id=draft["draft_id"])
    if not rec.get("recommendations"):
        return []
    return [rec["recommendations"][0]["player_id"]] + [p["player_id"] for p in rec.get("planned_picks", [])]


def _params(sims: int, seed: int, streaming: bool, il: bool = False, il_days: float = 7.0) -> SimParams:
    return SimParams(n_sims=sims, seed=seed, streaming=streaming, il_replacement=il, il_min_spell_days=il_days)


def _save(save: bool, kind_detail: str, params: SimParams, setup: _Setup, summary: dict, extra_inputs: dict | None = None) -> str | None:
    if not save:
        return None
    try:
        from fantasy.sim.persist import save_sim_run

        inputs = {**setup.ctx.inputs(), "league_source": setup.league.source,
                  "draft_id": setup.draft["draft_id"] if setup.draft else setup.league.draft_id,
                  "rosters": setup.league.to_json()["teams"], **(extra_inputs or {})}
        return save_sim_run("season_sim", {**params.to_dict(), "command": kind_detail}, inputs, summary)
    except Exception as e:  # recording must never block the answer
        print(f"warning: could not record sim run: {e}", file=sys.stderr)
        return None


def _resolve(q: str, setup: _Setup, as_json: bool) -> str:
    try:
        return resolve_player(q, setup.ctx.values)
    except NameResolutionError as e:
        fail(str(e), as_json, exit_code=EXIT_USAGE, code="unresolved_player", candidates=e.candidates)


# ---------------------------------------------------------------- commands
@app.command("fetch-schedule")
def fetch_schedule(season_end_year: Annotated[int, typer.Option("--season")] = 2027, as_json: JsonOpt = False) -> None:
    """Download the NBA regular-season schedule from basketball-reference (7 polite requests) into
    data/raw/bbref_schedule/ with provenance."""
    from fantasy.sim.schedule import fetch_nba_schedule

    try:
        res = fetch_nba_schedule(season_end_year)
    except Exception as e:
        fail(f"fetch failed: {e}", as_json)
    emit({"ok": True, **res}, as_json, f"cached {res['games']} games -> {res['path']}")


@app.command("export-league")
def export_league(
    out: Annotated[Path, typer.Option("--out", help="Where to write the --league-file JSON")],
    draft_id: DraftOpt = None,
    names_file: Annotated[Path | None, typer.Option(
        "--names-file", help="Team names: JSON list in slot order (Yahoo round-1 draft order) or {slot: name}")] = None,
    source: Annotated[str | None, typer.Option("--source", help="Provenance label stored in the file")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Write the actual rosters of a draft (team id = draft slot) as a `--league-file` JSON, with team names.
    Reports players missing from the projections (simulated at the replacement line)."""
    draft = store.get_draft(draft_id)
    if not draft:
        fail(f"draft not found: {draft_id or '(none exist)'}", as_json, code="not_found")
    assert draft is not None
    picks = store.get_picks(draft["draft_id"])
    try:
        names = parse_team_names(json.loads(names_file.read_text()), int(draft["num_teams"])) if names_file else None
    except (ValueError, json.JSONDecodeError) as e:
        fail(f"bad --names-file: {e}", as_json, exit_code=EXIT_USAGE)
    data = league_json_from_draft(draft, picks, names, source)
    known = set(load_projections(None).df.index)
    unknown = [{"team": int(t), "player_id": p} for t, v in data["teams"].items() for p in v["players"] if p not in known]
    data["unknown_players"] = unknown
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    res = {"ok": True, "out": str(out), "draft_id": draft["draft_id"], "draft_status": draft["status"],
           "picks_recorded": data["picks_recorded"], "picks_total": data["picks_total"],
           "teams": {t: {"name": v["name"], "players": len(v["players"])} for t, v in data["teams"].items()},
           "unknown_players": unknown}
    emit(res, as_json, f"wrote {out}: {data['picks_recorded']}/{data['picks_total']} picks, "
                       f"{len(unknown)} players not in projections")


@app.command("import-schedule")
def import_schedule(
    yahoo_capture: Annotated[Path, typer.Option("--yahoo-capture", help="data/raw/yahoo/league_schedule_<date>.json")],
    out: Annotated[Path, typer.Option("--out", help="Schedule JSON to write (default for sim: data/manual/league_schedule.json)")]
    = LEAGUE_SCHEDULE_PATH,
    as_json: JsonOpt = False,
) -> None:
    """Convert a Yahoo schedule capture (Yahoo team ids) into the sim's schedule JSON (team ids = draft slots)."""
    from fantasy.sim.schedule import MatchupSchedule, from_yahoo_capture

    try:
        data = from_yahoo_capture(json.loads(yahoo_capture.read_text()))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        fail(f"bad capture: {e}", as_json, exit_code=EXIT_USAGE)
    weeks = {w["week"]: [tuple(p) for p in w["matchups"]] for w in data["weeks"] if w.get("matchups")}
    errs = MatchupSchedule(weeks=weeks, source="check").validate(sorted({int(k) for k in data["team_names"]}))
    if errs:
        fail("invalid schedule: " + "; ".join(errs), as_json, exit_code=EXIT_USAGE)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    emit({"ok": True, "out": str(out), "weeks": len(data["weeks"]), "weeks_with_matchups": sorted(weeks)}, as_json,
         f"wrote {out}: {len(data['weeks'])} weeks, pairings for weeks {sorted(weeks)}")


@app.command()
def schedule(
    draft_id: DraftOpt = None, league_file: LeagueOpt = None, schedule: SchedOpt = None, nba_schedule: NbaOpt = None,
    week1_end: Week1Opt = None, merge_allstar: MergeOpt = True, playoff_weeks: POWeeksOpt = None,
    out: Annotated[Path | None, typer.Option("--out", help="Write the schedule JSON (template for Yahoo's actual one)")] = None,
    projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Show the fantasy week calendar, NBA games per team per week and the matchup schedule."""
    from fantasy.sim.schedule import schedule_to_json

    st = _setup(as_json, draft_id, league_file, False, "market", 0, schedule, nba_schedule, projections, week1_end,
                merge_allstar, None, playoff_weeks)
    ctx = st.ctx
    rows = []
    idx, M = ctx.nba.plays_matrix([d for w in ctx.calendar for d in w.days()])
    k = 0
    for w in ctx.calendar:
        n = len(w.days())
        g = M[:-1, k : k + n].sum(axis=1)
        k += n
        rows.append({"week": w.week, "start": w.start.isoformat(), "end": w.end.isoformat(), "days": n,
                     "nba_games_per_team_mean": round(float(g.mean()), 2), "min": int(g.min()), "max": int(g.max()),
                     "matchups": ctx.matchups.weeks.get(w.week, [])})
    if out:
        out.write_text(schedule_to_json(ctx.matchups, ctx.calendar))
    res = {"ok": True, "nba_schedule": ctx.nba.source, "matchup_source": ctx.matchups.source, "weeks": rows,
           "warnings": ctx.warnings, "written": str(out) if out else None}
    text = "\n".join(f"wk {r['week']:>2} {r['start']}..{r['end']} ({r['days']}d) NBA games/team "
                     f"{r['nba_games_per_team_mean']:.2f} [{r['min']}-{r['max']}]" for r in rows)
    emit(res, as_json, f"NBA schedule: {ctx.nba.source}\n{text}")


@app.command()
def season(
    draft_id: DraftOpt = None, league_file: LeagueOpt = None, sims: SimsOpt = 2000, seed: SeedOpt = 0,
    schedule: SchedOpt = None, nba_schedule: NbaOpt = None, complete_rosters_: CompleteOpt = False,
    my_plan: MyPlanOpt = "market", team: TeamOpt = None, week1_end: Week1Opt = None, merge_allstar: MergeOpt = True,
    playoff_teams: POTeamsOpt = None, playoff_weeks: POWeeksOpt = None, streaming: StreamOpt = True,
    save: SaveOpt = True, projections: ProjOpt = None, il_replacement: ILOpt = True, il_min_spell_days: ILDaysOpt = 7.0,
    scenario: Annotated[str | None, typer.Option(
        "--scenario", help='Label stored as summary.scenario, e.g. "post-draft: actual rosters" (dashboard)')] = None,
    as_json: JsonOpt = False,
) -> None:
    """Simulate the season: P(playoffs), P(title), expected category wins, weekly matchups, opponents."""
    if my_plan not in ("market", "optimizer"):
        fail("--my-plan must be market or optimizer", as_json, exit_code=EXIT_USAGE)
    st = _setup(as_json, draft_id, league_file, complete_rosters_, my_plan, seed, schedule, nba_schedule,
                projections, week1_end, merge_allstar, playoff_teams, playoff_weeks)
    params = _params(sims, seed, streaming, il_replacement, il_min_spell_days)
    res = st.ctx.simulator(params).run(st.league)
    t = team or st.league.my_team
    if t not in st.league.team_ids:
        fail(f"unknown team {t}; teams: {st.league.team_ids}", as_json, exit_code=EXIT_USAGE)
    summary = season_summary(res, st.league, t)
    summary["warnings"] = list(dict.fromkeys(st.ctx.warnings + summary["warnings"]))
    summary["matchup_schedule"] = st.ctx.matchup_info()
    summary["draft_id"] = st.draft["draft_id"] if st.draft else st.league.draft_id
    if scenario:
        summary["scenario"] = scenario
    run_id = _save(save, "season", params, st, summary)
    out = {"ok": True, "sim_version": SIM_VERSION, "run_id": run_id, **summary,
           "rosters": st.league.to_json()["teams"]}
    emit(out, as_json, _season_text(summary))


def _season_text(s: dict) -> str:
    m = s["my_team"]
    lines = [f"{m['name']}: P(playoffs) {m['p_playoffs']:.1%} ±{m['p_playoffs_se']:.1%}  P(title) {m['p_title']:.1%}  "
             f"exp cat W-L {m['exp_cat_wins']:.1f}-{m['exp_cat_losses']:.1f}  exp rank {m['exp_rank']:.1f}  "
             f"({s['n_sims']} sims, {s['compute_seconds']}s)", "", "team               P(po)  P(bye) P(title) catW   rank"]
    for r in s["league_table"]:
        lines.append(f"{r['name'][:18]:<18} {r['p_playoffs']:6.1%} {r['p_bye']:6.1%} {r['p_title']:7.1%} "
                     f"{r['exp_cat_wins']:6.1f} {r['exp_rank']:5.1f}")
    lines += ["", "week  opp                P(win) exp cats  games me/opp"]
    for w in m["weekly_matchups"]:
        lines.append(f"{w['week']:>4}  {w['opponent_name'][:18]:<18} {w['p_win']:5.1%}  {w['exp_cats_won']:5.2f}   "
                     f"{w['my_player_games']:.0f}/{w['opp_player_games']:.0f}")
    lines += [f"warning: {x}" for x in s["warnings"]]
    return "\n".join(lines)


@app.command()
def matchup(
    week: Annotated[int, typer.Option("--week")], opponent: Annotated[int | None, typer.Option("--opponent")] = None,
    draft_id: DraftOpt = None, league_file: LeagueOpt = None, sims: SimsOpt = 2000, seed: SeedOpt = 0,
    schedule: SchedOpt = None, nba_schedule: NbaOpt = None, complete_rosters_: CompleteOpt = False,
    team: TeamOpt = None, week1_end: Week1Opt = None, merge_allstar: MergeOpt = True, streaming: StreamOpt = True,
    projections: ProjOpt = None, il_replacement: ILOpt = True, il_min_spell_days: ILDaysOpt = 7.0,
    as_json: JsonOpt = False,
) -> None:
    """Week-N matchup breakdown for my team: per-category win probabilities, expected totals, games."""
    st = _setup(as_json, draft_id, league_file, complete_rosters_, "market", seed, schedule, nba_schedule,
                projections, week1_end, merge_allstar, None, None)
    if week not in [w.week for w in st.ctx.calendar]:
        fail(f"week {week} outside the calendar 1..{st.ctx.calendar[-1].week}", as_json, exit_code=EXIT_USAGE)
    res = st.ctx.simulator(_params(sims, seed, streaming, il_replacement, il_min_spell_days)).run(st.league)
    t = team or st.league.my_team
    try:
        rep = matchup_report(res, st.league, t, week, opponent)
    except ValueError as e:
        fail(str(e), as_json, exit_code=EXIT_USAGE)
    out = {"ok": True, "sim_version": SIM_VERSION, "n_sims": res.n, "seed": seed, **rep,
           "warnings": st.ctx.warnings + st.league.warnings + res.warnings}
    lines = [f"Week {week} ({rep['start']}..{rep['end']}): {rep['name']} vs {rep['opponent_name']}  "
             f"P(win) {rep['p_win']:.1%} tie {rep['p_tie']:.1%}  exp cats {rep['exp_cats_won']:.2f}/9  "
             f"games {rep['my_player_games']:.0f} vs {rep['opp_player_games']:.0f}"]
    for c in rep["categories"].values():
        lines.append(f"  {c['label']:<4} P(win) {c['p_win']:5.1%}  me {c['me_mean']:8.3f}  opp {c['opp_mean']:8.3f}")
    emit(out, as_json, "\n".join(lines))


@app.command()
def compare(
    add: Annotated[str, typer.Option("--add", help="Player(s) to add, comma-separated (name or id)")] = "",
    drop: Annotated[str, typer.Option("--drop", help="Player(s) to drop, comma-separated")] = "",
    draft_id: DraftOpt = None, league_file: LeagueOpt = None, sims: SimsOpt = 2000, seed: SeedOpt = 0,
    schedule: SchedOpt = None, nba_schedule: NbaOpt = None, complete_rosters_: CompleteOpt = False,
    team: TeamOpt = None, week1_end: Week1Opt = None, merge_allstar: MergeOpt = True, streaming: StreamOpt = True,
    save: SaveOpt = True, projections: ProjOpt = None, il_replacement: ILOpt = True,
    il_min_spell_days: ILDaysOpt = 7.0, as_json: JsonOpt = False,
) -> None:
    """Change in P(playoffs)/P(title) for a roster change (add/drop), paired with common random numbers."""
    st = _setup(as_json, draft_id, league_file, complete_rosters_, "market", seed, schedule, nba_schedule,
                projections, week1_end, merge_allstar, None, None)
    t = team or st.league.my_team
    adds = [_resolve(x, st, as_json) for x in add.split(",") if x.strip()]
    drops = [_resolve(x, st, as_json) for x in drop.split(",") if x.strip()]
    if not adds and not drops:
        fail("pass --add and/or --drop", as_json, exit_code=EXIT_USAGE)
    try:
        alt_league = st.league.with_change(t, adds, drops)
    except ValueError as e:
        fail(str(e), as_json, exit_code=EXIT_USAGE)
    params = _params(sims, seed, streaming, il_replacement, il_min_spell_days)
    sim = st.ctx.simulator(params)
    base, alt = sim.run(st.league), sim.run(alt_league)
    delta = paired_delta(base, alt, t)
    names = st.ctx.values["name"]
    out = {"ok": True, "sim_version": SIM_VERSION, "team": t, "n_sims": sims, "seed": seed,
           "add": [{"player_id": p, "name": str(names.get(p, p))} for p in adds],
           "drop": [{"player_id": p, "name": str(names.get(p, p))} for p in drops],
           **delta, "compute_seconds": round(base.seconds + alt.seconds, 2),
           "warnings": st.ctx.warnings + st.league.warnings + base.warnings + alt.warnings}
    out["run_id"] = _save(save, "compare", params, st, out)
    lines = [f"team {t}: +{', '.join(a['name'] for a in out['add']) or '-'} / -{', '.join(d['name'] for d in out['drop']) or '-'}"]
    for k in ("p_playoffs", "p_title", "exp_cat_wins"):
        d = delta[k]
        lines.append(f"  {k:<12} {d['before']:.4f} -> {d['after']:.4f}  delta {d['delta']:+.4f} ± {d['delta_se']:.4f}")
    emit(out, as_json, "\n".join(lines))


@app.command("draft-check")
def draft_check_cmd(
    candidates: Annotated[str, typer.Option("--candidates", help="Comma-separated names or ids")],
    draft_id: DraftOpt = None, sims: SimsOpt = 800, seed: SeedOpt = 0,
    completions: Annotated[int, typer.Option("--completions", help="Independent completions of other rosters")] = 4,
    schedule: SchedOpt = None,
    nba_schedule: NbaOpt = None, week1_end: Week1Opt = None, merge_allstar: MergeOpt = True,
    streaming: StreamOpt = True, save: SaveOpt = True, projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Advisory (not part of the pick loop): P(playoffs)/P(title) if I take each candidate next, with my
    later picks from the optimizer's plan and other teams completed by market order."""
    from fantasy.sim.draftcheck import draft_check

    st = _setup(as_json, draft_id, None, False, "market", seed, schedule, nba_schedule, projections, week1_end,
                merge_allstar, None, None)
    assert st.draft is not None
    cands = [_resolve(x, st, as_json) for x in candidates.split(",") if x.strip()]
    if not cands:
        fail("no candidates", as_json, exit_code=EXIT_USAGE)
    params = _params(sims, seed, streaming)
    try:
        rep = draft_check(st.draft, st.picks, st.ctx, cands, params, completions=completions)
    except ValueError as e:
        fail(str(e), as_json, exit_code=EXIT_USAGE)
    out = {"ok": True, "sim_version": SIM_VERSION, "draft_id": st.draft["draft_id"], **rep,
           "warnings": st.ctx.warnings + ["Advisory: completed rosters are expected picks; differences within ~2 SE are noise."]}
    out["run_id"] = _save(save, "draft-check", params, st, out)
    lines = [f"pick {rep['for_pick']} ({rep['n_sims']} sims over {rep['completions']} completions, {rep['compute_seconds']}s)"]
    for c in rep["candidates"]:
        lines.append(f"  {c['name']:<24} P(po) {c['p_playoffs']:.1%} ±{c['p_playoffs_se']:.1%}  P(title) {c['p_title']:.1%}  "
                     f"diff vs first {c['p_playoffs_diff_vs_first']:+.3f} ± {c['p_playoffs_diff_se'] or 0:.3f}")
    emit(out, as_json, "\n".join(lines))


@app.command()
def preseason(
    leagues: Annotated[int, typer.Option("--leagues", help="Simulated drafts (leagues); season sims are split across them")] = 8,
    sims: SimsOpt = 2000, seed: SeedOpt = 0,
    draft_seed: Annotated[int, typer.Option("--draft-seed")] = 0,
    noise: Annotated[float, typer.Option("--noise", help="Opponent ADP noise multiplier in the simulated drafts")] = 1.0,
    strategy: Annotated[str | None, typer.Option("--strategy", help="Force a draft strategy (default: scan)")] = None,
    slot: Annotated[int | None, typer.Option("--slot")] = None,
    schedule: SchedOpt = None, nba_schedule: NbaOpt = None, week1_end: Week1Opt = None, merge_allstar: MergeOpt = True,
    streaming: StreamOpt = True, save: SaveOpt = True, projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Pre-draft season projection: simulate full drafts (me = optimizer, others = noisy ADP), play each
    league's season and pool P(playoffs)/P(title)/rank by draft slot. Saved as kind season_sim with
    summary.scenario = "pre-draft projection"."""
    from fantasy.draft.optimizer import DEFAULT_STRATEGIES, DraftContext
    from fantasy.sim.persist import save_sim_run
    from fantasy.sim.preseason import preseason as run_preseason

    if strategy and strategy not in DEFAULT_STRATEGIES:
        fail(f"unknown --strategy {strategy}", as_json, exit_code=EXIT_USAGE, valid=list(DEFAULT_STRATEGIES))
    if leagues < 1:
        fail("--leagues must be >= 1", as_json, exit_code=EXIT_USAGE)
    if schedule:  # Yahoo schedules are keyed by Yahoo team id; simulated leagues are keyed by draft slot
        fail("--schedule is not supported pre-draft (team ids = draft slots); use sim season after the draft",
             as_json, exit_code=EXIT_USAGE)
    settings = load_settings()
    if slot:
        settings = settings.model_copy(update={"my_slot": slot})
    proj = load_projections(projections)
    team_ids = list(range(1, settings.num_teams + 1))
    try:
        sctx = build_context(proj, settings, team_ids, schedule_file=schedule, nba_schedule_file=nba_schedule,
                             week1_end=date.fromisoformat(week1_end) if week1_end else None,
                             merge_allstar=merge_allstar)
    except ValueError as e:
        fail(str(e), as_json, exit_code=EXIT_USAGE)
    dctx = DraftContext(proj, settings, values=sctx.values)
    params = _params(sims, seed, streaming)
    summary = run_preseason(sctx, dctx, n_leagues=leagues, params=params, draft_seed=draft_seed, noise=noise,
                            strategy=strategy)
    run_id = None
    if save:
        try:
            run_id = save_sim_run(
                "season_sim",
                {**params.to_dict(), "command": "preseason", "leagues": leagues, "draft_seed": draft_seed,
                 "noise": noise, "strategy": strategy, "my_slot": settings.my_slot},
                {**sctx.inputs(), "league_source": "simulated drafts", "draft_order_type": settings.draft_order_type,
                 "my_picks": [p.overall for p in dctx.order if p.slot == settings.my_slot]},
                summary)
        except Exception as e:  # recording must never block the answer
            print(f"warning: could not record sim run: {e}", file=sys.stderr)
    m = summary["my_outlook"]
    text = [f"pre-draft projection (slot {settings.my_slot}, {summary['n_leagues']} simulated leagues, "
            f"{summary['n_sims']} seasons, {summary['compute_seconds']}s): P(playoffs) {m['p_playoffs']:.1%} "
            f"±{m['p_playoffs_se']:.1%}  P(title) {m['p_title']:.1%}  exp rank {m['exp_rank']:.1f}  run {run_id}"]
    for r in summary["league_table"]:
        text.append(f"  {r['name']:<14} P(po) {r['p_playoffs']:6.1%}  P(title) {r['p_title']:6.1%}  rank {r['exp_rank']:.1f}")
    emit({"ok": True, "sim_version": SIM_VERSION, "run_id": run_id, **summary}, as_json, "\n".join(text))


@app.command()
def symmetric(
    drafts: Annotated[int, typer.Option("--drafts", help="Draft seeds (each plays every room)")] = 100,
    sims: Annotated[int, typer.Option("--sims", help="Season sims per league per truth")] = 500,
    workers: Annotated[int, typer.Option("--workers", help="Parallel processes")] = 1,
    full_subset: Annotated[int, typer.Option("--full-subset", help="Also run A_full (all 12 full-scan optimizers) on the first N seeds")] = 0,
    truths: Annotated[str, typer.Option("--truths", help="Comma list of ours,market,blend")] = "ours,market,blend",
    out: Annotated[Path, typer.Option("--out", help="Per-seed results (JSON lines; resumable)")] = Path("var/symmetric.jsonl"),
    summary_only: Annotated[bool, typer.Option("--summary-only", help="Only summarize an existing --out file")] = False,
    slot: Annotated[int | None, typer.Option("--slot", help="My draft slot (default: settings)")] = None,
    nba_schedule: NbaOpt = None, projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Optimism decomposition (docs/research/optimism_decomposition.md): play the same draft seeds in rooms
    where 0, 3, 6, 11 or all opponents draft with our optimizer, and grade them under our projections and a
    market-implied truth. Reports my-slot P(playoffs)/P(title) per room with paired SEs and slot values."""
    from fantasy.sim.symmetric import (
        build_experiment,
        decomposition_table,
        run_experiment,
        summarize,
    )
    from fantasy.sim.symmetric import merged_results as _merged

    settings = load_settings()
    if slot:
        settings = settings.model_copy(update={"my_slot": slot})
    if summary_only:
        if not out.exists():
            fail(f"{out} does not exist", as_json, exit_code=EXIT_USAGE)
        results = _merged(out)
        diag = None
    else:
        tr = tuple(t.strip() for t in truths.split(",") if t.strip())
        bad = [t for t in tr if t not in ("ours", "market", "blend")] or ([] if "ours" in tr else ["ours missing"])
        if bad:
            fail(f"bad --truths: {bad}", as_json, exit_code=EXIT_USAGE)
        exp, diag = build_experiment(load_projections(projections), settings, truths=tr, n_season_sims=sims,
                                     nba_schedule_file=nba_schedule)
        results = run_experiment(exp, list(range(drafts)), out, workers=workers, subset_seeds=set(range(full_subset)))
    summary = summarize(results, my_slot=settings.my_slot)
    table = decomposition_table(summary)
    text = [f"optimism decomposition: slot {settings.my_slot}, {summary['n_seeds']} draft seeds ({out})",
            table.to_string(index=False)]
    for k, v in summary["paired_diffs"].items():
        if v["p_title"]["n"]:
            text.append(f"  {k:<26} dP(po) {v['p_playoffs']['mean']:+.3f} ±{v['p_playoffs']['se']:.3f}  "
                        f"dP(title) {v['p_title']['mean']:+.3f} ±{v['p_title']['se']:.3f}  (n={v['p_title']['n']})")
    emit({"ok": True, "sim_version": SIM_VERSION, "market_truth": diag, **summary,
           "table": table.to_dict(orient="records")}, as_json, "\n".join(text))
