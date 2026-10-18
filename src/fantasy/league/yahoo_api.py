"""Small, typed, READ-ONLY Yahoo Fantasy Sports API v2 client (httpx + OAuth2 refresh token).

Scope: league settings, draft results, teams, rosters, players (eligible positions, ownership,
``draft_analysis`` ADP). Write operations (add/drop, waiver claims, lineup PUT, trades) exist in the
Yahoo API but are deliberately NOT implemented here; see ``docs/research/yahoo_api.md``. The client
refuses any non-GET request unless constructed with ``read_only=False`` (which nothing does yet).

Credentials (never logged; ``repr`` is redacted):
  * env vars ``YAHOO_CLIENT_ID`` / ``YAHOO_CLIENT_SECRET`` / ``YAHOO_REFRESH_TOKEN`` (take precedence), or
  * ``~/.config/fantasy/yahoo_oauth.json`` (path overridable via ``YAHOO_OAUTH_FILE``), written by
    ``fantasy settings yahoo-auth`` / ``python -m fantasy.league.yahoo_auth``.

Yahoo's JSON is notoriously irregular (lists of single-key dicts, collections keyed "0".."n-1" plus
"count"). The ``parse_*`` functions are pure and unit-tested against fixtures in
``tests/fixtures/yahoo/``; the client methods are thin wrappers around them.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
DEFAULT_REDIRECT_URI = "https://localhost:8080"  # Yahoo dropped `oob` for new apps (2026)
DEFAULT_TOKEN_PATH = Path("~/.config/fantasy/yahoo_oauth.json")

# Yahoo NBA stat_id -> our internal category code (projection CSV column names).
NBA_STAT_IDS: dict[int, str] = {
    3: "fga",
    4: "fgm",
    5: "fg_pct",
    6: "fta",
    7: "ftm",
    8: "ft_pct",
    9: "fg3a",
    10: "fg3m",
    11: "fg3_pct",
    12: "pts",
    13: "oreb",
    14: "dreb",
    15: "reb",
    16: "ast",
    17: "stl",
    18: "blk",
    19: "tov",
    20: "ast_tov",
    21: "pf",
    27: "dd",
    28: "td",
}


class YahooError(RuntimeError):
    """Base error for Yahoo API problems (messages never contain tokens)."""


class YahooAuthError(YahooError):
    pass


class YahooRateLimitError(YahooError):
    """Yahoo signals throttling with HTTP 999 (sometimes 429)."""


class YahooReadOnlyError(YahooError):
    pass


# ----------------------------------------------------------------------------- credentials


def token_path() -> Path:
    return Path(os.environ.get("YAHOO_OAUTH_FILE", str(DEFAULT_TOKEN_PATH))).expanduser()


@dataclass
class YahooCredentials:
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_token: str | None = field(default=None, repr=False)
    expires_at: float = 0.0  # unix seconds
    redirect_uri: str = DEFAULT_REDIRECT_URI

    def access_token_valid(self, now: float | None = None, skew: float = 60.0) -> bool:
        return bool(self.access_token) and (now or time.time()) < self.expires_at - skew

    def to_json(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "redirect_uri": self.redirect_uri,
        }


def load_credentials(path: Path | None = None) -> YahooCredentials:
    """Env vars override the token file field-by-field. Raises YahooAuthError if incomplete."""
    path = path or token_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise YahooAuthError(f"cannot read Yahoo token file {path}: {type(e).__name__}") from e
    env = {
        "client_id": os.environ.get("YAHOO_CLIENT_ID"),
        "client_secret": os.environ.get("YAHOO_CLIENT_SECRET"),
        "refresh_token": os.environ.get("YAHOO_REFRESH_TOKEN"),
    }
    for k, v in env.items():
        if v:
            data[k] = v
            # a different refresh token invalidates the cached access token
            if k == "refresh_token":
                data.pop("access_token", None)
                data["expires_at"] = 0.0
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not data.get(k)]
    if missing:
        raise YahooAuthError(
            f"missing Yahoo credentials: {', '.join(missing)}. Run `fantasy settings yahoo-auth` "
            "locally or set YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REFRESH_TOKEN."
        )
    return YahooCredentials(
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        refresh_token=data["refresh_token"],
        access_token=data.get("access_token"),
        expires_at=float(data.get("expires_at") or 0.0),
        redirect_uri=data.get("redirect_uri") or DEFAULT_REDIRECT_URI,
    )


def save_credentials(creds: YahooCredentials, path: Path | None = None) -> Path:
    """Write the token file with 0600 permissions."""
    path = path or token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(creds.to_json(), f, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def apply_token_response(
    creds: YahooCredentials, tok: dict[str, Any], now: float | None = None
) -> None:
    """Update credentials in place from a Yahoo /get_token response."""
    if not tok.get("access_token"):
        raise YahooAuthError("token response had no access_token")
    creds.access_token = tok["access_token"]
    creds.expires_at = (now or time.time()) + float(tok.get("expires_in") or 3600)
    if tok.get("refresh_token"):
        creds.refresh_token = tok["refresh_token"]


# ----------------------------------------------------------------------------- JSON helpers


def merge_fragments(obj: Any) -> dict[str, Any]:
    """Flatten Yahoo's ``[{"a": 1}, {"b": 2}, [], ...]`` fragment lists into one dict.

    Nested lists of fragments (e.g. a player's ``[[meta...], {"selected_position": ...}]``) are
    flattened recursively. Dicts are returned shallow-copied.
    """
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        out.update(obj)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                out.update(merge_fragments(item))
    return out


def iter_collection(coll: Any, item_key: str) -> Iterator[Any]:
    """Iterate a Yahoo collection ``{"0": {item_key: ...}, "1": ..., "count": n}`` (or a list)."""
    if isinstance(coll, list):
        for entry in coll:
            if isinstance(entry, dict) and item_key in entry:
                yield entry[item_key]
        return
    if not isinstance(coll, dict):
        return
    keys = sorted((k for k in coll if k.isdigit()), key=int)
    for k in keys:
        entry = coll[k]
        if isinstance(entry, dict) and item_key in entry:
            yield entry[item_key]


def _league_parts(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (league metadata, merged sub-resources) from a ``league/...`` response."""
    league = raw.get("fantasy_content", raw).get("league")
    if not isinstance(league, list) or not league:
        raise YahooError("response has no fantasy_content.league")
    meta = league[0] if isinstance(league[0], dict) else merge_fragments(league[0])
    subs = merge_fragments(league[1:])
    return meta, subs


def _to_int(v: Any) -> int | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _truthy(v: Any) -> bool:
    return v in (1, True, "1", "true", "True")


# ----------------------------------------------------------------------------- parsers


def parse_league_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse ``league/{key}/settings`` into a flat, typed dict (superset of settings_from_yahoo)."""
    meta, subs = _league_parts(raw)
    settings_list = subs.get("settings")
    if not settings_list:
        raise YahooError("league response has no settings sub-resource")
    s = merge_fragments(settings_list)

    roster_slots: dict[str, int] = {}
    for rp in s.get("roster_positions", []):
        rp = rp.get("roster_position", rp)
        pos = rp.get("position")
        if pos:
            roster_slots[pos] = roster_slots.get(pos, 0) + int(rp.get("count", 0) or 0)

    categories: list[dict[str, Any]] = []
    for st in (s.get("stat_categories") or {}).get("stats", []):
        st = st.get("stat", st)
        display_only = _truthy(st.get("is_only_display_stat"))
        if not display_only:
            for spt in st.get("stat_position_types", []) or []:
                if _truthy(spt.get("stat_position_type", {}).get("is_only_display_stat")):
                    display_only = True
        sid = _to_int(st.get("stat_id"))
        categories.append(
            {
                "stat_id": sid,
                "name": st.get("name"),
                "display_name": st.get("display_name"),
                "code": NBA_STAT_IDS.get(sid) if sid is not None else None,
                "higher_is_better": str(st.get("sort_order", "1")) == "1",
                "enabled": str(st.get("enabled", "1")) == "1",
                "display_only": display_only,
            }
        )

    playoff_start = _to_int(s.get("playoff_start_week"))
    end_week = _to_int(meta.get("end_week"))
    return {
        "league_key": meta.get("league_key"),
        "league_id": meta.get("league_id"),
        "name": meta.get("name"),
        "season": meta.get("season"),
        "game_code": meta.get("game_code"),
        "url": meta.get("url"),
        "num_teams": _to_int(meta.get("num_teams")),
        "scoring_type": s.get("scoring_type") or meta.get("scoring_type"),
        "draft_status": meta.get("draft_status"),
        "draft_type": s.get("draft_type"),
        "is_auction_draft": _truthy(s.get("is_auction_draft")),
        "draft_time": _to_int(s.get("draft_time")),
        "draft_pick_time": _to_int(s.get("draft_pick_time")),
        "start_week": _to_int(meta.get("start_week")),
        "end_week": end_week,
        "start_date": meta.get("start_date"),
        "end_date": meta.get("end_date"),
        "current_week": _to_int(meta.get("current_week")),
        "roster_slots": roster_slots,
        "categories": categories,
        "uses_playoff": _truthy(s.get("uses_playoff")),
        "playoff_start_week": playoff_start,
        "playoff_weeks": (
            list(range(playoff_start, end_week + 1)) if playoff_start and end_week else []
        ),
        "playoff_teams": _to_int(s.get("num_playoff_teams")),
        "has_playoff_consolation_games": _truthy(s.get("has_playoff_consolation_games")),
        "uses_playoff_reseeding": _truthy(s.get("uses_playoff_reseeding")),
        "max_weekly_adds": _to_int(s.get("max_weekly_adds")),
        "max_adds": _to_int(s.get("max_adds")),
        "waiver_type": s.get("waiver_type"),
        "waiver_rule": s.get("waiver_rule"),
        "waiver_days": _to_int(s.get("waiver_time")),
        "uses_faab": _truthy(s.get("uses_faab")),
        "post_draft_players": s.get("post_draft_players"),
        "trade_end_date": s.get("trade_end_date"),
        "trade_ratify_type": s.get("trade_ratify_type"),
        "trade_reject_time": _to_int(s.get("trade_reject_time")),
        "can_trade_draft_picks": _truthy(s.get("can_trade_draft_picks")),
        "cant_cut_list": s.get("cant_cut_list"),
        "player_pool": s.get("player_pool"),
    }


def settings_from_yahoo(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw ``league/{key}/settings`` JSON response to LeagueSettings-friendly plain keys.

    Keys: num_teams, categories (scoring Yahoo display names, e.g. ``FG%``, ``3PTM``, ``ST``, ``TO``),
    roster_slots (pos -> count, incl. BN/IL/IL+), draft_type, max_weekly_adds (None = unlimited),
    waiver_days, playoff_start_week, playoff_teams. Extra keys are provided for convenience:
    category_codes (our internal codes, e.g. ``fg_pct``), lower_is_better, is_auction_draft,
    draft_pick_time, waiver_type, uses_faab, playoff_weeks, end_week, season, league_key.
    """
    p = parse_league_settings(raw)
    scoring = [c for c in p["categories"] if c["enabled"] and not c["display_only"]]
    return {
        "num_teams": p["num_teams"],
        "categories": [c["display_name"] for c in scoring],
        "roster_slots": p["roster_slots"],
        "draft_type": p["draft_type"],
        "max_weekly_adds": p["max_weekly_adds"],
        "waiver_days": p["waiver_days"],
        "playoff_start_week": p["playoff_start_week"],
        "playoff_teams": p["playoff_teams"],
        # extras
        "category_codes": [c["code"] for c in scoring],
        "lower_is_better": [c["display_name"] for c in scoring if not c["higher_is_better"]],
        "is_auction_draft": p["is_auction_draft"],
        "draft_pick_time": p["draft_pick_time"],
        "waiver_type": p["waiver_type"],
        "uses_faab": p["uses_faab"],
        "playoff_weeks": p["playoff_weeks"],
        "end_week": p["end_week"],
        "season": p["season"],
        "league_key": p["league_key"],
        "scoring_type": p["scoring_type"],
    }


def parse_draft_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``league/{key}/draftresults``. Unmade picks have ``player_key=None``."""
    _, subs = _league_parts(raw)
    out = []
    for dr in iter_collection(subs.get("draft_results", {}), "draft_result"):
        dr = merge_fragments(dr)
        out.append(
            {
                "pick": _to_int(dr.get("pick")),
                "round": _to_int(dr.get("round")),
                "team_key": dr.get("team_key"),
                "player_key": dr.get("player_key") or None,
                "cost": _to_int(dr.get("cost")),
            }
        )
    out.sort(key=lambda d: d["pick"] or 0)
    return out


def _parse_team(team: Any) -> dict[str, Any]:
    t = merge_fragments(team)
    managers = [merge_fragments(m) for m in iter_collection(t.get("managers", []), "manager")]
    return {
        "team_key": t.get("team_key"),
        "team_id": _to_int(t.get("team_id")),
        "name": t.get("name"),
        "url": t.get("url"),
        "is_owned_by_current_login": _truthy(t.get("is_owned_by_current_login")),
        "draft_position": _to_int(t.get("draft_position")),
        "waiver_priority": _to_int(t.get("waiver_priority")),
        "number_of_moves": _to_int(t.get("number_of_moves")),
        "managers": [
            {"nickname": m.get("nickname"), "is_commissioner": _truthy(m.get("is_commissioner"))}
            for m in managers
        ],
    }


def parse_teams(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``league/{key}/teams``."""
    _, subs = _league_parts(raw)
    return [_parse_team(t) for t in iter_collection(subs.get("teams", {}), "team")]


def _parse_player(player: Any) -> dict[str, Any]:
    p = merge_fragments(player)
    name = p.get("name") or {}
    elig = [e.get("position") for e in p.get("eligible_positions", []) if isinstance(e, dict)]
    out: dict[str, Any] = {
        "player_key": p.get("player_key"),
        "player_id": _to_int(p.get("player_id")),
        "name": name.get("full") if isinstance(name, dict) else name,
        "nba_team": p.get("editorial_team_abbr"),
        "display_position": p.get("display_position"),
        "primary_position": p.get("primary_position"),
        "eligible_positions": [e for e in elig if e],
        "status": p.get("status") or None,
        "status_full": p.get("status_full") or None,
        "injury_note": p.get("injury_note") or None,
        "is_undroppable": _truthy(p.get("is_undroppable")),
    }
    if "selected_position" in p:
        sp = merge_fragments(p["selected_position"])
        out["selected_position"] = sp.get("position")
    if "percent_owned" in p:
        po = merge_fragments(p["percent_owned"])
        out["percent_owned"] = _to_float(po.get("value"))
    if "ownership" in p:
        ow = merge_fragments(p["ownership"])
        out["ownership_type"] = ow.get("ownership_type")
        out["owner_team_key"] = ow.get("owner_team_key")
    if "draft_analysis" in p:
        da = merge_fragments(p["draft_analysis"])
        out["draft_analysis"] = {
            k: _to_float(da.get(k))
            for k in (
                "average_pick",
                "average_round",
                "average_cost",
                "percent_drafted",
                "preseason_average_pick",
                "preseason_average_round",
                "preseason_average_cost",
                "preseason_percent_drafted",
            )
            if k in da
        }
    return out


def parse_players(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``league/{key}/players;...[;out=draft_analysis,percent_owned,ownership]``."""
    _, subs = _league_parts(raw)
    return [_parse_player(p) for p in iter_collection(subs.get("players", {}), "player")]


def parse_roster(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``team/{key}/roster[;date=YYYY-MM-DD]``."""
    team = raw.get("fantasy_content", raw).get("team")
    if not isinstance(team, list):
        raise YahooError("response has no fantasy_content.team")
    subs = merge_fragments(team[1:])
    roster = subs.get("roster") or {}
    players: Any = roster.get("players")
    if players is None:  # roster: {"0": {"players": {...}}, "coverage_type": ...}
        players = merge_fragments([v for k, v in roster.items() if k.isdigit()]).get("players", {})
    return [_parse_player(p) for p in iter_collection(players, "player")]


def parse_game(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse ``game/nba`` (current season) -> {game_key, season, ...}."""
    game = raw.get("fantasy_content", raw).get("game")
    g = merge_fragments(game)
    return {
        "game_key": str(g.get("game_key")) if g.get("game_key") is not None else None,
        "game_id": str(g.get("game_id")) if g.get("game_id") is not None else None,
        "code": g.get("code"),
        "season": str(g.get("season")) if g.get("season") is not None else None,
        "is_game_over": _truthy(g.get("is_game_over")),
        "is_offseason": _truthy(g.get("is_offseason")),
        "is_live_draft_lobby_active": _truthy(g.get("is_live_draft_lobby_active")),
    }


def league_key(game_key: str, league_id: int | str) -> str:
    return f"{game_key}.l.{league_id}"


# ----------------------------------------------------------------------------- client


class YahooClient:
    """Read-only Yahoo Fantasy API client. Use as a context manager or call ``close()``."""

    def __init__(
        self,
        creds: YahooCredentials | None = None,
        *,
        http: httpx.Client | None = None,
        token_file: Path | None = None,
        persist_tokens: bool = True,
        read_only: bool = True,
        min_interval: float = 0.25,
        timeout: float = 20.0,
    ) -> None:
        self._token_file = token_file
        self.creds = creds or load_credentials(token_file)
        self._http = http or httpx.Client(timeout=timeout)
        self._owns_http = http is None
        self._persist = persist_tokens
        self.read_only = read_only
        self._min_interval = min_interval
        self._last_request = 0.0

    def __enter__(self) -> YahooClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # -- auth
    def refresh(self) -> None:
        resp = self._http.post(
            TOKEN_URL,
            auth=(self.creds.client_id, self.creds.client_secret),
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.creds.refresh_token,
                "redirect_uri": self.creds.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            # Body may echo request params; log only status.
            raise YahooAuthError(
                f"token refresh failed (HTTP {resp.status_code}); re-run `fantasy settings yahoo-auth`"
            )
        apply_token_response(self.creds, resp.json())
        log.info("yahoo access token refreshed")
        if self._persist:
            try:
                save_credentials(self.creds, self._token_file)
            except OSError as e:  # e.g. read-only FS; env-var creds still work in-memory
                log.warning("could not persist refreshed yahoo token: %s", type(e).__name__)

    def _ensure_token(self) -> str:
        if not self.creds.access_token_valid():
            self.refresh()
        assert self.creds.access_token
        return self.creds.access_token

    # -- transport
    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self.read_only and method.upper() != "GET":
            raise YahooReadOnlyError(f"{method} refused: client is read-only")
        url = f"{API_BASE}/{path.lstrip('/')}"
        params = dict(kwargs.pop("params", {}) or {})
        params.setdefault("format", "json")
        for attempt in range(2):
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            headers = {"Authorization": f"Bearer {self._ensure_token()}"}
            resp = self._http.request(method, url, params=params, headers=headers, **kwargs)
            self._last_request = time.monotonic()
            if resp.status_code == 401 and attempt == 0:
                self.creds.access_token = None  # force refresh, retry once
                continue
            break
        if resp.status_code in (999, 429):
            raise YahooRateLimitError(f"Yahoo rate limit (HTTP {resp.status_code}) on {path}")
        if resp.status_code != 200:
            detail = ""
            try:
                detail = str(resp.json().get("error", {}).get("description", ""))[:300]
            except (ValueError, AttributeError):
                pass
            raise YahooError(
                f"Yahoo API {method} {path} -> HTTP {resp.status_code} {detail}".strip()
            )
        return resp

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self.request("GET", path, params=params).json()

    # -- resources
    def get_game(self, game_code: str = "nba") -> dict[str, Any]:
        """Current-season game for a code; ``game_key`` is the numeric prefix of league keys."""
        return parse_game(self.get(f"game/{game_code}"))

    def resolve_league_key(self, league_id: int | str, game_code: str = "nba") -> str:
        return league_key(self.get_game(game_code)["game_key"], league_id)

    def get_league_settings_raw(self, league_key: str) -> dict[str, Any]:
        return self.get(f"league/{league_key}/settings")

    def get_league_settings(self, league_key: str) -> dict[str, Any]:
        """Parsed settings (see ``parse_league_settings``); use ``settings_from_yahoo`` for the
        LeagueSettings mapping."""
        return parse_league_settings(self.get_league_settings_raw(league_key))

    def get_draft_results(self, league_key: str) -> list[dict[str, Any]]:
        return parse_draft_results(self.get(f"league/{league_key}/draftresults"))

    def get_teams(self, league_key: str) -> list[dict[str, Any]]:
        return parse_teams(self.get(f"league/{league_key}/teams"))

    def get_roster(self, team_key: str, day: date | str | None = None) -> list[dict[str, Any]]:
        path = f"team/{team_key}/roster"
        if day is not None:
            path += f";date={day.isoformat() if isinstance(day, date) else day}"
        return parse_roster(self.get(path))

    def get_players(
        self,
        league_key: str,
        start: int = 0,
        count: int = 25,
        *,
        status: str | None = None,
        position: str | None = None,
        sort: str | None = "OR",
        search: str | None = None,
        out: tuple[str, ...] = ("draft_analysis", "percent_owned", "ownership"),
    ) -> list[dict[str, Any]]:
        """One page of league players (Yahoo caps ``count`` at 25 per request).

        ``status``: A (all), FA, W, T (taken), K. ``sort``: OR (preseason/overall rank),
        AR (actual rank), NAME, or a stat_id. ``out`` adds sub-resources per player.
        """
        filters = [f"start={start}", f"count={min(count, 25)}"]
        for k, v in (
            ("status", status),
            ("position", position),
            ("sort", sort),
            ("search", search),
        ):
            if v:
                filters.append(f"{k}={v}")
        if out:
            filters.append("out=" + ",".join(out))
        return parse_players(self.get(f"league/{league_key}/players;" + ";".join(filters)))

    def iter_players(
        self, league_key: str, limit: int = 300, **kw: Any
    ) -> Iterator[dict[str, Any]]:
        start = 0
        while start < limit:
            page = self.get_players(league_key, start=start, count=min(25, limit - start), **kw)
            yield from page
            if len(page) < 25:
                return
            start += len(page)
