"""`fantasy league score`: validate a Yahoo matchup capture, score it, optionally save it (model_runs kind
`matchup_score`). Registered on `fantasy.league.sync_cli.app` (imported at the bottom of that module).
Schema: docs/research/dashboard_data.md section 7. Nothing here touches Yahoo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from fantasy.cli_common import emit, fail
from fantasy.data.provenance import sha256_file
from fantasy.league import matchup
from fantasy.league.settings import load_settings
from fantasy.league.sync import slot_map_from_file
from fantasy.league.sync_cli import DEFAULT_STATE, YAHOO_RAW, JsonOpt, SlotMapOpt, app
from fantasy.ops.common import EXIT_USAGE, OpsError


def _slot_map(state: Path, slot_map: Path | None) -> tuple[dict[int, int], dict[str, Any] | None]:
    """Yahoo team id -> draft slot: league state's yahoo_team_id first, then the league_schedule capture."""
    st = json.loads(state.read_text()) if state.exists() else None
    out: dict[int, int] = {}
    files = sorted(YAHOO_RAW.glob("league_schedule_*.json"))
    path = slot_map or (files[-1] if files else None)
    if path is not None and path.exists():
        out.update(slot_map_from_file(path))
    out.update(matchup.slot_map_from_state(st))
    return out, st


def summary_from_capture(capture: Path | None, html: Path | None, *, week: int | None, state: Path,
                         slot_map: Path | None, schedule: Path | None,
                         captured_at: str | None = None) -> tuple[dict[str, Any], str | None]:
    """(matchup_score summary, sha256 of the input file); raises MatchupError on an unusable input.
    `captured_at` fills a capture without one (and is required for saved HTML)."""
    from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH, load_schedule_json

    smap, st = _slot_map(state, slot_map)
    my_slot = int((st or {}).get("my_team") or load_settings().my_slot)
    if html is not None:
        my_yahoo = next((y for y, s in smap.items() if s == my_slot), 11)
        cap = matchup.parse_matchup_html(html.read_text(), my_team=my_yahoo, week=week, captured_at=captured_at,
                                         source=f"saved Yahoo matchup page {html.name}")
        src = html
    else:
        assert capture is not None
        cap = matchup.load_capture_json(capture)
        if captured_at and not cap.get("captured_at"):
            cap["captured_at"] = captured_at
        src = capture
    sp = schedule or LEAGUE_SCHEDULE_PATH
    ms = load_schedule_json(sp) if sp.exists() else None
    summary = matchup.build_summary(cap, my_slot=my_slot, slot_map=smap, week=week,
                                    calendar=ms.calendar if ms else None,
                                    opponent_of=ms.opponent if ms else None, capture_file=str(src))
    return summary, sha256_file(src)


def _text(s: dict[str, Any]) -> str:
    me, opp = s["me"], s["opp"]
    lines = [f"Week {s['week']} as of {s['observed_at']} ({s['date_et']} ET): {me['name']} (team {me['slot']}) "
             f"{s['score']['me']}-{s['score']['opp']}-{s['score']['ties']} vs {opp['name']} (team {opp['slot']})"]
    for c in s["categories"]:
        lines.append(f"  {c['cat']:<7} {c['me']!s:>8} {c['opp']!s:>8}  {c['leader']}")
    lines += [f"warning: {w}" for w in s["warnings"]]
    return "\n".join(lines)


@app.command("score")
def score_cmd(
    capture: Annotated[Path | None, typer.Option("--capture", help="JSON from scripts/yahoo/capture_matchup.js")] = None,
    html: Annotated[Path | None, typer.Option("--html", help="Saved Yahoo matchup page HTML (alternative to "
                                              "--capture; needs --captured-at)")] = None,
    captured_at: Annotated[str | None, typer.Option("--captured-at", help="ISO time the page was read (with --html, "
                                                    "or to fill a capture without captured_at)")] = None,
    week: Annotated[int | None, typer.Option("--week", help="Fantasy week (default: the capture's, else the "
                                             "schedule week of the observed date)")] = None,
    state: Annotated[Path, typer.Option("--state", help="League state (yahoo_team_id -> draft slot, my_team)")] = DEFAULT_STATE,
    slot_map: SlotMapOpt = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON (week dates, "
                                                  "opponent check)")] = None,
    save: Annotated[bool, typer.Option("--save", help="Write the model_runs row (kind matchup_score; idempotent "
                                       "per week, ET date and content)")] = False,
    use_rest: Annotated[bool, typer.Option("--rest", help="With --save: write Supabase over HTTPS (cloud sessions) "
                                           "instead of FANTASY_DB_URL")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Validate + score a Yahoo matchup capture (banked category totals this week; FG%/FT% from makes/attempts)."""
    if (capture is None) == (html is None):
        fail("pass exactly one of --capture and --html", as_json, EXIT_USAGE)
    if use_rest and not save:
        fail("--rest only applies with --save", as_json, EXIT_USAGE)
    if html is not None and not captured_at:
        fail("--html needs --captured-at (when the page was read)", as_json, EXIT_USAGE)
    try:
        summary, sha = summary_from_capture(capture, html, week=week, state=state, slot_map=slot_map,
                                            schedule=schedule, captured_at=captured_at)
    except matchup.MatchupError as e:
        fail(str(e), as_json, EXIT_USAGE, code="invalid_capture")
    row = matchup.score_row(summary, capture_sha256=sha)
    out: dict[str, Any] = {"ok": True, "run_id": row["run_id"], "summary": summary}
    text = _text(summary)
    if save:
        from fantasy.lineup import record

        try:
            st = record.store(use_rest)
            try:
                out["saved"] = matchup.save(row, st)
            finally:
                st.close()
        except OpsError as e:
            fail(e.message, as_json, e.exit_code, code=e.code)
        text += f"\n{'saved' if out['saved']['inserted'] else 'unchanged'}: {row['run_id']} ({out['saved']['store']})"
    emit(out, as_json, text)
