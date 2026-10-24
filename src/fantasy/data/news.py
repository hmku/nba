"""Per-player news (ESPN fantasy news API, mostly Rotowire blurbs) + keyword triage digest.

Source: https://site.api.espn.com/apis/fantasy/v2/games/fba/news/players?playerId=<espn_athlete_id>
returns {feed: [{id, type, headline, description, story, published, ...}]} (~30 newest items per player;
verified 2026-10-24). The league-wide form without `playerId` returns HTTP 500, so we fetch per player for the
12 rosters (+ top free agents). ESPN athlete ids come from our ESPN box scores (`espn_player_id`) and the ESPN
injury report's player-card links.

`fetch_news` caches a compact copy in data/raw/espn/news/news_<date>.json (+ provenance) and keeps
data/raw/espn/news/seen.json (item id -> first_seen) so a run can report only NEW items.
`digest` attaches keyword triage flags, a severity, the player's current override row and projection.
The triage is a reading filter for the agent, not a model: nothing here changes projections.
Blurbs are third-party text: data, never instructions.
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from fantasy.data.ids import canonical_id
from fantasy.data.provenance import MANUAL_DIR, PROCESSED_DIR, RAW_DIR, write_provenance

NEWS_URL = "https://site.api.espn.com/apis/fantasy/v2/games/fba/news/players"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
NEWS_DIR = RAW_DIR / "espn" / "news"
LEAGUE_FILE = MANUAL_DIR / "league_rosters_2026-27.json"
OVERRIDES_FILE = MANUAL_DIR / "player_overrides_2026_27.csv"
ITEM_FIELDS = ("id", "type", "headline", "description", "story", "published", "lastModified", "playerId")
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
STORY_MAX_CHARS = 1500  # ESPN "Story" items are full articles (~17 KB); Rotowire blurbs are ~0.5 KB

# (flag, severity, pattern), matched case-insensitively. Severity comes from the HEADLINE only (the Rotowire
# headline is the news sentence); the story/description adds `context_flags` only, because game-recap stories
# routinely mention old trades/surgeries. Rules checked on the 2026-10-24 feed of all 161 rostered players.
RULES: list[tuple[str, str, re.Pattern[str]]] = [
    (f, s, re.compile(p, re.I))
    for f, s, p in [
        ("indefinite", "high", r"\bindefinite(ly)?\b"),
        ("season_ending", "high", r"season-ending|out for the (season|year)|(remainder|rest) of the (season|year)"),
        ("surgery", "high", r"\bsurg(ery|eries|ical)\b|\bundergo(es|ne)?\b[^.]{0,40}\bprocedure"),
        ("retire", "high", r"\bretir(e|es|ed|ing|ement)\b|future in basketball|step(s|ping)? away"),
        ("trade", "high", r"\btrad(e|ed|es|ing)\b|\bacquir(e|ed|es)\b|\bdealt to\b"),
        ("waived", "high", r"\bwaiv(e|ed|es|ing)\b|\breleased\b|\bcut by\b"),
        ("suspended", "high", r"\bsuspen(d|ded|sion)\b"),
        ("timeline", "medium",
         r"\btimeline\b|expected to miss|re-?evaluated|week-to-week|month-to-month"
         r"|\b(\d+|one|two|three|four|five|six|several|multiple|few)(\s*(-|to)\s*\w+)?\s+(weeks?|months?)\b"),
        ("return", "medium",
         r"\breturn(s|ed|ing)? to (action|practice|the (lineup|court|floor)|play)\b|\bcleared\b|\bactivated\b"
         r"|make (his|her) (season |team )?debut"),
        ("minutes_limit", "medium",
         r"minutes? (restriction|limit|cap)|limited minutes|on a minutes|restricted minutes"),
        ("role", "medium",
         r"starting role|bench role|sixth man|\bdemot|\bpromot|(move[sd]?|shift(s|ed)?|relegated) to the bench"
         r"|\b(re)?(join|enter|crack)(s|ed)? the starting|start in place of|replac(e|es|ed|ing) \S+( \S+)? (in|as) the "
         r"starting|(start|starting|starter) (going|moving) forward|expected to start (the season|regularly)"),
        ("injury_exit", "medium",
         r"\bexited\b|\b(won't|will not|didn't|did not) (return|check back in)\b|\bleft \w+day's game\b"),
        ("g_league", "medium", r"\bg[ -]league\b|two-way|\bassigned to\b|\brecalled\b"),
        ("game_status", "low",
         r"\bprobable\b|\bquestionable\b|\bdoubtful\b|game-time decision|day-to-day|\bavailable\b|\bactive\b"
         r"|\b(won't|will not|didn't|did not|to not|not) (play|suit up)\b|\bsit(s)? out\b|\bruled out\b"
         r"|\b(unlikely|expected|likely|set) to play\b|\bwill play\b|\bout for (\w+day|tonight)|\bresting\b"
         r"|\brest day\b|load management"),
        ("lineup_game", "low",
         r"starting lineup for|\b(will|won't|will not|isn't|is not|not) start(ing)? (\w+day|tonight)\b"),
    ]
]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- ids + scope


def espn_id_map(
    boxscore_paths: Iterable[Path] | None = None, injuries_path: Path | None = None
) -> dict[str, str]:
    """player_id -> ESPN athlete id. Later box-score files win; the injury report only fills gaps."""
    if boxscore_paths is None:
        bdir = RAW_DIR / "espn_boxscores"
        boxscore_paths = [bdir / "player_games_2026.csv.gz", bdir / "player_games_2027.csv.gz"]
    if injuries_path is None:
        injuries_path = RAW_DIR / "espn" / "injuries.json"
    out: dict[str, str] = {}
    for p in boxscore_paths:
        if not Path(p).exists():
            continue
        df = pd.read_csv(p, usecols=["espn_player_id", "player_id"], dtype=str).dropna()
        df = df[(df["espn_player_id"] != "") & (df["player_id"] != "")].drop_duplicates("player_id", keep="last")
        out.update(zip(df["player_id"], df["espn_player_id"], strict=True))
    if Path(injuries_path).exists():
        d = json.loads(Path(injuries_path).read_text())
        for team in d.get("injuries", []):
            for e in team.get("injuries", []):
                ath = e.get("athlete", {}) or {}
                pid = canonical_id(ath.get("displayName", ""))
                m = None
                for link in ath.get("links", []) or []:
                    m = re.search(r"/id/(\d+)", link.get("href", ""))
                    if m:
                        break
                if pid and m and pid not in out:
                    out[pid] = m.group(1)
    return out


def load_league(league_file: Path) -> tuple[dict[str, dict[str, Any]], str]:
    """player_id -> {slot, team_name}, and my team's slot (league file `my_team`, default "1")."""
    d = json.loads(Path(league_file).read_text())
    mine = str(d.get("my_team", "1"))
    owners: dict[str, dict[str, Any]] = {}
    for slot, t in (d.get("teams") or {}).items():
        for pid in t.get("players", []):
            owners[pid] = {"slot": str(slot), "team_name": t.get("name", "")}
    return owners, mine


