"""Yahoo roster sync: a browser capture of all 12 rosters -> the simulator's league state, plus a diff.

Capture format (written by ``scripts/yahoo/capture_rosters.js``, normalised by
``fantasy league import-capture``)::

    {"source": ..., "captured_at": "2026-10-21T13:05:00Z",
     "teams": {"<yahoo team id>": {"name": ..., "draft_slot": 1 | null,
               "players": [{"slot": "PG", "name": "Jalen Brunson", "player_id": "jalen-brunson" | null,
                            "team": "NYK" | null, "positions": "PG,SG" | null, "status": "Q" | null,
                            "yahoo_player_id": 6014 | null}, ...]}}}

League state (``--league-file`` of ``fantasy sim``; team id = DRAFT SLOT)::

    {"source", "my_team", "draft_id", "synced_at",
     "teams": {"1": {"name", "players": [ids], "yahoo_team_id": 11,
                     "lineup": {id: yahoo slot}, "status": {id: tag}, "yahoo_ids": {id: yahoo id}}},
     "unknown_players": [...], "unresolved": [...]}

``fantasy.sim.league.from_json`` reads only ``name``/``players`` (extra keys are ignored), so the
lineup/status maps live next to the roster in the same file.

Player resolution order: capture ``player_id`` (if it is a projection id) -> the previous state's
Yahoo-id map (stable across renames) -> name (``fantasy.draft.names.resolve_player`` without fuzzy
matching: slug, aliases, suffixes, "N. Alexander-Walker") -> fuzzy match accepted only when the NBA
team agrees (reported in ``fuzzy_matches``). Anything else gets ``unknown:<canonical slug>`` and is
reported (``unknown_players``: not in projections; ``unresolved``: ambiguous between projection
players). No player is ever dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from fantasy.draft.names import NameResolutionError, resolve_player, unknown_player_id
from fantasy.model.availability import TEAM_ALIASES

VALID_SLOTS = ("PG", "SG", "G", "SF", "PF", "F", "C", "Util", "BN", "IL", "IL+")
BENCH_SLOTS = ("BN", "IL", "IL+")
KNOWN_TAGS = ("Q", "O", "P", "NA", "GTD", "DTD", "INJ", "SUSP")
OUT_TAGS = ("O", "INJ", "SUSP", "NA")  # not expected to play today
MAX_ROSTER = 16
NUM_TEAMS = 12


class CaptureError(ValueError):
    """The capture is malformed or inconsistent (CLI exit 2)."""


@dataclass
class CapturePlayer:
    slot: str
    name: str
    player_id: str | None = None
    team: str | None = None
    positions: str | None = None
    status: str | None = None
    yahoo_player_id: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {"slot": self.slot, "name": self.name, "player_id": self.player_id, "team": self.team,
                "positions": self.positions, "status": self.status, "yahoo_player_id": self.yahoo_player_id}


@dataclass
class CaptureTeam:
    yahoo_team_id: int
    name: str
    draft_slot: int | None
    players: list[CapturePlayer]


@dataclass
class Capture:
    path: str
    source: str
    captured_at: str | None
    teams: dict[int, CaptureTeam]  # yahoo team id -> team
    raw: dict[str, Any] = field(default_factory=dict)


def norm_team(team: str | None) -> str | None:
    """Yahoo NBA team code -> ours (CHA->CHO, PHX->PHO, BKN->BRK, ...)."""
    if not team:
        return None
    t = str(team).strip().upper()
    return TEAM_ALIASES.get(t, t) or None


def _opt_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    return int(v)


def parse_capture(raw: dict[str, Any], path: str = "<memory>", num_teams: int = NUM_TEAMS,
                  max_roster: int = MAX_ROSTER) -> Capture:
    """Validate a capture dict: `num_teams` teams, rosters <= `max_roster`, known lineup slots."""
    if not isinstance(raw, dict) or not isinstance(raw.get("teams"), dict):
        raise CaptureError("capture must be an object with a 'teams' object")
    teams: dict[int, CaptureTeam] = {}
    errors: list[str] = []
    for k, v in raw["teams"].items():
        try:
            tid = int(k)
        except ValueError:
            errors.append(f"team key {k!r} is not a Yahoo team id")
            continue
        players = []
        for i, p in enumerate(v.get("players") or []):
            slot, name = str(p.get("slot") or ""), str(p.get("name") or "").strip()
            if slot not in VALID_SLOTS:
                errors.append(f"team {tid} player {i} ({name}): unknown slot {slot!r}")
            if not name:
                errors.append(f"team {tid} player {i}: empty name")
            status = (str(p["status"]).strip() or None) if p.get("status") else None
            players.append(CapturePlayer(
                slot=slot, name=name, player_id=p.get("player_id") or None, team=norm_team(p.get("team")),
                positions=p.get("positions") or None, status=status,
                yahoo_player_id=_opt_int(p.get("yahoo_player_id"))))
        if len(players) > max_roster:
            errors.append(f"team {tid}: {len(players)} players > max roster {max_roster}")
        if not players:
            errors.append(f"team {tid}: empty roster")
        teams[tid] = CaptureTeam(yahoo_team_id=tid, name=str(v.get("name") or ""),
                                 draft_slot=_opt_int(v.get("draft_slot")), players=players)
    if len(teams) != num_teams:
        errors.append(f"expected {num_teams} teams, got {len(teams)}")
    ypids = [p.yahoo_player_id for t in teams.values() for p in t.players if p.yahoo_player_id is not None]
    dup = sorted({y for y in ypids if ypids.count(y) > 1})
    if dup:
        errors.append(f"Yahoo player ids on more than one roster: {dup}")
    if errors:
        raise CaptureError("; ".join(errors))
    return Capture(path=path, source=str(raw.get("source") or ""), captured_at=raw.get("captured_at"),
                   teams=teams, raw=raw)


def load_capture(path: Path | str, num_teams: int = NUM_TEAMS, max_roster: int = MAX_ROSTER) -> Capture:
    p = Path(path)
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise CaptureError(f"cannot read capture {p}: {e}") from e
    return parse_capture(raw, str(p), num_teams, max_roster)


# ---------------------------------------------------------------- player resolution


@dataclass
class Resolution:
    player_id: str
    method: str  # capture | yahoo_id | name | fuzzy | unknown | ambiguous
    candidates: list[tuple[str, str]] = field(default_factory=list)


class PlayerResolver:
    """Resolve Yahoo rows to our player ids (see module doc for the order)."""

    def __init__(self, projections: pd.DataFrame, yahoo_ids: dict[int, str] | None = None):
        self.proj = projections
        self.by_yahoo = dict(yahoo_ids or {})

    def resolve(self, name: str, team: str | None = None, yahoo_player_id: int | None = None,
                player_id: str | None = None) -> Resolution:
        if player_id and player_id in self.proj.index:
            return Resolution(player_id, "capture")
        if yahoo_player_id is not None and yahoo_player_id in self.by_yahoo:
            pid = self.by_yahoo[yahoo_player_id]
            if pid in self.proj.index:  # unknown:<slug> ids re-resolve by name (may be projected now)
                return Resolution(pid, "yahoo_id")
        try:
            return Resolution(resolve_player(name, self.proj, fuzzy=False), "name")
        except NameResolutionError as e:
            if e.candidates:
                return Resolution(unknown_player_id(name), "ambiguous", e.candidates)
        try:
            pid = resolve_player(name, self.proj, fuzzy=True)
        except NameResolutionError:
            return Resolution(unknown_player_id(name), "unknown")
        if team and norm_team(str(self.proj.loc[pid, "team"])) == norm_team(team):
            return Resolution(pid, "fuzzy")
        return Resolution(unknown_player_id(name), "unknown", [(pid, str(self.proj.loc[pid, "name"]))])

    def fill(self, p: CapturePlayer, pid: str) -> None:
        """Fill team/positions from projections when Yahoo replaced them with a status tag."""
        if pid not in self.proj.index:
            return
        row = self.proj.loc[pid]
        if not p.team:
            p.team = norm_team(str(row.get("team") or "")) or None
        if not p.positions:
            pos = row.get("positions")
            if isinstance(pos, list):
                p.positions = ",".join(pos) or None
            elif isinstance(pos, str) and pos:
                p.positions = pos.replace("|", ",")


def yahoo_id_map(state: dict[str, Any] | None) -> dict[int, str]:
    """Yahoo player id -> our id, from a previous synced state."""
    out: dict[int, str] = {}
    for t in (state or {}).get("teams", {}).values():
        if isinstance(t, dict):
            for pid, y in (t.get("yahoo_ids") or {}).items():
                out[int(y)] = pid
    return out


@dataclass
class ResolveReport:
    unknown_players: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    fuzzy_matches: list[dict[str, Any]] = field(default_factory=list)
    replaced_ids: list[dict[str, Any]] = field(default_factory=list)  # capture player_id we overrode

    def to_json(self) -> dict[str, Any]:
        return {"unknown_players": self.unknown_players, "unresolved": self.unresolved,
                "fuzzy_matches": self.fuzzy_matches, "replaced_ids": self.replaced_ids}


def resolve_capture(capture: Capture, resolver: PlayerResolver) -> ResolveReport:
    """Set every capture player's `player_id` (in place) and fill team/positions; report the misses."""
    rep = ResolveReport()
    for tid, t in capture.teams.items():
        for p in t.players:
            r = resolver.resolve(p.name, p.team, p.yahoo_player_id, p.player_id)
            info = {"yahoo_team_id": tid, "team": t.draft_slot, "name": p.name,
                    "yahoo_player_id": p.yahoo_player_id, "player_id": r.player_id}
            if p.player_id and p.player_id != r.player_id:
                rep.replaced_ids.append({**info, "capture_player_id": p.player_id})
            if r.method == "unknown":
                rep.unknown_players.append({**info, "near_miss": [c[0] for c in r.candidates]})
            elif r.method == "ambiguous":
                rep.unresolved.append({**info, "candidates": [c[0] for c in r.candidates]})
            elif r.method == "fuzzy":
                rep.fuzzy_matches.append(info)
            p.player_id = r.player_id
            resolver.fill(p, r.player_id)
    return rep


