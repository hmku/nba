"""`fantasy lineup plan`: position-aware, matchup-aware daily lineup (and weekly plan) for my team."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, NoReturn

import pandas as pd
import typer

from fantasy.cli_common import failure
from fantasy.data.provenance import MANUAL_DIR, RAW_DIR, sha256_file
from fantasy.league.dates import ET, et_date
from fantasy.league.settings import load_settings
from fantasy.lineup.plan import (
    LINEUP_MODEL_VERSION,
    LineupParams,
    build_plan,
    generic_per_game_values,
)
from fantasy.lineup.players import REST_SHARE, RosterPlayer, load_team
from fantasy.model.lines import load_projections
from fantasy.ops.common import EXIT_ERROR, EXIT_USAGE
from fantasy.sim.schedule import (
    LEAGUE_SCHEDULE_PATH,
    FantasyWeek,
    NBASchedule,
    load_nba_schedule,
    load_schedule_json,
)

app = typer.Typer(no_args_is_help=True, help="Daily lineup optimizer (position-aware, matchup-aware).")

LEAGUE_FILE = MANUAL_DIR / "league_rosters_2026-27.json"
YAHOO_DIR = RAW_DIR / "yahoo"


def newest_capture(directory: Path = YAHOO_DIR) -> Path | None:
    files = sorted(directory.glob("league_rosters_*.json"))
    return files[-1] if files else None


def _fail(msg: str, as_json: bool, code: str = "error", exit_code: int = EXIT_ERROR, **extra: Any) -> NoReturn:
    """Exit with the shared failure contract (`fantasy.cli_common.failure`: ok/error/code/message + extra) on
    stdout with --json; otherwise `error (<code>): msg` plus one line per extra on stderr."""
    if as_json:
        typer.echo(json.dumps(failure(msg, code, **extra)))
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
    iso = et_date(t)
    if iso is not None:
        return iso, t
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
        if league.get("captured_at"):
            return parse_timestamp_et(league["captured_at"])[0], "league file captured_at"
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
                                                     "raw totals already banked this week (overrides the matchup "
                                                     "score)")] = None,
    score: Annotated[Path | None, typer.Option("--score", help="Matchup capture (scripts/yahoo/capture_matchup.js) "
                                               "to use as the banked totals instead of the newest saved "
                                               "matchup_score row")] = None,
    no_score: Annotated[bool, typer.Option("--no-score", help="Do not look up the newest saved matchup_score "
                                           "(banked totals 0 unless --accumulated/--score)")] = False,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON")] = None,
    nba_schedule: Annotated[Path | None, typer.Option("--nba-schedule", help="NBA games CSV")] = None,
    projections: Annotated[Path | None, typer.Option("--projections")] = None,
    sims: Annotated[int, typer.Option("--sims", help="Monte Carlo sims (0 = analytic only)")] = 20000,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    protect_ratios: Annotated[bool, typer.Option("--protect-ratios", help="Allow benching a player with a game "
                                                 "whose marginal value is negative (FG%/FT%/TO protection)")] = False,
    save: Annotated[bool, typer.Option("--save", help="Record the plan as a model_runs row (kind lineup_plan) for "
                                       "the dashboard's Lineup view; idempotent per (date, content)")] = False,
    use_rest: Annotated[bool, typer.Option("--rest", help="Read the saved matchup score from / write --save to "
                                           "Supabase over HTTPS (cloud sessions) instead of FANTASY_DB_URL")] = False,
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
    fw, opp_id = _week_and_opponent(d0, sched_path, my_team, opponent_slot, as_json)
    nba = load_nba_schedule(nba_schedule)
    if nba is None:
        _fail("NBA schedule not cached (run `fantasy sim fetch-schedule`)", as_json)
    cap = roster or newest_capture()

    as_of, as_of_src = slots_as_of(league, _team_entry(league, my_team), cap)
    if current_slots is None and as_of != d0 and not allow_stale:
        _fail(f"current-slot data are from {as_of} (ET) via {as_of_src}, not {d0}: changes_vs_current would diff "
              "against stale slots. Re-capture Yahoo (fantasy league sync), pass --current-slots, or --allow-stale",
              as_json, code="stale_capture", exit_code=EXIT_USAGE, slots_as_of=as_of, slots_source=as_of_src)
    me, opp, team_warnings = _load_rosters(league, my_team, opp_id, proj.df, roster, cap, as_json)
    acc, acc_source, acc_warnings = _resolve_accumulated(accumulated, score, no_score, use_rest, d0, fw, my_team,
                                                         opp_id, lf, sched_path, as_json)
    me, pre_warnings = _overlay_slots_and_warn(me, current_slots, d0, as_of, as_of_src, fw,
                                               has_accumulated=acc_source["kind"] != "none",
                                               lookup_note=acc_source.get("note"))
    pre_warnings += acc_warnings
    days = [d0 + timedelta(days=k) for k in range((fw.end - d0).days + 1)]
    generic = generic_per_game_values(proj, settings)
    lock_ids = tuple(x.strip() for x in locked.split(",") if x.strip())
    params = LineupParams(n_sims=sims, seed=seed, protect_ratios=protect_ratios, rest_share=rest_share,
                          locked=lock_ids)
    try:
        out = build_plan(me, opp, proj.df, nba, days, settings, generic, acc.get("me"), acc.get("opp"), params)
    except ValueError as e:
        if str(e).startswith("infeasible_roster"):
            theirs = str(e).startswith("infeasible_roster[opponent]")
            action = ("the OPPONENT's roster as captured has no legal lineup (more players than slots): re-sync the "
                      "league (fantasy league sync) or pass a corrected roster; my lineup is not the problem"
                      if theirs else
                      "drop a player or keep an injured player in IL (more players than starting + bench slots)")
            _fail(str(e), as_json, code="infeasible_roster", exit_code=EXIT_USAGE, side="opponent" if theirs else "mine",
                  action=action,
                  roster=[{"player_id": p.player_id, "slot": p.slot, "status": p.status} for p in (opp if theirs else me)])
        _fail(str(e), as_json)
    names = _team_names(league)
    out = {"model_version": LINEUP_MODEL_VERSION, "week": fw.week, "week_start": fw.start.isoformat(),
           "week_end": fw.end.isoformat(), "my_team": my_team,
           "opponent": {"team_id": opp_id, "name": names.get(opp_id)}, **out}
    out["warnings"] = pre_warnings + team_warnings + out["warnings"]
    out["slots_as_of"] = {"date": as_of.isoformat() if as_of else None, "source": as_of_src,
                          "current_slots_file": str(current_slots) if current_slots else None}
    out["accumulated_source"] = acc_source
    # Week odds (P(win), cat_win_prob) are complete only when the banked totals reach the plan date: a plan for a later
    # day built on an earlier read scores that day onwards on top of stale banked stats (Sunday's plan read 8% on Wed).
    out["expected"]["covers_week"] = _odds_cover_week(d0, fw, acc_source)
    out["accumulated"] = {side: acc.get(side) for side in ("me", "opp")} if acc else None
    out["note"] = ("Run before the first tip-off of the day; after that pass --locked with the players whose games "
                   "have started (Yahoo locks each player at his own tip-off). Executor: set target_slots.")
    inputs = {"projections": (proj.path, proj.sha256), "league_file": (str(lf), sha256_file(lf) if lf.exists() else None),
              "roster_capture": (str(cap) if cap else None, sha256_file(cap) if cap else None),
              "league_schedule": (str(sched_path), sha256_file(sched_path)), "nba_schedule": (nba.source, None)}
    out["inputs"] = _inputs_provenance(inputs, current_slots=current_slots, accumulated=accumulated,
                                       matchup_score=score if acc_source["kind"] == "score_file" else None)
    if save:
        out["saved"] = _save_plan(out, nba, use_rest)
    if not week:
        out.pop("week_plan")
    if as_json:
        typer.echo(json.dumps(out, indent=1, default=str))
        return
    _print(out)


def _save_plan(out: dict[str, Any], nba: NBASchedule, use_rest: bool) -> dict[str, Any]:
    """Write the `lineup_plan` row (before `week_plan` is dropped for display). A storage failure does not lose
    the plan: it is reported in `saved.error` and the warnings."""
    from fantasy.lineup import record

    teams = {p["player_id"]: p.get("team") for p in out.get("players", [])}
    row = record.plan_row(out, record.game_markers(nba, teams, out["days"]))
    try:
        st = record.store(use_rest)
        try:
            return record.save(row, st)
        finally:
            st.close()
    except Exception as e:  # noqa: BLE001
        out["warnings"].append(f"--save failed: {type(e).__name__}: {e}")
        return {"run_id": row["run_id"], "inserted": False, "error": f"{type(e).__name__}: {e}"}


def _load_rosters(league: dict[str, Any] | None, my_team: int, opp_id: int, proj: pd.DataFrame,
                  roster: Path | None, cap: Path | None,
                  as_json: bool) -> tuple[list[RosterPlayer], list[RosterPlayer], list[str]]:
    """(my roster, the opponent's, their load warnings); exits on an unusable roster. A plain (single-team)
    `--roster` describes my team only, so the opponent then comes from the newest capture."""
    try:
        me, w1 = load_team(my_team, proj, _team_ids(league, my_team), cap, _team_entry(league, my_team))
        opp_cap = cap
        if roster is not None and "teams" not in json.loads(roster.read_text()):
            opp_cap = newest_capture()
        opp, w2 = load_team(opp_id, proj, _team_ids(league, opp_id), opp_cap, _team_entry(league, opp_id))
    except ValueError as e:
        _fail(str(e), as_json)
    return me, opp, w1 + w2


def _team_names(league: dict[str, Any] | None) -> dict[int, str | None]:
    """Team id (draft slot) -> name from the league file ({} without one; None for the legacy list form)."""
    if not league:
        return {}
    return {int(k): (v.get("name") if isinstance(v, dict) else None) for k, v in league["teams"].items()}


def _week_and_opponent(d0: date, sched_path: Path, my_team: int, opponent_slot: int | None,
                       as_json: bool) -> tuple[FantasyWeek, int]:
    """The fantasy week containing `d0` and my opponent in it (`--opponent-slot` wins); exits on failure."""
    if not sched_path.exists():
        _fail(f"league schedule {sched_path} not found", as_json)
    ms = load_schedule_json(sched_path)
    fw = next((w for w in (ms.calendar or []) if w.start <= d0 <= w.end), None)
    if fw is None:
        _fail(f"{d0} is not inside any week of {sched_path}", as_json)
    opp_id = opponent_slot or ms.opponent(my_team, fw.week)
    if opp_id is None:
        _fail(f"no opponent for team {my_team} in week {fw.week}; pass --opponent-slot", as_json)
    return fw, opp_id


def _inputs_provenance(inputs: dict[str, tuple[str | None, str | None]], **optional: Path | None) -> dict[str, Any]:
    """`out["inputs"]`: {name: {path, sha256}} for the fixed inputs plus each optional input file that was given."""
    for name, path in optional.items():
        if path:
            inputs[name] = (str(path), sha256_file(path))
    return {k: {"path": p, "sha256": h} for k, (p, h) in inputs.items()}


def _team_ids(league: dict[str, Any] | None, t: int) -> list[str] | None:
    """Player ids of team `t` in the league file (None without a league file or team)."""
    if not league:
        return None
    team = league["teams"].get(str(t))
    if team is None:
        return None
    return list(team["players"] if isinstance(team, dict) else team)


def _team_entry(league: dict[str, Any] | None, t: int) -> dict | None:
    """Team `t`'s dict entry in the league file (None for the legacy plain-list form)."""
    team = (league or {}).get("teams", {}).get(str(t))
    return team if isinstance(team, dict) else None


def _resolve_accumulated(accumulated: Path | None, score: Path | None, no_score: bool, use_rest: bool, d0: date,
                         fw: FantasyWeek, my_team: int, opp_id: int, league_file: Path, sched_path: Path,
                         as_json: bool) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Banked totals this week: (acc {"me": {stat: total}, "opp": ...} or {}, accumulated_source, warnings).

    Precedence: `--accumulated` file > `--score` capture > the newest saved `matchup_score` row for this week with
    me = my team, observed on or before `d0` (ET) and against this week's opponent (unless --no-score)."""
    from fantasy.league import matchup

    if accumulated is not None:
        return (json.loads(accumulated.read_text()),
                {"kind": "accumulated_file", "file": str(accumulated), "run_id": None, "observed_at": None}, [])
    if score is not None:
        from fantasy.league.matchup_cli import summary_from_capture

        try:
            s, _ = summary_from_capture(score, None, week=None, state=league_file, slot_map=None,
                                        schedule=sched_path)
        except matchup.MatchupError as e:
            _fail(f"--score {score}: {e}", as_json, code="invalid_capture", exit_code=EXIT_USAGE)
        problem = _score_mismatch(s, d0, fw, my_team, opp_id)
        if problem:
            _fail(f"--score {score}: {problem}", as_json, code="score_mismatch", exit_code=EXIT_USAGE)
        return matchup.accumulated(s), _score_source(s, "score_file", run_id=matchup.run_id(s), file=str(score)), \
            [f"matchup capture: {w}" for w in s.get("warnings", [])] + matchup.banked_checks(s, d0)
    none: dict[str, Any] = {"kind": "none", "run_id": None, "observed_at": None}
    if no_score:
        return {}, {**none, "note": "--no-score"}, []
    from fantasy.lineup import record

    try:
        st = record.store(use_rest)
        try:
            rows = st.rows(matchup.KIND, 500)
        finally:
            st.close()
    except Exception as e:  # noqa: BLE001 - a missing/unreachable store must not block the lineup
        return {}, {**none, "note": f"matchup_score lookup failed: {type(e).__name__}: {e}"}, []
    row = matchup.latest_score(rows, week=fw.week, my_slot=my_team, on_or_before=d0)
    if row is None:
        return {}, {**none, "note": f"no saved matchup_score for week {fw.week} observed on or before {d0}"}, []
    s = row["summary"]
    problem = _score_mismatch(s, d0, fw, my_team, opp_id)
    if problem:
        return {}, {**none, "note": f"newest matchup_score {row['run_id']} not used: {problem}"}, []
    return matchup.accumulated(s), _score_source(s, "matchup_score", run_id=row["run_id"],
                                                 store=getattr(st, "name", None)), matchup.banked_checks(s, d0)


def _odds_cover_week(d0: date, fw: FantasyWeek, acc_source: dict[str, Any]) -> bool:
    """True when banked + simulated days cover the whole week: the plan starts the week, or its banked totals were read
    on the plan date (ET) or come from an explicit --accumulated file."""
    if d0 <= fw.start or acc_source.get("kind") == "accumulated_file":
        return True
    read = acc_source.get("date_et")
    return acc_source.get("kind") != "none" and read is not None and str(read) >= d0.isoformat()


def _score_mismatch(s: dict[str, Any], d0: date, fw: FantasyWeek, my_team: int, opp_id: int) -> str | None:
    if s.get("week") != fw.week:
        return f"it is week {s.get('week')}, the plan is for week {fw.week}"
    if (s.get("me") or {}).get("slot") != my_team:
        return f"its 'me' is team {(s.get('me') or {}).get('slot')}, not my team {my_team}"
    if (s.get("opp") or {}).get("slot") != opp_id:
        return f"its opponent is team {(s.get('opp') or {}).get('slot')}, the plan's is {opp_id}"
    if str(s.get("date_et")) > d0.isoformat():
        return f"it was read on {s.get('date_et')} (ET), after the plan date {d0}: it already holds later games"
    return None


def _score_source(s: dict[str, Any], kind: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, **extra, "observed_at": s.get("observed_at"), "date_et": s.get("date_et"),
            "week": s.get("week"), "score": s.get("score")}


def _overlay_slots_and_warn(me: list[RosterPlayer], current_slots: Path | None, d0: date, as_of: date | None,
                            as_of_src: str, fw: FantasyWeek, has_accumulated: bool,
                            lookup_note: str | None = None) -> tuple[list[RosterPlayer], list[str]]:
    """Apply `--current-slots` (if given) and collect the warnings that precede the plan's own: current-slot
    problems or stale slots (--allow-stale), and a mid-week plan without banked totals."""
    warnings: list[str] = []
    if current_slots is not None:
        me, slot_warnings = _apply_current_slots(me, current_slots)
        warnings += slot_warnings
    elif as_of != d0:
        warnings.append(f"STALE SLOTS (--allow-stale): current slots are from {as_of} (ET) via {as_of_src}; "
                        "changes_vs_current may be wrong -- prefer target_slots")
    if d0 > fw.start and not has_accumulated:
        warnings.append(f"MID-WEEK WITHOUT BANKED TOTALS: stats banked {fw.start}..{d0 - timedelta(days=1)} are "
                        "assumed 0 for both teams, so the category weights ignore the current score (capture the "
                        "matchup: scripts/yahoo/capture_matchup.js + `fantasy league score --save`, or pass "
                        f"--score/--accumulated){f'; {lookup_note}' if lookup_note else ''}")
    return me, warnings


def _apply_current_slots(me: list[RosterPlayer], current_slots: Path) -> tuple[list[RosterPlayer], list[str]]:
    """Overlay `--current-slots` ({player_id: slot} or {"slots": {...}}) on my roster; warnings for ids that are
    not on my roster and for my players without a given slot (they keep the capture/league slot)."""
    raw_cs = json.loads(current_slots.read_text())
    cs = raw_cs.get("slots", raw_cs) if isinstance(raw_cs, dict) else {}
    warnings: list[str] = []
    unknown = sorted(set(cs) - {p.player_id for p in me})
    if unknown:
        warnings.append(f"--current-slots: ids not on my roster ignored: {unknown}")
    missing = [p.player_id for p in me if p.player_id not in cs]
    if missing:
        warnings.append(f"--current-slots: no slot given for {missing}; using the capture/league slot")
    return [replace(p, slot=cs[p.player_id]) if p.player_id in cs else p for p in me], warnings


_CAT_LABEL = {"fg_pct": "FG%", "ft_pct": "FT%"}


def ratio_bench_line(rb: dict | None) -> str | None:
    """One text line when the ratio-bench check suggests benching (None otherwise)."""
    if not rb or not rb.get("suggest") or not rb.get("benches"):
        return None
    parts = []
    starts = rb.get("starts") or []
    for b in rb["benches"]:
        day = date.fromisoformat(b["date"]).strftime("%a")
        helped = [_CAT_LABEL.get(c["cat"], c["cat"].upper()) for c in b.get("cats_helped", [])[:2]]
        parts.append(f"{b['name']} {day}" + (f" to protect {'/'.join(helped)}" if helped else ""))
    for day_iso in sorted({s["date"] for s in starts}):
        names = " & ".join(s["name"] for s in starts if s["date"] == day_iso)
        parts.append(f"{names} starts instead {date.fromisoformat(day_iso).strftime('%a')}")
    se = rb.get("delta_expected_cats_se")
    return (f"RATIO BENCH (suggestion only, not applied): sit {'; '.join(parts)}: "
            f"{rb['delta_expected_cats']:+.2f} ± {se:.2f} exp. cats, P(win) {rb['delta_p_win'] + 0.0:+.3f}")


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
    src = out.get("accumulated_source") or {}
    if src.get("kind") != "none":
        typer.echo(f"Banked totals: {src.get('kind')} {src.get('run_id') or src.get('file') or ''} "
                   f"(observed {src.get('observed_at')}, score {src.get('score')})")
    cwp = ex.get("cat_win_prob") or {}
    if cwp:
        typer.echo("P(win category by end of week): " + ", ".join(f"{k} {v:.2f}" for k, v in cwp.items()))
    mc = out.get("monte_carlo") or {}
    for name, r in (mc.get("plans") or {}).items():
        typer.echo(f"  MC {name}: E[cats] {r['expected_cats']:.3f} ± {r['expected_cats_se']:.3f}, "
                   f"P(win) {r['p_win_matchup']:.3f} ± {r['p_win_matchup_se']:.3f}")
    rb_line = ratio_bench_line(out.get("ratio_bench"))
    if rb_line:
        typer.echo(rb_line)
    for d in out.get("week_plan", []):
        typer.echo(f"  {d['date']}: {d['players_with_game']} with game / {d['starting_slots']} slots, "
                   f"unused {d['unused_starts']} {d['unused_players']}")
    for w in out["warnings"]:
        typer.echo(f"warning: {w}")
    if out.get("saved"):
        sv = out["saved"]
        typer.echo(f"saved {sv['run_id']} ({'new row' if sv.get('inserted') else 'not inserted: identical row exists or error'})")


# registers `lineup record|backfill` on `app`
from fantasy.lineup import record_cli as _record_cli  # noqa: E402,F401
