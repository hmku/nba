"""Yahoo Fantasy public draft analysis (ADP, position eligibility, injury status).

The public page https://basketball.fantasysports.yahoo.com/nba/draftanalysis is rendered client-side
from Yahoo's public fantasy API, which answers without login for the "public" league context:

    https://pub-api-rw.fantasysports.yahoo.com/fantasy/v2/league/<game>.l.public/players;
        start=<i>;count=25;sort=DA_AP;out=draft_analysis?format=json

Rows carry Yahoo's average pick (ADP across Yahoo drafts), eligible positions and, for injured
players, `status` / `injury_note`. Raw JSON pages are cached in data/raw/yahoo_public/ with
provenance; `parse()` turns them into a flat table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from fantasy.data.provenance import RAW_DIR, write_provenance

SOURCE = "yahoo_public"
GAME_ID = "478"  # Yahoo NBA game id for 2026-27 (from the draftanalysis page's gameId)
API = "https://pub-api-rw.fantasysports.yahoo.com/fantasy/v2/league/{game}.l.public/players"
PAGE = 25  # Yahoo caps player collections at 25 per request
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
POSITIONS = ("PG", "SG", "SF", "PF", "C")


def page_url(start: int, game: str = GAME_ID) -> str:
    return (
        API.format(game=game)
        + f";start={start};count={PAGE};sort=DA_AP;out=draft_analysis?format=json"
    )


def fetch(n: int = 300, game: str = GAME_ID, timeout: float = 30.0) -> dict[str, Any]:
    """Fetch the top-n players by Yahoo ADP into one raw JSON file (list of page payloads)."""
    out_dir = RAW_DIR / SOURCE
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with httpx.Client(timeout=timeout, headers={"User-Agent": UA}) as client:
        for start in range(0, n, PAGE):
            r = client.get(page_url(start, game))
            r.raise_for_status()
            pages.append({"start": start, "url": page_url(start, game), "data": r.json()})
    path = out_dir / f"draft_analysis_{game}.json"
    path.write_text(json.dumps(pages, ensure_ascii=False, indent=1) + "\n")
    rows = len(parse(path))
    return write_provenance(
        path,
        url=page_url(0, game).replace("start=0", f"start={{0..{n - PAGE} step {PAGE}}}"),
        method="httpx GET (no auth), Yahoo public league context",
        rows=rows,
        fetched_at=fetched_at,
        notes="Yahoo public draft analysis sorted by average pick (DA_AP); raw API pages",
    )


def _flatten(items: list) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items:
        if isinstance(it, dict):
            out.update(it)
    return out


def _player_row(entry: list) -> dict[str, Any]:
    info = _flatten(entry[0])
    da = _flatten(entry[1].get("draft_analysis", [])) if len(entry) > 1 else {}
    elig = [p["position"] for p in info.get("eligible_positions", []) if "position" in p]

    def num(k: str) -> float | None:
        v = da.get(k)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "yahoo_id": info.get("player_id"),
        "name": info.get("name", {}).get("full", ""),
        "team": info.get("editorial_team_abbr", ""),
        "display_position": info.get("display_position", ""),
        "positions": "|".join(p for p in POSITIONS if p in elig),
        "status": info.get("status", ""),
        "status_full": info.get("status_full", ""),
        "injury_note": info.get("injury_note", ""),
        "adp": num("average_pick"),
        "preseason_adp": num("preseason_average_pick"),
        "percent_drafted": num("percent_drafted"),
    }


def parse(path: Path) -> pd.DataFrame:
    pages = json.loads(Path(path).read_text())
    rows = []
    for pg in pages:
        players = pg["data"]["fantasy_content"]["league"][1]["players"]
        for k, v in players.items():
            if k == "count":
                continue
            rows.append(_player_row(v["player"]))
    df = pd.DataFrame(rows)
    return df.drop_duplicates("yahoo_id").reset_index(drop=True)


# ------------------------------------------------------------------ manual-file writers

# Yahoo's average_pick is conditional on the player being drafted. For the availability model we
# want the unconditional mean draft slot, so a player drafted in only a fraction p of drafts gets
#   adp_eff = p * adp + (1 - p) * UNDRAFTED_SLOT
# where UNDRAFTED_SLOT is one round past the end of a 12-team, 13-round draft. Players Yahoo lists
# but nobody drafts get adp_eff = UNDRAFTED_SLOT.
UNDRAFTED_SLOT = 12 * 13 + 12


def effective_adp(adp: float | None, pct: float | None) -> float:
    if adp is None or pd.isna(adp) or pct is None or pd.isna(pct):
        return float(UNDRAFTED_SLOT)
    p = min(max(float(pct), 0.0), 1.0)
    return round(p * float(adp) + (1 - p) * UNDRAFTED_SLOT, 1)


ADP_HEADER = """\
# Yahoo ADP for 2026-27 from Yahoo's public draft analysis API (no login), fetched {fetched_at}.
# Raw: data/raw/yahoo_public/draft_analysis_{game}.json (+ .provenance.json). Rebuild with
# `fantasy data fetch-yahoo --apply`. `adp` is the EFFECTIVE (unconditional) mean slot used by the
# draft availability model: adp = pct_drafted*yahoo_adp + (1-pct_drafted)*{undrafted} (Yahoo's
# average pick only counts drafts where the player was taken); yahoo_adp is Yahoo's raw value.
# Players Yahoo lists in its top {n} but nobody drafts get adp={undrafted}.
"""

POS_HEADER = """\
# Yahoo position eligibility (pipe-separated), from Yahoo's public draft analysis API
# (eligible_positions) fetched {fetched_at} for the top {n} players by Yahoo ADP order.
# Rows with source=manual are older hand-set guesses for players outside that set.
"""


def update_manual(
    df: pd.DataFrame,
    fetched_at: str,
    *,
    game: str = GAME_ID,
    n_positions: int = 250,
    manual_dir: Path | None = None,
) -> dict[str, Any]:
    """Rewrite data/manual/adp_yahoo_2026_27.csv and positions_overrides.csv from parsed rows."""
    from fantasy.data.ids import canonical_id
    from fantasy.data.provenance import MANUAL_DIR

    mdir = manual_dir or MANUAL_DIR
    as_of = fetched_at[:10]
    df = df.copy()
    df["player_id"] = df["name"].map(canonical_id)

    adp = pd.DataFrame(
        {
            "name": df["name"],
            "adp": [
                effective_adp(a, p) for a, p in zip(df["adp"], df["percent_drafted"], strict=True)
            ],
            "source": "yahoo public draftanalysis API (game " + game + ")",
            "as_of": as_of,
            "yahoo_adp": df["adp"],
            "pct_drafted": df["percent_drafted"],
        }
    )
    path = mdir / "adp_yahoo_2026_27.csv"
    path.write_text(
        ADP_HEADER.format(fetched_at=fetched_at, game=game, undrafted=UNDRAFTED_SLOT, n=len(df))
        + adp.to_csv(index=False)
    )

    pos_path = mdir / "positions_overrides.csv"
    old = pd.read_csv(pos_path, comment="#", dtype=str).fillna("") if pos_path.exists() else None
    top = df.head(n_positions)
    top = top[top["positions"] != ""]
    pos = pd.DataFrame(
        {
            "name": top["name"],
            "positions": top["positions"],
            "note": "yahoo display " + top["display_position"],
            "source": "yahoo public API " + as_of,
        }
    )
    kept = 0
    if old is not None and not old.empty:
        have = set(top["player_id"])
        old_ids = old["name"].map(canonical_id)
        rest = old[~old_ids.isin(have)].copy()
        rest["source"] = "manual"
        kept = len(rest)
        pos = pd.concat([pos, rest[["name", "positions", "note", "source"]]], ignore_index=True)
    pos_path.write_text(
        POS_HEADER.format(fetched_at=fetched_at, n=n_positions) + pos.to_csv(index=False)
    )
    return {
        "adp_rows": len(adp),
        "adp_drafted": int(df["adp"].notna().sum()),
        "position_rows": len(pos),
        "position_rows_kept_manual": kept,
    }