# ---------------------------------------------------------------- league state


def slot_map_from_file(path: Path | str) -> dict[int, int]:
    """Yahoo team id -> draft slot from a Yahoo league capture (key `teams`: {id: {draft_slot}})."""
    raw = json.loads(Path(path).read_text())
    return {int(k): int(v["draft_slot"]) for k, v in raw["teams"].items() if v.get("draft_slot")}


def assign_draft_slots(capture: Capture, previous_state: dict[str, Any] | None,
                       slot_map: dict[int, int] | None = None) -> None:
    """Fill missing `draft_slot`s: previous state's `yahoo_team_id`, then `slot_map`. Raises
    CaptureError if any team stays unmapped or two teams map to the same slot."""
    prev = {int(v["yahoo_team_id"]): int(k) for k, v in (previous_state or {}).get("teams", {}).items()
            if isinstance(v, dict) and v.get("yahoo_team_id") is not None}
    for tid, t in capture.teams.items():
        if t.draft_slot is None:
            t.draft_slot = prev.get(tid) or (slot_map or {}).get(tid)
    missing = sorted(tid for tid, t in capture.teams.items() if t.draft_slot is None)
    if missing:
        raise CaptureError(f"no draft slot for Yahoo team ids {missing} (pass --slot-map)")
    slots = [t.draft_slot for t in capture.teams.values()]
    if len(set(slots)) != len(slots):
        raise CaptureError(f"duplicate draft slots in capture: {sorted(slots)}")


