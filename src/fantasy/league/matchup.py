"""Live H2H matchup score: Yahoo matchup-page capture -> validated, normalised `matchup_score` model_runs row.

Input: the JSON returned by `scripts/yahoo/capture_matchup.js` (read-only browser fetch of
`/nba/44495/matchup?week=N&mid1=11`), or the saved page HTML (`parse_matchup_html`, the same table logic in
Python; it is what the tests exercise). Output: summary schema `matchup_score/1` (docs/research/dashboard_data.md
section 7): both teams' raw totals so far this week, FG%/FT% recomputed from makes/attempts (Yahoo's displayed % is
only cross-checked), the current leader per category, and the category score. Yahoo team ids are mapped to our team
ids (= draft slots) via the league state's `yahoo_team_id`, else the league_schedule capture.

Rows are immutable and idempotent per (week, ET date, content): run_id `ms-w<week>-<date>-<sha12>`, hash of the
week, date and both teams' totals/games (not observed_at: re-reading an unchanged score the same day is a no-op).
`fantasy lineup plan` reads the newest row for its week (`latest_score`) as the banked totals (`accumulated`).
Nothing here touches Yahoo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fantasy.league.dates import ET, et_date
from fantasy.ops.common import canonical_json, git_sha, json_safe, now, sha256_hex

if TYPE_CHECKING:
    from fantasy.lineup.record import RunStore

KIND = "matchup_score"
SCHEMA = "matchup_score/1"
MODEL_VERSION = "matchup-score-0.1.0"
LEAGUE = 44495
# raw totals, same keys/order as fantasy.model.lines.STATS (what `lineup plan` takes as accumulated)
TOTALS = ("fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov")
# scoring categories in Yahoo order: (key, kind, higher_is_better, makes, attempts | stat)
CATS: tuple[tuple[str, str, bool, str, str | None], ...] = (
    ("fg_pct", "ratio", True, "fgm", "fga"), ("ft_pct", "ratio", True, "ftm", "fta"),
    ("fg3m", "count", True, "fg3m", None), ("pts", "count", True, "pts", None), ("reb", "count", True, "reb", None),
    ("ast", "count", True, "ast", None), ("stl", "count", True, "stl", None), ("blk", "count", True, "blk", None),
    ("tov", "count", False, "tov", None),
)
PCT_DECIMALS = 3  # Yahoo shows (and compares) FG%/FT% to 3 decimals
PCT_TOLERANCE = 0.0015  # displayed % vs makes/attempts: more than rounding = a parse problem


class MatchupError(ValueError):
    """Unusable capture (CLI: exit 2, code invalid_capture)."""


# ------------------------------------------------------------------ HTML parsing (mirrors capture_matchup.js)

# header label (lowercase, "*" removed, spaces collapsed) -> field; keep in sync with COLS in capture_matchup.js
HEADER_ALIASES: tuple[tuple[str, str], ...] = (
    (r"^fgm ?/ ?a$", "fgm_a"), (r"^fg ?%$", "fg_pct"), (r"^ftm ?/ ?a$", "ftm_a"), (r"^ft ?%$", "ft_pct"),
    (r"^(3ptm|3pm|3-ptm|3pt|3ptm?\.)$", "fg3m"), (r"^pts$", "pts"), (r"^reb$", "reb"), (r"^ast$", "ast"),
    (r"^(st|stl)$", "stl"), (r"^blk$", "blk"), (r"^(to|tov)$", "tov"), (r"^(score|cats|cat)$", "cats_won"),
    (r"^(gp|games played)$", "games_played"),
    (r"^(rem|remaining|games left|gl|games remaining|remaining games)$", "games_remaining"),
)
_CAT_FIELDS = {"fgm_a", "fg_pct", "ftm_a", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"}


def header_field(label: str) -> str | None:
    n = re.sub(r"\s+", " ", label.replace("*", "")).strip().lower()
    return next((f for pat, f in HEADER_ALIASES if re.match(pat, n)), None)


# Yahoo shows "-" (and "-/-" for makes/attempts) before a team has any stats this week: that is 0, not missing
_DASH = r"[-\u2013\u2014]"


def _num(s: str | None) -> float | None:
    t = (s or "").replace(",", "").strip()
    if re.fullmatch(_DASH, t):
        return 0.0
    return float(t) if re.fullmatch(r"-?\d*\.?\d+", t) else None


def _pct(s: str | None) -> float | None:
    """Displayed % (".474", "47.4"); "-" (no attempts yet) -> None: the % is always recomputed from makes/attempts."""
    if re.fullmatch(_DASH, (s or "").strip()):
        return None
    v = _num(s)
    return None if v is None else (v / 100 if v > 1 else v)


def _pair(s: str | None) -> tuple[float | None, float | None]:
    t = (s or "").replace(",", "")
    if re.fullmatch(rf"\s*{_DASH}\s*/\s*{_DASH}\s*", t):
        return (0.0, 0.0)
    m = re.search(r"(\d+)\s*/\s*(\d+)", t)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


# games strip above the score table (2026-10-20 layout): "30 Remaining | 0/30 Games Played 0/26 | Remaining 26"
# = left team (first table row) remaining, played/total per team, right team (second row) remaining. Matched on the
# page's text nodes joined by single spaces (same rule in capture_matchup.js).
GAMES_STRIP_RE = re.compile(r"(\d+)\s+Remaining\s+(\d+)\s*/\s*(\d+)\s+Games Played\s+(\d+)\s*/\s*(\d+)"
                            r"\s+Remaining\s+(\d+)", re.I)
GAMES_MID_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s+Games Played\s+(\d+)\s*/\s*(\d+)", re.I)


def games_strip(text: str) -> tuple[list[tuple[int, int]] | None, list[str]]:
    """[(played, remaining) of the left team, of the right team] from the page text, and problems."""
    m = GAMES_STRIP_RE.search(text)
    if m:
        rem_l, p1, tot1, p2, tot2, rem_r = (int(x) for x in m.groups())
        problems = [f"games strip: {side} remaining {rem} != total {tot} - played {pl}"
                    for side, rem, pl, tot in (("left", rem_l, p1, tot1), ("right", rem_r, p2, tot2))
                    if rem != tot - pl]
        return [(p1, rem_l), (p2, rem_r)], problems
    m = GAMES_MID_RE.search(text)
    if m:
        p1, tot1, p2, tot2 = (int(x) for x in m.groups())
        return [(p1, tot1 - p1), (p2, tot2 - p2)], []
    return None, ["games played/remaining strip not found on the page"]


@dataclass
class _Cell:
    tag: str
    text: str = ""
    title: str | None = None
    hrefs: list[str] = field(default_factory=list)
    link_texts: list[str] = field(default_factory=list)  # text of each <a href> in the cell, in order

    @property
    def label(self) -> str:
        t = re.sub(r"\s+", " ", self.text).strip()
        return self.title if (not t and self.title) else t


class _Tables(HTMLParser):
    """Every <table> as rows of cells (text, title, hrefs); nested tables are kept separate. Also the selected
    week option and the page title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[_Cell]]] = []
        self._stack: list[list[list[_Cell]]] = []
        self._cell: list[_Cell | None] = []
        self.title = ""
        self._in_title = False
        self.week_option: str | None = None
        self._in_week_select = False
        self._in_selected_option = False
        self.text_chunks: list[str] = []
        self._in_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table":
            self._stack.append([])
            self._cell.append(None)
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack and self._stack[-1]:
            c = _Cell(tag, title=a.get("title"))
            self._stack[-1][-1].append(c)
            self._cell[-1] = c
        elif tag == "a" and self._cell and self._cell[-1] is not None and a.get("href"):
            self._cell[-1].hrefs.append(str(a["href"]))
            self._cell[-1].link_texts.append("")
            self._in_link = True
        elif tag == "title":
            self._in_title = True
        elif tag == "select" and (a.get("name") == "week" or a.get("id") == "week"):
            self._in_week_select = True
        elif tag == "option" and self._in_week_select and "selected" in a:
            self._in_selected_option = True
            self.week_option = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._stack:
            self.tables.append(self._stack.pop())
            self._cell.pop()
        elif tag in ("td", "th") and self._cell:
            self._cell[-1] = None
        elif tag == "a":
            self._in_link = False
        elif tag == "title":
            self._in_title = False
        elif tag == "select":
            self._in_week_select = False
        elif tag == "option":
            self._in_selected_option = False

    def handle_data(self, data: str) -> None:
        if self._cell and self._cell[-1] is not None:
            self._cell[-1].text += " " + data
            if self._in_link and self._cell[-1].link_texts:
                self._cell[-1].link_texts[-1] += " " + data
        if self._in_title:
            self.title += data
        if self._in_selected_option and self.week_option is not None:
            self.week_option += data
        if len(self.text_chunks) < 50000 and data.strip():
            self.text_chunks.append(data.strip())


