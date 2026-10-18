"""Current NBA injury report: ESPN injuries API (primary) + CBS Sports injuries page (cross-check).

ESPN's public site API returns status, comments, a fantasy status (e.g. OFS = out for season) and an
estimated return date:  https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries
CBS (https://www.cbssports.com/nba/injuries/) is an HTML table (player, position, updated, injury,
status) parsed with a regex. Raw responses are cached in data/raw/<source>/ with provenance.

Expected games missed is derived from ESPN's return date by spreading 82 games uniformly over the
regular season (SEASON_START..SEASON_END); ESPN return dates are estimates, often conservative.
"""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from fantasy.data.ids import canonical_id
from fantasy.data.provenance import RAW_DIR, write_provenance

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
CBS_URL = "https://www.cbssports.com/nba/injuries/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
SEASON_START = date(2026, 10, 20)
SEASON_END = date(2027, 4, 11)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(timeout: float = 30.0) -> list[dict[str, Any]]:
    provs = []
    with httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True) as c:
        for source, url, name, parser in [
            ("espn", ESPN_URL, "injuries.json", parse_espn),
            ("cbs", CBS_URL, "injuries.html", parse_cbs),
        ]:
            fetched_at = _now()
            r = c.get(url)
            r.raise_for_status()
            out_dir = RAW_DIR / source
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / name
            path.write_bytes(r.content)
            provs.append(
                write_provenance(
                    path,
                    url=url,
                    method="httpx GET",
                    rows=len(parser(path)),
                    fetched_at=fetched_at,
                    notes=f"{source} NBA injury report",
                )
            )
    return provs


def games_missed_from_return(ret: str | None, as_of: date | None = None) -> int | None:
    """Team games expected to be missed before `ret` (ISO date), assuming 82 games spread evenly."""
    if not ret:
        return None
    d = date.fromisoformat(ret[:10])
    start = max(SEASON_START, as_of or SEASON_START)
    season_days = (SEASON_END - SEASON_START).days
    days = (min(d, SEASON_END) - start).days
    return max(0, min(82, round(82 * days / season_days)))


def parse_espn(path: Path) -> pd.DataFrame:
    d = json.loads(Path(path).read_text())
    rows = []
    for team in d.get("injuries", []):
        for e in team.get("injuries", []):
            ath = e.get("athlete", {})
            det = e.get("details", {}) or {}
            rows.append(
                {
                    "name": ath.get("displayName", ""),
                    "team": (ath.get("team") or {}).get("abbreviation", "")
                    or team.get("displayName", ""),
                    "status": e.get("status", ""),
                    "fantasy_status": (det.get("fantasyStatus") or {}).get("abbreviation", ""),
                    "injury": " ".join(
                        x for x in [det.get("side", ""), det.get("type", "")] if x
                    ).strip(),
                    "return_date": det.get("returnDate", ""),
                    "updated": e.get("date", "")[:10],
                    "comment": e.get("shortComment", ""),
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["player_id"] = df["name"].map(canonical_id)
    return df


_ROW = re.compile(r'<tr class="TableBase-bodyTr">(.*?)</tr>', re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_LONG = re.compile(r'CellPlayerName--long">.*?<a [^>]*>(.*?)</a>', re.S)


def _text(s: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def parse_cbs(path: Path) -> pd.DataFrame:
    s = Path(path).read_text(encoding="utf-8", errors="replace")
    rows = []
    for m in _ROW.finditer(s):
        tds = _TD.findall(m.group(1))
        if len(tds) < 5:
            continue
        nm = _LONG.search(tds[0])
        rows.append(
            {
                "name": _text(nm.group(1)) if nm else _text(tds[0]),
                "position": _text(tds[1]),
                "updated": _text(tds[2]),
                "injury": _text(tds[3]),
                "status": _text(tds[4]),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["player_id"] = df["name"].map(canonical_id)
    return df


STATUS_MAP = {"Out": "out", "Day-To-Day": "gtd", "Questionable": "gtd", "Doubtful": "gtd"}


def model_status(espn_status: str, fantasy_status: str) -> str:
    if fantasy_status == "OFS":
        return "out_for_season"
    return STATUS_MAP.get(espn_status, "unknown")
