"""`fantasy league ...` — Yahoo roster sync (browser capture -> simulator league state + diff).

Register in fantasy/cli.py as ("fantasy.league.sync_cli", "app", "league").
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer

from fantasy.cli_common import emit, fail
from fantasy.league.settings import REPO_ROOT
from fantasy.league.sync import (
    CaptureError,
    PlayerResolver,
    assign_draft_slots,
    load_capture,
    normalise_free_agents,
    normalised_capture,
    parse_capture,
    resolve_capture,
    slot_map_from_file,
    sync,
    yahoo_id_map,
)
from fantasy.model.lines import load_projections
from fantasy.ops.common import EXIT_USAGE

app = typer.Typer(no_args_is_help=True, help="Yahoo roster sync (read-only captures -> league state).")

DEFAULT_STATE = REPO_ROOT / "data" / "manual" / "league_rosters_2026-27.json"
YAHOO_RAW = REPO_ROOT / "data" / "raw" / "yahoo"

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
ProjOpt = Annotated[Path | None, typer.Option("--projections", help="Projections CSV (default data/processed)")]
SlotMapOpt = Annotated[Path | None, typer.Option(
    "--slot-map", help="Yahoo league capture with teams{yahoo id: {draft_slot}} (default: newest "
                       "data/raw/yahoo/league_schedule_*.json); only used for teams without a draft slot")]


def prev_path(state: Path) -> Path:
    """Where `--write` keeps the replaced state: `<stem>.prev.json` next to it (one generation; git has the rest)."""
    return state.with_name(state.stem + ".prev.json")


def _default_slot_map() -> Path | None:
    files = sorted(YAHOO_RAW.glob("league_schedule_*.json"))
    return files[-1] if files else None


def _load_slot_map(path: Path | None, as_json: bool) -> dict[int, int]:
    path = path or _default_slot_map()
    if not path:
        return {}
    try:
        return slot_map_from_file(path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        fail(f"bad --slot-map {path}: {e}", as_json, EXIT_USAGE)


def _load_state(path: Path, as_json: bool) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        fail(f"bad --state {path}: {e}", as_json, EXIT_USAGE)
    if not isinstance(data, dict) or not isinstance(data.get("teams"), dict):
        fail(f"bad --state {path}: no 'teams' object", as_json, EXIT_USAGE)
    return data


def _projections(path: Path | None, as_json: bool):  # -> pd.DataFrame
    try:
        return load_projections(path).df
    except (OSError, ValueError) as e:
        fail(f"cannot load projections: {e}", as_json, EXIT_USAGE)


def _diff_text(diff: dict, resolve: dict) -> str:
    lines = []
    if not diff["roster_changed"]:
        lines.append("rosters unchanged")
    for m in diff["moves"]:
        lines.append(f"TRADE/MOVE {m['name']}: team {m['from']} -> team {m['to']}")
    for t, v in diff["teams"].items():
        for k in ("adds", "drops"):
            for p in v.get(k, []):
                lines.append(f"team {t} ({v['name']}): {k[:-1]} {p['name']}")
        for s in v.get("status_changes", []):
            lines.append(f"team {t}: {s['name']} status {s['old']} -> {s['new']}")
        if "renamed" in v:
            lines.append(f"team {t}: renamed {v['renamed']['old']!r} -> {v['renamed']['new']!r}")
    me = diff["my_team"]
    lines.append(f"my team {me['team']} ({me['name']}): {me['players']} players; status {me['status_now'] or '{}'}")
    for p in me["out_in_starting_slot"]:
        lines.append(f"  WARNING {p['name']} is {p['status']} but in starting slot {p['slot']}")
    for k in ("unknown_players", "unresolved", "fuzzy_matches"):
        for p in resolve[k]:
            lines.append(f"{k}: team {p['team']} {p['name']} -> {p['player_id']}")
    return "\n".join(lines)


@app.command("sync")
def sync_cmd(
    capture: Annotated[Path, typer.Option("--capture", help="Roster capture JSON (scripts/yahoo/capture_rosters.js)")],
    state: Annotated[Path, typer.Option("--state", help="League state (--league-file) to diff against / replace")] = DEFAULT_STATE,
    write: Annotated[bool, typer.Option("--write", help="Write the new state (old one kept as <stem>.prev.json)")] = False,
    projections: ProjOpt = None,
    slot_map: SlotMapOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Turn a Yahoo roster capture into the simulator's league state and show what changed.
    Dry run (diff only) unless --write."""
    try:
        cap = load_capture(capture)
    except CaptureError as e:
        fail(str(e), as_json, EXIT_USAGE, code="invalid_capture")
    old = _load_state(state, as_json)
    proj = _projections(projections, as_json)
    try:
        res = sync(cap, old, proj, _load_slot_map(slot_map, as_json))
    except CaptureError as e:
        fail(str(e), as_json, EXIT_USAGE, code="invalid_capture")
    written = None
    if write:
        state.parent.mkdir(parents=True, exist_ok=True)
        if state.exists():
            shutil.copyfile(state, prev_path(state))
        state.write_text(json.dumps(res["state"], indent=1, ensure_ascii=False) + "\n")
        written = {"state": str(state), "previous": str(prev_path(state)) if old is not None else None}
    out = {"ok": True, "capture": str(capture), "state": str(state), "written": written,
           "diff": res["diff"], **res["resolve"]}
    emit(out, as_json, _diff_text(res["diff"], res["resolve"])
         + (f"\nwrote {state}" if written else "\n(dry run; --write to save)"))