def parse_matchup_html(html: str, *, my_team: int = 11, week: int | None = None, league: int = LEAGUE,
                       captured_at: str | None = None, source: str | None = None) -> dict[str, Any]:
    """A saved Yahoo matchup page -> the capture JSON `capture_matchup.js` returns (same table rules)."""
    p = _Tables()
    p.feed(html)
    team_re = re.compile(rf"/nba/{league}/(\d+)(?:[/?#]|$)")
    problems: list[str] = []
    best: tuple[list[str], list[list[_Cell]]] | None = None
    headers_seen: list[list[str]] = []
    for rows in p.tables:
        hdr = next((r for r in rows if any(c.tag == "th" for c in r)
                    and sum(header_field(c.label) in _CAT_FIELDS for c in r) >= 6), None)
        if hdr is None:
            continue
        labels = [c.label for c in hdr]
        headers_seen.append(labels)
        team_rows = [r for r in rows if r is not hdr and any(team_re.search(h) for c in r for h in c.hrefs)]
        if len(team_rows) >= 2:
            best = (labels, team_rows)
            break
    teams: list[dict[str, Any]] = []
    if best is None:
        problems.append("no score table found (header with >= 6 category columns and >= 2 team rows): "
                        "logged out or layout change?")
    else:
        labels, team_rows = best
        fields = [header_field(x) for x in labels]
        if fields and "cats_won" not in fields and not labels[-1].strip():
            fields[-1] = "cats_won"  # trailing blank-header column = categories won (2026-10-20 layout)
        for need in [*sorted(_CAT_FIELDS), "cats_won"]:
            if need not in fields:
                problems.append(f"column for {need} not found in header {labels}")
        for r in team_rows[:2]:
            off = len(fields) - len(r)
            if off:
                problems.append(f"row has {len(r)} cells, header {len(fields)}: aligned from the right")
            href = next(h for c in r for h in c.hrefs if team_re.search(h))
            name_cell = next(c for c in r if any(team_re.search(h) for h in c.hrefs))
            # the logo link comes first with no text; the name is the first non-empty team-link text
            name = next((" ".join(x.split()) for c in r for h, x in zip(c.hrefs, c.link_texts, strict=True)
                         if team_re.search(h) and x.strip()), name_cell.label)
            m = team_re.search(href)
            assert m is not None
            totals: dict[str, float | None] = {k: None for k in
                                               ("fgm", "fga", "fg_pct", "ftm", "fta", "ft_pct", *TOTALS[4:])}
            t: dict[str, Any] = {"yahoo_team_id": int(m.group(1)), "name": name, "totals": totals,
                                 "cats_won": None, "games_played": None, "games_remaining": None}
            for i, c in enumerate(r):
                f = fields[i + off] if 0 <= i + off < len(fields) else None
                if f is None:
                    continue
                s = c.label
                if f == "fgm_a":
                    totals["fgm"], totals["fga"] = _pair(s)
                elif f == "ftm_a":
                    totals["ftm"], totals["fta"] = _pair(s)
                elif f in ("fg_pct", "ft_pct"):
                    totals[f] = _pct(s)
                elif f in ("cats_won", "games_played", "games_remaining"):
                    t[f] = _num(s)
                else:
                    totals[f] = _num(s)
            for k, v in totals.items():
                if v is None and not k.endswith("_pct"):
                    problems.append(f"team {t['yahoo_team_id']}: {k} not parsed")
            teams.append(t)
        if not any(t["yahoo_team_id"] == my_team for t in teams):
            problems.append(f"my team {my_team} not among the score rows")
    if week is None:
        m = re.search(r"(\d+)", p.week_option or "") or re.search(r"Week\s+(\d+)", p.title + " " + " ".join(p.text_chunks),
                                                                  re.I)
        week = int(m.group(1)) if m else None
        if week is None:
            problems.append("week not found on the page (pass --week)")
    if teams and all(t["games_remaining"] is None and t["games_played"] is None for t in teams):
        games, gp_problems = games_strip(" ".join(p.text_chunks))
        problems += gp_problems
        if games:  # left / right of the strip = first / second table row
            for t, (played, remaining) in zip(teams, games, strict=False):
                t["games_played"], t["games_remaining"] = played, remaining
    return {"source": source or "saved Yahoo matchup page HTML (fantasy.league.matchup.parse_matchup_html)",
            "captured_at": captured_at, "url": None, "week": week, "teams": teams, "problems": problems,
            "diagnostics": {"score_table_headers": headers_seen[:4]}}


