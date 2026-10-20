"""`fantasy inseason ...` — position-aware lineups, streaming ranker, trade evaluation (analysis only)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer

from fantasy.cli_common import emit, fail
from fantasy.inseason import INSEASON_VERSION

app = typer.Typer(no_args_is_help=True, help="In-season analysis: lineups, streaming, trades (never touches Yahoo).")

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
LeagueOpt = Annotated[Path | None, typer.Option("--league-file", help="Rosters JSON ({my_team, teams: {id: {name, players}}})")]
DraftOpt = Annotated[str | None, typer.Option("--draft-id", envvar="FANTASY_DRAFT_ID", help="Rosters from this draft")]
TeamOpt = Annotated[int | None, typer.Option("--team", help="Team id (default: my team)")]
ProjOpt = Annotated[Path | None, typer.Option("--projections", help="Projections CSV (default data/processed)")]
NbaOpt = Annotated[Path | None, typer.Option("--nba-schedule", help="NBA games CSV (default data/raw/bbref_schedule)")]


def _league(league_file: Path | None, draft_id: str | None, as_json: bool):
    from fantasy.sim.league import from_draft, from_json

    if league_file:
        return from_json(league_file)
    from fantasy.draft import store

    draft = store.get_draft(draft_id)
    if not draft:
        fail("no rosters: pass --league-file or --draft-id", as_json, code="not_found")
    assert draft is not None
    return from_draft(draft, store.get_picks(draft["draft_id"]))


def _days(start: str, end: str) -> list[date]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [a + timedelta(i) for i in range((b - a).days + 1)]


def _nba(proj, nba_schedule: Path | None):
    from fantasy.sim.schedule import season_schedule

    nba, _ = season_schedule(nba_schedule, sorted({str(t) for t in proj.df["team"].dropna()}))
    return nba


@app.command("lineup")
def lineup_cmd(
    start: Annotated[str, typer.Option("--start", help="First day YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--end", help="Last day YYYY-MM-DD")],
    league_file: LeagueOpt = None, draft_id: DraftOpt = None, team: TeamOpt = None,
    sims: Annotated[int, typer.Option("--sims", help="0 = everyone healthy; >0 = Monte Carlo with p_play = gp/82")] = 0,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    projections: ProjOpt = None, nba_schedule: NbaOpt = None, as_json: JsonOpt = False,
) -> None:
    """Position-aware daily lineups: games available vs started, and games lost to slot rules."""
    from fantasy.inseason.lineup import PlayerSpec, simulate_lineups, team_days
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections, parse_positions
    from fantasy.model.valuation import compute_values

    lg = _league(league_file, draft_id, as_json)
    t = team or lg.my_team
    proj, st = load_projections(projections), load_settings()
    values, _, _ = compute_values(proj, st)
    df = proj.df
    specs = []
    for p in lg.rosters[t]:
        if p not in df.index:
            continue
        is_out = str(df.loc[p, "status"]) in ("out", "out_for_season")
        specs.append(PlayerSpec(p, str(df.loc[p, "name"]), str(df.loc[p, "team"]), parse_positions(df.loc[p, "positions"]),
                                float(values["value"].get(p, 0.0)),
                                0.0 if is_out else (min(1.0, float(df.loc[p, "gp"]) / 82) if sims > 0 else 1.0)))
    days = _days(start, end)
    res = simulate_lineups(specs, st.starting_slots, days, team_days(_nba(proj, nba_schedule).games, days), n_sims=sims, seed=seed)
    out: dict[str, Any] = {"ok": True, "version": INSEASON_VERSION, "team": t, "summary": res.summary(), "players": res.per_player(),
           "days": res.per_day(), "notes": res.notes}
    s = out["summary"]
    lines = [f"team {t} {start}..{end}: {s['player_games']} player-games, {s['starts']} starts, "
             f"{s['lost_to_positions']} lost to positions, {s['lost_to_slot_count']} lost to the 10-slot count"]
    lines += [f"  {p['name']:<24} {p['positions']:<10} games {p['games']:>2}  starts {p['exp_starts']}" for p in out["players"]]
    emit(out, as_json, "\n".join(lines))


@app.command("stream")
def stream_cmd(
    day: Annotated[str, typer.Option("--date", help="Today YYYY-MM-DD (default: today); the scan covers the rest of "
                                     "this matchup week")] = "",
    league_file: LeagueOpt = None, team: TeamOpt = None,
    free_agents: Annotated[Path | None, typer.Option("--free-agents", help="Yahoo free-agent capture (default newest "
                                                     "data/raw/yahoo/free_agents_*.json)")] = None,
    roster: Annotated[Path | None, typer.Option("--roster", help="Yahoo roster capture for tags/positions (default "
                                                "newest data/raw/yahoo/league_rosters_*.json)")] = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON (default "
                                                  "data/manual/league_schedule.json)")] = None,
    drop: Annotated[str, typer.Option("--drop", help="Drop candidates, comma-separated (default: my 3 lowest values)")] = "",
    adds_used: Annotated[int, typer.Option("--adds-used", help="Adds already used this matchup week")] = 0,
    accumulated: Annotated[Path | None, typer.Option("--accumulated", help='JSON {"me": {fgm: ..}, "opp": {..}} '
                                                     "raw totals already banked this week")] = None,
    week_sims: Annotated[int, typer.Option("--week-sims", help="Paired matchup Monte Carlo sims (shortlist)")] = 4000,
    season_sims: Annotated[int, typer.Option("--season-sims", help="Paired season sims per shortlisted pair "
                                             "(0 = skip the season check)")] = 2000,
    season_top: Annotated[int, typer.Option("--season-top", help="Pairs that get a season sim")] = 8,
    top: Annotated[int, typer.Option("--top", help="Extra pairs listed beyond the shortlist")] = 10,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    window_only: Annotated[str, typer.Option("--window-only", help="Legacy inseason-0.1.0 ranker: START..END window, "
                                             "opponent-agnostic G-scores, no season check")] = "",
    projections: ProjOpt = None, nba_schedule: NbaOpt = None, as_json: JsonOpt = False,
) -> None:
    """Add/drop pairs for THIS matchup week: Δ expected cats and ΔP(win) vs the real opponent (position-aware,
    matchup weights), the drop's season cost (paired season sim ΔP(playoffs)/ΔP(title) ± SE), waiver timing, adds
    budget. Recommends nothing unless a pair beats 2 SE. Analysis only; every add/drop needs the user's approval."""
    from fantasy.data.provenance import sha256_file
    from fantasy.inseason.freeagents import load_free_agents
    from fantasy.inseason.possim import PositionAwareSimulator
    from fantasy.inseason.streamscan import StreamParams, scan_streams
    from fantasy.league.settings import load_settings
    from fantasy.lineup.cli import LEAGUE_FILE, YAHOO_DIR, newest_capture
    from fantasy.lineup.plan import generic_per_game_values
    from fantasy.lineup.players import load_team
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.engine import SimParams
    from fantasy.sim.league import from_json
    from fantasy.sim.report import paired_delta
    from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH, load_nba_schedule, load_schedule_json

    if window_only:
        return _legacy_stream(window_only, league_file, team, free_agents, drop, top, projections, nba_schedule, as_json)
    d0 = date.fromisoformat(day) if day else date.today()
    lf = league_file or LEAGUE_FILE
    lg = from_json(lf)
    raw_league = json.loads(Path(lf).read_text())
    me_id = team or lg.my_team
    st = load_settings().model_copy(update={"num_teams": len(lg.rosters)})
    proj = load_projections(projections)
    sched = schedule or LEAGUE_SCHEDULE_PATH
    ms = load_schedule_json(sched)
    fw = next((w for w in (ms.calendar or []) if w.start <= d0 <= w.end), None)
    if fw is None:
        fail(f"{d0} is not inside any week of {sched}", as_json)
    assert fw is not None
    opp_id = ms.opponent(me_id, fw.week)
    if opp_id is None:
        fail(f"no opponent for team {me_id} in week {fw.week}", as_json)
    nba = load_nba_schedule(nba_schedule)
    if nba is None:
        fail("NBA schedule not cached (run `fantasy sim fetch-schedule`)", as_json)
    cap = roster or newest_capture()
    fa_path = free_agents or (sorted(YAHOO_DIR.glob("free_agents_*.json")) or [None])[-1]
    if fa_path is None:
        fail("no free-agent capture: pass --free-agents", as_json)

    def entry(t: int) -> dict:
        e = raw_league["teams"][str(t)]
        return e if isinstance(e, dict) else {"players": e}

    me, w1 = load_team(me_id, proj.df, lg.rosters[me_id], cap, entry(me_id))
    opp, w2 = load_team(opp_id, proj.df, lg.rosters[opp_id], cap, entry(opp_id))
    fas, w3 = load_free_agents(Path(fa_path), d0)
    taken = {p for ps in lg.rosters.values() for p in ps}
    fas = [f for f in fas if f.player_id not in taken]
    ctx = build_context(proj, st, lg.team_ids, schedule_file=sched, nba=nba)
    drops = None
    if drop.strip():
        from fantasy.draft.names import NameResolutionError, resolve_player

        try:
            drops = [resolve_player(x, ctx.values) for x in drop.split(",") if x.strip()]
        except NameResolutionError as e:
            fail(str(e), as_json, exit_code=2)
        bad = [p for p in drops if p not in lg.rosters[me_id]]
        if bad:
            fail(f"not on my roster: {bad}", as_json, exit_code=2)
    params = StreamParams(week_sims=week_sims, season_sims=season_sims, season_top=season_top, top=top, seed=seed)
    season_eval = None
    if season_sims > 0:
        sim = PositionAwareSimulator(ctx.proj.df, ctx.values, ctx.replacement, ctx.settings, ctx.nba, ctx.calendar,
                                     ctx.matchups, SimParams(n_sims=season_sims, seed=seed))
        base = sim.run(lg)

        def season_eval(add: str, dr: str | None, eff: date) -> dict:
            new = lg.copy()
            new.rosters[me_id] = [*new.rosters[me_id], add]
            windows: dict = {add: (eff, None)}
            if dr is not None:
                windows[dr] = (None, eff)
            return paired_delta(base, sim.run(new, windows), me_id)

    acc = json.loads(accumulated.read_text()) if accumulated else {}
    days = _days(d0.isoformat(), fw.end.isoformat())
    rep = scan_streams(proj.df, st, nba, me, opp, fas, days, generic_per_game_values(proj, st), ctx.values["value"],
                       params, drops, acc.get("me"), acc.get("opp"), season_eval, adds_used)
    warnings = w1 + w2 + w3
    if d0 > fw.start and accumulated is None:
        warnings.append(f"mid-week without --accumulated: stats banked {fw.start}..{d0 - timedelta(days=1)} assumed 0")
    out: dict[str, Any] = {
        "ok": True, "version": INSEASON_VERSION, "team": me_id, "week": fw.week,
        "opponent": {"team_id": opp_id, "name": lg.team_name(opp_id)}, **rep,
        "labels": {
            "week_*": "model: lineup-0.2.0 analytic normal matchup model (fitted game noise and projection error; "
                      "availability priors: rest_share, injury-tag priors); week_mc = its paired Monte Carlo (simulated)",
            "season": f"simulated: position-aware season sim ({SIM_LABEL}), paired, {season_sims} sims; ± = Monte Carlo "
                      "SE only (excludes projection/model error); other teams never add/drop",
            "season_proxy_d_value": "valuation value(add) - value(drop): season G-score totals (screen only)",
            "effective": "ASSUMPTION: free agent plays from today; waiver claim from the date it clears; the claim "
                         "may lose to a team higher in the rolling waiver order"},
        "inputs": {"league_file": str(lf), "roster_capture": str(cap) if cap else None, "free_agents": str(fa_path),
                   "free_agents_sha256": sha256_file(Path(fa_path)), "schedule": str(sched),
                   "projections": proj.path, "projections_sha256": proj.sha256},
        "warnings": warnings + ctx.warnings,
        "note": "Analysis only: never touches Yahoo. Every add/drop needs the user's approval (txn ledger).",
    }
    emit(out, as_json, _stream_text(out))


