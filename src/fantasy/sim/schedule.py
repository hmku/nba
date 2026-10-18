"""Schedules: NBA games per team per day, the fantasy week calendar, and the H2H matchup schedule.

* NBA schedule: parsed from basketball-reference's monthly "NBA_<year>_games-<month>.html" pages and
  cached as `data/raw/bbref_schedule/nba_<year>_games.csv` (date,visitor,home) with a provenance
  sidecar. If no cache exists the simulator falls back to a synthetic schedule in which every team
  plays every other day (3.5 games per 7 days; teams alternate offsets) and says so.
* Fantasy calendar: Yahoo weeks run Monday-Sunday; week 1 starts on the NBA opening day and ends on
  the first Sunday (or `week1_end` for a long week 1). Week dates can be overridden from JSON.
* Matchup schedule: circle-method round robin over the regular-season weeks (each team plays exactly
  once per week; with 12 teams and 17 weeks the 11-round cycle repeats for weeks 12-17), or Yahoo's
  actual schedule from JSON.

Schedule JSON (all keys optional except `weeks`):
  {"source": "yahoo 2026-10-25",
   "weeks": [{"week": 1, "start": "2026-10-20", "end": "2026-10-25", "matchups": [[1, 7], [2, 8], ...]}, ...]}
Team ids in `matchups` are the league-state team ids (draft slots unless the league state JSON says
otherwise).
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from fantasy.data.provenance import RAW_DIR, read_provenance, sha256_file, write_provenance

SEASON_END_YEAR = 2027
NBA_OPENING_DAY = date(2026, 10, 20)
NBA_LAST_DAY_GUESS = date(2027, 4, 11)  # used only by the synthetic fallback
BBREF_MONTHS = ["october", "november", "december", "january", "february", "march", "april"]
CACHE_DIR = RAW_DIR / "bbref_schedule"
UA = "Mozilla/5.0 (fantasy-nba research; low-volume schedule fetch)"
UNIFORM_GAMES_PER_WEEK = 3.5

_ROW = re.compile(r'<tr\s*><th scope="row"[^>]*data-stat="date_game"[^>]*csk="(\d{8})[^"]*"[^>]*>.*?</tr>', re.S)
_VIS = re.compile(r'data-stat="visitor_team_name"[^>]*csk="([A-Z]{3})\.')
_HOME = re.compile(r'data-stat="home_team_name"[^>]*csk="([A-Z]{3})\.')


def cache_path(season_end_year: int = SEASON_END_YEAR) -> Path:
    return CACHE_DIR / f"nba_{season_end_year}_games.csv"


# ---------------------------------------------------------------- NBA schedule
def parse_bbref_month(html: str) -> list[tuple[str, str, str]]:
    """(iso date, visitor, home) for every game row of a bbref monthly schedule page."""
    out = []
    for m in _ROW.finditer(html):
        row = m.group(0)
        v, h = _VIS.search(row), _HOME.search(row)
        if not (v and h):
            continue
        d = m.group(1)
        out.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", v.group(1), h.group(1)))
    return out


def fetch_nba_schedule(season_end_year: int = SEASON_END_YEAR, pause: float = 3.5, timeout: float = 30.0) -> dict:
    """Download the season's monthly pages from basketball-reference (7 requests, `pause` s apart),
    write the cached CSV + provenance. Network use: CLI only, never tests."""
    import httpx

    games: list[tuple[str, str, str]] = []
    urls = []
    with httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for i, month in enumerate(BBREF_MONTHS):
            url = f"https://www.basketball-reference.com/leagues/NBA_{season_end_year}_games-{month}.html"
            if i:
                time.sleep(pause)
            r = client.get(url)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            rows = parse_bbref_month(r.text)
            games += rows
            urls.append({"url": url, "games": len(rows)})
    games = sorted(set(games))
    if not games:
        raise RuntimeError("no games parsed from basketball-reference")
    path = cache_path(season_end_year)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "visitor", "home"])
        w.writerows(games)
    prov = write_provenance(
        path, url=f"https://www.basketball-reference.com/leagues/NBA_{season_end_year}_games.html", method="httpx GET, regex parse",
        rows=len(games), notes="regular-season schedule (all months); one row per game; team codes are bbref abbreviations",
        extra={"pages": urls},
    )
    return {"path": str(path), "games": len(games), "provenance": prov}


@dataclass
class NBASchedule:
    """Which NBA teams play on which day."""

    games: list[tuple[date, str, str]]
    source: str  # "bbref:<sha12>" | "uniform-3.5"
    teams: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.teams:
            self.teams = sorted({t for _, v, h in self.games for t in (v, h)})

    @property
    def first_day(self) -> date:
        return min(d for d, _, _ in self.games)

    @property
    def last_day(self) -> date:
        return max(d for d, _, _ in self.games)

    @property
    def is_uniform(self) -> bool:
        return self.source.startswith("uniform")

    def plays_matrix(self, days: list[date]) -> tuple[dict[str, int], np.ndarray]:
        """(team -> row, bool matrix (n_teams + 1, n_days)). The extra last row is an 'unknown team'
        that plays on the days most teams play (used for free agents / unmapped teams)."""
        idx = {t: i for i, t in enumerate(self.teams)}
        day_i = {d: j for j, d in enumerate(days)}
        M = np.zeros((len(self.teams) + 1, len(days)), dtype=bool)
        for d, v, h in self.games:
            j = day_i.get(d)
            if j is None:
                continue
            M[idx[v], j] = True
            M[idx[h], j] = True
        # unknown team: every other day (3.5/week) on days with any NBA game
        any_game = M[:-1].any(axis=0)
        alt = (np.arange(len(days)) % 2 == 0)
        M[-1] = any_game & alt
        return idx, M


def fill_tbd_games(s: NBASchedule, target: int = 82) -> tuple[NBASchedule, int]:
    """bbref lists 80 games per team; the other 2 depend on the NBA Cup group stage and are dated
    'TBD' (played on the empty December days). Place each team's missing games on the zero-game days
    of December, alternating days by team so no team plays twice a day. Returns (schedule, n added)."""
    from collections import Counter

    per_day = Counter(d for d, _, _ in s.games)
    count = Counter(t for _, v, h in s.games for t in {v, h})
    d0 = date(s.first_day.year, 12, 1)
    window = [d0 + timedelta(days=k) for k in range(31) if per_day[d0 + timedelta(days=k)] == 0]
    if not window:
        return s, 0
    games = list(s.games)
    added = 0
    for i, t in enumerate(s.teams):
        need = max(0, target - count[t])
        for k in range(need):
            d = window[(i % 2 + 2 * k) % len(window)]
            games.append((d, t, t))  # self-pair = one game for team t (opponent unknown)
            added += 1
    return NBASchedule(games=games, source=s.source + f"+tbd{added}", teams=s.teams), added


def load_nba_schedule(path: Path | None = None, season_end_year: int = SEASON_END_YEAR) -> NBASchedule | None:
    p = path or cache_path(season_end_year)
    if not p.exists():
        return None
    games = []
    with open(p) as f:
        for row in csv.DictReader(f):
            games.append((date.fromisoformat(row["date"]), row["visitor"], row["home"]))
    if not games:
        return None
    prov = read_provenance(p) or {}
    sha = prov.get("sha256") or sha256_file(p)
    sched, _ = fill_tbd_games(NBASchedule(games=games, source=f"bbref:{p.name}:{sha[:12]}"))
    return sched


def uniform_schedule(teams: list[str], start: date = NBA_OPENING_DAY, end: date = NBA_LAST_DAY_GUESS) -> NBASchedule:
    """Fallback: every team plays every other day (alternating offsets), i.e. 3.5 games per 7 days."""
    games = []
    n = (end - start).days + 1
    teams = sorted(teams)
    for k in range(n):
        d = start + timedelta(days=k)
        playing = [t for i, t in enumerate(teams) if (k + i) % 2 == 0]
        for a, b in zip(playing[::2], playing[1::2], strict=False):
            games.append((d, a, b))
        if len(playing) % 2:  # odd one out plays a phantom opponent
            games.append((d, playing[-1], playing[-1]))
    return NBASchedule(games=games, source="uniform-3.5", teams=teams)


def season_schedule(path: Path | None = None, teams: list[str] | None = None) -> tuple[NBASchedule, list[str]]:
    """Cached bbref schedule, else the uniform fallback. Returns (schedule, warnings)."""
    s = load_nba_schedule(path)
    if s is not None:
        return s, []
    fallback = uniform_schedule(teams or ["UNK"])
    return fallback, [
        "NBA schedule not cached (run `fantasy sim fetch-schedule`): using uniform 3.5 games/week per team, "
        "so schedule (games-per-week) variance and back-to-back effects are ignored."
    ]


# ---------------------------------------------------------------- fantasy calendar
@dataclass(frozen=True)
class FantasyWeek:
    week: int
    start: date
    end: date

    def days(self) -> list[date]:
        return [self.start + timedelta(days=k) for k in range((self.end - self.start).days + 1)]


def allstar_break(nba: NBASchedule) -> tuple[date, date] | None:
    """Longest run (>= 3 days) of zero-game days between Jan 15 and Mar 15 (the All-Star break)."""
    played = {d for d, _, _ in nba.games}
    lo, hi = date(nba.first_day.year + 1, 1, 15), date(nba.first_day.year + 1, 3, 15)
    best: tuple[date, date] | None = None
    d, run_start = lo, None
    while d <= hi:
        if d not in played:
            run_start = run_start or d
            if best is None or (d - run_start) > (best[1] - best[0]):
                best = (run_start, d)
        else:
            run_start = None
        d += timedelta(days=1)
    return best if best and (best[1] - best[0]).days >= 2 else None


def build_calendar(
    n_weeks: int, start: date = NBA_OPENING_DAY, week1_end: date | None = None,
    merge: tuple[date, date] | None = None,
) -> list[FantasyWeek]:
    """Week 1: `start` .. first Sunday on/after start (or `week1_end`); then Monday-Sunday weeks.
    `merge` (e.g. the All-Star break): the Monday-Sunday weeks it touches become one long week, as
    Yahoo does for the All-Star break."""
    if week1_end is None:
        week1_end = start + timedelta(days=(6 - start.weekday()) % 7)
    spans = [(start, week1_end)]
    s = week1_end + timedelta(days=1)
    while len(spans) < n_weeks + 4:
        spans.append((s, s + timedelta(days=6)))
        s += timedelta(days=7)
    if merge:
        hit = [i for i, (a, b) in enumerate(spans) if a <= merge[1] and b >= merge[0]]
        if len(hit) > 1:
            spans = spans[: hit[0]] + [(spans[hit[0]][0], spans[hit[-1]][1])] + spans[hit[-1] + 1 :]
    return [FantasyWeek(w + 1, a, b) for w, (a, b) in enumerate(spans[:n_weeks])]


# ---------------------------------------------------------------- H2H matchups
@dataclass
class MatchupSchedule:
    weeks: dict[int, list[tuple[int, int]]]  # regular-season week -> pairs of team ids
    source: str
    calendar: list[FantasyWeek] | None = None  # explicit week dates (from JSON), else derived

    def opponent(self, team: int, week: int) -> int | None:
        for a, b in self.weeks.get(week, []):
            if a == team:
                return b
            if b == team:
                return a
        return None

    def validate(self, team_ids: list[int]) -> list[str]:
        errs = []
        ids = set(team_ids)
        for w, pairs in sorted(self.weeks.items()):
            seen = [t for p in pairs for t in p]
            if len(seen) != len(set(seen)):
                errs.append(f"week {w}: a team plays twice")
            if set(seen) - ids:
                errs.append(f"week {w}: unknown team ids {sorted(set(seen) - ids)}")
            if len(ids) % 2 == 0 and set(seen) != ids:
                errs.append(f"week {w}: teams without a matchup {sorted(ids - set(seen))}")
        return errs


def round_robin(team_ids: list[int], n_weeks: int) -> MatchupSchedule:
    """Circle method: n-1 rounds in which every pair meets once, cycled to fill `n_weeks`."""
    ids = list(team_ids)
    if len(ids) % 2:
        ids.append(-1)  # bye
    n = len(ids)
    rounds: list[list[tuple[int, int]]] = []
    arr = ids[:]
    for _ in range(n - 1):
        pairs = [(arr[i], arr[n - 1 - i]) for i in range(n // 2)]
        rounds.append([(a, b) for a, b in pairs if a != -1 and b != -1])
        arr = [arr[0], arr[-1], *arr[1:-1]]
    weeks = {w: rounds[(w - 1) % len(rounds)] for w in range(1, n_weeks + 1)}
    return MatchupSchedule(weeks=weeks, source=f"round_robin({len(team_ids)} teams, {n_weeks} weeks)")


def load_schedule_json(path: Path) -> MatchupSchedule:
    raw = json.loads(Path(path).read_text())
    weeks: dict[int, list[tuple[int, int]]] = {}
    cal: list[FantasyWeek] = []
    for w in raw["weeks"]:
        k = int(w["week"])
        if w.get("matchups"):
            weeks[k] = [(int(a), int(b)) for a, b in w["matchups"]]
        if w.get("start") and w.get("end"):
            cal.append(FantasyWeek(k, date.fromisoformat(w["start"]), date.fromisoformat(w["end"])))
    cal.sort(key=lambda fw: fw.week)
    return MatchupSchedule(weeks=weeks, source=str(raw.get("source") or f"json:{Path(path).name}"), calendar=cal or None)


def schedule_to_json(s: MatchupSchedule, calendar: list[FantasyWeek]) -> str:
    buf = io.StringIO()
    weeks = []
    for fw in calendar:
        row: dict = {"week": fw.week, "start": fw.start.isoformat(), "end": fw.end.isoformat()}
        if fw.week in s.weeks:
            row["matchups"] = [list(p) for p in s.weeks[fw.week]]
        weeks.append(row)
    json.dump({"source": s.source, "weeks": weeks}, buf, indent=1)
    return buf.getvalue()
