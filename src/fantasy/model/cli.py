"""`fantasy values ...` — player valuation runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from fantasy.league.settings import load_settings
from fantasy.model.lines import load_projections
from fantasy.model.valuation import ValuationParams, run_valuation

app = typer.Typer(no_args_is_help=True, help="Category valuation of players (G-scores over replacement).")


def _parse_punt(punt: str) -> list[str]:
    return [p.strip() for p in punt.split(",") if p.strip()]


@app.command()
def build(
    projections: Annotated[Path | None, typer.Option("--projections")] = None,
    kappa: Annotated[float, typer.Option("--kappa", help="0=z-scores, 1=G-scores")] = 1.0,
    punt: Annotated[str, typer.Option("--punt", help="Comma-separated categories to zero-weight")] = "",
    save: Annotated[bool, typer.Option("--save/--no-save")] = True,
    top: Annotated[int, typer.Option("--top")] = 40,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Value all players from the projections file; store a model run + player values."""
    s = load_settings()
    proj = load_projections(projections)
    run = run_valuation(proj, s, ValuationParams(kappa=kappa, punt=_parse_punt(punt)))
    if save:
        from fantasy.draft.store import save_valuation

        save_valuation(run, proj.df)
    v = run.values.head(top)
    if as_json:
        rows = [{"player_id": pid, "rank": int(r["rank"]), "name": r["name"], "team": r["team"],
                 "positions": list(r["positions"]), "value": round(float(r["value"]), 3),
                 "adp": None if r["adp"] != r["adp"] else float(r["adp"]),
                 "cat_values": {k: round(float(r[k]), 3) for k in s.categories}} for pid, r in v.iterrows()]
        typer.echo(json.dumps({"run_id": run.run_id, "saved": save, "model_version": run.model_version,
                               "params": run.params, "inputs": run.inputs, "top": rows}, indent=2, default=str))
        return
    typer.echo(f"run {run.run_id} ({'saved' if save else 'not saved'})")
    cols = ["rank", "name", "team", "value", "adp", *s.categories]
    with __import__("pandas").option_context("display.width", 200, "display.max_columns", 20):
        typer.echo(v[cols].round(2).to_string(index=False))
