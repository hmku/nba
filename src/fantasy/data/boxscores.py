"""Season-to-date NBA player box scores from ESPN's public site API (no auth).

Endpoints (same public API family as `fantasy.data.injuries`):
  scoreboard  https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD
  summary     https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=ID

Selection: events with season.type == 2 ("regular-season") and status.type.completed. ESPN also types
All-Star games as season.type 2 (competition type "ALLSTAR"); those are dropped. The NBA Cup final
(competition type "CC") is also season.type 2 but does NOT count in standings or regular-season stats,
so it is kept with `regular_season = False`; every other kept game ("STD", incl. Cup group/knockout
games) has `regular_season = True`. Play-in (type 5) and playoffs (type 3) are excluded.

Outputs (committed, gzipped CSV + provenance sidecar):
  data/raw/espn_boxscores/player_games_<season>.csv.gz  one row per (game, team, player in the box)
  data/raw/espn_boxscores/team_games_<season>.csv.gz    one row per (game, team) from the scoreboard
Raw JSON responses are cached (gzipped) in the gitignored var/cache/espn/ so re-runs are cheap.
`season` is the end year (2025 = 2024-25). Team codes are basketball-reference style (BRK, CHO, PHO).

Parsers (`parse_scoreboard`, `parse_summary`, `team_games_rows`) are pure functions of decoded JSON.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from fantasy.data.ids import canonical_id
from fantasy.data.provenance import RAW_DIR, REPO_ROOT, write_provenance

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
SCOREBOARD_URL = BASE + "/scoreboard?dates={ymd}&limit=100"
SUMMARY_URL = BASE + "/summary?event={event_id}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
OUT_DIR = RAW_DIR / "espn_boxscores"
CACHE_DIR = REPO_ROOT / "var" / "cache" / "espn"
ET = ZoneInfo("America/New_York")

REGULAR_SEASON_TYPE = 2
EXCLUDED_COMP_TYPES = {"ALLSTAR"}
NON_STANDINGS_COMP_TYPES = {"CC"}  # NBA Cup championship: stats do not count in the regular season

# ESPN team codes -> basketball-reference codes (as in data/raw/bbref_schedule, lineup.players).
ESPN_TEAM_MAP = {
    "BKN": "BRK", "CHA": "CHO", "PHX": "PHO", "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS",
    "UTAH": "UTA", "WSH": "WAS",
}
TEAMS = {
    "ATL", "BOS", "BRK", "CHI", "CHO", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL",
    "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHO", "POR", "SAC", "SAS", "TOR",
    "UTA", "WAS",
}

EXPECTED_LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK"]
PLAYER_COLUMNS = [
    "season", "game_id", "game_date", "team", "opp", "home", "regular_season", "espn_player_id",
    "player_name", "player_id", "played", "dnp_reason", "starter", "min", "pts", "fgm", "fga",
    "fg3m", "fg3a", "ftm", "fta", "reb", "ast", "stl", "blk", "tov",
]
TEAM_COLUMNS = ["season", "game_id", "game_date", "team", "opp", "home", "comp_type", "regular_season"]
COUNT_COLS = ["min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"]


def espn_team(abbr: str) -> str:
    """ESPN team abbreviation -> repo (basketball-reference) code."""
    a = (abbr or "").upper()
    return ESPN_TEAM_MAP.get(a, a)


def player_games_path(season: int) -> Path:
    return OUT_DIR / f"player_games_{season}.csv.gz"


def team_games_path(season: int) -> Path:
    return OUT_DIR / f"team_games_{season}.csv.gz"


def default_range(season: int, today: date | None = None) -> tuple[date, date]:
    """Oct 1 .. Apr 30 of the season, clipped to yesterday/today (dates with no type-2 games skip)."""
    end = min(date(season, 4, 30), today or date.today())
    return date(season - 1, 10, 1), end


def et_date(iso_utc: str) -> str:
    """ESPN ISO timestamp ('2026-10-21T01:50Z') -> US Eastern calendar date (ISO)."""
    s = iso_utc.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET).date().isoformat()


# --------------------------------------------------------------------------------------- parsers
def parse_scoreboard(d: dict[str, Any]) -> list[dict[str, Any]]:
    """All events on a scoreboard -> game dicts (no filtering; see `keep_game`)."""
    games = []
    for e in d.get("events", []) or []:
        comp = (e.get("competitions") or [{}])[0]
        season = e.get("season") or {}
        teams = {c.get("homeAway"): espn_team((c.get("team") or {}).get("abbreviation", ""))
                 for c in comp.get("competitors", []) or []}
        comp_type = ((comp.get("type") or {}).get("abbreviation") or "STD").upper()
        games.append({
            "game_id": str(e.get("id", "")),
            "season": int(season.get("year") or 0),
            "season_type": int(season.get("type") or 0),
            "comp_type": comp_type,
            "regular_season": comp_type not in NON_STANDINGS_COMP_TYPES,
            "completed": bool(((e.get("status") or {}).get("type") or {}).get("completed")),
            "date_utc": e.get("date", ""),
            "game_date": et_date(e["date"]) if e.get("date") else "",
            "home": teams.get("home", ""),
            "away": teams.get("away", ""),
        })
    return games


def keep_game(g: dict[str, Any]) -> bool:
    """Completed regular-season (type 2) NBA games between two real teams (drops All-Star)."""
    return (g["season_type"] == REGULAR_SEASON_TYPE and g["completed"]
            and g["comp_type"] not in EXCLUDED_COMP_TYPES
            and g["home"] in TEAMS and g["away"] in TEAMS)


def team_games_rows(games: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kept scoreboard games -> two (game, team) rows each."""
    rows = []
    for g in games:
        for team, opp, home in ((g["home"], g["away"], True), (g["away"], g["home"], False)):
            rows.append({"season": g["season"], "game_id": g["game_id"], "game_date": g["game_date"],
                         "team": team, "opp": opp, "home": home, "comp_type": g["comp_type"],
                         "regular_season": g["regular_season"]})
    return rows