SIM_LABEL = "season-sim, PositionAwareSimulator"


def _fmt_d(d: dict | None, k: str) -> str:
    if not d:
        return "-"
    x = d[k]
    return f"{x['delta'] * 100:+.1f}±{x['delta_se'] * 100:.1f}"


def _stream_text(out: dict) -> str:
    b = out["baseline"]
    lines = [f"Week {out['week']} {out['window'][0]}..{out['window'][1]} vs {out['opponent']['name']}: "
             f"E[cats] {b['exp_cats']:.2f}, P(win) {b['p_win']:.3f} (analytic). Adds {out['adds']['used']}/"
             f"{out['adds']['max_weekly']} used. Pairs scanned {out['n_pairs_scanned']}.",
             f"{'add':<22} {'drop':<20} {'own':<10} {'eff':<10} {'st/g':>5} {'ΔE[c]':>7} {'ΔPwin':>7} "
             f"{'MC ΔPwin':>12} {'ΔPpo pp':>10} {'ΔPtitle pp':>11}"]
    for r in out["pairs"]:
        mc = r.get("week_mc")
        mcs = f"{mc['d_p_win']:+.3f}±{mc['d_p_win_se']:.3f}" if mc else "-"
        s = r.get("season")
        lines.append(f"{r['add_name'][:22]:<22} {r['drop_name'][:20]:<20} {r['ownership']:<10} {r['effective'][5:]:<10} "
                     f"{r['add_starts']}/{r['add_games']:<3} {r['week_d_exp_cats']:+7.3f} {r['week_d_p_win']:+7.3f} "
                     f"{mcs:>12} {_fmt_d(s, 'p_playoffs'):>10} {_fmt_d(s, 'p_title'):>11} {r.get('season_verdict', '')}")
    if out.get("blocked"):
        lines.append(f"BLOCKED: {out['blocked']}")
    rec = out.get("recommendation")
    lines.append("Recommendation: " + (f"add {rec['add_name']} / drop {rec['drop_name']} ({rec['why']}); needs approval"
                                       if rec else "none (no pair's season effect beats 2 SE)"))
    for w in out.get("week_only", []):
        lines.append(f"  week-only gain: {w['add_name']} / {w['drop_name']}: ΔP(win) {w['week_mc']['d_p_win']:+.3f} ± "
                     f"{w['week_mc']['d_p_win_se']:.3f}; season ΔP(title) lower bound "
                     f"{w['season_title_lower_bound'] * 100:+.1f} pp")
    lines += [f"warning: {w}" for w in out["warnings"]]
    return "\n".join(lines)


