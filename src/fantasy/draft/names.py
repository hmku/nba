"""Resolve free-text player names (as seen on Yahoo) to projection player_ids."""

from __future__ import annotations

import difflib
import re

import pandas as pd

from fantasy.data.ids import ALIASES, canonical_id, slugify


def slug(name: str) -> str:
    """Canonical player-id slug (same as `fantasy.data.ids.slugify`, the projections' id scheme)."""
    return slugify(name)


def unknown_player_id(name: str) -> str:
    """Id for a drafted player who is not in our projections: ``unknown:<canonical id>``. Identical to the
    web's ``unknownId`` (web/src/lib/agentPure.ts), so the same unknown player recorded by the CLI and by
    the dashboard/agent API is the same id (and caught as a duplicate)."""
    return f"unknown:{canonical_id(name)}"


class NameResolutionError(ValueError):
    def __init__(self, query: str, candidates: list[tuple[str, str]]):
        self.query = query
        self.candidates = candidates
        msg = f"could not uniquely resolve '{query}'"
        if candidates:
            msg += "; candidates: " + ", ".join(f"{n} ({p})" for p, n in candidates)
        super().__init__(msg)


_ABBREV = re.compile(r"^([A-Za-z])\.\s*(\S.*)$")


def _strip_sfx(s: str) -> str:
    return re.sub(r"-(jr|sr|ii|iii|iv)$", "", s)


def _abbreviated(query: str, name_slugs: dict[str, str]) -> list[str]:
    """Candidates for an abbreviated first name as Yahoo prints it ("N. Alexander-Walker"): same
    last name (suffixes ignored) and a first name starting with that initial."""
    m = _ABBREV.match(query)
    if not m:
        return []
    initial, rest = m.group(1).lower(), _strip_sfx(slug(m.group(2)))
    return [pid for pid, s in name_slugs.items()
            if s.startswith(initial) and _strip_sfx(s).endswith("-" + rest)]


def resolve_player(query: str, values: pd.DataFrame, cutoff: float = 0.82, fuzzy: bool = True) -> str:
    """Return player_id for `query` (a player_id or a display name). Raises NameResolutionError.

    Order: exact id / slug / known alias / exact name slug / name without suffix (Jr., III) /
    abbreviated first name ("N. Alexander-Walker") / (only if `fuzzy`) difflib close match."""
    q = query.strip()
    if q in values.index:
        return q
    qs = slug(q)
    if qs in values.index:
        return qs
    qa = ALIASES.get(qs, qs)  # known spelling variants ("Cam Johnson" -> cameron-johnson), as the web does
    if qa in values.index:
        return qa
    name_slugs = {pid: slug(str(n)) for pid, n in values["name"].items()}
    exact = [pid for pid, s in name_slugs.items() if s == qs]
    if len(exact) == 1:
        return exact[0]
    # drop suffixes (jr, sr, ii, iii) and retry
    base = [pid for pid, s in name_slugs.items() if _strip_sfx(s) == _strip_sfx(qs)]
    if len(base) == 1:
        return base[0]
    abbr = _abbreviated(q, name_slugs)
    if len(abbr) == 1:
        return abbr[0]
    if not fuzzy:
        raise NameResolutionError(q, [(p, str(values.loc[p, "name"])) for p in (exact or base or abbr)])
    close = difflib.get_close_matches(qs, list(name_slugs.values()), n=4, cutoff=cutoff)
    inv: dict[str, list[str]] = {}
    for pid, s in name_slugs.items():
        inv.setdefault(s, []).append(pid)
    cands = [pid for c in close for pid in inv[c]]
    if len(cands) == 1:
        return cands[0]
    raise NameResolutionError(q, [(p, str(values.loc[p, "name"])) for p in (exact or base or abbr or cands)])
