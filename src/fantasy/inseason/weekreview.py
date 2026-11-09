"""Weekly matchup retrospective: what happened last week and why we won or lost (deterministic, no Yahoo access).

Inputs (all local files, plus read-only ledger/model_runs reads):
  * Yahoo matchup captures `data/raw/yahoo/matchup_*.json` (banked category totals; one per read) -> final/latest
    score and the day-by-day score trajectory;
  * roster/lineup captures `data/raw/yahoo/league_rosters_*.json` (teams keyed by Yahoo id, `players[].slot`) and the
    league state `data/manual/league_rosters_2026-27*.json` (teams keyed by draft slot, `lineup {player_id: slot}`):
    which players were in an ACTIVE slot (not BN/IL) on each day -> the games that counted;
  * ESPN box scores `data/raw/espn_boxscores/player_games_2027.csv.gz` (actual per-game stats);
  * per-game projections AS OF WEEK START (`projections_as_of`): the in-season file as committed in git before the
    week's first day, else the preseason file, else the current file (labelled; `projection_source` in the output).

Decomposition per team and category (count cats; ratio cats in makes):
    actual - expected_at_week_start = (actual - proj_given_games)              per-game over/under-performance
                                    + (proj_given_games - expected_at_week_start)   games volume / roster effect
`proj_given_games` = sum over the games that counted of the player's projected per-game line; its z-score uses the
fitted game-noise model (fantasy.model.noise: var = a*m + b*m^2 per game, players independent). FG%/FT%: makes above
expected ON THE ACTUAL ATTEMPTS (sum over games of p_proj * attempts), binomial variance attempts * p * (1 - p).
`expected_at_week_start` is a model estimate: week-start roster (non-IL), each player's NBA games that week times
P(plays) = 1 - REST_SHARE * (1 - gp/82) (the lineup planner's PRIOR; out tags -> 0), capped per day at the starting
slots (position eligibility ignored). The noise model under-states WEEKLY variance by ~14-46% for FGA/PTS/FGM/REB in the 24-30 mpg
tier (noise.py docstring), so |z| here is somewhat too large for those stats.

Output schema `week_review/1` (docs/research/dashboard_data.md section 8); `--save` writes a `model_runs` row of kind
`week_review`, idempotent per (week, content).
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from fantasy.data.provenance import REPO_ROOT, sha256_file
from fantasy.league.dates import ET, et_date
from fantasy.league.matchup import CATS, PCT_DECIMALS, TOTALS
from fantasy.model.noise import NOISE_MODEL_VERSION, game_var
from fantasy.ops.common import canonical_json, git_sha, json_safe, now, sha256_hex

if TYPE_CHECKING:
    from fantasy.lineup.record import RunStore

KIND = "week_review"
SCHEMA = "week_review/1"
MODEL_VERSION = "week-review-0.1.0"

YAHOO_RAW = REPO_ROOT / "data" / "raw" / "yahoo"
MANUAL = REPO_ROOT / "data" / "manual"
BOXSCORES = REPO_ROOT / "data" / "raw" / "espn_boxscores" / "player_games_2027.csv.gz"
PROJ_INSEASON = "data/processed/projections_2026_27.inseason.csv"
PROJ_PRESEASON = "data/processed/projections_2026_27.csv"

INACTIVE_SLOTS = frozenset({"BN", "IL", "IL+"})
COUNT_STATS = ("fg3m", "pts", "reb", "ast", "stl", "blk", "tov")
CAT_LABEL = {"fg_pct": "FG%", "ft_pct": "FT%", "fg3m": "3PTM", "pts": "PTS", "reb": "REB", "ast": "AST",
             "stl": "STL", "blk": "BLK", "tov": "TO"}
OUT_STATUSES = frozenset({"out", "out_for_season", "o", "inj"})


# ------------------------------------------------------------------ lineup snapshots


@dataclass
class TeamLineup:
    yahoo_team_id: int
    slot: int | None  # our team id = draft slot
    name: str | None
    lineup: dict[str, str | None]  # player_id -> Yahoo slot (None = unknown)
    status: dict[str, str | None] = field(default_factory=dict)  # player_id -> Yahoo tag (Q/O/INJ/...)


@dataclass
class Snapshot:
    observed_at: datetime  # UTC
    source: str
    teams: dict[int, TeamLineup] = field(default_factory=dict)  # by Yahoo team id

    @property
    def date_et(self) -> date:
        return self.observed_at.astimezone(ET).date()


def _parse_ts(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)


def _manual_observed(raw: dict[str, Any]) -> datetime | None:
    """League state: when the underlying Yahoo capture was read (`source: ... (captured <iso>)`), else synced_at."""
    m = re.search(r"captured (\S+?)\)", str(raw.get("source") or ""))
    return _parse_ts(m.group(1) if m else None) or _parse_ts(raw.get("synced_at"))


def parse_snapshot(raw: dict[str, Any], source: str) -> Snapshot | None:
    """One roster capture (raw: teams keyed by Yahoo id with `players[].slot`) or league state (teams keyed by draft
    slot with `yahoo_team_id` and `lineup {player_id: slot}`) -> Snapshot; None if it has no timestamp/teams."""
    teams = raw.get("teams")
    if not isinstance(teams, dict):
        return None
    obs = _parse_ts(raw.get("captured_at")) or _manual_observed(raw)
    if obs is None:
        return None
    snap = Snapshot(observed_at=obs, source=source)
    for key, t in teams.items():
        if not isinstance(t, dict):
            continue
        if isinstance(t.get("players"), list) and t["players"] and isinstance(t["players"][0], dict):
            yid, slot = int(key), (int(t["draft_slot"]) if t.get("draft_slot") else None)
            lineup = {str(p["player_id"]): (p.get("slot") or None) for p in t["players"] if p.get("player_id")}
            status = {str(p["player_id"]): p.get("status") for p in t["players"] if p.get("player_id")}
        elif t.get("yahoo_team_id") is not None:
            yid, slot = int(t["yahoo_team_id"]), int(key)
            lu: dict[str, Any] = t["lineup"] if isinstance(t.get("lineup"), dict) else {}
            lineup = {str(p): (lu.get(p) or None) for p in (t.get("players") or [])}
            status = dict(t.get("status") or {}) if isinstance(t.get("status"), dict) else {}
        else:
            continue
        snap.teams[yid] = TeamLineup(yid, slot, t.get("name"), lineup, status)
    return snap if snap.teams else None


def load_snapshots(paths: Iterable[Path]) -> tuple[list[Snapshot], list[dict[str, Any]]]:
    out, files = [], []
    for p in sorted(set(paths)):
        try:
            raw = json.loads(Path(p).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        s = parse_snapshot(raw, _rel(p)) if isinstance(raw, dict) else None
        if s is not None:
            out.append(s)
            files.append({"file": _rel(p), "sha256": sha256_file(Path(p))})
    out.sort(key=lambda s: s.observed_at)
    return out, files


def default_snapshot_paths() -> list[Path]:
    return sorted(YAHOO_RAW.glob("league_rosters_2*.json")) + sorted(MANUAL.glob("league_rosters_2026-27*.json"))


def lineup_on(snaps: list[Snapshot], yahoo_id: int, day: date) -> tuple[TeamLineup | None, dict[str, Any]]:
    """The team's lineup on ET `day`: the newest snapshot read on or before that day (a read on `day` shows that
    day's lineup; an earlier read is carried forward, as Yahoo does). No read on or before -> the earliest later read
    (roster only, slots unknown: every game counts)."""
    before = [s for s in snaps if yahoo_id in s.teams and s.date_et <= day]
    if before:
        s = before[-1]
        return s.teams[yahoo_id], {"source": s.source, "observed_at": s.observed_at.isoformat(),
                                   "carried_forward": s.date_et < day, "slots_known": True}
    after = [s for s in snaps if yahoo_id in s.teams]
    if after:
        s = after[0]
        t = s.teams[yahoo_id]
        return TeamLineup(t.yahoo_team_id, t.slot, t.name, {p: None for p in t.lineup}), {
            "source": s.source, "observed_at": s.observed_at.isoformat(), "carried_forward": False,
            "slots_known": False}
    return None, {"source": None, "observed_at": None, "carried_forward": False, "slots_known": False}


# ------------------------------------------------------------------ matchup captures


def load_matchup_captures(paths: Iterable[Path], week: int, my_yahoo: int,
                          calendar_week_of: Callable[[date], int | None] | None = None
                          ) -> tuple[list[dict[str, Any]], list[str]]:
    """Validated captures of `week` with my team in them, oldest first: {observed_at, date_et, file, me, opp}."""
    from fantasy.league.matchup import MatchupError, categories, parse_teams

    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for p in sorted(paths):
        try:
            cap = json.loads(Path(p).read_text())
            teams, w = parse_teams(cap)
        except (OSError, json.JSONDecodeError, MatchupError) as e:
            warnings.append(f"matchup capture {_rel(p)} skipped: {e}")
            continue
        obs = _parse_ts(cap.get("captured_at"))
        if obs is None:
            continue
        d_et = obs.astimezone(ET).date()
        wk = cap.get("week")
        wk = int(wk) if wk is not None else (calendar_week_of(d_et) if calendar_week_of else None)
        mine = [t for t in teams if t.yahoo_team_id == my_yahoo]
        if wk != week or len(mine) != 1:
            continue
        me = mine[0]
        opp = teams[1] if teams[0] is me else teams[0]
        cats, score = categories(me, opp)
        out.append({"observed_at": obs.isoformat(), "date_et": d_et.isoformat(), "file": _rel(p),
                    "sha256": sha256_file(Path(p)), "me": me, "opp": opp, "categories": cats, "score": score,
                    "warnings": w})
    out.sort(key=lambda c: str(c["observed_at"]))
    return out, warnings


def banked_through(cap: dict[str, Any]) -> date:
    """Last ET day whose games a capture contains: a read before noon ET has the previous day's games; a later
    read may hold that day's early games too, so it is treated as through that day (the overlap is warned)."""
    obs = datetime.fromisoformat(cap["observed_at"]).astimezone(ET)
    return obs.date() if obs.hour >= 12 else obs.date() - timedelta(days=1)


