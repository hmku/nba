"""Lineup plans and actual (Yahoo) lineups as `model_runs` rows, for the dashboard's Lineup view.

Two kinds, both immutable and idempotent per (date, content hash):

* `lineup_plan` (`fantasy lineup plan --save`): what the optimizer wants for a date (and the rest of the
  week), with the decide-at-tip-off items, IL moves and expected categories / P(win) with their
  availability assumption. run_id `lp-<date>-<sha12>`.
* `lineup_actual` (`fantasy lineup record`, `fantasy league sync --write --record-lineup`,
  `fantasy lineup backfill`): what Yahoo actually showed for my team on a date, with when and how it was
  read (a verified server re-read after a lineup txn, or a roster capture). run_id `la-<date>-<sha12>`.

Re-saving identical content inserts nothing (same run_id). Readers show the newest row per date (plans:
the newest plan made ON that date, else the newest earlier plan's `week_plan` entry; actuals: the newest
`created_at`). Schemas: docs/research/dashboard_data.md section 6. Nothing here touches Yahoo.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import insert, select

from fantasy.db.schema import model_runs
from fantasy.ops.common import canonical_json, engine, git_sha, json_safe, now, row_dict, sha256_hex

if TYPE_CHECKING:
    from fantasy.db.rest import RestClient
    from fantasy.sim.schedule import NBASchedule

PLAN_KIND = "lineup_plan"
ACTUAL_KIND = "lineup_actual"
PLAN_SCHEMA = "lineup_plan/1"
ACTUAL_SCHEMA = "lineup_actual/1"
RECORD_VERSION = "lineup-record-0.1.0"  # model_version of lineup_actual rows (plans carry the optimizer's)
SOURCES = ("yahoo_verified", "yahoo_capture")
SLOTS = ("PG", "SG", "G", "SF", "PF", "F", "C", "Util", "BN", "IL", "IL+")
_SLOT_ALIASES = {s.upper(): s for s in SLOTS} | {"UTIL": "Util", "IR": "IL"}


class RecordError(ValueError):
    """Bad input for a lineup record (CLI exit code: usage)."""


# ------------------------------------------------------------------ helpers


def norm_slot(v: object) -> str | None:
    """'Util2' / 'BN1' / 'c' -> 'Util' / 'BN' / 'C'; None if not a lineup slot."""
    if not isinstance(v, str):
        return None
    k = v.strip()
    if k.upper() in _SLOT_ALIASES:
        return _SLOT_ALIASES[k.upper()]
    k = re.sub(r"[\s_-]*\d+$", "", k).upper()
    return _SLOT_ALIASES.get(k)


def name_key(name: str) -> str:
    """Accent/punctuation/suffix-insensitive name key ('Nikola Jokić' == 'nikola-jokic')."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    return " ".join(s.split())


def game_markers(nba: NBASchedule, teams: dict[str, str | None], days: list[str]) -> dict[str, dict[str, str]]:
    """{player_id: {date: '@DET' | 'vs PHI'}} for the days each player's NBA team plays (absent = no game)."""
    want = set(days)
    by_day: dict[str, dict[str, str]] = {}
    for d, v, h in nba.games:
        k = d.isoformat()
        if k not in want:
            continue
        by_day.setdefault(k, {})[v] = f"@{h}"
        by_day.setdefault(k, {})[h] = f"vs {v}"
    out: dict[str, dict[str, str]] = {}
    for pid, team in teams.items():
        g = {k: m[team] for k, m in by_day.items() if team and team in m}
        if g:
            out[pid] = dict(sorted(g.items()))
    return out


def _content_hash(summary: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(json_safe(summary)))


# ------------------------------------------------------------------ lineup_plan


def _slot_rank(slot: str) -> int:
    return SLOTS.index(slot) if slot in SLOTS else len(SLOTS)


def _day_slots(entries: list[dict[str, Any]], games: dict[str, dict[str, str]], day: str) -> list[dict[str, Any]]:
    out = []
    for e in sorted(entries, key=lambda e: (_slot_rank(e["slot"]), e["player_id"])):
        row = {"slot": e["slot"], "player_id": e["player_id"], "has_game": bool(e.get("has_game")),
               "p_play": e.get("p_play"), "value": e.get("value")}
        g = games.get(e["player_id"], {}).get(day)
        if g:
            row["game"] = g
        out.append(row)
    return out


