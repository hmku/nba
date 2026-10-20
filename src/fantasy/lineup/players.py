"""Roster players for lineup decisions: positions, NBA team, Yahoo status tag, current slot, P(plays).

Inputs are plain data so this module does not depend on how rosters are synced: a Yahoo roster capture
(`data/raw/yahoo/league_rosters_<date>.json`: `teams.<yahoo id>.{draft_slot, players[]}`), a plain
`{"players": [...]}` object or a bare list of player dicts with keys
`player_id, name, positions ("PG,SG" | "PG|SG" | list), team, status (Yahoo tag or null), slot`.

P(plays a scheduled game) -- see docs/research/lineup_optimizer.md section 3:
* O / INJ / out-for-season / NA / SUSP -> 0 for every day of the week (PRIOR: an O tag rarely clears within the
  week; re-run the planner when it does).
* Q / GTD / DTD / P -> TAG_PRIORS for the player's NEXT scheduled game only (PRIORS, not fitted); later games
  of the week use the healthy value (short-term tags usually resolve within days -- also a prior).
* healthy -> 1 - REST_SHARE * (1 - gp/82). The projections' gp/82 is the season-average availability and
  includes multi-week injury spells; a player who is healthy today misses this week's games mainly through
  scattered one-game rests, the share REST_SHARE of all missed games (the season simulator's prior
  `SimParams.rest_share`). Using gp/82 itself would double count spells we already know are not happening.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from fantasy.model.lines import SEASON_GAMES
from fantasy.sim.engine import SimParams

BASE_POSITIONS = ("PG", "SG", "SF", "PF", "C")

# PRIORS (not fitted): P(plays his next game | Yahoo tag). Documented in docs/research/lineup_optimizer.md.
TAG_PRIORS: dict[str, float] = {"P": 0.9, "GTD": 0.6, "DTD": 0.6, "Q": 0.5, "D": 0.25}
# Tags that mean "will not play" (p = 0 all week). IL-eligible subset: Yahoo allows IL only for O / out-for-season.
OUT_TAGS = frozenset({"O", "OUT", "INJ", "OFS", "NA", "SUSP", "IR"})
IL_TAGS = frozenset({"O", "OUT", "INJ", "OFS", "IR"})
# PRIOR shared with the season simulator: share of missed games that are scattered one-game rests.
REST_SHARE = SimParams().rest_share

_STATUS_ALIASES = {
    "": None, "HEALTHY": None, "NONE": None, "ACTIVE": None,
    "OUT": "O", "OUT_FOR_SEASON": "OFS", "OUT FOR SEASON": "OFS", "O-OFS": "OFS",
    "QUESTIONABLE": "Q", "PROBABLE": "P", "DOUBTFUL": "D", "DAY-TO-DAY": "DTD", "GAME-TIME DECISION": "GTD",
    "SUSPENDED": "SUSP", "NOT ACTIVE": "NA", "UNKNOWN": None,
}

# Yahoo / ESPN team codes -> basketball-reference codes used by the NBA schedule.
TEAM_ALIASES = {"CHA": "CHO", "PHX": "PHO", "BKN": "BRK", "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS",
                "UTAH": "UTA", "WSH": "WAS", "NOR": "NOP", "SAN": "SAS"}


def normalize_status(s: object) -> str | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    k = str(s).strip().upper()
    if k in _STATUS_ALIASES:
        return _STATUS_ALIASES[k]
    return k


def normalize_team(t: object) -> str | None:
    if t is None or (isinstance(t, float) and pd.isna(t)) or not str(t).strip():
        return None
    k = str(t).strip().upper()
    return TEAM_ALIASES.get(k, k)


def parse_positions(v: object) -> tuple[str, ...]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ()
    items = v if isinstance(v, list | tuple) else str(v).replace("|", ",").replace("/", ",").split(",")
    out = [str(p).strip().upper() for p in items]
    return tuple(p for p in BASE_POSITIONS if p in out)


@dataclass(frozen=True)
class RosterPlayer:
    player_id: str
    name: str
    positions: tuple[str, ...]
    team: str | None  # bbref code
    status: str | None  # normalised Yahoo tag, None = healthy
    slot: str | None = None  # current Yahoo slot (PG, ..., Util, BN, IL), None = unknown
    gp: float = 70.0  # projected games played (for the healthy P(plays))
    yahoo_player_id: int | None = None

    @property
    def is_out(self) -> bool:
        return self.status in OUT_TAGS

    @property
    def il_eligible(self) -> bool:
        return self.status in IL_TAGS

    def p_play(self, first_game: bool, rest_share: float = REST_SHARE) -> float:
        """P(plays a scheduled game). `first_game`: this is his next scheduled game (the tag applies)."""
        if self.is_out:
            return 0.0
        healthy = 1.0 - rest_share * (1.0 - min(max(self.gp, 0.0), SEASON_GAMES) / SEASON_GAMES)
        if self.status in TAG_PRIORS and first_game:
            return TAG_PRIORS[self.status]
        if self.status is not None and self.status not in TAG_PRIORS and first_game:
            return 0.5  # unknown tag: PRIOR, treated like Q
        return healthy


def _as_int(x: object) -> int | None:
    try:
        return int(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _player_dicts(raw: object, draft_slot: int | None, yahoo_team_id: int | None = None) -> list[dict] | None:
    """Player dicts for one team from any accepted roster format (None if the team is absent). Capture teams are
    matched by `draft_slot` (may be null in raw browser output) or, failing that, by Yahoo team id (the capture's
    key or `yahoo_team_id`, from the league state)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and "teams" in raw:
        teams = raw["teams"].items() if isinstance(raw["teams"], dict) else enumerate(raw["teams"])
        teams = [(k, t) for k, t in teams if isinstance(t, dict)]
        for _, t in teams:
            if draft_slot is not None and _as_int(t.get("draft_slot")) == draft_slot:
                return list(t.get("players") or [])
        if yahoo_team_id is not None:
            for k, t in teams:
                if yahoo_team_id in (_as_int(t.get("yahoo_team_id")), _as_int(k)):
                    return list(t.get("players") or [])
        return None
    if isinstance(raw, dict) and "players" in raw:
        return list(raw["players"])
    raise ValueError("unrecognised roster format (expected a Yahoo capture, {'players': [...]} or a list)")