# ------------------------------------------------------------------ box scores and projections


def load_box_scores(path: Path, start: date, end: date) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
    df = df[(df["game_date"] >= start) & (df["game_date"] <= end)]
    df = df[df["played"].astype(str).str.lower().isin({"true", "1"})]
    df = df.drop_duplicates(["player_id", "game_id"]) if "game_id" in df.columns else df
    for s in TOTALS:
        df[s] = pd.to_numeric(df[s], errors="coerce").fillna(0.0)
    return df.reset_index(drop=True)


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return r.stdout if r.returncode == 0 else None


def projections_as_of(week_start: date, override: Path | None = None
                      ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Per-game projections valid at the start of the week, and where they came from.

    Order: `override`; the in-season file as last committed before week_start 00:00 ET; the preseason file as last
    committed before then; the current in-season file; the current preseason file (the last two are labelled
    `current_file`: NOT as of week start)."""
    if override is not None:
        text = Path(override).read_text()
        return _proj_frame(text), {"kind": "file", "path": str(override), "as_of_week_start": None,
                                   "sha256": sha256_hex(text)}
    cutoff = datetime.combine(week_start, time(0, 0), tzinfo=ET).isoformat()
    for rel in (PROJ_INSEASON, PROJ_PRESEASON):
        log = _git("log", "-1", "--format=%H %cI", f"--before={cutoff}", "--", rel)
        if not log or not log.strip():
            continue
        sha, committed = log.split()
        shown = _git("show", f"{sha}:{rel}")
        if not shown:
            continue
        text = shown
        meta_txt = _git("show", f"{sha}:{rel.replace('.csv', '.meta.json')}")
        try:
            as_of = json.loads(meta_txt).get("as_of") if meta_txt else None
        except json.JSONDecodeError:
            as_of = None
        return _proj_frame(text), {"kind": "git", "path": rel, "commit": sha[:12], "committed_at": committed,
                                   "data_as_of": as_of, "as_of_week_start": True, "sha256": sha256_hex(text)}
    for rel in (PROJ_INSEASON, PROJ_PRESEASON):
        p = REPO_ROOT / rel
        if p.exists():
            text = p.read_text()
            return _proj_frame(text), {"kind": "current_file", "path": rel, "as_of_week_start": False,
                                       "sha256": sha256_hex(text),
                                       "note": "no version committed before week start: CURRENT projections used "
                                               "(they already know the week's games)"}
    raise FileNotFoundError("no projections file found")


def _proj_frame(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    return df.drop_duplicates("player_id").set_index("player_id")


def p_play(row: pd.Series, yahoo_tag: str | None = None) -> float:
    """P(plays a scheduled game) at week start, the lineup planner's rule (fantasy.lineup.players.p_play): out
    (projection status or Yahoo tag O/INJ/...) -> 0, else 1 - REST_SHARE * (1 - gp/82) (healthy players miss this
    week's games mainly through one-game rests). PRIORS (REST_SHARE, tags), not a fitted weekly availability;
    Q/GTD tags are treated as healthy here."""
    from fantasy.lineup.players import OUT_TAGS, REST_SHARE

    if str(row.get("status") or "").strip().lower() in OUT_STATUSES or str(yahoo_tag or "").upper() in OUT_TAGS:
        return 0.0
    gp = pd.to_numeric(row.get("gp"), errors="coerce")
    if not pd.notna(gp):
        return 0.0
    return float(1.0 - REST_SHARE * (1.0 - np.clip(gp / 82.0, 0.0, 1.0)))


# ------------------------------------------------------------------ games that counted


def counted_games(box: pd.DataFrame, snaps: list[Snapshot], yahoo_id: int, days: list[date]
                  ) -> tuple[pd.DataFrame, list[dict[str, Any]], list[str]]:
    """Box-score rows of the team's players in an active slot on each day (columns of `box` + slot, counted,
    slot_known). Benched/IL rows are returned with counted=False. Also per-day lineup sources and warnings."""
    rows, per_day, warnings = [], [], []
    by_day = {d: g for d, g in box.groupby("game_date")}
    for d in days:
        team, info = lineup_on(snaps, yahoo_id, d)
        per_day.append({"date": d.isoformat(), **info})
        if team is None:
            warnings.append(f"team {yahoo_id}: no roster capture at all for {d}: no games counted that day")
            continue
        if not info["slots_known"]:
            warnings.append(f"team {yahoo_id} {d}: no lineup read on/before the day; roster from {info['source']} "
                            "and EVERY game of its players counted (slots unknown)")
        g = by_day.get(d)
        if g is None:
            continue
        g = g[g["player_id"].isin(team.lineup.keys())]
        for _, r in g.iterrows():
            slot = team.lineup.get(str(r["player_id"]))
            known = slot is not None
            if not known and info["slots_known"]:
                warnings.append(f"team {yahoo_id} {d}: {r['player_id']} slot unknown in {info['source']}: counted")
            rows.append({**r.to_dict(), "slot": slot, "slot_known": known,
                         "counted": (not known) or slot not in INACTIVE_SLOTS})
    cols = [*box.columns, "slot", "slot_known", "counted"]
    return pd.DataFrame(rows, columns=cols), per_day, warnings


def _proj_line(proj: pd.DataFrame, pid: str) -> dict[str, float] | None:
    if pid not in proj.index:
        return None
    r = proj.loc[pid]
    vals = {s: float(pd.to_numeric(r.get(s), errors="coerce")) for s in TOTALS}
    return None if any(not np.isfinite(v) for v in vals.values()) else vals


def expected_at_week_start(snaps: list[Snapshot], yahoo_id: int, proj: pd.DataFrame, days: list[date],
                           team_games: Callable[[str, date], bool], start_slots: int,
                           nba_team: dict[str, str] | None = None) -> dict[str, Any]:
    """Expected games and raw totals from the week-start roster (non-IL players): per day sum of P(plays) over
    players whose NBA team plays, capped at `start_slots` (all players scaled down equally when over the cap)."""
    team, info = lineup_on(snaps, yahoo_id, days[0])
    if team is None:
        return {"games": None, "totals": None, "source": None, "method": "unavailable: no roster capture"}
    players = []
    for pid, slot in team.lineup.items():
        line = _proj_line(proj, pid)
        if line is None or slot in {"IL", "IL+"}:
            continue
        nba = (nba_team or {}).get(pid) or str(proj.loc[pid].get("team") or "")
        players.append((pid, nba, p_play(proj.loc[pid], team.status.get(pid)), line))
    games = 0.0
    totals = dict.fromkeys(TOTALS, 0.0)
    for d in days:
        today = [(p, line) for pid, nba, p, line in players if team_games(nba, d) and p > 0]
        s = sum(p for p, _ in today)
        scale = min(1.0, start_slots / s) if s > 0 else 0.0
        games += s * scale
        for p, line in today:
            for k in TOTALS:
                totals[k] += scale * p * line[k]
    return {"games": round(games, 2), "totals": {k: round(v, 2) for k, v in totals.items()},
            "roster_source": info["source"], "roster_observed_at": info["observed_at"],
            "method": f"week-start roster (non-IL) x NBA games x P(plays) = 1 - rest_share*(1 - gp/82) (PRIOR; "
                      f"out -> 0), capped at "
                      f"{start_slots} starts/day, positions ignored"}


# ------------------------------------------------------------------ category analysis


def team_breakdown(counted: pd.DataFrame, proj: pd.DataFrame, expected: dict[str, Any]
                   ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Per category: actual, projected given the games that counted, over/under + z (noise model), volume effect.
    Also per-player rows (with contributions to the team's category z) and warnings (unprojected players)."""
    warnings: list[str] = []
    c = counted[counted["counted"]] if len(counted) else counted
    pids = sorted(set(c["player_id"])) if len(c) else []
    unproj = [p for p in pids if _proj_line(proj, p) is None]
    if unproj:
        warnings.append(f"no projection for {unproj}: their games count in actual AND projected (deviation 0)")
    actual = {k: float(c[k].sum()) if len(c) else 0.0 for k in TOTALS}
    pg: dict[str, dict[str, float]] = {}
    for _, r in c.iterrows():
        line = _proj_line(proj, r["player_id"])
        pg[r["player_id"]] = line if line is not None else {}
    # per player-game projected means (the actual line for unprojected players)
    m = {k: np.array([(pg[r["player_id"]] or {}).get(k, float(r[k])) for _, r in c.iterrows()], dtype=float)
         for k in TOTALS}
    proj_tot = {k: float(m[k].sum()) for k in TOTALS}
    var = {k: float(game_var(k, m[k]).sum()) if len(c) else 0.0 for k in TOTALS}
    exp_tot = expected.get("totals") or None
    games_n = int(len(c))
    cats: dict[str, dict[str, Any]] = {}
    for key, kind, higher, mk, at in CATS:
        if kind == "count":
            dev = actual[mk] - proj_tot[mk]
            sd = float(np.sqrt(var[mk]))
            ex = exp_tot[mk] if exp_tot else None
            cats[key] = {"actual": _r(actual[mk], 1), "proj_given_games": _r(proj_tot[mk], 1),
                         "over_under": _r(dev, 1), "sd": _r(sd, 2), "z": _z(dev, sd),
                         "expected_week_start": _r(ex, 1),
                         "volume_effect": _r(proj_tot[mk] - ex, 1) if ex is not None else None,
                         "total_vs_week_start": _r(actual[mk] - ex, 1) if ex is not None else None,
                         "helps": _helps(dev, higher)}
        else:
            assert at is not None
            att = np.array([float(r[at]) for _, r in c.iterrows()], dtype=float)
            with np.errstate(divide="ignore", invalid="ignore"):
                p = np.where(m[at] > 0, m[mk] / np.where(m[at] > 0, m[at], 1.0), 0.0)
            p = np.clip(p, 0.0, 1.0)
            exp_makes = float((p * att).sum())
            sd = float(np.sqrt((att * p * (1 - p)).sum()))
            dev = actual[mk] - exp_makes
            ex_pct = (exp_tot[mk] / exp_tot[at]) if exp_tot and exp_tot[at] > 0 else None
            proj_pct = proj_tot[mk] / proj_tot[at] if proj_tot[at] > 0 else None
            act_pct = actual[mk] / actual[at] if actual[at] > 0 else None
            cats[key] = {"actual": _r(act_pct, 4), "makes": _r(actual[mk], 1), "attempts": _r(actual[at], 1),
                         "proj_given_games": _r(proj_pct, 4),
                         "proj_makes": _r(proj_tot[mk], 1), "proj_attempts": _r(proj_tot[at], 1),
                         "expected_pct_on_actual_attempts": _r(exp_makes / actual[at], 4) if actual[at] > 0 else None,
                         "makes_above_expected": _r(dev, 1), "sd_makes": _r(sd, 2), "z": _z(dev, sd),
                         "over_under": _r(act_pct - exp_makes / actual[at], 4) if act_pct is not None else None,
                         "attempts_vs_proj": _r(actual[at] - proj_tot[at], 1),
                         "expected_week_start": _r(ex_pct, 4),
                         "volume_effect": _r(proj_pct - ex_pct, 4) if proj_pct is not None and ex_pct is not None
                         else None,
                         "total_vs_week_start": _r(act_pct - ex_pct, 4) if act_pct is not None and ex_pct is not None
                         else None,
                         "helps": _helps(dev, higher)}
    # per player: deviations and contribution to each team-category z (sums over players to the team z)
    players = []
    for pid in pids:
        g = c[c["player_id"] == pid]
        line = pg.get(pid) or {}
        n = len(g)
        act = {k: float(g[k].sum()) for k in TOTALS}
        prj = {k: line.get(k, act[k] / max(n, 1)) * n for k in TOTALS}
        contrib = {}
        for key, kind, higher, mk, at in CATS:
            if kind == "count":
                d, sd = act[mk] - prj[mk], (cats[key]["sd"] or 0)
            else:
                assert at is not None
                pp = (line[mk] / line[at]) if line.get(at) else (act[mk] / act[at] if act[at] else 0.0)
                d, sd = act[mk] - pp * act[at], (cats[key]["sd_makes"] or 0)
            contrib[key] = round((d / sd if sd > 0 else 0.0) * (1 if higher else -1), 3)
        players.append({"player_id": pid, "name": str(g["player_name"].iloc[0]) if "player_name" in g else pid,
                        "games": n, "projected": bool(line), "actual": {k: _r(v, 1) for k, v in act.items()},
                        "proj": {k: _r(v, 1) for k, v in prj.items()},
                        "contrib_z": contrib, "score": round(sum(contrib.values()), 3)})
    players.sort(key=lambda r: -r["score"])
    out: dict[str, Any] = {"games_counted": games_n, "categories": cats, "totals": {k: _r(v, 1) for k, v in actual.items()}}
    return out, players, warnings


def _helps(dev: float, higher: bool) -> bool:
    return (dev > 0) if higher else (dev < 0)


def _r(v: float | None, nd: int) -> float | None:
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def _z(dev: float, sd: float) -> float | None:
    return round(dev / sd, 2) if sd > 0 else None


def score_categories(me: dict[str, float], opp: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Category winners from raw totals (FG%/FT% from makes/attempts, compared at Yahoo's 3 decimals)."""
    rows, score = [], {"me": 0, "opp": 0, "ties": 0}
    for key, kind, higher, mk, at in CATS:
        if kind == "ratio":
            assert at is not None
            a = me[mk] / me[at] if me[at] > 0 else None
            b = opp[mk] / opp[at] if opp[at] > 0 else None
        else:
            a, b = me[mk], opp[mk]
        if a is None or b is None:
            win = "tie"
        else:
            x, y = (round(a, PCT_DECIMALS), round(b, PCT_DECIMALS)) if kind == "ratio" else (a, b)
            d = (x - y) if higher else (y - x)
            win = "me" if d > 0 else ("opp" if d < 0 else "tie")
        score["ties" if win == "tie" else win] += 1
        nd = 4 if kind == "ratio" else 1
        rows.append({"cat": key, "me": _r(a, nd), "opp": _r(b, nd), "winner": win,
                     "margin": _r((a - b) if a is not None and b is not None else None, nd)})
    return rows, score


# ------------------------------------------------------------------ ledger and P(win)


def ledger_window(start: date, end: date, *, use_rest: bool = False, fetch: Callable[..., Any] | None = None
                  ) -> dict[str, Any]:
    """Transactions, decisions, approvals and error events of the week [start 00:00 ET, end+1 00:00 ET) from the
    ops ledger (read-only). `fetch(table) -> list[dict]` overrides the source (tests)."""
    lo = datetime.combine(start, time(0, 0), tzinfo=ET).astimezone(UTC)
    hi = datetime.combine(end + timedelta(days=1), time(0, 0), tzinfo=ET).astimezone(UTC)
    try:
        get = fetch or _ledger_fetcher(use_rest, lo)
        txns = [r for r in get("transactions") if _in(r.get("created_at"), lo, hi)]
        decs = [r for r in get("decisions") if _in(r.get("created_at"), lo, hi)]
        apps = [r for r in get("approvals") if _in(r.get("requested_at"), lo, hi) or _in(r.get("expires_at"), lo, hi)]
        evs = [r for r in get("agent_events") if r.get("kind") == "error" and _in(r.get("ts"), lo, hi)]
    except Exception as e:  # noqa: BLE001 -- the review is still useful without the ledger
        return {"available": False, "error": f"{type(e).__name__}: {e}", "transactions": [], "decisions": [],
                "missed": []}
    t_out = [{"at": _iso(r.get("created_at")), "txn_id": r.get("txn_id"), "kind": r.get("kind"),
              "status": r.get("status"), "what": _txn_what(r), "error": _short(r.get("error"), 160)}
             for r in sorted(txns, key=lambda r: str(r.get("created_at")))]
    d_out = [{"at": _iso(r.get("created_at")), "kind": r.get("kind"), "agrees_with_model": r.get("agrees_with_model"),
              "choice": _short(canonical_json(r.get("choice")), 160), "rationale": _short(r.get("rationale"), 200)}
             for r in sorted(decs, key=lambda r: str(r.get("created_at")))]
    missed = []
    for r in txns:
        st = r.get("status")
        if st in {"failed", "cancelled", "awaiting_approval", "planned", "submitted"}:
            missed.append({"at": _iso(r.get("created_at")), "type": f"txn_{st}", "kind": r.get("kind"),
                           "what": _txn_what(r), "detail": _short(r.get("error"), 160), "id": r.get("txn_id")})
    for r in apps:
        if r.get("status") == "expired":  # rejected = the user's decision, not a miss
            missed.append({"at": _iso(r.get("requested_at")), "type": f"approval_{r.get('status')}",
                           "kind": r.get("action_type"), "what": _short(r.get("summary"), 160), "detail": None,
                           "id": r.get("approval_id")})
    seen_txn = {m["id"] for m in missed}
    for r in evs:
        if any(t and t in str(r.get("message") or "") for t in seen_txn):
            continue  # the failed txn is already listed
        missed.append({"at": _iso(r.get("ts")), "type": "error_event", "kind": "error",
                       "what": _short(r.get("message"), 160), "detail": None, "id": r.get("event_id")})
    missed.sort(key=lambda r: str(r["at"]))
    return {"available": True, "transactions": t_out, "decisions": d_out, "missed": missed,
            "counts": {"transactions": len(t_out), "decisions": len(d_out), "missed": len(missed)}}


def _ledger_fetcher(use_rest: bool, lo: datetime) -> Callable[[str], list[dict[str, Any]]]:
    if use_rest:
        from fantasy.db import rest

        cl = rest.default_client()
        col = {"transactions": "created_at", "decisions": "created_at", "approvals": "requested_at",
               "agent_events": "ts"}

        since = (lo - timedelta(days=14)).isoformat()

        def get_rest(table: str) -> list[dict[str, Any]]:
            resp = cl.request("GET", table, params={"select": "*", col[table]: f"gte.{since}"})
            cl.check(resp, f"read {table}")
            return list(resp.json())
        return get_rest

    from sqlalchemy import select

    from fantasy.db import schema
    from fantasy.ops.common import engine, row_dict

    def get_sql(table: str) -> list[dict[str, Any]]:
        t = getattr(schema, table)
        c = t.c[{"transactions": "created_at", "decisions": "created_at", "approvals": "requested_at",
                 "agent_events": "ts"}[table]]
        with engine().connect() as conn:
            return [row_dict(r) for r in conn.execute(select(t).where(c >= lo - timedelta(days=14)))]
    return get_sql


def _in(ts: Any, lo: datetime, hi: datetime) -> bool:
    dt = ts if isinstance(ts, datetime) else _parse_ts(ts)
    if dt is None:
        return False
    dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return lo <= dt < hi


def _iso(ts: Any) -> str | None:
    dt = ts if isinstance(ts, datetime) else _parse_ts(ts)
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _short(s: Any, n: int) -> str | None:
    if s is None:
        return None
    t = " ".join(str(s).split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _txn_what(r: dict[str, Any]) -> str:
    p = r.get("payload") or {}
    if not isinstance(p, dict):
        return _short(p, 120) or ""
    def name(x: Any) -> Any:
        return (x.get("player_id") or x.get("name")) if isinstance(x, dict) else x
    if r.get("kind") == "lineup":
        ch = p.get("changes") or []
        parts = [f"{c.get('player_id') or c.get('player') or c.get('name')} {c.get('from')}->{c.get('to')}"
                 for c in ch if isinstance(c, dict)]
        return f"{p.get('date')}: " + (", ".join(parts) if parts else "no change")
    bits = []
    if p.get("add"):
        bits.append(f"add {name(p['add'])}")
    if p.get("drop"):
        bits.append(f"drop {name(p['drop'])}")
    return ", ".join(bits) or (_short(canonical_json(p), 120) or "")


def pwin_trajectory(rows: list[dict[str, Any]], week: int, my_slot: int) -> list[dict[str, Any]]:
    """P(win week) per saved `lineup_plan` of the week (Monte Carlo when it ran, else analytic), oldest first."""
    out = []
    for r in rows:
        s = r.get("summary") or {}
        if s.get("week") != week or (s.get("my_team") not in (None, my_slot)):
            continue
        e = s.get("expected") or {}
        mc = (((e.get("monte_carlo") or {}).get("plans") or {}).get("optimized") or {})
        an = (e.get("optimized") or {}).get("p_win_matchup")
        p, method = (mc.get("p_win_matchup"), "monte_carlo") if mc.get("p_win_matchup") is not None else (an, "analytic")
        if p is None:
            continue
        out.append({"date": s.get("date"), "created_at": _iso(r.get("created_at")), "run_id": r.get("run_id"),
                    "p_win": round(float(p), 3), "se": mc.get("p_win_matchup_se") if method == "monte_carlo" else None,
                    "method": method})
    out.sort(key=lambda x: str(x["created_at"]))
    return out


# ------------------------------------------------------------------ assembly


@dataclass
class ReviewInputs:
    week: int
    start: date
    end: date
    my_yahoo: int
    opp_yahoo: int
    my_slot: int
    opp_slot: int | None
    names: dict[int, str | None]
    snapshots: list[Snapshot]
    box: pd.DataFrame
    proj: pd.DataFrame
    proj_source: dict[str, Any]
    team_games: Callable[[str, date], bool]
    start_slots: int = 10
    captures: list[dict[str, Any]] = field(default_factory=list)
    ledger: dict[str, Any] | None = None
    pwin: list[dict[str, Any]] = field(default_factory=list)
    plan_games: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    today: date | None = None


def build_review(inp: ReviewInputs) -> dict[str, Any]:
    warnings = list(inp.warnings)
    box_max = max(inp.box["game_date"]) if len(inp.box) else None
    last = min(inp.end, box_max) if box_max else inp.start - timedelta(days=1)
    days = [inp.start + timedelta(k) for k in range((inp.end - inp.start).days + 1)]
    covered = [d for d in days if d <= last]
    if last < inp.end:
        warnings.append(f"box scores end {last}: {(inp.end - last).days} day(s) of the week not in the per-player analysis")
    teams: dict[str, Any] = {}
    counted_by: dict[str, pd.DataFrame] = {}
    # NBA team per player: the box scores' (latest game of the week; trades after the projection build), else proj
    nba_team = {str(k): str(v) for k, v in inp.box.sort_values("game_date").groupby("player_id")["team"].last()
                .items()} if len(inp.box) else {}
    for side, yid, slot in (("me", inp.my_yahoo, inp.my_slot), ("opp", inp.opp_yahoo, inp.opp_slot)):
        cg, per_day, w = counted_games(inp.box, inp.snapshots, yid, covered)
        warnings += w
        exp = expected_at_week_start(inp.snapshots, yid, inp.proj, days, inp.team_games, inp.start_slots, nba_team)
        # scale the full-week expectation to the covered days for a like-for-like comparison
        exp_cov = expected_at_week_start(inp.snapshots, yid, inp.proj, covered, inp.team_games, inp.start_slots,
                                         nba_team) if covered else {"games": 0, "totals": None}
        br, players, w2 = team_breakdown(cg, inp.proj, exp_cov)
        warnings += [f"{side}: {x}" for x in w2]
        benched = cg[~cg["counted"]] if len(cg) else cg
        counted_by[side] = cg
        teams[side] = {
            "yahoo_team_id": yid, "slot": slot, "name": inp.names.get(yid),
            "games_counted": br["games_counted"], "games_benched": int(len(benched)),
            "benched": [{"date": str(r["game_date"]), "player_id": r["player_id"], "slot": r["slot"],
                         "pts": _r(float(r["pts"]), 0)} for _, r in benched.iterrows()],
            "games_expected_week_start": exp["games"], "games_expected_covered_days": exp_cov.get("games"),
            "expected_method": exp.get("method"), "expected_roster_source": exp.get("roster_source"),
            "totals": br["totals"], "categories": br["categories"],
            "top_over": [_brief(p) for p in players[:3] if p["score"] > 0],
            "top_under": [_brief(p) for p in reversed(players[-3:]) if p["score"] < 0],
            "players": players, "lineup_sources": per_day,
        }
    if inp.plan_games:
        for side in ("me", "opp"):
            teams[side]["games_planned_lineup_plan"] = inp.plan_games.get(side)

    # ---- scoreboard: Yahoo's latest read + our counted box-score games after it
    traj = [{"observed_at": c["observed_at"], "date_et": c["date_et"], "score": c["score"],
             "games_played": {"me": c["me"].games_played, "opp": c["opp"].games_played},
             "totals": {"me": {k: c["me"].totals[k] for k in ("pts", "fg3m", "reb", "ast")},
                        "opp": {k: c["opp"].totals[k] for k in ("pts", "fg3m", "reb", "ast")}}}
            for c in inp.captures]
    final_totals: dict[str, dict[str, float]]
    recon: dict[str, Any] | None = None
    if inp.captures:
        cap = inp.captures[-1]
        thru = banked_through(cap)
        if datetime.fromisoformat(cap["observed_at"]).astimezone(ET).hour >= 12:
            warnings.append(f"latest Yahoo read {cap['observed_at']} is after noon ET: that day's games may be "
                            "in Yahoo's totals and are treated as banked")
        final_totals, recon = {}, {"yahoo_observed_at": cap["observed_at"], "banked_through": thru.isoformat()}
        for side in ("me", "opp"):
            cg = counted_by[side]
            c = cg[cg["counted"]] if len(cg) else cg
            before = c[c["game_date"] <= thru] if len(c) else c
            after = c[c["game_date"] > thru] if len(c) else c
            y = cap[side].totals
            final_totals[side] = {k: float(y[k]) + (float(after[k].sum()) if len(after) else 0.0) for k in TOTALS}
            recon[side] = {"yahoo": {"games": cap[side].games_played, **{k: y[k] for k in ("pts", "fgm", "fga", "reb")}},
                           "ours": {"games": int(len(before)),
                                    **{k: float(before[k].sum()) if len(before) else 0.0
                                       for k in ("pts", "fgm", "fga", "reb")}},
                           "added_after_read": {"games": int(len(after)),
                                                "pts": float(after["pts"].sum()) if len(after) else 0.0}}
            gap = recon[side]["ours"]["pts"] - float(y["pts"])
            if abs(gap) > 0.02 * max(float(y["pts"]), 1):
                warnings.append(f"{side}: our slot-filtered PTS through {thru} = {recon[side]['ours']['pts']:g} vs "
                                f"Yahoo {y['pts']:g} ({gap:+g}): lineup captures miss intra-day changes or a box "
                                "score is missing")
        both_done = all((cap[s].games_remaining or 0) == 0 for s in ("me", "opp"))
        complete = both_done or last >= inp.end
        basis = "yahoo_final" if both_done else ("yahoo+box_scores" if thru < last else "yahoo_latest")
    else:
        warnings.append("no Yahoo matchup capture for this week: score from our slot-filtered box scores only")
        final_totals = {s: {k: float(teams[s]["totals"][k] or 0) for k in TOTALS} for s in ("me", "opp")}
        complete, basis, thru = last >= inp.end, "box_scores", last
    if basis == "yahoo+box_scores":
        complete = last >= inp.end
    cat_rows, score = score_categories(final_totals["me"], final_totals["opp"])
    result = "W" if score["me"] > score["opp"] else ("L" if score["me"] < score["opp"] else "T")
    final = {"score": score, "result": result, "complete": bool(complete), "basis": basis,
             "through": (inp.end if complete else max(thru, last)).isoformat(), "categories": cat_rows,
             "totals": {s: {k: _r(v, 1) for k, v in final_totals[s].items()} for s in ("me", "opp")}}

    review = {
        "schema": SCHEMA, "week": inp.week, "week_start": inp.start.isoformat(), "week_end": inp.end.isoformat(),
        "generated_for": (inp.today or date.today()).isoformat(),
        "me": {"yahoo_team_id": inp.my_yahoo, "slot": inp.my_slot, "name": inp.names.get(inp.my_yahoo)},
        "opponent": {"yahoo_team_id": inp.opp_yahoo, "slot": inp.opp_slot, "name": inp.names.get(inp.opp_yahoo)},
        "final": final, "trajectory": traj, "reconciliation": recon,
        "analysis_days": {"from": inp.start.isoformat(), "to": last.isoformat() if covered else None,
                          "complete": last >= inp.end},
        "projection_source": inp.proj_source, "noise_model": NOISE_MODEL_VERSION,
        "teams": teams, "categories": _cat_table(cat_rows, teams),
        "decisions": inp.ledger or {"available": False, "error": "not read", "transactions": [], "decisions": [],
                                    "missed": []},
        "p_win_trajectory": inp.pwin,
        "warnings": warnings, "model_version": MODEL_VERSION,
    }
    review["summary"] = summarize(review)
    return json_safe(review)


def _brief(p: dict[str, Any]) -> dict[str, Any]:
    """Player line for the top lists: score and the 2 biggest category contributions (signed: + helps the team)."""
    top = sorted(p["contrib_z"].items(), key=lambda kv: -abs(kv[1]))[:2]
    notes = []
    for k, v in top:
        mk = {"fg_pct": "fgm", "ft_pct": "ftm"}.get(k, k)
        d = (p["actual"][mk] or 0) - (p["proj"][mk] or 0)
        notes.append({"cat": k, "contrib_z": v, "delta": round(d, 1)})
    return {"player_id": p["player_id"], "name": p["name"], "games": p["games"], "score": p["score"], "top": notes}


def _cat_table(final_rows: list[dict[str, Any]], teams: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in final_rows:
        k = row["cat"]
        t = {s: teams[s]["categories"][k] for s in ("me", "opp")}
        out.append({"cat": k, "label": CAT_LABEL[k], "winner": row["winner"], "final_me": row["me"],
                    "final_opp": row["opp"],
                    **{f"{s}_{f}": t[s].get(f) for s in ("me", "opp")
                       for f in ("actual", "proj_given_games", "over_under", "z", "volume_effect",
                                 "makes_above_expected")}})
    return out


def _fmt(v: float, kind: str) -> str:
    return f"{v:.3f}".lstrip("0") if kind == "ratio" else f"{v:g}"


def summarize(r: dict[str, Any]) -> dict[str, Any]:
    """Deterministic headline + <= 4 bullets from the numbers."""
    f = r["final"]
    word = {"W": "Won", "L": "Lost", "T": "Tied"}[f["result"]]
    s = f["score"]
    opp = r["opponent"]["name"] or f"team {r['opponent']['slot']}"
    head = f"{word} {s['me']}-{s['opp']}" + (f"-{s['ties']}" if s["ties"] else "") + f" vs {opp}"
    if not f["complete"]:
        head += f" (provisional, through {f['through']})"
    pw = r.get("p_win_trajectory") or []
    if len(pw) >= 2:
        head += f"; P(win) {pw[0]['p_win']:.2f} Mon -> {pw[-1]['p_win']:.2f} last plan"
    bullets: list[str] = []
    kinds = {c[0]: c[1] for c in CATS}
    # (team, cat) per-game deviations with a z: the biggest that hurt us and the biggest that helped us
    cand = []
    for side in ("me", "opp"):
        for k, c in r["teams"][side]["categories"].items():
            if c.get("z") is None:
                continue
            good_for_us = c["helps"] if side == "me" else not c["helps"]
            cand.append((abs(c["z"]), side, k, c, good_for_us))
    for want_good in (False, True):
        pool = sorted((x for x in cand if x[4] == want_good), key=lambda x: -x[0])
        if pool and pool[0][0] >= 1.0:
            _, side, k, c, _ = pool[0]
            who = "We" if side == "me" else "Opponent"
            if kinds[k] == "ratio":
                bullets.append(f"{who} shot {_fmt(c['actual'], 'ratio')} {CAT_LABEL[k]} vs "
                               f"{_fmt(c['expected_pct_on_actual_attempts'], 'ratio')} expected on the attempts "
                               f"({c['makes_above_expected']:+g} makes, {c['z']:+.1f}σ)")
            else:
                bullets.append(f"{who} {c['over_under']:+g} {CAT_LABEL[k]} vs projection for the games played "
                               f"({c['z']:+.1f}σ)")
    me, op = r["teams"]["me"], r["teams"]["opp"]
    if me.get("games_expected_covered_days") is not None:
        bullets.append(f"Games: {me['games_counted']} counted vs {me['games_expected_covered_days']:g} expected at week "
                       f"start (opponent {op['games_counted']} vs {op['games_expected_covered_days'] or 0:g})"
                       + (f"; {me['games_benched']} of our games on BN/IL" if me["games_benched"] else ""))
    close = []
    for row in f["categories"]:
        if row["winner"] == "tie" or row["margin"] is None:
            continue
        k, kind = row["cat"], kinds[row["cat"]]
        scale = abs(row["margin"]) / (0.01 if kind == "ratio" else max(abs(row["me"] or 0), abs(row["opp"] or 0), 1))
        close.append((scale, row))
    close.sort(key=lambda x: x[0])
    if close:
        parts = [f"{'won' if row['winner'] == 'me' else 'lost'} {CAT_LABEL[row['cat']]} by "
                 f"{_fmt(abs(row['margin']), kinds[row['cat']])}" for _, row in close[:2]]
        bullets.append("Closest: " + "; ".join(parts))
    missed = (r.get("decisions") or {}).get("missed") or []
    if missed and len(bullets) < 4:
        bullets.append(f"{len(missed)} missed/failed move(s): " + "; ".join(
            f"{m['type']} {m['what']}" for m in missed[:2]))
    return {"headline": head, "bullets": bullets[:4]}


# ------------------------------------------------------------------ persistence


def content_hash(review: dict[str, Any]) -> str:
    keep = {k: v for k, v in review.items() if k not in {"generated_for", "summary"}}
    return sha256_hex(canonical_json(json_safe(keep)))


def run_id(review: dict[str, Any]) -> str:
    return f"wr-w{review['week']}-{content_hash(review)[:12]}"


def review_row(review: dict[str, Any], *, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": run_id(review), "kind": KIND, "model_version": MODEL_VERSION, "git_sha": git_sha(),
            "params": json_safe(params), "inputs": json_safe({**inputs, "content_sha256": content_hash(review)}),
            "summary": review, "created_at": now()}


def save(row: dict[str, Any], st: RunStore) -> dict[str, Any]:
    inserted = st.insert_missing(row)
    return {"run_id": row["run_id"], "kind": KIND, "week": row["summary"]["week"], "inserted": inserted,
            "store": st.name}


def latest_review(rows: list[dict[str, Any]], week: int | None = None) -> dict[str, Any] | None:
    """Newest saved `week_review` row (by created_at), optionally for one week."""
    ok = [r for r in rows if (r.get("summary") or {}).get("schema") == SCHEMA
          and (week is None or r["summary"].get("week") == week)]
    return max(ok, key=lambda r: str(r.get("created_at") or "")) if ok else None


def _rel(p: Path | str) -> str:
    try:
        return str(Path(p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def default_week(calendar: list[Any], today: date) -> int | None:
    """Most recently completed fantasy week (end < today)."""
    done = [w for w in calendar if w.end < today]
    return max(done, key=lambda w: w.end).week if done else None


def _is_final_score(row: dict[str, Any]) -> bool:
    s = row.get("summary") or {}
    rem = [(s.get(side) or {}).get("games_remaining") for side in ("me", "opp")]
    return all(r == 0 for r in rem)


def backlog(calendar: list[Any], today: date, score_rows: list[dict[str, Any]],
            review_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Completed weeks (end < today) whose dashboard rows are not final yet: no `matchup_score` row with both teams'
    games_remaining == 0 (`needs_final_score`) and/or no `week_review` row with `final.complete` (`needs_review`).
    The daily routine works this list every day, so a missed Monday is caught up the next run."""
    out = []
    for w in sorted((w for w in calendar if w.end < today), key=lambda w: w.week):
        final = any((r.get("summary") or {}).get("week") == w.week and _is_final_score(r) for r in score_rows)
        rev = [r for r in review_rows if (r.get("summary") or {}).get("schema") == SCHEMA
               and r["summary"].get("week") == w.week]
        complete = any(((r["summary"].get("final") or {}).get("complete")) for r in rev)
        if not (final and complete):
            out.append({"week": w.week, "start": w.start.isoformat(), "end": w.end.isoformat(),
                        "needs_final_score": not final, "needs_review": not complete})
    return out


__all__ = ["KIND", "SCHEMA", "MODEL_VERSION", "ReviewInputs", "Snapshot", "TeamLineup", "backlog", "build_review",
           "counted_games", "default_week", "et_date", "latest_review", "ledger_window", "lineup_on",
           "load_box_scores", "load_matchup_captures", "load_snapshots", "parse_snapshot", "projections_as_of",
           "pwin_trajectory", "review_row", "save", "score_categories", "summarize", "team_breakdown"]