# ------------------------------------------------------------------ validation / normalisation


def load_capture_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, str):  # the JS tool may hand back the JSON string itself
            data = json.loads(data)
    except (OSError, json.JSONDecodeError) as e:
        raise MatchupError(f"cannot read capture {path}: {e}") from e
    if not isinstance(data, dict):
        raise MatchupError(f"{path}: expected a JSON object")
    return data


def _opt_num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    return _num(str(v))


def pct(makes: float, attempts: float) -> float | None:
    """Category %: makes / attempts (None without attempts)."""
    return makes / attempts if attempts > 0 else None


@dataclass
class TeamScore:
    yahoo_team_id: int
    name: str | None
    totals: dict[str, float]
    games_played: float | None
    games_remaining: float | None
    cats_won_yahoo: float | None
    slot: int | None = None

    def cat_value(self, key: str) -> float | None:
        c = next(c for c in CATS if c[0] == key)
        if c[1] == "ratio":
            assert c[4] is not None
            return pct(self.totals[c[3]], self.totals[c[4]])
        return self.totals[c[3]]

    def to_json(self) -> dict[str, Any]:
        fg, ft = pct(self.totals["fgm"], self.totals["fga"]), pct(self.totals["ftm"], self.totals["fta"])
        return {"slot": self.slot, "yahoo_team_id": self.yahoo_team_id, "name": self.name,
                "totals": {k: _int_if_whole(self.totals[k]) for k in TOTALS},
                "fg_pct": None if fg is None else round(fg, 4), "ft_pct": None if ft is None else round(ft, 4),
                "games_played": _int_if_whole(self.games_played), "games_remaining": _int_if_whole(self.games_remaining)}


