"""`fantasy lineup plan`: position-aware, matchup-aware daily lineup (and weekly plan) for my team."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from fantasy.data.provenance import MANUAL_DIR, RAW_DIR, sha256_file
from fantasy.league.settings import load_settings
from fantasy.lineup.plan import (
    LINEUP_MODEL_VERSION,
    LineupParams,
    build_plan,
    generic_per_game_values,
)
from fantasy.lineup.players import REST_SHARE, load_team
from fantasy.model.lines import load_projections
from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH, load_nba_schedule, load_schedule_json

app = typer.Typer(no_args_is_help=True, help="Daily lineup optimizer (position-aware, matchup-aware).")

ET = ZoneInfo("America/New_York")
LEAGUE_FILE = MANUAL_DIR / "league_rosters_2026-27.json"
YAHOO_DIR = RAW_DIR / "yahoo"


def newest_capture(directory: Path = YAHOO_DIR) -> Path | None:
    files = sorted(directory.glob("league_rosters_*.json"))
    return files[-1] if files else None


def _fail(msg: str, as_json: bool, code: str = "error", exit_code: int = 1, **extra: object) -> None:
    if as_json:
        typer.echo(json.dumps({"ok": False, "code": code, "error": msg, **extra}, default=str))
    else:
        typer.echo(f"error ({code}): {msg}", err=True)
        for k, v in extra.items():
            typer.echo(f"  {k}: {v}", err=True)
    raise typer.Exit(exit_code)


_TS = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T~]+(\d{1,2}:\d{2}(?::\d{2})?))?\s*(UTC|Z|ET|EDT|EST)?")


def parse_timestamp_et(text: object) -> tuple[date | None, str | None]:
    """US-Eastern calendar date of a capture timestamp: an ISO datetime (`captured_at`) or the first
    'YYYY-MM-DD[ ~HH:MM] [UTC|ET]' in a free-text `source`. A time without a zone is taken as UTC (captures and
    sync write UTC); a bare date is taken as given. Returns (date, the matched text)."""
    if not text:
        return None, None
    t = str(text)
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(ET).date(), t
    except ValueError:
        pass
    m = _TS.search(t)
    if not m:
        return None, None
    d, hm, tz = m.group(1), m.group(2), m.group(3)
    if hm is None:
        return date.fromisoformat(d), m.group(0)
    dt = datetime.fromisoformat(f"{d}T{hm if hm.count(':') == 2 else hm + ':00'}")
    if tz in ("ET", "EDT", "EST"):
        return dt.date(), m.group(0)
    return dt.replace(tzinfo=UTC).astimezone(ET).date(), m.group(0)


def slots_as_of(league: dict | None, my_team_entry: dict | None, capture: Path | None) -> tuple[date | None, str]:
    """When were the current-slot data read from Yahoo (ET date) and from which file."""
    if my_team_entry and my_team_entry.get("lineup") is not None and league:
        for key in ("captured_at",):
            if league.get(key):
                return parse_timestamp_et(league[key])[0], f"league file {key}"
        m = re.search(r"\(captured ([^)]+)\)", str(league.get("source") or ""))
        if m:
            return parse_timestamp_et(m.group(1))[0], "league file source (captured ...)"
        return parse_timestamp_et(league.get("synced_at"))[0], "league file synced_at"
    if capture is not None and capture.exists():
        raw = json.loads(capture.read_text())
        if isinstance(raw, dict):
            if raw.get("captured_at"):
                return parse_timestamp_et(raw["captured_at"])[0], f"{capture.name} captured_at"
            return parse_timestamp_et(raw.get("source"))[0], f"{capture.name} source"
    return None, "none"


@app.command("plan")
def plan(
    day: Annotated[str, typer.Option("--date", help="Lineup date YYYY-MM-DD (default today)")] = "",
    week: Annotated[bool, typer.Option("--week", help="Include the day-by-day plan for the rest of the week")] = False,
    roster: Annotated[Path | None, typer.Option("--roster", "--capture", help="Yahoo roster capture or plain roster "
                                                "JSON (default newest data/raw/yahoo/league_rosters_*.json)")] = None,
    current_slots: Annotated[Path | None, typer.Option("--current-slots", help="JSON {player_id: slot} of my CURRENT "
                                                       "Yahoo slots (read just now); overrides capture/league slots")] = None,
    allow_stale: Annotated[bool, typer.Option("--allow-stale", help="Plan even if the slot data were not read from "
                                              "Yahoo on --date (ET); changes_vs_current may then be wrong")] = False,
    locked: Annotated[str, typer.Option("--locked", help="Comma-separated player ids whose game already started "
                                        "today: pinned to their current slot")] = "",
    rest_share: Annotated[float, typer.Option("--rest-share", help="PRIOR: share of a healthy player's missed games "
                                              "that are one-game rests (1.0 = season-average gp/82)")] = REST_SHARE,
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

    as_of, as_of_src = slots_as_of(league, team_entry(my_team), cap)
    if current_slots is None and as_of != d0:
        if not allow_stale:
            _fail(f"current-slot data are from {as_of} (ET) via {as_of_src}, not {d0}: changes_vs_current would diff "
                  "against stale slots. Re-capture Yahoo (fantasy league sync), pass --current-slots, or --allow-stale",
                  as_json, code="stale_capture", exit_code=2, slots_as_of=as_of, slots_source=as_of_src)
    try:
        me, w1 = load_team(my_team, proj.df, ids(my_team), cap, team_entry(my_team))
        # a plain (single-team) --roster describes my team only; the opponent comes from the newest capture
        opp_cap = cap
        if roster is not None and "teams" not in json.loads(roster.read_text()):
            opp_cap = newest_capture()
        opp, w2 = load_team(opp_id, proj.df, ids(opp_id), opp_cap, team_entry(opp_id))
    except ValueError as e:
        _fail(str(e), as_json)
    pre_warnings: list[str] = []
    if current_slots is not None:
        raw_cs = json.loads(current_slots.read_text())
        cs = raw_cs.get("slots", raw_cs) if isinstance(raw_cs, dict) else {}
        unknown = sorted(set(cs) - {p.player_id for p in me})
        if unknown:
            pre_warnings.append(f"--current-slots: ids not on my roster ignored: {unknown}")
        missing = [p.player_id for p in me if p.player_id not in cs]
        if missing:
            pre_warnings.append(f"--current-slots: no slot given for {missing}; using the capture/league slot")
        me = [replace(p, slot=cs[p.player_id]) if p.player_id in cs else p for p in me]
    elif as_of != d0:
        pre_warnings.append(f"STALE SLOTS (--allow-stale): current slots are from {as_of} (ET) via {as_of_src}; "
                            "changes_vs_current may be wrong -- prefer target_slots")
    if d0 > fw.start and accumulated is None:
        pre_warnings.append(f"MID-WEEK WITHOUT --accumulated: stats banked {fw.start}..{d0 - timedelta(days=1)} are "
                            "assumed 0 for both teams, so the category weights ignore the current score")
    acc = json.loads(accumulated.read_text()) if accumulated else {}
    days = [d0 + timedelta(days=k) for k in range((fw.end - d0).days + 1)]
    generic = generic_per_game_values(proj, settings)
    lock_ids = tuple(x.strip() for x in locked.split(",") if x.strip())
    params = LineupParams(n_sims=sims, seed=seed, protect_ratios=protect_ratios, rest_share=rest_share,
                          locked=lock_ids)
    try:
        out = build_plan(me, opp, proj.df, nba, days, settings, generic, acc.get("me"), acc.get("opp"), params)
    except ValueError as e:
        if str(e).startswith("infeasible_roster"):
            _fail(str(e), as_json, code="infeasible_roster", exit_code=2,
                  action="drop a player or keep an injured player in IL (more players than starting + bench slots)",
                  roster=[{"player_id": p.player_id, "slot": p.slot, "status": p.status} for p in me])
        _fail(str(e), as_json)
    names = {}
    if league:
        names = {int(k): (v.get("name") if isinstance(v, dict) else None) for k, v in league["teams"].items()}
    out = {"model_version": LINEUP_MODEL_VERSION, "week": fw.week, "week_start": fw.start.isoformat(),
           "week_end": fw.end.isoformat(), "my_team": my_team,
           "opponent": {"team_id": opp_id, "name": names.get(opp_id)}, **out}
    out["warnings"] = pre_warnings + w1 + w2 + out["warnings"]
    out["slots_as_of"] = {"date": as_of.isoformat() if as_of else None, "source": as_of_src,
                          "current_slots_file": str(current_slots) if current_slots else None}
    out["note"] = ("Run before the first tip-off of the day; after that pass --locked with the players whose games "
                   "have started (Yahoo locks each player at his own tip-off). Executor: set target_slots.")
    inputs = {"projections": (proj.path, proj.sha256), "league_file": (str(lf), sha256_file(lf) if lf.exists() else None),
              "roster_capture": (str(cap) if cap else None, sha256_file(cap) if cap else None),
              "league_schedule": (str(sched_path), sha256_file(sched_path)), "nba_schedule": (nba.source, None)}
    if current_slots:
        inputs["current_slots"] = (str(current_slots), sha256_file(current_slots))
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
        typer.echo(f"Decide at tip-off: {t['name']} ({t['status']}, p={t['p_play']}): {t['note']}")
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