def newest_free_agents(raw_dir: Path | None = None) -> Path | None:
    files = sorted((raw_dir or RAW_DIR / "yahoo").glob("free_agents_*.json"))
    return files[-1] if files else None


def load_free_agents(fa_file: Path | None, top: int) -> dict[str, int]:
    """player_id -> Yahoo list_rank for the top `top` free agents (by list_rank)."""
    if not fa_file or not Path(fa_file).exists():
        return {}
    d = json.loads(Path(fa_file).read_text())
    rows = [p for p in d.get("players", []) if p.get("player_id") or p.get("name")]
    rows.sort(key=lambda p: p.get("list_rank") or 10**6)
    out: dict[str, int] = {}
    for p in rows[:top]:
        out[p.get("player_id") or canonical_id(p["name"])] = p.get("list_rank")
    return out


# --------------------------------------------------------------------------- fetch


def parse_feed(content: bytes) -> list[dict[str, Any]]:
    """Compact feed items from a response body (gzip-tolerant: httpx decodes Content-Encoding, but a body
    saved/served without the header still starts with the gzip magic)."""
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    d = json.loads(content.decode("utf-8"))
    items = []
    for it in d.get("feed", []) or []:
        if it.get("id") is None:
            continue
        row = {k: it.get(k) for k in ITEM_FIELDS if it.get(k) is not None}
        if len(row.get("story") or "") > STORY_MAX_CHARS:
            row["story"] = row["story"][:STORY_MAX_CHARS] + " [truncated]"
        items.append(row)
    return items