def to_league_state(capture: Capture, previous_state: dict[str, Any] | None,
                    report: ResolveReport | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The `--league-file` JSON for a resolved capture (team id = draft slot)."""
    prev = previous_state or {}
    synced_at = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    teams: dict[str, Any] = {}
    for t in sorted(capture.teams.values(), key=lambda x: int(x.draft_slot or 0)):
        ids = [str(p.player_id) for p in t.players]
        teams[str(t.draft_slot)] = {
            "name": t.name or (prev.get("teams", {}).get(str(t.draft_slot), {}) or {}).get("name"),
            "players": ids,
            "yahoo_team_id": t.yahoo_team_id,
            "lineup": {str(p.player_id): p.slot for p in t.players},
            "status": {str(p.player_id): p.status for p in t.players if p.status},
            "yahoo_ids": {str(p.player_id): p.yahoo_player_id for p in t.players
                          if p.yahoo_player_id is not None},
        }
    src = f"yahoo roster capture {Path(capture.path).as_posix()}"
    if capture.captured_at:
        src += f" (captured {capture.captured_at})"
    out: dict[str, Any] = {"source": src, "my_team": int(prev.get("my_team", 1)),
                           "draft_id": prev.get("draft_id"), "synced_at": synced_at, "teams": teams}
    if report is not None:
        out["unknown_players"] = report.unknown_players
        out["unresolved"] = report.unresolved
    return out


# ---------------------------------------------------------------- diff


def _team_maps(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out = {}
    for k, v in (state or {}).get("teams", {}).items():
        out[int(k)] = {"players": [str(x) for x in v], "name": None} if isinstance(v, list) else v
    return out


def diff_states(old: dict[str, Any], new: dict[str, Any],
                names: dict[str, str] | None = None) -> dict[str, Any]:
    """Roster/status differences between two league states.

    Per team: `adds` (from free agency/waivers), `drops` (to free agency), `moved_in`/`moved_out`
    (a player changing fantasy teams = trade), `status_changes` (Yahoo tag; only when `old` carried
    a status map, otherwise reported as `status_baseline: false` and tags listed under `status_now`),
    `renamed`. `my_team` additionally lists lineup slot changes and players tagged out in a starting slot.
    """
    nm = names or {}
    label = lambda pid: {"player_id": pid, "name": nm.get(pid, pid)}  # noqa: E731
    o, n = _team_maps(old), _team_maps(new)
    owner_old = {p: t for t, v in o.items() for p in v["players"]}
    owner_new = {p: t for t, v in n.items() for p in v["players"]}
    moves = sorted(({"player_id": p, "name": nm.get(p, p), "from": owner_old[p], "to": t}
                    for p, t in owner_new.items() if p in owner_old and owner_old[p] != t),
                   key=lambda m: (m["from"], m["player_id"]))
    teams: dict[str, Any] = {}
    roster_changed = status_changed = False
    for t in sorted(set(o) | set(n)):
        ov, nv = o.get(t, {"players": []}), n.get(t, {"players": []})
        op, np_ = set(ov["players"]), set(nv["players"])
        adds = [label(p) for p in nv["players"] if p not in op and p not in owner_old]
        drops = [label(p) for p in ov["players"] if p not in np_ and p not in owner_new]
        moved_in = [{**label(m["player_id"]), "from": m["from"]} for m in moves if m["to"] == t]
        moved_out = [{**label(m["player_id"]), "to": m["to"]} for m in moves if m["from"] == t]
        entry: dict[str, Any] = {}
        if adds or drops or moved_in or moved_out:
            roster_changed = True
            entry.update(adds=adds, drops=drops, moved_in=moved_in, moved_out=moved_out)
        new_status = nv.get("status") or {}
        if "status" in ov:
            old_status = ov.get("status") or {}
            changes = [{**label(p), "old": old_status.get(p), "new": new_status.get(p)}
                       for p in nv["players"] if p in op and old_status.get(p) != new_status.get(p)]
            if changes:
                status_changed = True
                entry["status_changes"] = changes
        if ov.get("name") and nv.get("name") and ov["name"] != nv["name"]:
            entry["renamed"] = {"old": ov["name"], "new": nv["name"]}
        if entry:
            teams[str(t)] = {"name": nv.get("name") or ov.get("name"), **entry}
    baseline = all("status" in v for v in o.values()) if o else False
    me = int(new.get("my_team", old.get("my_team", 1)))
    mo, mn = o.get(me, {"players": []}), n.get(me, {"players": []})
    lineup_old, lineup_new = mo.get("lineup") or {}, mn.get("lineup") or {}
    status_new = mn.get("status") or {}
    my = {
        "team": me, "name": mn.get("name"), "players": len(mn["players"]),
        **{k: teams.get(str(me), {}).get(k, []) for k in ("adds", "drops", "moved_in", "moved_out",
                                                          "status_changes")},
        "status_now": {p: s for p, s in status_new.items()},
        "lineup_changes": [{**label(p), "old": lineup_old.get(p), "new": s} for p, s in lineup_new.items()
                           if lineup_old and lineup_old.get(p) != s],
        "out_in_starting_slot": [{**label(p), "slot": s, "status": status_new[p]} for p, s in
                                 lineup_new.items() if s not in BENCH_SLOTS and status_new.get(p) in OUT_TAGS],
    }
    out: dict[str, Any] = {"roster_changed": roster_changed, "status_changed": status_changed,
                           "status_baseline": baseline, "moves": moves, "teams": teams, "my_team": my}
    if not baseline:
        out["status_now"] = {str(t): v.get("status") or {} for t, v in n.items() if v.get("status")}
    return out


def sync(capture: Capture, previous_state: dict[str, Any] | None, projections: pd.DataFrame,
         slot_map: dict[int, int] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Resolve + build the new state + diff. Returns {state, diff, resolve}."""
    assign_draft_slots(capture, previous_state, slot_map)
    resolver = PlayerResolver(projections, yahoo_id_map(previous_state))
    report = resolve_capture(capture, resolver)
    dup = _duplicates(capture)
    if dup:
        raise CaptureError(f"player ids on more than one roster after resolution: {dup}")
    state = to_league_state(capture, previous_state, report, now)
    names = {str(k): str(v) for k, v in projections["name"].items()}
    names.update({str(p.player_id): p.name for t in capture.teams.values() for p in t.players})
    diff = diff_states(previous_state or {"teams": {}}, state, names)
    return {"state": state, "diff": diff, "resolve": report.to_json()}


def _duplicates(capture: Capture) -> list[str]:
    seen: dict[str, int] = {}
    for t in capture.teams.values():
        for p in t.players:
            seen[str(p.player_id)] = seen.get(str(p.player_id), 0) + 1
    return sorted(p for p, c in seen.items() if c > 1)


def normalised_capture(capture: Capture) -> dict[str, Any]:
    """The capture with resolved player ids and filled team/positions (for data/raw/yahoo/)."""
    out = {k: v for k, v in capture.raw.items() if k != "teams"}
    out["teams"] = {str(tid): {"name": t.name, "draft_slot": t.draft_slot,
                               "players": [p.to_json() for p in t.players]}
                    for tid, t in sorted(capture.teams.items())}
    return out


# ---------------------------------------------------------------- free agents


def normalise_free_agents(raw: dict[str, Any], resolver: PlayerResolver) -> dict[str, Any]:
    """Resolve ids / fill team+positions / normalise team codes in a free-agent capture
    (``scripts/yahoo/capture_free_agents.js``); players not in projections keep player_id null and are
    listed in `unknown_players`."""
    if not isinstance(raw, dict) or not isinstance(raw.get("players"), list):
        raise CaptureError("free-agent capture must be an object with a 'players' list")
    players, unknown = [], []
    for p in raw["players"]:
        cp = CapturePlayer(slot="BN", name=str(p.get("name") or "").strip(), player_id=p.get("player_id"),
                           team=norm_team(p.get("team")), positions=p.get("positions") or None,
                           status=p.get("status") or None, yahoo_player_id=_opt_int(p.get("yahoo_player_id")))
        if not cp.name:
            raise CaptureError(f"free-agent row without a name: {p}")
        r = resolver.resolve(cp.name, cp.team, cp.yahoo_player_id, cp.player_id)
        pid = r.player_id if r.method not in ("unknown", "ambiguous") else None
        if pid:
            resolver.fill(cp, pid)
        else:
            unknown.append({"name": cp.name, "yahoo_player_id": cp.yahoo_player_id,
                            "candidates": [c[0] for c in r.candidates]})
        row = {**p, **{k: v for k, v in cp.to_json().items() if k != "slot"}, "player_id": pid}
        players.append(row)
    return {**{k: v for k, v in raw.items() if k != "players"}, "players": players, "unknown_players": unknown}