def _deadline(row: dict[str, Any], names: dict[str, str], games: dict[str, dict[str, str]], day: str) -> str | None:
    """Plain-language latest moment to act on a decide-at-tip-off item (Yahoo locks each player at his tip-off)."""
    swap = row.get("swap_with")
    if row.get("slot") not in ("BN", "IL", "IL+"):
        g = games.get(row["player_id"], {}).get(day)
        return f"before {row['name']}'s tip-off{f' ({g})' if g else ''}"
    if swap:
        g = games.get(swap, {}).get(day)
        return f"before {names.get(swap, swap)}'s tip-off{f' ({g})' if g else ''}"
    return None


def plan_summary(out: dict[str, Any], games: dict[str, dict[str, str]]) -> dict[str, Any]:
    """The compact display payload (`lineup_plan/1`) of a `fantasy lineup plan` result.

    `out` must still hold `week_plan` (the CLI drops it without --week; the saved row always keeps it)."""
    day = str(out["date"])
    players = {p["player_id"]: {"name": p["name"], "positions": list(p.get("positions") or []),
                                "nba_team": p.get("team"), "status": p.get("status"),
                                "current_slot": p.get("current_slot"), "games_left": p.get("games_left"),
                                "value_per_game": p.get("value_per_game")}
               for p in out.get("players", [])}
    names = {pid: p["name"] for pid, p in players.items()}
    today = out["lineup"] + out["bench"] + out["il"]
    tipoff = []
    for r in out.get("decide_at_tipoff", []):
        row = dict(r)
        if row.get("swap_with"):
            row["swap_with_name"] = names.get(row["swap_with"], row["swap_with"])
        row["game"] = games.get(row["player_id"], {}).get(day)
        row["deadline"] = _deadline(row, names, games, day)
        tipoff.append(row)
    ex = out.get("expected") or {}
    mc = out.get("monte_carlo") or {}
    expected: dict[str, Any] = {
        "method": ex.get("method"), "availability_assumption": ex.get("availability_assumption"),
        "evidence": "simulated (model output; availability and tag P(plays) are PRIORS)",
        **{k: ex[k] for k in ("optimized", "naive_season_value", "static_current_lineup", "cat_win_prob",
                              "cat_win_prob_method", "cat_win_prob_analytic", "covers_week") if k in ex},
    }
    if mc.get("plans"):
        expected["monte_carlo"] = {
            "n_sims": mc.get("n_sims"), "seed": mc.get("seed"),
            "plans": {name: {k: r.get(k) for k in ("expected_cats", "expected_cats_se", "p_win_matchup",
                                                   "p_win_matchup_se")}
                      for name, r in mc["plans"].items()}}
    week_plan = [{"date": d["date"], "slots": _day_slots(d["lineup"], games, d["date"]),
                  **{k: d.get(k) for k in ("players_with_game", "started_with_game", "starting_slots",
                                            "unused_starts", "unused_players", "empty_slots", "decide_at_tipoff")}}
                 for d in out.get("week_plan", [])]
    return json_safe({
        "schema": PLAN_SCHEMA, "date": day, "week": out.get("week"), "week_start": out.get("week_start"),
        "week_end": out.get("week_end"), "my_team": out.get("my_team"), "opponent": out.get("opponent"),
        "players": players,
        "slots": _day_slots(today, games, day),
        "bench_with_game": [{"player_id": e["player_id"], "name": e["name"], "game": games.get(e["player_id"], {}).get(day)}
                            for e in out["bench"] if e.get("has_game")],
        "decide_at_tipoff": tipoff,
        "il_moves": out.get("il_moves", []),
        "changes_vs_current": out.get("changes_vs_current", []),
        "today": out.get("today"),
        "week_totals": out.get("week_totals"),
        "week_plan": week_plan,
        "games": games,
        "expected": expected,
        "fixed_point": out.get("fixed_point"),
        "slots_as_of": out.get("slots_as_of"),
        # lineup-0.3.0 (additive): banked totals used and the per-category weights behind the plan
        "accumulated_source": out.get("accumulated_source"),
        "accumulated": out.get("accumulated"),
        "category_weights": {k: {f: round(float(v), 5) for f, v in d.items()}
                             for k, d in (out.get("category_weights") or {}).items()},
        # lineup-0.4.0 (additive): protect-ratios plan vs the default one -- a SUGGESTION, never applied
        "ratio_bench": out.get("ratio_bench"),
        "warnings": out.get("warnings", []),
        "model_version": out.get("model_version"),
    })