Getter = Callable[[str], bytes]


def _httpx_getter(timeout: float) -> tuple[Getter, httpx.Client]:
    client = httpx.Client(timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True)

    def get(espn_id: str) -> bytes:
        r = client.get(NEWS_URL, params={"playerId": espn_id})
        r.raise_for_status()
        return r.content

    return get, client


def _fetch_one(get: Getter, espn_id: str, retries: int = 1) -> list[dict[str, Any]]:
    err: Exception | None = None
    for _ in range(retries + 1):
        try:
            return parse_feed(get(espn_id))
        except (httpx.HTTPError, ValueError) as e:  # network / non-2xx / bad JSON
            err = e
    raise RuntimeError(f"{type(err).__name__}: {err}")


def fetch_news(
    scope: str = "rostered",
    league_file: Path = LEAGUE_FILE,
    fa_file: Path | None = None,
    fa_top: int = 50,
    out_dir: Path = NEWS_DIR,
    id_map: dict[str, str] | None = None,
    get: Getter | None = None,
    workers: int = 4,
    timeout: float = 20.0,
    max_age_days: int = 7,
    keep_days: int = 45,
    today: date | None = None,
) -> dict[str, Any]:
    """Fetch news for the scope's players, cache it, update seen.json; return a summary.

    An item is NEW when its id is not in seen.json AND it was published within `max_age_days` (so the first run,
    or a player newly in scope, doesn't flood the digest with months-old history; old items are still marked seen).
    The cached file keeps only items published within `keep_days` (repo size; seen.json keeps every id).
    """
    if scope not in ("rostered", "rostered+fa"):
        raise ValueError(f"scope must be rostered|rostered+fa, got {scope!r}")
    owners, _ = load_league(league_file)
    players = list(owners)
    fa: dict[str, int] = {}
    if scope == "rostered+fa":
        fa = load_free_agents(fa_file if fa_file is not None else newest_free_agents(), fa_top)
        players += [p for p in fa if p not in owners]
    ids = espn_id_map() if id_map is None else id_map
    mapped = {p: ids[p] for p in players if p in ids}
    unmapped = sorted(p for p in players if p not in ids)

    client = None
    if get is None:
        get, client = _httpx_getter(timeout)
    fetched_at = _now()
    results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as ex:
            futs = {p: ex.submit(_fetch_one, get, eid) for p, eid in mapped.items()}
            for p, f in futs.items():
                try:
                    results[p] = f.result()
                except RuntimeError as e:
                    errors[p] = str(e)
    finally:
        if client is not None:
            client.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    seen_path = out_dir / "seen.json"
    seen: dict[str, Any] = json.loads(seen_path.read_text()) if seen_path.exists() else {}
    seen_items: dict[str, dict[str, Any]] = seen.get("items", {})
    today = today or datetime.now(UTC).date()
    cutoff = (today - timedelta(days=max_age_days)).isoformat()
    keep_cutoff = (today - timedelta(days=max(keep_days, max_age_days))).isoformat()
    new_ids: list[str] = []
    for p, items in results.items():
        for it in items:
            iid = str(it["id"])
            if iid in seen_items:
                continue
            seen_items[iid] = {"first_seen": fetched_at, "player_id": p, "published": it.get("published", "")}
            if (it.get("published") or "")[:10] >= cutoff:
                new_ids.append(iid)

    n_fetched = sum(len(v) for v in results.values())
    results = {p: [i for i in v if (i.get("published") or "")[:10] >= keep_cutoff] for p, v in results.items()}
    day = fetched_at[:10]
    path = out_dir / f"news_{day}.json"
    if path.exists():  # a second run the same day keeps the earlier run's new items in today's digest
        prev = json.loads(path.read_text())
        new_ids = list(dict.fromkeys([*map(str, prev.get("new_ids", [])), *new_ids]))
    payload = {
        "fetched_at": fetched_at,
        "scope": scope,
        "max_age_days": max_age_days,
        "new_ids": new_ids,
        "players": {p: {"espn_id": mapped[p], "items": results[p]} for p in sorted(results)},
        "free_agents": fa,
        "unmapped": unmapped,
        "errors": errors,
    }
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    n_items = sum(len(v) for v in results.values())
    prov = write_provenance(
        path,
        url=NEWS_URL + "?playerId=<espn_id>",
        method=f"httpx GET per player ({len(mapped)} players, <=4 concurrent, 1 retry)",
        rows=n_items,
        fetched_at=fetched_at,
        notes=(
            f"ESPN fantasy player news (Rotowire etc.), scope={scope}; compact fields {','.join(ITEM_FIELDS)}; "
            f"items published in the last {max(keep_days, max_age_days)} days; story cut at {STORY_MAX_CHARS} chars"
        ),
    )
    seen_path.write_text(
        json.dumps({"last_run": fetched_at, "last_file": path.name, "items": seen_items}, indent=0) + "\n"
    )
    return {
        "fetched": prov,
        "players_in_scope": len(players),
        "players_fetched": len(results),
        "items_fetched": n_fetched,
        "items_cached": n_items,
        "new_items": len(new_ids),  # includes earlier same-day runs (today's file accumulates)
        "unmapped": unmapped,
        "errors": errors,
    }


