"""External (third-party) season projections: ESPN fantasy API and FantasyPros consensus.

Both sources are normalised to one per-game table (`EXTERNAL_COLUMNS`), keyed by our
`player_id` (`canonical_id` of the display name), and cached under `data/raw/<source>/`:

- `espn`: ESPN's fantasy API (`lm-api-reads.fantasy.espn.com`, public, unauthenticated, undocumented).
  The `kona_player_info` view carries, per player, the stat set with `statSourceId = 1` and
  `id = "10<season>"`: ESPN's season projection, as per-game `averageStats` incl. FGM/FGA/FTM/FTA,
  minutes and games. The same id exists for past seasons (e.g. `102026` = the 2025-26 projection),
  which is what the historical backtest uses (see docs/research/projection_quality.md for the
  caveat that ESPN may revise stored projections after the preseason).
- `fantasypros`: FantasyPros "Overall" consensus projections page (HTML table of season totals:
  PTS REB AST BLK STL FG% FT% 3PM GP MIN TO). It has no attempts, so `fga`/`fta` (and the makes)
  are left empty here; `fantasy.model.blend` imputes attempts from our projection when blending.

Fetches are polite (one request per payload, browser UA, a pause between requests) and cached:
an existing raw payload is re-parsed unless `refresh=True`. Parsing is deterministic and offline.
Licensing: personal, non-commercial use only; see docs/research/data_sources.md.
"""

from __future__ import annotations

import html as htmllib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from fantasy.data.ids import canonical_id
from fantasy.data.provenance import RAW_DIR, write_provenance

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
SOURCES = ("espn", "fantasypros")
EXTERNAL_COLUMNS = [
    "player_id", "name", "team", "gp", "mpg", "fgm", "fga", "ftm", "fta", "fg_pct", "ft_pct",
    "fg3m", "pts", "reb", "ast", "stl", "blk", "tov", "source_rank", "source", "season",
]  # fmt: skip

