"""Yahoo free-agent captures: who is available, from when (waiver timing), and the realistic refill.

Input: `data/raw/yahoo/free_agents_<date>.json` (`scripts/yahoo/capture_free_agents.js`, normalised by
`fantasy league import-capture --kind free_agents`): `{captured_at|capture_date|source, notes, players: [{player_id,
name, team, positions, status, ownership ("FA" | "W (Oct 22)"), waiver_until ("Oct 22")}]}`. Older captures (the
2026-10-20 one) carry no per-player ownership; their `notes` say "All listed players were on waivers 'W (Oct 22)'",
which is applied to every player (source recorded in `waiver_source`).

Timing rules (league 44495 settings, `data/manual/league_settings.json`; Yahoo semantics are an ASSUMPTION, not
verified on a processed claim yet):
* free agent ("FA"): added today, plays from today (`weekly_deadline = daily_today`) if his game has not started.
* waiver ("W (Mon D)"): a claim is processed on that date; the player plays from that date + `lag_days` (default 1 in
  the stream scan: plays the next day). The dropped player stays on my roster (and plays) until the claim processes.
  Waiver order is a continual rolling list, so the claim is not guaranteed (another team may claim first).
* a player dropped by anyone goes on waivers for `waiver_days` (2) days.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from fantasy.lineup.players import OUT_TAGS, normalize_status, normalize_team, parse_positions

_W = re.compile(r"W\s*\(\s*([A-Za-z]{3,9})\s+(\d{1,2})\s*\)")
_MDAY = re.compile(r"^\s*([A-Za-z]{3,9})\s+(\d{1,2})\s*$")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], start=1)}


@dataclass(frozen=True)
class FreeAgent:
    player_id: str
    name: str
    team: str | None  # bbref code
    positions: tuple[str, ...]
    status: str | None  # normalised injury tag
    waiver_until: date | None  # None = free agent (can be added today)
    waiver_source: str  # "player" | "capture notes" | "none"
    yahoo_rank: float | None = None

    @property
    def is_out(self) -> bool:
        return self.status in OUT_TAGS

    def available_from(self, today: date, lag_days: int = 0) -> date:
        """First day he can play for me: today (free agent) or the waiver date + `lag_days` (ASSUMPTION: the claim
        processes on the waiver date and he plays from the next day, lag 1, per the 2026-10-20 note)."""
        return max(today, self.waiver_until + timedelta(days=lag_days)) if self.waiver_until else today

    @property
    def ownership(self) -> str:
        return f"W ({self.waiver_until:%b} {self.waiver_until.day})" if self.waiver_until else "FA"


def _month_day(text: str, year_hint: date) -> date | None:
    m = _MDAY.match(text) or _W.search(text)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3].lower())
    if mon is None:
        return None
    d = date(year_hint.year, mon, int(m.group(2)))
    if (d - year_hint).days < -180:  # Dec capture, Jan waiver date
        d = date(year_hint.year + 1, mon, int(m.group(2)))
    return d


def capture_date(raw: dict, fallback: date) -> date:
    for k in ("capture_date", "captured_at"):
        v = raw.get(k)
        if v:
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
            except ValueError:
                pass
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(raw.get("source") or ""))
    return date.fromisoformat(m.group(1)) if m else fallback


def load_free_agents(path: Path, today: date) -> tuple[list[FreeAgent], list[str]]:
    """Free agents with a projection id (others are listed in the warnings and skipped)."""
    raw = json.loads(Path(path).read_text())
    cap = capture_date(raw, today)
    notes = str(raw.get("notes") or "")
    blanket = _W.search(notes) if re.search(r"all listed players were on waivers", notes, re.I) else None
    out, warnings, skipped = [], [], []
    for p in raw.get("players", []):
        pid = p.get("player_id")
        if not pid:
            skipped.append(str(p.get("name")))
            continue
        wd, src = None, "none"
        own = str(p.get("ownership") or "")
        if p.get("waiver_until"):
            wd, src = _month_day(str(p["waiver_until"]), cap), "player"
        elif own:
            wd, src = (_month_day(own, cap), "player") if own.upper().startswith("W") else (None, "player")
        elif blanket is not None:
            wd, src = _month_day(blanket.group(0), cap), "capture notes"
        out.append(FreeAgent(str(pid), str(p.get("name") or pid), normalize_team(p.get("team")),
                             parse_positions(p.get("positions")), normalize_status(p.get("status")), wd, src,
                             float(p["yahoo_rank"]) if p.get("yahoo_rank") is not None else None))
    if skipped:
        warnings.append(f"{len(skipped)} free agent(s) not in projections, skipped: {', '.join(skipped[:8])}")
    if (today - cap).days > 1:
        warnings.append(f"free-agent capture is from {cap} ({(today - cap).days} days before {today}): "
                        "availability may have changed; re-capture before acting")
    return out, warnings


def realistic_refills(fas: list[FreeAgent], value: dict[str, float] | None = None) -> list[str]:
    """Free agents a team could actually add (not tagged out), best first by `value` (default: capture order)."""
    ok = [f for f in fas if not f.is_out]
    if value is not None:
        ok.sort(key=lambda f: -float(value.get(f.player_id, -99.0)))
    return [f.player_id for f in ok]