def _legacy_stream(window: str, league_file: Path | None, team: int | None, free_agents: Path | None, drop: str,
                   top: int, projections: Path | None, nba_schedule: Path | None, as_json: bool) -> None:
    from fantasy.inseason.lineup import team_days
    from fantasy.inseason.stream import rank_streamers
    from fantasy.inseason.trade import rostered
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections

    start, _, end = window.partition("..")
    lg = _league(league_file, None, as_json)
    t = team or lg.my_team
    proj, st = load_projections(projections), load_settings()
    cands, flags = None, {}
    if free_agents:
        fa = json.loads(Path(free_agents).read_text()).get("players", [])
        cands = [p["player_id"] for p in fa if p.get("player_id")]
        flags = {p["player_id"]: f"Yahoo {p['status']}" for p in fa if p.get("player_id") and p.get("status")}
    out_ids = {p for p in flags if flags[p] in ("Yahoo O", "Yahoo INJ")}
    days = _days(start, end or start)
    rep = rank_streamers(proj, st, lg.rosters[t], rostered(lg), days, team_days(_nba(proj, nba_schedule).games, days),
                         drop=drop or None, out=out_ids, flags=flags, top=top, candidates=cands)
    out: dict[str, Any] = {"ok": True, "version": INSEASON_VERSION, "team": t, **rep,
                           "caveat": "Legacy window gain only (opponent-agnostic); no season check."}
    lines = [f"{start}..{end}, drop {rep['drop']['name']} (window cost {rep['drop']['window_cost']})"]
    lines += [f"  {c['name']:<24} {c['team']} {c['positions']:<10} games {c['games']} starts {c['starts']} "
              f"gain {c['gain']:+.2f} {c['flag']}" for c in rep["candidates"]]
    emit(out, as_json, "\n".join(lines))