def _num(s: Any) -> float | None:
    if s is None:
        return None
    s = str(s).strip()
    if s in ("", "--", "-"):
        return None
    try:
        return float(s.lstrip("+"))
    except ValueError:
        return None


def _made_att(s: Any) -> tuple[float | None, float | None]:
    if s is None or "-" not in str(s) or str(s).strip() in ("--", "-"):
        return None, None
    a, b = str(s).split("-", 1)
    return _num(a), _num(b)


def parse_summary(d: dict[str, Any], game: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """ESPN summary JSON -> one row per player listed in the box score.

    `game` (from `parse_scoreboard`) supplies season/date/regular_season; otherwise they are read
    from `header`. Stats are NaN for DNP rows. `played` = not didNotPlay and (min > 0 or any
    counting stat/attempt > 0); `dnp_reason` is only filled when not played (ESPN sets
    reason "COACH'S DECISION" on many players who did play).
    """
    header = d.get("header") or {}
    hcomp = (header.get("competitions") or [{}])[0]
    game_id = str((game or {}).get("game_id") or header.get("id") or "")
    season = int((game or {}).get("season") or (header.get("season") or {}).get("year") or 0)
    game_date = (game or {}).get("game_date") or (et_date(hcomp["date"]) if hcomp.get("date") else "")
    comp_type = ((hcomp.get("type") or {}).get("abbreviation") or "STD").upper()
    regular_season = (game or {}).get("regular_season", comp_type not in NON_STANDINGS_COMP_TYPES)
    side = {espn_team((c.get("team") or {}).get("abbreviation", "")): c.get("homeAway")
            for c in hcomp.get("competitors", []) or []}

    blocks = (d.get("boxscore") or {}).get("players", []) or []
    teams = [espn_team((b.get("team") or {}).get("abbreviation", "")) for b in blocks]
    rows: list[dict[str, Any]] = []
    for b, team in zip(blocks, teams, strict=True):
        opp = next((t for t in teams if t != team), "")
        stats = (b.get("statistics") or [{}])[0]
        labels = [str(x).upper() for x in stats.get("labels", []) or []]
        idx = {lab: i for i, lab in enumerate(labels)}
        for a in stats.get("athletes", []) or []:
            ath = a.get("athlete") or {}
            vals = a.get("stats") or []

            def get(lab: str, vals: list[Any] = vals, idx: dict[str, int] = idx) -> Any:
                i = idx.get(lab)
                return vals[i] if i is not None and i < len(vals) else None

            fgm, fga = _made_att(get("FG"))
            fg3m, fg3a = _made_att(get("3PT"))
            ftm, fta = _made_att(get("FT"))
            rec: dict[str, Any] = {
                "season": season, "game_id": game_id, "game_date": game_date, "team": team,
                "opp": opp, "home": side.get(team) == "home", "regular_season": bool(regular_season),
                "espn_player_id": str(ath.get("id", "")),
                # a few DNP entries carry only `shortName` (no id/displayName): keep, but no player_id
                "player_name": ath.get("displayName") or ath.get("shortName", ""),
                "player_id": canonical_id(ath["displayName"]) if ath.get("displayName") else "",
                "starter": bool(a.get("starter")),
                "min": _num(get("MIN")), "pts": _num(get("PTS")), "fgm": fgm, "fga": fga,
                "fg3m": fg3m, "fg3a": fg3a, "ftm": ftm, "fta": fta, "reb": _num(get("REB")),
                "ast": _num(get("AST")), "stl": _num(get("STL")), "blk": _num(get("BLK")),
                "tov": _num(get("TO")),
            }
            any_stat = any((rec[c] or 0) > 0 for c in COUNT_COLS)
            played = (not a.get("didNotPlay")) and any_stat
            rec["played"] = bool(played)
            rec["dnp_reason"] = "" if played else (a.get("reason") or "")
            rec["_did_not_play_flag"] = bool(a.get("didNotPlay"))
            rows.append(rec)
    return rows


def anomalies(rows: list[dict[str, Any]], d: dict[str, Any] | None = None) -> Counter[str]:
    """Count oddities in parsed rows (for the fetch summary)."""
    c: Counter[str] = Counter()
    for r in rows:
        if not r["espn_player_id"]:
            c["athlete_missing_id"] += 1
        if not r["_did_not_play_flag"] and not r["played"]:
            c["not_dnp_but_no_stats"] += 1
        if r["played"] and not (r["min"] or 0) > 0:
            c["played_zero_min"] += 1
        if r["_did_not_play_flag"] and any((r[k] or 0) > 0 for k in COUNT_COLS):
            c["dnp_with_stats"] += 1
        if r["played"] and r["fga"] is not None and r["fgm"] is not None and r["fgm"] > r["fga"]:
            c["fgm_gt_fga"] += 1
    if d is not None:
        for b in (d.get("boxscore") or {}).get("players", []) or []:
            labels = [str(x).upper() for x in ((b.get("statistics") or [{}])[0].get("labels") or [])]
            if labels[: len(EXPECTED_LABELS)] != EXPECTED_LABELS:
                c["unexpected_labels"] += 1
        if len((d.get("boxscore") or {}).get("players", []) or []) != 2:
            c["box_not_two_teams"] += 1
    return c


# --------------------------------------------------------------------------------------- fetching
class _Http:
    """Rate-limited (max `rps` req/s across threads), retrying GET with a gzipped JSON disk cache."""

    def __init__(self, client: httpx.Client, rps: float = 6.0, retries: int = 4):
        self.client, self.min_gap, self.retries = client, 1.0 / rps, retries
        self._lock, self._next = threading.Lock(), 0.0
        self.requests = 0

    def _wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            t = max(now, self._next)
            self._next = t + self.min_gap
        if t > now:
            time.sleep(t - now)

    def get_json(self, url: str, cache: Path | None, use_cache: bool = True) -> dict[str, Any]:
        if cache is not None and use_cache and cache.exists():
            with gzip.open(cache, "rt") as f:
                return json.load(f)
        err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait()
            try:
                r = self.client.get(url)
                self.requests += 1
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                data = r.json()
                if cache is not None:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    tmp = cache.with_suffix(".tmp")
                    with gzip.open(tmp, "wt") as f:
                        json.dump(data, f)
                    tmp.replace(cache)
                return data
            except (httpx.HTTPError, ValueError) as e:
                err = e
                time.sleep(min(30.0, 1.5 * 2**attempt))
        raise RuntimeError(f"GET failed after retries: {url}: {err}")


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _read(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"game_id": str, "espn_player_id": str, "player_id": str})
    for c in ("espn_player_id", "player_name", "player_id", "dnp_reason"):
        if c in df.columns:
            df[c] = df[c].fillna("")
    return df