def _int_if_whole(v: float | None) -> float | int | None:
    return int(v) if v is not None and float(v).is_integer() else v


def parse_teams(cap: dict[str, Any]) -> tuple[list[TeamScore], list[str]]:
    """Validated teams (exactly 2) and warnings (capture problems, displayed-% and PTS cross-checks)."""
    warnings = [f"capture: {p}" for p in cap.get("problems") or []]
    raw = cap.get("teams")
    if isinstance(raw, dict):  # {yahoo id: {...}} is accepted too
        raw = [{"yahoo_team_id": k, **v} for k, v in raw.items() if isinstance(v, dict)]
    if not isinstance(raw, list) or len(raw) != 2:
        raise MatchupError(f"expected exactly 2 teams in the capture, got {len(raw) if isinstance(raw, list) else raw!r}"
                           + (f"; capture problems: {cap.get('problems')}" if cap.get("problems") else ""))
    out: list[TeamScore] = []
    errors: list[str] = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise MatchupError(f"teams[{i}]: expected an object")
        tid = _opt_num(t.get("yahoo_team_id"))
        if tid is None:
            errors.append(f"teams[{i}]: yahoo_team_id missing")
            continue
        tot_raw = t.get("totals") or {}
        totals: dict[str, float] = {}
        for k in TOTALS:
            v = _opt_num(tot_raw.get(k))
            if v is None or v < 0:
                errors.append(f"team {int(tid)}: totals.{k} missing or negative ({tot_raw.get(k)!r})")
            else:
                totals[k] = v
        if len(totals) == len(TOTALS):
            for m, a in (("fgm", "fga"), ("ftm", "fta")):
                if totals[m] > totals[a]:
                    errors.append(f"team {int(tid)}: {m} {totals[m]:g} > {a} {totals[a]:g}")
                shown = _opt_num(tot_raw.get(f"{m[:2]}_pct"))
                if shown is not None and shown > 1:
                    shown /= 100
                p = pct(totals[m], totals[a])
                if shown is not None and p is not None and abs(round(p, PCT_DECIMALS) - shown) > PCT_TOLERANCE:
                    warnings.append(f"team {int(tid)}: displayed {m[:2]}% {shown:.3f} != {m}/{a} = {p:.4f} "
                                    "(makes/attempts used)")
            if totals["fg3m"] > totals["fgm"]:
                errors.append(f"team {int(tid)}: fg3m {totals['fg3m']:g} > fgm {totals['fgm']:g}")
            exp_pts = 2 * totals["fgm"] + totals["fg3m"] + totals["ftm"]
            if abs(exp_pts - totals["pts"]) > 0.5:
                warnings.append(f"team {int(tid)}: pts {totals['pts']:g} != 2 FGM + 3PM + FTM = {exp_pts:g} "
                                "(check the column mapping)")
        out.append(TeamScore(int(tid), (str(t.get("name")).strip() or None) if t.get("name") else None, totals,
                             _opt_num(t.get("games_played")), _opt_num(t.get("games_remaining")),
                             _opt_num(t.get("cats_won"))))
    if errors:
        raise MatchupError("; ".join(errors) + (f" (capture problems: {cap.get('problems')})" if cap.get("problems")
                                                 else ""))
    if out[0].yahoo_team_id == out[1].yahoo_team_id:
        raise MatchupError(f"both rows are Yahoo team {out[0].yahoo_team_id}")
    return out, warnings


