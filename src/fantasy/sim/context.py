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
    FantasyWeek,
    MatchupSchedule,
    NBASchedule,
    allstar_break,
    build_calendar,
    load_schedule_json,
    round_robin,
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

    def inputs(self) -> dict:
        return {"projections_path": self.proj.path, "projections_sha256": self.proj.sha256,
                "nba_schedule_source": self.nba.source, "matchup_schedule_source": self.matchups.source,
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
) -> SimContext:
    warnings: list[str] = []
    values, _, replacement = compute_values(proj, settings, val_params)
    if nba is None:
        teams = sorted({str(t) for t in proj.df["team"].dropna() if str(t).strip()})
        nba, w = season_schedule(nba_schedule_file, teams)
        warnings += w
    last_week = max(playoff_weeks or settings.playoff_weeks)
    matchups = load_schedule_json(schedule_file) if schedule_file else None
    if matchups is not None and matchups.calendar:
        calendar = matchups.calendar
        if max(w.week for w in calendar) < last_week:
            raise ValueError(f"schedule file calendar ends at week {max(w.week for w in calendar)} < {last_week}")
    else:
        merge = allstar_break(nba) if merge_allstar and not nba.is_uniform else None
        calendar = build_calendar(last_week, start=nba.first_day, week1_end=week1_end, merge=merge)
    first_po = min(playoff_weeks or settings.playoff_weeks)
    regular = [w.week for w in calendar if w.week < first_po]
    if matchups is None or not matchups.weeks:
        rr = round_robin(team_ids, len(regular))
        matchups = MatchupSchedule(weeks=rr.weeks, source=rr.source, calendar=matchups.calendar if matchups else None)
    errs = matchups.validate(team_ids)
    missing = [w for w in regular if w not in matchups.weeks]
    if missing:
        errs.append(f"no matchups for regular-season weeks {missing}")
    if errs:
        raise ValueError("invalid matchup schedule: " + "; ".join(errs))
    if not settings.verified.get("playoff_weeks"):
        warnings.append(
            f"Playoff weeks {list(playoff_weeks or settings.playoff_weeks)} and the week calendar are unverified "
            f"(weeks {calendar[0].start}..{calendar[-1].end}); load Yahoo's schedule with --schedule after the draft.")
    return SimContext(proj=proj, settings=settings, values=values, replacement=replacement,
                      market=market_order(values), nba=nba, calendar=calendar, matchups=matchups, warnings=warnings)
