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
    start: Annotated[str, typer.Option("--start", help="First day a pickup can play (after waivers clear)")],
    end: Annotated[str, typer.Option("--end")],
    league_file: LeagueOpt = None, draft_id: DraftOpt = None, team: TeamOpt = None,
    free_agents: Annotated[Path | None, typer.Option("--free-agents", help="Yahoo capture JSON ({players: [{player_id, status}]}); default: projections minus rosters")] = None,
    drop: Annotated[str | None, typer.Option("--drop", help="Player to drop (default: lowest per-game value)")] = None,
    top: Annotated[int, typer.Option("--top")] = 15,
    projections: ProjOpt = None, nba_schedule: NbaOpt = None, as_json: JsonOpt = False,
) -> None:
    """Rank free agents by position-aware value added to my lineup over a date window."""
    from fantasy.inseason.lineup import team_days
    from fantasy.inseason.stream import rank_streamers
    from fantasy.inseason.trade import rostered
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections

    lg = _league(league_file, draft_id, as_json)
    t = team or lg.my_team
    proj, st = load_projections(projections), load_settings()
    cands, flags = None, {}
    if free_agents:
        fa = json.loads(Path(free_agents).read_text()).get("players", [])
        cands = [p["player_id"] for p in fa if p.get("player_id")]
        flags = {p["player_id"]: f"Yahoo {p['status']}" for p in fa if p.get("player_id") and p.get("status")}
    out_ids = {p for p in flags if flags[p] in ("Yahoo O", "Yahoo INJ")}
    days = _days(start, end)
    rep = rank_streamers(proj, st, lg.rosters[t], rostered(lg), days, team_days(_nba(proj, nba_schedule).games, days),
                         drop=drop, out=out_ids, flags=flags, top=top, candidates=cands)
    out: dict[str, Any] = {"ok": True, "version": INSEASON_VERSION, "team": t, **rep,
           "caveat": "Window gain only; check the drop's rest-of-season cost with `fantasy sim compare` and get the "
                     "user's approval before any add/drop."}
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
