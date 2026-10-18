"""`fantasy data ...` commands (ingestion + projection build)."""

from __future__ import annotations

import json

import typer

from fantasy.cli_common import dumps

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Data ingestion and projections")


def _emit(obj: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(dumps(obj))
    else:
        for k, v in obj.items():
            if isinstance(v, (list, dict)) and len(json.dumps(v, default=str)) > 200:
                typer.echo(f"{k}: ({len(v)} entries)")
            else:
                typer.echo(f"{k}: {v}")


@app.command("build-projections")
def build_projections(
    as_json: bool = typer.Option(False, "--json", help="JSON summary on stdout"),
    blend: str = typer.Option(
        "",
        "--blend",
        help="also write a blend with cached external projections, e.g. 'ours=0.5,espn=0.5' "
        "(sources: ours, espn, fantasypros; 'default' = ours=0.5,espn=0.5)",
    ),
    blend_gp: str = typer.Option("", "--blend-gp", help="blend games too, e.g. 'ours=1' (default: ours only)"),
    blend_output: str = typer.Option("", "--blend-output", help="blend CSV path (default data/processed/projections_2026_27.blend.csv)"),
) -> None:
    """Regenerate data/processed/projections_2026_27.csv from cached raw + manual data (no network).

    With --blend, additionally writes the blended file (the default projections file is unchanged)."""
    from fantasy.model.projections import write

    meta = write()
    summary = {"ok": True} | {
        k: meta[k]
        for k in ("model_version", "season", "output", "rows", "sha256", "sources", "source_counts")
    }
    summary["inputs"] = meta["inputs"]
    if blend:
        from pathlib import Path

        from fantasy.model.blend import BLEND_OUTPUT, DEFAULT_WEIGHTS, BlendSpec, write_blend

        spec = BlendSpec.parse(DEFAULT_WEIGHTS if blend == "default" else blend, blend_gp or None)
        bmeta = write_blend(spec, out_path=Path(blend_output) if blend_output else BLEND_OUTPUT)
        summary["blend"] = {k: v for k, v in bmeta.items() if k != "ignored_external"}
        summary["blend"]["ignored_external"] = {
            k: len(v) for k, v in bmeta.get("ignored_external", {}).items()
        }
    _emit(summary, as_json)


@app.command("fetch-bbref")
def fetch_bbref(as_json: bool = typer.Option(False, "--json")) -> None:
    """Refresh Basketball-Reference tables from the sumitrodatta GitHub mirror into data/raw/."""
    from fantasy.data.bbref_github import fetch

    _emit({"fetched": fetch()}, as_json)


@app.command("fetch-yahoo")
def fetch_yahoo(
    n: int = typer.Option(300, help="players to fetch, in Yahoo ADP order"),
    apply: bool = typer.Option(False, "--apply", help="rewrite ADP + positions manual files"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch Yahoo's public draft analysis (ADP, eligibility, injury status) into data/raw/."""
    from fantasy.data.yahoo_public import fetch, parse, update_manual

    prov = fetch(n=n)
    out: dict = {"fetched": prov}
    if apply:
        from fantasy.data.provenance import REPO_ROOT

        out["applied"] = update_manual(parse(REPO_ROOT / prov["path"]), prov["fetched_at"])
    _emit(out, as_json)


@app.command("fetch-injuries")
def fetch_injuries(as_json: bool = typer.Option(False, "--json")) -> None:
    """Cache the ESPN (API) and CBS injury reports into data/raw/ (review, then edit overrides)."""
    from fantasy.data.injuries import fetch

    _emit({"fetched": fetch()}, as_json)


@app.command("fetch-external")
def fetch_external(
    source: str = typer.Option(..., help="espn | fantasypros | all"),
    season: int = typer.Option(2027, help="season end year (2027 = 2026-27); ESPN also has past seasons"),
    n: int = typer.Option(450, help="ESPN: players to request, in ESPN draft-rank order"),
    refresh: bool = typer.Option(False, "--refresh", help="re-download even if the raw payload is cached"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch third-party season projections (cached, parsed to data/raw/<source>/projections_*.csv)."""
    from fantasy.data.external import SOURCES, fetch

    sources = list(SOURCES) if source == "all" else [source]
    _emit({"fetched": [fetch(s, season=season, n=n, refresh=refresh) for s in sources]}, as_json)


@app.command("fetch-nba-api")
def fetch_nba_api(
    seasons: str = typer.Option("2025-26,2024-25,2023-24", help="comma-separated NBA seasons"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Fetch season totals from stats.nba.com via nba_api (run on a machine where it is reachable;
    requires `pip install -e .[nba]`)."""
    from fantasy.data.nba_api_ingest import fetch

    _emit({"fetched": fetch([s.strip() for s in seasons.split(",") if s.strip()])}, as_json)


@app.command("top")
def top(
    n: int = typer.Option(30, help="rows to show"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Sanity check: top-N by a simple per-game 9-cat z-score of the processed projections."""
    import pandas as pd

    from fantasy.model.projections import OUTPUT_PATH, zscores

    df = pd.read_csv(OUTPUT_PATH)
    z = zscores(df).head(n)
    cols = [
        "player_id",
        "name",
        "team",
        "positions",
        "gp",
        "mpg",
        "pts",
        "reb",
        "ast",
        "stl",
        "blk",
        "fg3m",
        "tov",
        "adp",
        "z",
    ]
    rows = z[cols].round(2).to_dict(orient="records")
    if as_json:
        typer.echo(dumps({"ok": True, "players": rows}))
    else:
        typer.echo(z[cols].round(2).to_string(index=False))