def roster_from_records(
    records: list[dict], proj: pd.DataFrame, ids: list[str] | None = None,
) -> tuple[list[RosterPlayer], list[str]]:
    """Build RosterPlayers. `records` (capture/plain) give Yahoo positions, team, tag and slot; projections fill
    gaps. `ids` (league file membership), when given, defines the roster; records only add attributes."""
    warnings: list[str] = []
    by_id = {str(r["player_id"]): r for r in records if r.get("player_id")}
    if ids is None:
        ids = [str(r.get("player_id") or f"unmatched:{r.get('name')}") for r in records]
    else:
        extra = sorted(set(by_id) - set(ids))
        missing = [p for p in ids if p not in by_id]
        if records and (extra or missing):
            warnings.append(f"roster membership differs between league file and Yahoo capture: only in league file "
                            f"{missing}, only in capture {extra}; using the league file")
    out = []
    for pid in ids:
        r = by_id.get(pid, {})
        row = proj.loc[pid] if pid in proj.index else None
        if row is None:
            warnings.append(f"{pid}: not in projections -> zero stats")
        pos = parse_positions(r.get("positions"))
        filled = []
        if not pos and row is not None:
            pos = parse_positions(list(row["positions"]))
            filled.append("positions")
        team = normalize_team(r.get("team"))
        if team is None and row is not None:
            team = normalize_team(row["team"])
            filled.append("NBA team")
        if filled and ("positions" in r or "team" in r):  # Yahoo row with them blank (usually a tagged row)
            warnings.append(f"{pid}: {' and '.join(filled)} not in the Yahoo row (status {r.get('status')!r}); "
                            f"using projections ({'|'.join(pos)}, {team}) -- check eligibility on Yahoo")
        status = normalize_status(r["status"]) if "status" in r else (
            normalize_status(row["status"]) if row is not None else None)
        out.append(RosterPlayer(
            player_id=pid, name=str(r.get("name") or (row["name"] if row is not None else pid)), positions=pos,
            team=team, status=status, slot=r.get("slot"), gp=float(row["gp"]) if row is not None else 0.0,
            yahoo_player_id=_as_int(r.get("yahoo_player_id"))))
    return out, warnings


def load_team(
    draft_slot: int | None, proj: pd.DataFrame, ids: list[str] | None = None, roster_file: Path | None = None,
    league_team: dict | None = None,
) -> tuple[list[RosterPlayer], list[str]]:
    """One team's roster: `ids` from the league file, attributes from `roster_file` (capture or plain).

    When the league-state team (`league_team`, written by `fantasy league sync --write`) carries `lineup`
    ({player_id: slot}) / `status` ({player_id: tag}) maps, those take precedence for slot and tag (a status map
    lists tagged players only: absent = healthy); the capture then only supplies positions / NBA team."""
    records: list[dict] = []
    warnings: list[str] = []
    lt = league_team if isinstance(league_team, dict) else {}
    if roster_file is not None:
        got = _player_dicts(json.loads(Path(roster_file).read_text()), draft_slot, _as_int(lt.get("yahoo_team_id")))
        if got is None:
            warnings.append(f"team {draft_slot} not found in {roster_file}; positions/status from projections")
        records = got or []
    lineup, status = lt.get("lineup"), lt.get("status")
    if ids is not None and (lineup is not None or status is not None):
        by_id = {str(r["player_id"]): dict(r) for r in records if r.get("player_id")}
        merged = []
        for pid in ids:
            r = by_id.get(pid, {"player_id": pid})
            if lineup is not None:
                r["slot"] = lineup.get(pid)
            if status is not None:
                r["status"] = status.get(pid)
            if (lt.get("yahoo_ids") or {}).get(pid) is not None:
                r["yahoo_player_id"] = lt["yahoo_ids"][pid]
            merged.append(r)
        records = merged
    if ids is None and not records:
        raise ValueError(f"no roster for team {draft_slot}")
    players, w = roster_from_records(records, proj, ids)
    return players, warnings + w


def with_status(players: list[RosterPlayer], overrides: dict[str, str | None]) -> list[RosterPlayer]:
    return [replace(p, status=normalize_status(overrides[p.player_id])) if p.player_id in overrides else p
            for p in players]
