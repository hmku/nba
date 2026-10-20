"""`fantasy lineup plan`: position-aware, matchup-aware daily lineup (and weekly plan) for my team."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

from fantasy.data.provenance import MANUAL_DIR, RAW_DIR, sha256_file
from fantasy.league.settings import load_settings
from fantasy.lineup.plan import (
    LINEUP_MODEL_VERSION,
    LineupParams,
    build_plan,
    generic_per_game_values,
)
from fantasy.lineup.players import load_team
from fantasy.model.lines import load_projections
from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH, load_nba_schedule, load_schedule_json

app = typer.Typer(no_args_is_help=True, help="Daily lineup optimizer (position-aware, matchup-aware).")

LEAGUE_FILE = MANUAL_DIR / "league_rosters_2026-27.json"
YAHOO_DIR = RAW_DIR / "yahoo"


def newest_capture(directory: Path = YAHOO_DIR) -> Path | None:
    files = sorted(directory.glob("league_rosters_*.json"))
    return files[-1] if files else None


def _fail(msg: str, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"error": msg}))
    else:
        typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(1)


@app.command("plan")
def plan(
    day: Annotated[str, typer.Option("--date", help="Lineup date YYYY-MM-DD (default today)")] = "",
    week: Annotated[bool, typer.Option("--week", help="Include the day-by-day plan for the rest of the week")] = False,
    roster: Annotated[Path | None, typer.Option("--roster", help="Yahoo roster capture or plain roster JSON "
                                                "(default newest data/raw/yahoo/league_rosters_*.json)")] = None,
    league_file: Annotated[Path | None, typer.Option("--league-file", help="League rosters JSON")] = None,
    opponent_slot: Annotated[int | None, typer.Option("--opponent-slot", help="Opponent team id (draft slot)")] = None,
    accumulated: Annotated[Path | None, typer.Option("--accumulated", help='JSON {"me": {fgm: .., ...}, "opp": {...}} '
                                                     "raw totals already banked this week")] = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON")] = None,
    nba_schedule: Annotated[Path | None, typer.Option("--nba-schedule", help="NBA games CSV")] = None,
    projections: Annotated[Path | None, typer.Option("--projections")] = None,
    sims: Annotated[int, typer.Option("--sims", help="Monte Carlo sims (0 = analytic only)")] = 20000,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    protect_ratios: Annotated[bool, typer.Option("--protect-ratios", help="Allow benching a player with a game "
                                                 "whose marginal value is negative (FG%/FT%/TO protection)")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        d0 = date.fromisoformat(day) if day else date.today()
    except ValueError:
        _fail(f"bad --date {day!r}", as_json)
    settings = load_settings()
    proj = load_projections(projections)
    lf = league_file or LEAGUE_FILE
    league = json.loads(lf.read_text()) if lf.exists() else None
    my_team = int(league.get("my_team", settings.my_slot)) if league else settings.my_slot
    sched_path = schedule or LEAGUE_SCHEDULE_PATH
    if not sched_path.exists():
        _fail(f"league schedule {sched_path} not found", as_json)
    ms = load_schedule_json(sched_path)
    fw = next((w for w in (ms.calendar or []) if w.start <= d0 <= w.end), None)
    if fw is None:
        _fail(f"{d0} is not inside any week of {sched_path}", as_json)
    opp_id = opponent_slot or ms.opponent(my_team, fw.week)
    if opp_id is None:
        _fail(f"no opponent for team {my_team} in week {fw.week}; pass --opponent-slot", as_json)
    nba = load_nba_schedule(nba_schedule)
    if nba is None:
        _fail("NBA schedule not cached (run `fantasy sim fetch-schedule`)", as_json)
    cap = roster or newest_capture()

    def ids(t: int) -> list[str] | None:
        if not league:
            return None
        team = league["teams"].get(str(t))
        if team is None:
            return None
        return list(team["players"] if isinstance(team, dict) else team)

    def team_entry(t: int) -> dict | None:
        team = (league or {}).get("teams", {}).get(str(t))
        return team if isinstance(team, dict) else None

    try:
        me, w1 = load_team(my_team, proj.df, ids(my_team), cap, team_entry(my_team))
        # a plain (single-team) --roster describes my team only; the opponent comes from the newest capture
        opp_cap = cap
        if roster is not None and "teams" not in json.loads(roster.read_text()):
            opp_cap = newest_capture()
        opp, w2 = load_team(opp_id, proj.df, ids(opp_id), opp_cap, team_entry(opp_id))
    except ValueError as e:
        _fail(str(e), as_json)
    acc = json.loads(accumulated.read_text()) if accumulated else {}
    days = [d0 + timedelta(days=k) for k in range((fw.end - d0).days + 1)]
    generic = generic_per_game_values(proj, settings)
    params = LineupParams(n_sims=sims, seed=seed, protect_ratios=protect_ratios)
    out = build_plan(me, opp, proj.df, nba, days, settings, generic, acc.get("me"), acc.get("opp"), params)
    names = {}
    if league:
        names = {int(k): (v.get("name") if isinstance(v, dict) else None) for k, v in league["teams"].items()}
    out = {"model_version": LINEUP_MODEL_VERSION, "week": fw.week, "week_start": fw.start.isoformat(),
           "week_end": fw.end.isoformat(), "my_team": my_team,
           "opponent": {"team_id": opp_id, "name": names.get(opp_id)}, **out}
    out["warnings"] = w1 + w2 + out["warnings"]
    inputs = {"projections": (proj.path, proj.sha256), "league_file": (str(lf), sha256_file(lf) if lf.exists() else None),
              "roster_capture": (str(cap) if cap else None, sha256_file(cap) if cap else None),
              "league_schedule": (str(sched_path), sha256_file(sched_path)), "nba_schedule": (nba.source, None)}
    if accumulated:
        inputs["accumulated"] = (str(accumulated), sha256_file(accumulated))
    out["inputs"] = {k: {"path": p, "sha256": h} for k, (p, h) in inputs.items()}
    if not week:
        out.pop("week_plan")
    if as_json:
        typer.echo(json.dumps(out, indent=1, default=str))
        return
    _print(out)


def _print(out: dict) -> None:
    typer.echo(f"Lineup {out['date']} (week {out['week']}, vs {out['opponent']['name'] or out['opponent']['team_id']})")
    for e in out["lineup"]:
        g = "game" if e["has_game"] else "no game"
        typer.echo(f"  {e['slot']:<5} {e['name']:<26} {g:<8} p={e['p_play']:.2f} v={e['value']:.4f}")
    for e in out["bench"] + out["il"]:
        g = "game" if e["has_game"] else "no game"
        typer.echo(f"  {e['slot']:<5} {e['name']:<26} {g}")
    if out["changes_vs_current"]:
        typer.echo("Moves vs current Yahoo lineup:")
        for c in out["changes_vs_current"]:
            typer.echo(f"  {c['name']}: {c['from']} -> {c['to']}")
    for t in out["decide_at_tipoff"]:
        typer.echo(f"Decide at tip-off: {t['name']} ({t['status']}, p={t['p_play']})")
    ex = out["expected"]
    typer.echo(f"Expected cats (analytic): optimized {ex['optimized']['expected_cats']:.2f}, naive "
               f"{ex['naive_season_value']['expected_cats']:.2f}, static {ex['static_current_lineup']['expected_cats']:.2f}; "
               f"P(win matchup) {ex['optimized']['p_win_matchup']:.3f}")
    mc = out.get("monte_carlo") or {}
    for name, r in (mc.get("plans") or {}).items():
        typer.echo(f"  MC {name}: E[cats] {r['expected_cats']:.3f} ± {r['expected_cats_se']:.3f}, "
                   f"P(win) {r['p_win_matchup']:.3f} ± {r['p_win_matchup_se']:.3f}")
    for d in out.get("week_plan", []):
        typer.echo(f"  {d['date']}: {d['players_with_game']} with game / {d['starting_slots']} slots, "
                   f"unused {d['unused_starts']} {d['unused_players']}")
    for w in out["warnings"]:
        typer.echo(f"warning: {w}")