# ESPN proTeamId -> Basketball-Reference team code (0 = free agent).
ESPN_TEAMS = {
    0: "FA", 1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
    9: "GSW", 10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN",
    17: "BRK", 18: "NYK", 19: "ORL", 20: "PHI", 21: "PHO", 22: "POR", 23: "SAC", 24: "SAS",
    25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR", 29: "MEM", 30: "CHO",
}  # fmt: skip
# ESPN stat ids (averageStats keys) -> our columns.
ESPN_STATS = {
    "0": "pts", "1": "blk", "2": "stl", "3": "ast", "6": "reb", "11": "tov", "13": "fgm",
    "14": "fga", "15": "ftm", "16": "fta", "17": "fg3m", "40": "mpg", "42": "gp",
}  # fmt: skip
ESPN_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"
    "/segments/0/leaguedefaults/1?view=kona_player_info"
)
FP_URL = "https://www.fantasypros.com/nba/projections/overall.php"
# FantasyPros team codes that differ from bbref's.
FP_TEAMS = {"BKN": "BRK", "CHA": "CHO", "PHX": "PHO", "GS": "GSW", "NO": "NOP", "NY": "NYK",
            "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}  # fmt: skip


def season_label(season: int) -> str:
    """bbref/ESPN season year (end year) -> file label, e.g. 2027 -> '2026_27'."""
    return f"{season - 1}_{str(season)[-2:]}"


def raw_path(source: str, season: int) -> Path:
    ext = "json" if source == "espn" else "html"
    return RAW_DIR / source / f"projections_{season_label(season)}.{ext}"


def csv_path(source: str, season: int) -> Path:
    return RAW_DIR / source / f"projections_{season_label(season)}.csv"


# ---------------------------------------------------------------- ESPN


def espn_filter(limit: int) -> str:
    return json.dumps(
        {
            "players": {
                "limit": limit,
                "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
                "filterStatsForSplitTypeIds": {"value": [0]},
                "filterStatsForSourceIds": {"value": [1]},
            }
        },
        separators=(",", ":"),
    )


def parse_espn(payload: dict[str, Any], season: int) -> pd.DataFrame:
    """ESPN kona_player_info payload -> per-game projection table (players with a projection)."""
    rows = []
    want = f"10{season}"
    for i, p in enumerate(payload.get("players", [])):
        pl = p.get("player", {})
        st = next(
            (s for s in pl.get("stats", []) or [] if s.get("id") == want and s.get("averageStats")),
            None,
        )
        if st is None:
            continue
        avg = st["averageStats"]
        row: dict[str, Any] = {c: avg.get(k) for k, c in ESPN_STATS.items()}
        if row.get("gp") is None or row.get("pts") is None:
            continue
        name = str(pl.get("fullName", "")).strip()
        row.update(
            player_id=canonical_id(name),
            name=name,
            team=ESPN_TEAMS.get(int(pl.get("proTeamId") or 0), "FA"),
            source_rank=i + 1,
            source="espn",
            season=season,
        )
        rows.append(row)
    return _finish(pd.DataFrame(rows))


# ---------------------------------------------------------------- FantasyPros

_FP_ROW = re.compile(r"<tr class=\"mpb-player-\d+\">(.*?)</tr>", re.S)
_FP_NAME = re.compile(r'fp-player-name="([^"]+)"')
_FP_TEAM = re.compile(r"<small>\(([A-Z]+)\s*-\s*([^)]*)\)</small>")
_FP_CELL = re.compile(r'<td class="center">([^<]*)</td>')
FP_COLS = ["pts", "reb", "ast", "blk", "stl", "fg_pct", "ft_pct", "fg3m", "gp", "min", "tov"]


def _num(s: str) -> float | None:
    s = s.strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    return float(s)


def parse_fantasypros(text: str, season: int) -> pd.DataFrame:
    """FantasyPros overall projections HTML (season totals) -> per-game table (no attempts)."""
    rows = []
    for i, m in enumerate(_FP_ROW.finditer(text)):
        body = m.group(1)
        nm = _FP_NAME.search(body)
        cells = [_num(c) for c in _FP_CELL.findall(body)]
        if nm is None or len(cells) != len(FP_COLS):
            continue
        vals = dict(zip(FP_COLS, cells, strict=True))
        gp = vals["gp"] or 0.0
        if gp <= 0:
            continue
        tm = _FP_TEAM.search(body)
        team = tm.group(1) if tm else "FA"
        name = htmllib.unescape(nm.group(1)).strip()
        row: dict[str, Any] = {
            c: (vals[c] / gp if vals[c] is not None else None)
            for c in ["pts", "reb", "ast", "blk", "stl", "fg3m", "tov"]
        }
        row.update(
            player_id=canonical_id(name),
            name=name,
            team=FP_TEAMS.get(team, team),
            gp=gp,
            mpg=(vals["min"] or 0.0) / gp,
            fg_pct=vals["fg_pct"],
            ft_pct=vals["ft_pct"],
            source_rank=i + 1,
            source="fantasypros",
            season=season,
        )
        rows.append(row)
    return _finish(pd.DataFrame(rows))


# ---------------------------------------------------------------- common


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    for c in EXTERNAL_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[EXTERNAL_COLUMNS].copy()
    num = [c for c in EXTERNAL_COLUMNS if c not in ("player_id", "name", "team", "source")]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # percentages from makes/attempts when available (ESPN); FantasyPros gives them directly
    has_fg = df["fga"].fillna(0) > 0
    df.loc[has_fg, "fg_pct"] = df.loc[has_fg, "fgm"] / df.loc[has_fg, "fga"]
    has_ft = df["fta"].fillna(0) > 0
    df.loc[has_ft, "ft_pct"] = df.loc[has_ft, "ftm"] / df.loc[has_ft, "fta"]
    # one row per player id (keep the better-ranked duplicate)
    df = df.sort_values("source_rank").drop_duplicates("player_id").reset_index(drop=True)
    return df


def parse(source: str, path: Path, season: int) -> pd.DataFrame:
    if source == "espn":
        return parse_espn(json.loads(path.read_text()), season)
    if source == "fantasypros":
        return parse_fantasypros(path.read_text(), season)
    raise ValueError(f"unknown external source {source!r}; choose from {SOURCES}")


def _get_with_retry(
    url: str, headers: dict[str, str], timeout: float, pause: float, attempts: int = 3
) -> httpx.Response:
    last: Exception | None = None
    for k in range(attempts):
        try:
            with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
                r = client.get(url)
                r.raise_for_status()
                return r
        except (httpx.TransportError, httpx.HTTPStatusError) as e:  # transient: back off, retry
            last = e
            time.sleep(pause * (2**k))
    assert last is not None
    raise last


def fetch(
    source: str,
    season: int = 2027,
    n: int = 450,
    refresh: bool = False,
    timeout: float = 90.0,
    pause: float = 2.0,
) -> dict[str, Any]:
    """Download (unless cached) and parse one source/season; writes raw + normalised CSV + provenance."""
    if source not in SOURCES:
        raise ValueError(f"unknown external source {source!r}; choose from {SOURCES}")
    raw = raw_path(source, season)
    raw.parent.mkdir(parents=True, exist_ok=True)
    cached = raw.exists() and not refresh
    if source == "espn":
        url = ESPN_URL.format(season=season)
    elif season == 2027:
        url = FP_URL
    else:
        raise ValueError("FantasyPros only serves the current season's projections")
    if not cached:
        time.sleep(pause)  # politeness: never hammer a source, even on a one-off refresh
        headers = {"User-Agent": UA}
        if source == "espn":
            headers["X-Fantasy-Filter"] = espn_filter(n)
        r = _get_with_retry(url, headers, timeout, pause)
        raw.write_bytes(r.content)
        write_provenance(
            raw,
            url=url,
            method="httpx GET, browser UA"
            + (f", X-Fantasy-Filter {espn_filter(n)}" if source == "espn" else ""),
            rows=None,
            notes="raw payload (not committed: third-party ToS); parsed by fantasy.data.external",
        )
    df = parse(source, raw, season)
    out = csv_path(source, season)
    df.to_csv(out, index=False, lineterminator="\n", float_format="%.4f")
    prov = write_provenance(
        out,
        url=url,
        method=f"fantasy.data.external.parse({source!r}) of {raw.name}",
        rows=len(df),
        notes="per-game projections normalised to EXTERNAL_COLUMNS; player_id = canonical_id(name)",
        extra={"cached": cached, "raw": raw.name},
    )
    return prov


def load(source: str, season: int = 2027) -> pd.DataFrame:
    """Normalised external projections (empty frame if never fetched)."""
    p = csv_path(source, season)
    if not p.exists():
        return pd.DataFrame(columns=EXTERNAL_COLUMNS)
    return pd.read_csv(p)
