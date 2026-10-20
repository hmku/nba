"""Assemble a SeasonSimulator from projections, settings and schedules (shared by CLI, draft-check, tests)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import ProjectionSet
from fantasy.model.valuation import ValuationParams, compute_values, market_order
from fantasy.sim.engine import SeasonSimulator, SimParams
from fantasy.sim.schedule import (
    ALLSTAR_BREAK_GUESS,
    FantasyWeek,
    MatchupSchedule,
    NBASchedule,
    allstar_break,
    build_calendar,
    fill_missing_weeks,
    fit_week1_end,
    load_schedule_json,
    season_schedule,
)


@dataclass
class SimContext:
    proj: ProjectionSet
    settings: LeagueSettings
    values: pd.DataFrame
    replacement: pd.Series
    market: list[str]
    nba: NBASchedule
    calendar: list[FantasyWeek]
    matchups: MatchupSchedule
    warnings: list[str] = field(default_factory=list)

    def simulator(self, params: SimParams | None = None) -> SeasonSimulator:
        return SeasonSimulator(self.proj.df, self.values, self.replacement, self.settings, self.nba,
                               self.calendar, self.matchups, params)

    def matchup_info(self) -> dict:
        """Where the weekly pairings come from; `assumed_weeks` are round-robin assumptions, not Yahoo's."""
        assumed = list(self.matchups.assumed_weeks)
        note = ("Yahoo's league schedule (who plays whom each week)" if not assumed else
                f"ASSUMPTION: weeks {assumed[0]}-{assumed[-1]} use a Yahoo-style round robin (circle method, every "
                "pair meets once per 11 weeks) because the league's real matchup schedule is not loaded "
                "(data/manual/league_schedule.json); the opponent shown for those weeks is not the real one")
        return {"source": self.matchups.source, "assumed_weeks": assumed, "all_assumed": bool(assumed) and set(self.matchups.weeks) <= set(assumed), "note": note}

    def inputs(self) -> dict:
        return {"projections_path": self.proj.path, "projections_sha256": self.proj.sha256,
                "nba_schedule_source": self.nba.source, "matchup_schedule_source": self.matchups.source,
                "matchup_assumed_weeks": list(self.matchups.assumed_weeks),
                "calendar": [{"week": w.week, "start": w.start.isoformat(), "end": w.end.isoformat()}
                             for w in self.calendar]}


def build_context(
    proj: ProjectionSet,
    settings: LeagueSettings,
    team_ids: list[int],
    schedule_file: Path | None = None,
    nba_schedule_file: Path | None = None,
    nba: NBASchedule | None = None,
    week1_end: date | None = None,
    merge_allstar: bool = True,
    playoff_weeks: list[int] | None = None,
    val_params: ValuationParams | None = None,
    team_names: dict[int, str] | None = None,
) -> SimContext:
    warnings: list[str] = []
    values, _, replacement = compute_values(proj, settings, val_params)
    if nba is None:
        teams = sorted({str(t) for t in proj.df["team"].dropna() if str(t).strip()})
        nba, w = season_schedule(nba_schedule_file, teams)
        warnings += w
    last_week = max(playoff_weeks or settings.playoff_weeks)
    by_name = {v: k for k, v in (team_names or {}).items()}
    matchups = load_schedule_json(schedule_file, by_name) if schedule_file else None
    if matchups is not None and matchups.calendar and max(w.week for w in matchups.calendar) < last_week:
        warnings.append(f"Schedule file gives week dates only through week {max(w.week for w in matchups.calendar)} "
                        f"(< {last_week}); its dates are ignored and the derived calendar is used.")
        matchups.calendar = None
    if matchups is not None and matchups.calendar:
        calendar = matchups.calendar
    else:
        merge = None
        if merge_allstar:
            merge = ALLSTAR_BREAK_GUESS if nba.is_uniform else allstar_break(nba)
        basis = f"week 1 ends {week1_end} (given)" if week1_end else "short week 1"
        po_end = date.fromisoformat(settings.playoff_end_date) if settings.playoff_end_date else None
        if week1_end is None and po_end is not None and last_week == max(settings.playoff_weeks):
            # Yahoo settings page gives the end of the last playoff week; fit week 1's length to it
            week1_end = fit_week1_end(last_week, po_end, start=nba.first_day, merge=merge)
            basis = (f"week 1 = {nba.first_day}..{week1_end} fitted so week {last_week} ends {po_end} "
                     f"(settings.playoff_end_date), All-Star break merge "
                     + (f"{merge[0]}..{merge[1]}" if merge else "off"))
        calendar = build_calendar(last_week, start=nba.first_day, week1_end=week1_end, merge=merge)
        warnings.append(f"Week dates derived (assumption until Yahoo's schedule is loaded with --schedule): {basis}.")
    first_po = min(playoff_weeks or settings.playoff_weeks)
    regular = [w.week for w in calendar if w.week < first_po]
    matchups = fill_missing_weeks(matchups or MatchupSchedule(weeks={}, source="none"), team_ids, regular)
    if matchups.assumed_weeks:
        a = matchups.assumed_weeks
        warnings.append(f"Matchups for weeks {a[0]}-{a[-1]} are an ASSUMED round robin (league schedule not loaded: "
                        "data/manual/league_schedule.json); weekly opponents there are not Yahoo's.")
    errs = matchups.validate(team_ids)
    missing = [w for w in regular if w not in matchups.weeks]
    if missing:
        errs.append(f"no matchups for regular-season weeks {missing}")
    if errs:
        raise ValueError("invalid matchup schedule: " + "; ".join(errs))
    if not (settings.verified.get("playoff_weeks") and settings.verified.get("playoff_end_date")):
        warnings.append(
            f"Playoff weeks {list(playoff_weeks or settings.playoff_weeks)} and the week calendar are unverified "
            f"(weeks {calendar[0].start}..{calendar[-1].end}); load Yahoo's schedule with --schedule after the draft.")
    return SimContext(proj=proj, settings=settings, values=values, replacement=replacement,
                      market=market_order(values), nba=nba, calendar=calendar, matchups=matchups, warnings=warnings)