def plan_row(out: dict[str, Any], games: dict[str, dict[str, str]]) -> dict[str, Any]:
    summary = plan_summary(out, games)
    h = _content_hash(summary)
    return {"run_id": f"lp-{summary['date']}-{h[:12]}", "kind": PLAN_KIND,
            "model_version": str(out.get("model_version") or "lineup"), "git_sha": git_sha(),
            "params": json_safe(out.get("params") or {}),
            "inputs": json_safe({**(out.get("inputs") or {}), "content_sha256": h}),
            "summary": summary, "created_at": now()}


# ------------------------------------------------------------------ lineup_actual


class Roster:
    """My roster as known locally (league state + capture + projections): resolves Yahoo slot maps keyed by
    our id, Yahoo player id or name, and fills name / positions / NBA team / status."""

    def __init__(self, players: dict[str, dict[str, Any]] | None = None) -> None:
        self.players: dict[str, dict[str, Any]] = dict(players or {})

    @classmethod
    def build(cls, league_team: dict[str, Any] | None, capture_players: list[dict[str, Any]] | None = None,
              names: dict[str, str] | None = None) -> Roster:
        info: dict[str, dict[str, Any]] = {}
        lt = league_team or {}
        for pid in lt.get("players") or []:
            info[str(pid)] = {"name": (names or {}).get(str(pid), str(pid)),
                              "yahoo_player_id": (lt.get("yahoo_ids") or {}).get(pid),
                              "status": (lt.get("status") or {}).get(pid)}
        for p in capture_players or []:
            pid = p.get("player_id")
            if not pid:
                continue
            row = info.setdefault(str(pid), {})
            row.update({k: v for k, v in {"name": p.get("name"), "yahoo_player_id": p.get("yahoo_player_id"),
                                          "positions": p.get("positions"), "nba_team": p.get("team"),
                                          "status": p.get("status")}.items() if v is not None})
            row.setdefault("status", None)
        return cls(info)

    def resolve(self, key: object) -> str | None:
        k = str(key).strip()
        if k in self.players:
            return k
        if k.isdigit():
            for pid, p in self.players.items():
                if str(p.get("yahoo_player_id")) == k:
                    return pid
        nk = name_key(k)
        hits = [pid for pid, p in self.players.items() if name_key(p.get("name") or "") == nk or name_key(pid) == nk]
        if len(hits) == 1:
            return hits[0]
        # last name only ('Jokic'), when unique
        hits = [pid for pid, p in self.players.items() if nk and name_key(p.get("name") or pid).split()[-1:] == [nk]]
        return hits[0] if len(hits) == 1 else None


def _pairs(raw: Any) -> list[tuple[object, object]]:
    """(player key, slot) pairs from {player: slot}, {slot: player}, [{slot, player_id|name|...}] or {"slots": ...}."""
    if isinstance(raw, dict) and isinstance(raw.get("slots"), dict | list):
        return _pairs(raw["slots"])
    if isinstance(raw, list):
        out = []
        for i, e in enumerate(raw):
            if not isinstance(e, dict):
                raise RecordError(f"slots[{i}]: expected an object with slot + player_id/name")
            key = next((e[k] for k in ("player_id", "yahoo_player_id", "player", "name") if e.get(k) not in (None, "")),
                       None)
            out.append((key, e.get("slot")))
        return out
    if isinstance(raw, dict):
        items = list(raw.items())
        if items and all(norm_slot(k) for k, _ in items) and not all(norm_slot(v) for _, v in items):
            return [(v, k) for k, v in items]  # {slot: player}
        return items
    raise RecordError("slots: expected {player: slot}, {slot: player} or a list of {slot, player_id|name}")