@app.command("import-capture")
def import_capture(
    raw: Annotated[Path, typer.Option("--raw", help="JSON returned by scripts/yahoo/capture_*.js")],
    kind: Annotated[str, typer.Option("--kind", help="rosters | free_agents")],
    out: Annotated[Path | None, typer.Option("--out", help="Default data/raw/yahoo/<kind>_<date>.json")] = None,
    state: Annotated[Path, typer.Option("--state", help="League state (Yahoo id map, draft slots)")] = DEFAULT_STATE,
    projections: ProjOpt = None,
    slot_map: SlotMapOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Normalise a browser capture: resolve our player ids, fill team/positions hidden by status tags,
    map team codes (CHA->CHO ...) and draft slots, and save it under data/raw/yahoo/."""
    if kind not in ("rosters", "free_agents"):
        fail(f"invalid --kind {kind!r}; valid: rosters, free_agents", as_json, EXIT_USAGE,
             valid=["rosters", "free_agents"])
    try:
        data = json.loads(raw.read_text())
    except (OSError, json.JSONDecodeError) as e:
        fail(f"cannot read --raw {raw}: {e}", as_json, EXIT_USAGE)
    if isinstance(data, str):  # the JS tool may hand back the JSON string itself
        data = json.loads(data)
    prev = _load_state(state, as_json)
    resolver = PlayerResolver(_projections(projections, as_json), yahoo_id_map(prev))
    try:
        if kind == "rosters":
            cap = parse_capture(data, str(raw))
            assign_draft_slots(cap, prev, _load_slot_map(slot_map, as_json))
            rep = resolve_capture(cap, resolver).to_json()
            result = normalised_capture(cap)
            result["resolve"] = rep
            summary = {k: len(v) for k, v in rep.items()}
        else:
            result = normalise_free_agents(data, resolver)
            summary = {"players": len(result["players"]), "unknown_players": len(result["unknown_players"])}
    except CaptureError as e:
        fail(str(e), as_json, EXIT_USAGE, code="invalid_capture")
    stamp = str(data.get("captured_at") or date.today().isoformat())[:10]
    dest = out or YAHOO_RAW / f"{'league_rosters' if kind == 'rosters' else 'free_agents'}_{stamp}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    emit({"ok": True, "kind": kind, "out": str(dest), **summary}, as_json, f"wrote {dest}: {summary}")
