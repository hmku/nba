"""Resolve free-text player names (as seen on Yahoo) to projection player_ids."""

from __future__ import annotations

import difflib
import re

import pandas as pd

from fantasy.data.ids import slugify


def slug(name: str) -> str:
    """Canonical player-id slug (same as `fantasy.data.ids.slugify`, the projections' id scheme)."""
    return slugify(name)


class NameResolutionError(ValueError):
    def __init__(self, query: str, candidates: list[tuple[str, str]]):
        self.query = query
        self.candidates = candidates
        msg = f"could not uniquely resolve '{query}'"
        if candidates:
            msg += "; candidates: " + ", ".join(f"{n} ({p})" for p, n in candidates)
        super().__init__(msg)


def resolve_player(query: str, values: pd.DataFrame, cutoff: float = 0.82) -> str:
    """Return player_id for `query` (a player_id or a display name). Raises NameResolutionError."""
    q = query.strip()
    if q in values.index:
        return q
    qs = slug(q)
    if qs in values.index:
        return qs
    name_slugs = {pid: slug(str(n)) for pid, n in values["name"].items()}
    exact = [pid for pid, s in name_slugs.items() if s == qs]
    if len(exact) == 1:
        return exact[0]
    # drop suffixes (jr, sr, ii, iii) and retry
    def strip_sfx(s: str) -> str:
        return re.sub(r"-(jr|sr|ii|iii|iv)$", "", s)
    base = [pid for pid, s in name_slugs.items() if strip_sfx(s) == strip_sfx(qs)]
    if len(base) == 1:
        return base[0]
    close = difflib.get_close_matches(qs, list(name_slugs.values()), n=4, cutoff=cutoff)
    inv: dict[str, list[str]] = {}
    for pid, s in name_slugs.items():
        inv.setdefault(s, []).append(pid)
    cands = [pid for c in close for pid in inv[c]]
    if len(cands) == 1:
        return cands[0]
    raise NameResolutionError(q, [(p, str(values.loc[p, "name"])) for p in (exact or base or cands)])