def normalise_slots(raw: Any, roster: Roster) -> tuple[list[dict[str, Any]], list[str]]:
    """Slot entries [{slot, player_id, name, ...}] sorted by slot; warnings for unresolved players."""
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for key, slot in _pairs(raw):
        if key in (None, ""):
            continue  # an empty slot
        s = norm_slot(slot)
        if s is None:
            raise RecordError(f"{key}: unknown slot {slot!r} (valid: {', '.join(SLOTS)})")
        pid = roster.resolve(key)
        if pid is None:
            warnings.append(f"player {key!r} not on my roster (kept by name, player_id null)")
            out.append({"slot": s, "player_id": None, "name": str(key)})
            continue
        p = roster.players[pid]
        row = {"slot": s, "player_id": pid, "name": p.get("name") or pid}
        for k in ("positions", "nba_team", "status"):
            if p.get(k) is not None:
                row[k] = p[k]
        out.append(row)
    ids = [r["player_id"] for r in out if r["player_id"]]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise RecordError(f"players in more than one slot: {dup}")
    out.sort(key=lambda r: (_slot_rank(r["slot"]), r["player_id"] or r["name"]))
    return out, warnings


def actual_summary(day: str, slots: list[dict[str, Any]], *, source: str, team: int | None,
                   observed_at: str | None, how: str | None, txn_id: str | None = None,
                   capture_file: str | None = None, games: dict[str, dict[str, str]] | None = None,
                   roster_size: int | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
    if source not in SOURCES:
        raise RecordError(f"source must be one of {list(SOURCES)}, got {source!r}")
    try:
        date.fromisoformat(day)
    except ValueError as e:
        raise RecordError(f"date: expected YYYY-MM-DD, got {day!r}") from e
    if not slots:
        raise RecordError("no slots to record")
    rows = []
    for s in slots:
        r = dict(s)
        g = (games or {}).get(r.get("player_id") or "", {}).get(day)
        if g:
            r["game"] = g
        rows.append(r)
    return json_safe({
        "schema": ACTUAL_SCHEMA, "date": day, "team": team, "source": source,
        "observed_at": observed_at, "how": how, "txn_id": txn_id, "capture_file": capture_file,
        "slots": rows,
        "complete": None if roster_size is None else len([r for r in rows if r.get("player_id")]) >= roster_size,
        "games_known": games is not None,
        "warnings": warnings or [],
    })


def actual_row(summary: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    # hash: what Yahoo showed + where it came from (not observed_at: re-reading the same state is a no-op)
    h = _content_hash({k: summary.get(k) for k in ("date", "team", "source", "slots", "txn_id")})
    return {"run_id": f"la-{summary['date']}-{h[:12]}", "kind": ACTUAL_KIND, "model_version": RECORD_VERSION,
            "git_sha": git_sha(), "params": json_safe(params or {}),
            "inputs": {"content_sha256": h, "txn_id": summary.get("txn_id"),
                       "capture_file": summary.get("capture_file")},
            "summary": summary, "created_at": now()}


def capture_team(capture: dict[str, Any], my_team: int, yahoo_team_id: int | None = None) -> dict[str, Any] | None:
    """My team's entry in a roster capture (by draft slot, else Yahoo team id)."""
    teams = capture.get("teams") or {}
    for t in teams.values():
        if isinstance(t, dict) and t.get("draft_slot") is not None and int(t["draft_slot"]) == my_team:
            return t
    if yahoo_team_id is not None and str(yahoo_team_id) in teams:
        return teams[str(yahoo_team_id)]
    return None


# ------------------------------------------------------------------ storage


class RunStore(Protocol):
    name: str

    def insert_missing(self, row: dict[str, Any]) -> bool:
        """Insert unless a row with this run_id exists; True if inserted."""
        ...

    def rows(self, kind: str, limit: int = 500) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class LocalStore:
    name = "db"

    def insert_missing(self, row: dict[str, Any]) -> bool:
        with engine().begin() as conn:
            if conn.execute(select(model_runs.c.run_id).where(model_runs.c.run_id == row["run_id"])).first():
                return False
            conn.execute(insert(model_runs).values(**row))
            return True

    def rows(self, kind: str, limit: int = 500) -> list[dict[str, Any]]:
        with engine().connect() as conn:
            res = conn.execute(select(model_runs).where(model_runs.c.kind == kind)
                               .order_by(model_runs.c.created_at.desc()).limit(limit))
            return [row_dict(r) for r in res]

    def close(self) -> None:
        pass


class RestStore:
    """Supabase over HTTPS (same auth as `fantasy publish --rest`)."""

    name = "rest"

    def __init__(self, client: RestClient | None = None) -> None:
        from fantasy.db import rest  # local import: keeps httpx off the default path

        self._rest = rest
        self.cl = client if client is not None else rest.default_client()

    def insert_missing(self, row: dict[str, Any]) -> bool:
        payload = {k: self._rest.to_json_value(v) for k, v in row.items()}
        n = self.cl.insert_missing("model_runs", [payload], ["run_id"])
        return bool(n)

    def rows(self, kind: str, limit: int = 500) -> list[dict[str, Any]]:
        resp = self.cl.request("GET", "model_runs", params={
            "select": "*", "kind": f"eq.{kind}", "order": "created_at.desc", "limit": limit})
        self.cl.check(resp, "read model_runs")
        out = []
        for r in resp.json():
            if isinstance(r.get("created_at"), str):
                r["created_at"] = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            out.append(r)
        return out

    def close(self) -> None:
        self.cl.close()


def store(use_rest: bool = False, client: RestClient | None = None) -> RunStore:
    return RestStore(client) if use_rest else LocalStore()


def save(row: dict[str, Any], st: RunStore) -> dict[str, Any]:
    inserted = st.insert_missing(row)
    return {"run_id": row["run_id"], "kind": row["kind"], "date": row["summary"]["date"], "inserted": inserted,
            "store": st.name}


# ------------------------------------------------------------------ backfill helpers


EVIDENCE_SLOT_KEYS = ("slots", "server_slots", "final_slots", "after", "lineup")


def txn_date(txn: dict[str, Any]) -> str | None:
    p = txn.get("payload") or {}
    for k in ("date", "for_date", "day"):
        v = p.get(k) if isinstance(p, dict) else None
        if isinstance(v, str):
            try:
                return date.fromisoformat(v[:10]).isoformat()
            except ValueError:
                continue
    return None


def txn_slots(txn: dict[str, Any]) -> Any:
    """The verified slot map in a lineup txn's verification evidence (None if it holds none)."""
    v = txn.get("verification") or {}
    ev = v.get("evidence") if isinstance(v, dict) else None
    for src in (ev, v):
        if isinstance(src, dict):
            for k in EVIDENCE_SLOT_KEYS:
                if src.get(k):
                    return src[k]
    return None


def txn_changes(txn: dict[str, Any]) -> list[dict[str, Any]]:
    p = txn.get("payload") or {}
    ch = p.get("changes") if isinstance(p, dict) else None
    return [c for c in ch if isinstance(c, dict)] if isinstance(ch, list) else []


def apply_changes(base: dict[str, str], changes: list[dict[str, Any]], roster: Roster) -> dict[str, str]:
    """Replay a lineup txn's `changes` ([{player|player_id|name, from, to}]) on a {player_id: slot} state.
    Raises RecordError when a player is unknown or a change's `from` disagrees with the base state (then the
    base is not the state the txn was applied to, and the derived lineup would be wrong)."""
    out = dict(base)
    for i, c in enumerate(changes):
        key = next((c[k] for k in ("player_id", "player", "name", "yahoo_player_id") if c.get(k) not in (None, "")), None)
        pid = roster.resolve(key) if key is not None else None
        if pid is None or pid not in out:
            raise RecordError(f"changes[{i}]: player {key!r} not in the base lineup")
        frm, to = norm_slot(c.get("from")), norm_slot(c.get("to"))
        if to is None:
            raise RecordError(f"changes[{i}]: unknown target slot {c.get('to')!r}")
        if frm is not None and out[pid] != frm:
            raise RecordError(f"changes[{i}]: {pid} is in {out[pid]} in the base lineup, not {frm}")
        out[pid] = to
    return out