@app.command("trade")
def trade_cmd(
    partner: Annotated[int, typer.Option("--partner", help="Other team's id")],
    give: Annotated[str, typer.Option("--give", help="My players, comma-separated (name or id)")],
    get: Annotated[str, typer.Option("--get", help="Their players, comma-separated")],
    league_file: LeagueOpt = None, draft_id: DraftOpt = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="Matchup schedule JSON (team ids as in the league file)")] = None,
    sims: Annotated[int, typer.Option("--sims")] = 4000, seed: Annotated[int, typer.Option("--seed")] = 0,
    refill_skip: Annotated[int, typer.Option("--refill-skip", help="Short roster adds the (k+1)-th best free agent (0 = best)")] = 0,
    position_aware: Annotated[bool, typer.Option("--position-aware/--position-blind")] = True,
    projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Paired season simulation of a trade: both teams' P(playoffs)/P(title)/category wins, with SEs."""
    from fantasy.draft.names import NameResolutionError, resolve_player
    from fantasy.inseason.possim import PositionAwareSimulator
    from fantasy.inseason.trade import Trade, evaluate_trades
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.engine import SimParams

    lg = _league(league_file, draft_id, as_json)
    st = load_settings().model_copy(update={"num_teams": len(lg.rosters)})
    ctx = build_context(load_projections(projections), st, lg.team_ids, schedule_file=schedule)
    try:
        g = [resolve_player(x, ctx.values) for x in give.split(",") if x.strip()]
        r = [resolve_player(x, ctx.values) for x in get.split(",") if x.strip()]
    except NameResolutionError as e:
        fail(str(e), as_json, exit_code=2)
    params = SimParams(n_sims=sims, seed=seed)
    sim = (PositionAwareSimulator(ctx.proj.df, ctx.values, ctx.replacement, ctx.settings, ctx.nba, ctx.calendar,
                                  ctx.matchups, params) if position_aware else ctx.simulator(params))
    try:
        _, rows = evaluate_trades(sim, lg, lg.my_team, [Trade(partner, g, r)], ctx.values, st.num_rounds,
                                  refill_skip=refill_skip)
    except ValueError as e:
        fail(str(e), as_json, exit_code=2)
    row = rows[0]
    out: dict[str, Any] = {"ok": True, "version": INSEASON_VERSION, "n_sims": sims, "seed": seed, "refill_skip": refill_skip,
           "position_aware": position_aware, **row, "warnings": ctx.warnings,
           "note": "Analysis only. Trades always need the user's approval; never propose on Yahoo from here."}
    lines = [row["trade"]]
    for side, d in (("me", row["me"]), (f"team {partner}", row["partner_delta"])):
        lines.append(f"  {side:<8} " + "  ".join(f"{k} {d[k]['delta']:+.4f} ± {d[k]['delta_se']:.4f}"
                                                 for k in ("p_playoffs", "p_title", "exp_cat_wins")))
    emit(out, as_json, "\n".join(lines))