def slot_map_from_state(state: dict[str, Any] | None) -> dict[int, int]:
    """Yahoo team id -> our team id (draft slot) from the league state's `yahoo_team_id`s."""
    return {int(v["yahoo_team_id"]): int(k) for k, v in ((state or {}).get("teams") or {}).items()
            if isinstance(v, dict) and v.get("yahoo_team_id") is not None}


def categories(me: TeamScore, opp: TeamScore) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Per-category current values and leader; FG%/FT% from makes/attempts compared at 3 decimals (Yahoo's
    display precision; a team without attempts has no % and the category counts as a tie)."""
    rows = []
    score = {"me": 0, "opp": 0, "ties": 0}
    for key, kind, higher, _, _ in CATS:
        a, b = me.cat_value(key), opp.cat_value(key)
        if a is None or b is None:
            leader = "tie"
        else:
            x, y = (round(a, PCT_DECIMALS), round(b, PCT_DECIMALS)) if kind == "ratio" else (a, b)
            d = (x - y) if higher else (y - x)
            leader = "me" if d > 0 else ("opp" if d < 0 else "tie")
        score["ties" if leader == "tie" else leader] += 1
        rows.append({"cat": key, "me": None if a is None else (round(a, 4) if kind == "ratio" else _int_if_whole(a)),
                     "opp": None if b is None else (round(b, 4) if kind == "ratio" else _int_if_whole(b)),
                     "leader": leader})
    return rows, score


def build_summary(cap: dict[str, Any], *, my_slot: int, slot_map: dict[int, int], week: int | None = None,
                  calendar: list[Any] | None = None, opponent_of: Any = None,
                  capture_file: str | None = None) -> dict[str, Any]:
    """The `matchup_score/1` summary. `slot_map`: Yahoo team id -> our team id; `calendar`: FantasyWeeks (week from
    the observed date when the capture has none); `opponent_of(team, week)`: schedule check (warning only)."""
    teams, warnings = parse_teams(cap)
    for t in teams:
        t.slot = slot_map.get(t.yahoo_team_id)
    mine = [t for t in teams if t.slot == my_slot]
    if len(mine) != 1:
        raise MatchupError(f"my team (slot {my_slot}) is not exactly one of the captured teams "
                           f"{[(t.yahoo_team_id, t.slot) for t in teams]}: check the league state's yahoo_team_id "
                           "map / --slot-map")
    me = mine[0]
    opp = teams[1] if teams[0] is me else teams[0]
    if opp.slot is None:
        warnings.append(f"opponent Yahoo team {opp.yahoo_team_id} has no known draft slot (slot null)")
    observed_at = cap.get("captured_at")
    d_et = et_date(observed_at)
    if d_et is None:
        raise MatchupError("capture has no ISO captured_at (needed: banked totals are 'as of' that time)")
    cap_week = _opt_num(cap.get("week"))
    wk = week if week is not None else (int(cap_week) if cap_week is not None else None)
    by_date = next((w.week for w in (calendar or []) if w.start <= d_et <= w.end), None)
    if wk is None:
        wk = by_date
    if wk is None:
        raise MatchupError("week unknown: not in the capture and the observed date is in no week of the schedule; "
                           "pass --week")
    if cap_week is not None and week is not None and int(cap_week) != week:
        warnings.append(f"--week {week} overrides the capture's week {int(cap_week)}")
    if by_date is not None and by_date != wk:
        warnings.append(f"week {wk} captured on {d_et} (in week {by_date}): a past/future week's page")
    if opponent_of is not None:
        exp = opponent_of(my_slot, wk)
        if exp is not None and opp.slot is not None and exp != opp.slot:
            warnings.append(f"schedule says my week-{wk} opponent is team {exp}, the page shows team {opp.slot}")
    cats, score = categories(me, opp)
    for side, t in (("me", me), ("opp", opp)):
        if t.cats_won_yahoo is not None and t.cats_won_yahoo != score[side]:
            warnings.append(f"Yahoo shows {side} winning {t.cats_won_yahoo:g} categories, computed {score[side]} "
                            "(ties / % rounding / parse problem?)")
    return json_safe({
        "schema": SCHEMA, "week": wk, "date_et": d_et.isoformat(), "observed_at": observed_at,
        "source": cap.get("source"), "url": cap.get("url"), "capture_file": capture_file,
        "me": me.to_json(), "opp": opp.to_json(), "categories": cats, "score": score,
        "yahoo_cats_won": {"me": _int_if_whole(me.cats_won_yahoo), "opp": _int_if_whole(opp.cats_won_yahoo)},
        "warnings": warnings, "model_version": MODEL_VERSION,
    })


def content_hash(summary: dict[str, Any]) -> str:
    """sha256 of what Yahoo showed: week, ET date, and per team its ids, totals and games (not names, sources or
    observed_at, so the same score read twice in a day -- by the JS capture or from saved HTML -- is one row)."""
    keep = ("slot", "yahoo_team_id", "totals", "games_played", "games_remaining")
    return sha256_hex(canonical_json(json_safe({
        "week": summary.get("week"), "date_et": summary.get("date_et"),
        **{side: {k: (summary.get(side) or {}).get(k) for k in keep} for side in ("me", "opp")}})))


def run_id(summary: dict[str, Any]) -> str:
    return f"ms-w{summary['week']}-{summary['date_et']}-{content_hash(summary)[:12]}"


def score_row(summary: dict[str, Any], *, capture_sha256: str | None = None,
              params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"run_id": run_id(summary), "kind": KIND, "model_version": MODEL_VERSION, "git_sha": git_sha(),
            "params": json_safe(params or {"command": "league score"}),
            "inputs": {"capture_file": summary.get("capture_file"), "capture_sha256": capture_sha256,
                       "content_sha256": content_hash(summary)},
            "summary": summary, "created_at": now()}


def save(row: dict[str, Any], st: RunStore) -> dict[str, Any]:
    inserted = st.insert_missing(row)
    return {"run_id": row["run_id"], "kind": KIND, "week": row["summary"]["week"],
            "date_et": row["summary"]["date_et"], "inserted": inserted, "store": st.name}


# ------------------------------------------------------------------ readers (lineup plan)


def observed_et(summary: dict[str, Any]) -> datetime | None:
    ts = summary.get("observed_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ET)


def latest_score(rows: list[dict[str, Any]], *, week: int, my_slot: int, on_or_before: date) -> dict[str, Any] | None:
    """The newest saved `matchup_score` row (by observed_at) for `week` with me = `my_slot`, observed on or
    before `on_or_before` (ET date): a later read already contains that day's games."""
    ok = [r for r in rows if isinstance(r.get("summary"), dict) and r["summary"].get("schema") == SCHEMA
          and r["summary"].get("week") == week and (r["summary"].get("me") or {}).get("slot") == my_slot
          and str(r["summary"].get("date_et") or "9999") <= on_or_before.isoformat()]
    if not ok:
        return None
    return max(ok, key=lambda r: (str(r["summary"].get("observed_at") or ""), str(r.get("created_at") or "")))