def load_player_games(season: int) -> pd.DataFrame:
    df = _read(player_games_path(season))
    if df is None:
        raise FileNotFoundError(f"{player_games_path(season)} (run `fantasy data fetch-boxscores`)")
    return df


def load_team_games(season: int) -> pd.DataFrame:
    df = _read(team_games_path(season))
    if df is None:
        raise FileNotFoundError(f"{team_games_path(season)} (run `fantasy data fetch-boxscores`)")
    return df


def fetch_season(
    season: int,
    start: date | None = None,
    end: date | None = None,
    *,
    rps: float = 6.0,
    workers: int = 4,
    timeout: float = 30.0,
    today: date | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Fetch/parse completed regular-season games in [start, end] and merge into the season files.

    Without `start`, resumes from the latest game_date already in the file (re-fetching that day),
    else from Oct 1. Scoreboards for the last 2 days before `today` are always re-downloaded;
    completed-game summaries are cached forever. Returns a JSON-able summary.
    """
    today = today or date.today()
    d0, d1 = default_range(season, today)
    old_p, old_t = _read(player_games_path(season)), _read(team_games_path(season))
    if start is None:
        start = date.fromisoformat(str(old_t["game_date"].max())) if old_t is not None and len(old_t) else d0
    end = min(end or d1, today)
    fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = progress or (lambda s: None)

    games: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    with httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True) as c:
        http = _Http(c, rps=rps)

        def scoreboard(dt: date) -> list[dict[str, Any]]:
            ymd = dt.strftime("%Y%m%d")
            cache = CACHE_DIR / "scoreboard" / f"{ymd}.json.gz"
            fresh = dt < today - timedelta(days=2)
            return parse_scoreboard(http.get_json(SCOREBOARD_URL.format(ymd=ymd), cache, use_cache=fresh))

        with ThreadPoolExecutor(workers) as ex:
            for dt, gs in zip(_dates(start, end), ex.map(scoreboard, _dates(start, end)), strict=True):
                for g in gs:
                    if keep_game(g) and g["season"] == season:
                        games.append(g)
                    elif g["season_type"] == REGULAR_SEASON_TYPE and g["comp_type"] in EXCLUDED_COMP_TYPES:
                        skipped["allstar"] += 1
                    elif g["season_type"] == REGULAR_SEASON_TYPE and not g["completed"]:
                        skipped["not_completed"] += 1
                    elif g["season_type"] != REGULAR_SEASON_TYPE:
                        skipped[f"season_type_{g['season_type']}"] += 1
                    else:
                        skipped["other"] += 1
                if dt.day == 1:
                    log(f"scoreboards through {dt}: {len(games)} games")
        games = list({g["game_id"]: g for g in games}.values())
        log(f"{len(games)} games; fetching summaries")

        def summary(g: dict[str, Any]) -> tuple[list[dict[str, Any]], Counter[str]]:
            cache = CACHE_DIR / "summary" / str(season) / f"{g['game_id']}.json.gz"
            d = http.get_json(SUMMARY_URL.format(event_id=g["game_id"]), cache)
            rows = parse_summary(d, g)
            return rows, anomalies(rows, d)

        prow: list[dict[str, Any]] = []
        anom: Counter[str] = Counter()
        with ThreadPoolExecutor(workers) as ex:
            for i, (rows, a) in enumerate(ex.map(summary, games), 1):
                prow.extend(rows)
                anom.update(a)
                if i % 200 == 0:
                    log(f"summaries {i}/{len(games)}")
        n_requests = http.requests

    new_p = pd.DataFrame(prow, columns=PLAYER_COLUMNS) if prow else pd.DataFrame(columns=PLAYER_COLUMNS)
    new_t = pd.DataFrame(team_games_rows(games), columns=TEAM_COLUMNS)
    pdf = _merge(old_p, new_p, ["game_id", "team", "espn_player_id", "player_name"], PLAYER_COLUMNS)
    tdf = _merge(old_t, new_t, ["game_id", "team"], TEAM_COLUMNS)
    _check_team_vs_player(tdf, pdf, anom)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {"season": season, "range": [start.isoformat(), end.isoformat()], "requests": n_requests,
           "new_games": len(games), "skipped_events": dict(skipped), "anomalies": dict(anom),
           "files": []}
    common = {"games": int(tdf["game_id"].nunique()) if len(tdf) else 0,
              "regular_season_games": int(tdf.loc[tdf["regular_season"].astype(bool), "game_id"].nunique())
              if len(tdf) else 0,
              "first_date": str(tdf["game_date"].min()) if len(tdf) else None,
              "last_date": str(tdf["game_date"].max()) if len(tdf) else None}
    for path, df, url, note in [
        (player_games_path(season), pdf, SUMMARY_URL, "ESPN box scores, one row per (game, team, player)"),
        (team_games_path(season), tdf, SCOREBOARD_URL, "ESPN scoreboard, one row per (game, team)"),
    ]:
        df.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
        prov = write_provenance(
            path, url=url, method="httpx GET (fantasy.data.boxscores.fetch_season)", rows=len(df),
            fetched_at=fetched_at,
            notes=note + "; season.type 2, completed, All-Star dropped, NBA Cup final regular_season=False",
            extra={"season": season, "last_fetch_range": out["range"], **common},
        )
        out["files"].append({"path": prov["path"], "rows": len(df), "bytes": path.stat().st_size})
    out.update(common)
    return out


def _merge(old: pd.DataFrame | None, new: pd.DataFrame, key: list[str], cols: list[str]) -> pd.DataFrame:
    df = new if old is None or old.empty else pd.concat([old[cols], new[cols]], ignore_index=True)
    df = df.drop_duplicates(key, keep="last")
    return df.sort_values(["game_date", "game_id", "team"] + (["espn_player_id"] if "espn_player_id" in cols
                                                               else [])).reset_index(drop=True)


def _check_team_vs_player(tdf: pd.DataFrame, pdf: pd.DataFrame, anom: Counter[str]) -> None:
    if tdf.empty:
        return
    have = set(zip(pdf["game_id"].astype(str), pdf["team"], strict=True)) if len(pdf) else set()
    missing = [k for k in zip(tdf["game_id"].astype(str), tdf["team"], strict=True) if k not in have]
    if missing:
        anom["team_game_without_box"] = len(missing)