@app.command("trade-scan")
def trade_scan_cmd(
    league_file: LeagueOpt = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON (default "
                                                  "data/manual/league_schedule.json)")] = None,
    free_agents: Annotated[Path | None, typer.Option("--free-agents", help="Yahoo free-agent capture for refills "
                                                     "(default newest data/raw/yahoo/free_agents_*.json)")] = None,
    market_projections: Annotated[Path | None, typer.Option("--market-projections", help="Conservative projections CSV "
                                                            "(`fantasy data alt-projections --blend market --out ..`; "
                                                            "default var/projections_alt/market.csv)")] = None,
    no_market: Annotated[bool, typer.Option("--no-market", help="Blend projections only")] = False,
    partner: Annotated[str, typer.Option("--partner", help="Only these team ids, comma-separated")] = "",
    kinds: Annotated[str, typer.Option("--kinds", help="1-1,2-1 (I give 2),1-2 (I get 2)")] = "1-1,2-1,1-2",
    top_per_team: Annotated[int, typer.Option("--top-per-team", help="Per partner, by the regular-season screen")] = 3,
    top_po_per_team: Annotated[int, typer.Option("--top-po-per-team", help="Per partner, extra by the playoff-weeks "
                                                 "screen (0 = off)")] = 1,
    max_trades: Annotated[int, typer.Option("--max-trades", help="Shortlist size cap (simulated trades)")] = 44,
    partner_tol: Annotated[float, typer.Option("--partner-tol", help="PRIOR: partner proxy may fall this much "
                                               "(cats/week) and still pass the screen")] = 0.05,
    naive_tol: Annotated[float, typer.Option("--naive-tol", help="PRIOR: partner may lose this much summed valuation "
                                             "value (by rankings) and still pass the screen")] = 0.5,
    sims: Annotated[int, typer.Option("--sims", help="Paired season sims per trade and projection set")] = 2000,
    refill_skip: Annotated[int, typer.Option("--refill-skip", help="Refill with the (k+1)-th best realistic free "
                                             "agent (0 = best; 11 = everyone else claims first)")] = 0,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    top: Annotated[int, typer.Option("--top", help="Rows printed (text mode)")] = 10,
    projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Scan 1-for-1 and 2-for-1 trades with every team: cheap all-play category screen, then paired position-aware
    season sims of the shortlist under the blend AND the conservative market projections; flags trades that help
    under both, with the partner's Δ (acceptability) and schedule decomposition. Analysis only."""
    from fantasy.data.provenance import REPO_ROOT, sha256_file
    from fantasy.inseason.freeagents import load_free_agents, realistic_refills
    from fantasy.inseason.possim import PositionAwareSimulator
    from fantasy.inseason.tradescan import (
        ScanParams,
        Screen,
        games_per_week,
        screen_trades,
        simulate_shortlist,
        start_shares,
        trade_rows,
    )
    from fantasy.league.settings import load_settings
    from fantasy.lineup.cli import LEAGUE_FILE, YAHOO_DIR
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.engine import SimParams
    from fantasy.sim.league import from_json
    from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH

    lf = league_file or LEAGUE_FILE
    lg = from_json(lf)
    me_id = lg.my_team
    st = load_settings().model_copy(update={"num_teams": len(lg.rosters)})
    sched = schedule or LEAGUE_SCHEDULE_PATH
    proj = load_projections(projections)
    ctx = build_context(proj, st, lg.team_ids, schedule_file=sched)
    fa_path = free_agents or (sorted(YAHOO_DIR.glob("free_agents_*.json")) or [None])[-1]
    warnings = list(ctx.warnings)
    taken = {p for ps in lg.rosters.values() for p in ps}
    if fa_path is not None:
        fas, w = load_free_agents(Path(fa_path), date.today())
        warnings += w
        pool = [p for p in realistic_refills(fas, ctx.values["value"].to_dict()) if p not in taken]
    else:
        pool = [p for p in ctx.values.index if p not in taken]
        warnings.append("no free-agent capture: refills use the best unrostered projected player (optimistic)")
    pool = pool[refill_skip:]
    sets = ["blend"]
    sims_by_set = {"blend": PositionAwareSimulator(ctx.proj.df, ctx.values, ctx.replacement, ctx.settings, ctx.nba,
                                                   ctx.calendar, ctx.matchups, SimParams(n_sims=sims, seed=seed))}
    mkt_path = market_projections or REPO_ROOT / "var" / "projections_alt" / "market.csv"
    mkt_info = None
    if not no_market:
        if Path(mkt_path).exists():
            mproj = load_projections(Path(mkt_path))
            mctx = build_context(mproj, st, lg.team_ids, schedule_file=sched, nba=ctx.nba)
            sims_by_set["market"] = PositionAwareSimulator(mctx.proj.df, mctx.values, mctx.replacement, mctx.settings,
                                                           mctx.nba, mctx.calendar, mctx.matchups,
                                                           SimParams(n_sims=sims, seed=seed))
            sets.append("market")
            mkt_info = {"path": str(mkt_path), "sha256": mproj.sha256}
        else:
            warnings.append(f"market projections {mkt_path} not found: run `fantasy data alt-projections --blend market "
                            "--out var/projections_alt/market.csv` (or pass --market-projections); blend only, so "
                            "no trade can be flagged helps_both")
    params = ScanParams(top_per_team=top_per_team, max_trades=max_trades, partner_tol=partner_tol, naive_tol=naive_tol,
                        kinds=tuple(k.strip() for k in kinds.split(",") if k.strip()), sims=sims, seed=seed,
                        partners=tuple(int(x) for x in partner.split(",") if x.strip()) or None,
                        top_po_per_team=top_po_per_team)
    regular = [w.week for w in ctx.calendar if w.week < min(st.playoff_weeks)]
    reg_days = [d for w in ctx.calendar if w.week in regular for d in w.days()]
    base = sims_by_set["blend"].run(lg)
    gpw = games_per_week(ctx.nba, ctx.calendar, regular)
    shares = start_shares(lg, ctx.proj.df, ctx.values, st, ctx.nba, reg_days, params.start_share_sims, seed)
    screen = Screen(base, lg, ctx.proj.df, ctx.values, st, gpw, shares)
    po_weeks = list(st.playoff_weeks)
    po_screen = Screen(base, lg, ctx.proj.df, ctx.values, st, games_per_week(ctx.nba, ctx.calendar, po_weeks), shares,
                       weeks=po_weeks) if top_po_per_team > 0 else None
    rows, n_enum = screen_trades(screen, lg, me_id, ctx.values, st.num_rounds, pool, params, po_screen)
    simulate_shortlist(rows, lg, me_id, sims_by_set, params.sig)
    table = trade_rows(rows, ctx.values, ctx.proj.df, ctx.nba, ctx.calendar, st.playoff_weeks, gpw, lg, sets)
    names = ctx.values["name"]
    out: dict[str, Any] = {
        "ok": True, "version": INSEASON_VERSION, "team": me_id, "projection_sets": sets,
        "n_enumerated": n_enum, "n_simulated": len(rows),
        "refill_free_agent": {"player_id": pool[0], "name": str(names.get(pool[0], pool[0]))} if pool else None,
        "start_share_by_rank": [round(float(x), 3) for x in shares[:13]],
        "trades": table,
        "helps_both": [r["trade"] for r in table if r["helps_both"]],
        "rule": "helps_both = season verdict 'helps' (ΔP(playoffs) or ΔP(title) > 2 paired SE, neither < -2 SE) under "
                "the blend AND the market projections",
        "labels": {"sim": f"simulated: position-aware season sim, paired, {sims} sims per set; ± = Monte Carlo SE only",
                   "screen": "deterministic proxy (all-play expected cats/week, anchored on the base sim); start shares "
                             "measured by the lineup Monte Carlo with gp/82 iid availability (prior)",
                   "partner_tol": "prior", "refill": "ASSUMPTION: the short roster wins the claim for the best "
                                                     "realistic free agent of the capture"},
        "params": {**params.to_dict(), "refill_skip": refill_skip},
        "inputs": {"league_file": str(lf), "schedule": str(sched), "projections": proj.path,
                   "projections_sha256": proj.sha256, "market_projections": mkt_info,
                   "free_agents": str(fa_path) if fa_path else None,
                   "free_agents_sha256": sha256_file(Path(fa_path)) if fa_path else None},
        "warnings": warnings,
        "note": "Analysis only. Trades always need the user's approval; never propose on Yahoo from here.",
    }
    lines = [f"{n_enum} trades enumerated, {len(rows)} simulated ({', '.join(sets)}; {sims} paired sims each). "
             f"Refill FA: {out['refill_free_agent']['name'] if pool else '-'}"]
    for r in table[:top]:
        s = "  ".join(f"{k}: me ΔPpo {_fmt_d(r['sim'][k]['me'], 'p_playoffs')} ΔPtitle "
                      f"{_fmt_d(r['sim'][k]['me'], 'p_title')} | partner ΔPpo {_fmt_d(r['sim'][k]['partner'], 'p_playoffs')}"
                      for k in sets if k in r["sim"])
        lines.append(f"{'*' if r['helps_both'] else ' '} {r['trade']}\n      {s}")
    lines += [f"warning: {w}" for w in warnings]
    emit(out, as_json, "\n".join(lines))