# --------------------------------------------------------------------------- triage + digest


def triage(headline: str, context: str = "") -> tuple[list[str], list[str], str]:
    """(headline flags, extra flags found only in the story/description, severity of the headline flags;
    info = no headline flag)."""
    flags = [f for f, _, rx in RULES if rx.search(headline or "")]
    ctx = [f for f, _, rx in RULES if f not in flags and rx.search(context or "")]
    sev = min((s for f, s, _ in RULES if f in flags), key=SEVERITY_RANK.__getitem__, default="info")
    return flags, ctx, sev


_ISO = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def override_date(row: dict[str, str]) -> str | None:
    """The override's source date: first ISO date in `source`, else `note`, else `injury_note`.
    (First, not max: rows often also cite a later estimated-return date.)"""
    for col in ("source", "note", "injury_note"):
        m = _ISO.search(row.get(col, "") or "")
        if m:
            return m.group(1)
    return None


def load_overrides(path: Path = OVERRIDES_FILE) -> dict[str, dict[str, str]]:
    if not Path(path).exists():
        return {}
    df = pd.read_csv(path, comment="#", skipinitialspace=True, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    return {canonical_id(r["name"]): {k: v for k, v in r.items() if v != ""} for r in df.to_dict("records")}


def load_projection_rows(processed_dir: Path = PROCESSED_DIR) -> tuple[dict[str, dict[str, Any]], str | None]:
    for name in ("projections_2026_27.inseason.csv", "projections_2026_27.csv"):
        p = processed_dir / name
        if p.exists():
            df = pd.read_csv(p, dtype={"player_id": str})
            cols = [c for c in ("name", "team", "gp", "gp_sd", "status", "injury_note") if c in df.columns]
            df = df.set_index("player_id")[cols]
            df = df.astype(object).where(df.notna(), None)
            return df.to_dict("index"), name
    return {}, None


def newest_news_file(out_dir: Path = NEWS_DIR) -> Path | None:
    files = sorted(out_dir.glob("news_*.json"))
    files = [f for f in files if not f.name.endswith(".provenance.json")]
    return files[-1] if files else None


def digest(
    news_file: Path | None = None,
    since: str | None = None,
    new_only: bool = True,
    min_severity: str = "low",
    league_file: Path = LEAGUE_FILE,
    fa_file: Path | None = None,
    overrides_file: Path = OVERRIDES_FILE,
    processed_dir: Path = PROCESSED_DIR,
) -> dict[str, Any]:
    """Per-player triage of new (or since-`since`) items, sorted my team -> other rostered -> FA -> other,
    then by max severity, then newest item. `override_stale`: a high/medium item published after the override's
    source date (None when there is no override or its date is unknown); `needs_review`: a high/medium item and
    the override is missing, stale or undated (high anywhere; medium only on my team / FA)."""
    news_file = news_file or newest_news_file()
    if news_file is None:
        raise FileNotFoundError("no news file: run `fantasy data fetch-news` first")
    d = json.loads(Path(news_file).read_text())
    owners, mine = load_league(league_file)
    fa = d.get("free_agents") or load_free_agents(fa_file or newest_free_agents(), 10**6)
    overrides = load_overrides(overrides_file)
    proj, proj_file = load_projection_rows(processed_dir)
    new_ids = set(map(str, d.get("new_ids", [])))
    max_rank = SEVERITY_RANK[min_severity]

    players = []
    counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
    for pid, pdata in d.get("players", {}).items():
        items = []
        for it in pdata.get("items", []):
            pub = it.get("published") or ""
            if since is not None:
                if pub < since:
                    continue
            elif new_only and str(it["id"]) not in new_ids:
                continue
            head = str(it.get("headline") or "")
            ctx = " ".join(str(it.get(k) or "") for k in ("description", "story") if it.get(k) != head)
            flags, ctx_flags, sev = triage(head, ctx)
            counts[sev] += 1
            if SEVERITY_RANK[sev] > max_rank:
                continue
            items.append(
                {
                    "id": it["id"],
                    "published": pub,
                    "source": it.get("type", ""),
                    "headline": it.get("headline", ""),
                    "story": (it.get("story") or it.get("description") or "")[:600],
                    "flags": flags,
                    "context_flags": ctx_flags,
                    "severity": sev,
                }
            )
        if not items:
            continue
        items.sort(key=lambda i: i["published"], reverse=True)
        items.sort(key=lambda i: SEVERITY_RANK[i["severity"]])
        own = owners.get(pid)
        if own and own["slot"] == mine:
            group = "mine"
        elif own:
            group = "rostered"
        elif pid in fa:
            group = "fa"
        else:
            group = "other"
        ov = overrides.get(pid)
        ov_date = override_date(ov) if ov else None
        hm = [i for i in items if i["severity"] in ("high", "medium")]
        # the daily-routine reading policy: every high item; medium only for my roster and free agents
        to_read = [i for i in hm if i["severity"] == "high" or group in ("mine", "fa")]
        latest_hm = max((i["published"][:10] for i in hm), default=None)
        stale = None if not (ov and ov_date and latest_hm) else latest_hm > ov_date
        pr = proj.get(pid) or {}
        players.append(
            {
                "player_id": pid,
                "name": pr.get("name") or pid,
                "group": group,
                "fantasy_team": (f"{own['slot']}: {own['team_name']}" if own else ("FA" if group == "fa" else "")),
                "fa_list_rank": fa.get(pid) if group == "fa" else None,
                "max_severity": items[0]["severity"],
                "override": ov,
                "override_date": ov_date,
                "override_stale": stale,
                "needs_review": bool(to_read) and (ov is None or ov_date is None or bool(stale)),
                "projection": {k: pr.get(k) for k in ("gp", "gp_sd", "status", "injury_note")} if pr else None,
                "items": items,
            }
        )
    group_rank = {"mine": 0, "rostered": 1, "fa": 2, "other": 3}
    players.sort(key=lambda p: max(i["published"] for i in p["items"]), reverse=True)
    players.sort(key=lambda p: (group_rank[p["group"]], SEVERITY_RANK[p["max_severity"]]))
    return {
        "news_file": Path(news_file).name,
        "fetched_at": d.get("fetched_at"),
        "filter": {"since": since} if since is not None else {"new_only": new_only},
        "min_severity": min_severity,
        "projections_file": proj_file,
        "item_counts": counts,
        "players_listed": len(players),
        "needs_review": [p["player_id"] for p in players if p["needs_review"]],
        "unmapped": d.get("unmapped", []),
        "fetch_errors": d.get("errors", {}),
        "players": players,
    }
