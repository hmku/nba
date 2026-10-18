"""`fantasy settings ...` — effective league settings and their verification status."""

from __future__ import annotations

from typing import Annotated

import typer

from fantasy.cli_common import emit, fail
from fantasy.draft.order import picks_for_slot
from fantasy.league.settings import load_settings, save_settings
from fantasy.ops.common import EXIT_USAGE

app = typer.Typer(no_args_is_help=True, help="League settings (verify against Yahoo before trusting).")


@app.command()
def show(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    s = load_settings()
    out = {**s.model_dump(), "num_rounds": s.num_rounds, "starting_slots": s.starting_slots,
           "unverified": s.unverified_fields(),
           "my_picks": picks_for_slot(s.my_slot, s.num_teams, s.num_rounds, s.draft_order_type)}
    if as_json:
        emit(out, True)
        return
    for k, v in out.items():
        typer.echo(f"{k:>18}: {v}")


@app.command()
def verify(
    field: Annotated[list[str], typer.Argument(help="Fields confirmed against Yahoo, e.g. categories roster_slots")],
    source: Annotated[str, typer.Option("--source", help="yahoo_api | browser | manual")] = "browser",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark settings fields as verified (after checking them on Yahoo). Edit data/manual/league_settings.json to change values."""
    s = load_settings()
    known = set(type(s).model_fields)
    bad = [f for f in field if f not in known]
    if bad:
        fail(f"unknown fields: {bad}", as_json, EXIT_USAGE, valid=sorted(known))
    s.verified.update({f: True for f in field})
    s.source = source
    path = save_settings(s)
    out = {"ok": True, "path": str(path), "verified": s.verified, "unverified": s.unverified_fields()}
    emit(out, as_json, f"saved {path}; still unverified: {out['unverified']}")


try:  # one-time interactive Yahoo OAuth (run locally)
    from fantasy.league import yahoo_auth

    app.command("yahoo-auth")(yahoo_auth.cli_command)
except (ImportError, AttributeError):
    pass