def accumulated(summary: dict[str, Any]) -> dict[str, dict[str, float]]:
    """`lineup plan` accumulated totals {"me": {stat: total}, "opp": {...}}: raw makes/attempts and counts only
    (the planner forms FG%/FT% from makes/attempts)."""
    return {side: {k: float(summary[side]["totals"][k]) for k in TOTALS} for side in ("me", "opp")}


def banked_checks(summary: dict[str, Any], plan_day: date) -> list[str]:
    """Warnings about how the banked totals line up with a plan for `plan_day` whose remaining days start that day:
    a read before plan_day misses the games played between the read and plan_day (counted nowhere); a read on
    plan_day after noon ET may already include that day's games (counted twice)."""
    obs = observed_et(summary)
    if obs is None:
        return ["matchup score has no parseable observed_at"]
    out = []
    if obs.date() < plan_day:
        out.append(f"BANKED STATS AS OF {obs:%Y-%m-%d %H:%M} ET (before {plan_day}): games played after that read "
                   f"and before {plan_day} are counted neither as banked nor as remaining -- re-capture the matchup")
    elif obs.date() == plan_day and obs.hour >= 12:
        out.append(f"BANKED STATS READ {obs:%H:%M} ET ON {plan_day}: games of {plan_day} that had started are counted "
                   "as banked AND as remaining (prefer a morning capture, or pass --locked and accept the overlap)")
    return out
