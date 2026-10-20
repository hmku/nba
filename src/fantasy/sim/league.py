"""League state: which players each fantasy team rosters.

Sources:
* a draft in the DB (`draft_picks.team_slot` -> team id = draft slot). Works mid-draft (partial rosters).
* a JSON file (post-draft Yahoo sync):
    {"source": "yahoo 2026-10-20", "my_team": 1,
     "teams": {"1": {"name": "Me", "players": ["nikola-jokic", ...]}, "2": {...}, ...}}

Mid-draft rosters can be completed with *expected* picks (`complete_rosters`): the remaining pick
order is replayed with every other team taking the next player of a noisy market (ADP) order that
keeps its starting lineup fillable ("respects positions loosely"), and my team taking either the same
market order or an explicit plan (the optimizer's planned picks). Completed picks are labelled in
`LeagueState.projected` so outputs never present them as real.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from fantasy.draft.availability import sample_market_positions, sd_mult_for
from fantasy.draft.order import order_for_draft
from fantasy.draft.roster import max_starting_matching
from fantasy.league.settings import LeagueSettings


@dataclass
class LeagueState:
    rosters: dict[int, list[str]]  # team id -> player ids (actual + projected)
    my_team: int
    source: str
    names: dict[int, str] = field(default_factory=dict)
    projected: dict[int, list[str]] = field(default_factory=dict)  # team id -> players added by completion
    draft_id: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def team_ids(self) -> list[int]:
        return sorted(self.rosters)

    def team_name(self, t: int) -> str:
        return self.names.get(t) or f"Team {t}"

    def roster_sizes(self) -> dict[int, int]:
        return {t: len(p) for t, p in self.rosters.items()}

    def copy(self) -> LeagueState:
        return LeagueState(
            rosters={t: list(p) for t, p in self.rosters.items()}, my_team=self.my_team, source=self.source,
            names=dict(self.names), projected={t: list(p) for t, p in self.projected.items()},
            draft_id=self.draft_id, warnings=list(self.warnings),
        )

    def with_change(self, team: int, add: list[str], drop: list[str]) -> LeagueState:
        new = self.copy()
        for pid in drop:
            if pid not in new.rosters[team]:
                raise ValueError(f"{pid} is not on team {team}")
            new.rosters[team].remove(pid)
        for pid in add:
            owner = next((t for t, ps in new.rosters.items() if pid in ps), None)
            if owner is not None:
                raise ValueError(f"{pid} is already rostered by team {owner}")
            new.rosters[team].append(pid)
        return new

    def to_json(self) -> dict:
        return {"source": self.source, "my_team": self.my_team, "draft_id": self.draft_id,
                "teams": {str(t): {"name": self.team_name(t), "players": self.rosters[t],
                                   "projected_players": self.projected.get(t, [])} for t in self.team_ids}}


def from_draft(draft: dict, picks: list[dict], settings: LeagueSettings | None = None) -> LeagueState:
    n = int(draft["num_teams"])
    rosters: dict[int, list[str]] = {t: [] for t in range(1, n + 1)}
    for p in sorted(picks, key=lambda r: r["overall_pick"]):
        if p.get("player_id"):
            rosters[int(p["team_slot"])].append(p["player_id"])
    names = {int(k): v for k, v in ((draft.get("config") or {}).get("team_names") or {}).items()}
    return LeagueState(rosters=rosters, my_team=int(draft["my_slot"]), source=f"draft:{draft['draft_id']}",
                       names=names, draft_id=draft["draft_id"])


def from_json(path: Path | str) -> LeagueState:
    raw = json.loads(Path(path).read_text())
    rosters, names = {}, {}
    for k, v in raw["teams"].items():
        t = int(k)
        if isinstance(v, list):
            rosters[t] = [str(x) for x in v]
        else:
            rosters[t] = [str(x) for x in v["players"]]
            if v.get("name"):
                names[t] = str(v["name"])
    return LeagueState(rosters=rosters, my_team=int(raw.get("my_team", 1)),
                       source=str(raw.get("source") or f"json:{Path(path).name}"), names=names,
                       draft_id=raw.get("draft_id"))


def parse_team_names(raw: object, num_teams: int) -> dict[int, str]:
    """Team names by slot from a list (index 0 = slot 1, e.g. Yahoo's round-1 draft order) or a
    {slot: name} mapping, optionally wrapped as {"source": ..., "names": <list|mapping>}."""
    if isinstance(raw, dict) and "names" in raw:
        raw = raw["names"]
    if isinstance(raw, list):
        names = {i + 1: str(n) for i, n in enumerate(raw)}
    elif isinstance(raw, dict):
        names = {int(k): str(v) for k, v in raw.items()}
    else:
        raise ValueError("team names must be a list (slot order) or a {slot: name} object")
    bad = sorted(t for t in names if not 1 <= t <= num_teams)
    if bad:
        raise ValueError(f"team slots out of range 1..{num_teams}: {bad}")
    if len(set(names.values())) != len(names):
        raise ValueError("duplicate team names")
    return names


def league_json_from_draft(draft: dict, picks: list[dict], names: dict[int, str] | None = None,
                           source: str | None = None) -> dict:
    """The `--league-file` JSON for a finished (or partial) draft: team id = draft slot, players in
    pick order, names from `names` (else drafts.config.team_names)."""
    st = from_draft(draft, picks)
    if names:
        st.names = {**st.names, **names}
    out = st.to_json()
    out["source"] = source or st.source
    out["picks_recorded"] = sum(1 for p in picks if p.get("player_id"))
    out["picks_total"] = int(draft["num_teams"]) * int(draft["num_rounds"])
    for t in out["teams"].values():
        t.pop("projected_players", None)
    return out


def remaining_order(draft: dict, picks: list[dict]) -> list[tuple[int, int]]:
    """(overall, team slot) of every pick not yet recorded."""
    done = {int(p["overall_pick"]) for p in picks if p.get("player_id")}
    return [(pk.overall, pk.slot) for pk in order_for_draft(draft) if pk.overall not in done]


def complete_rosters(
    state: LeagueState,
    remaining: list[tuple[int, int]],
    values: pd.DataFrame,
    market: list[str],
    settings: LeagueSettings,
    seed: int = 0,
    noise: float = 1.0,
    my_plan: list[str] | None = None,
    reserved: set[str] | None = None,
) -> LeagueState:
    """Fill the remaining picks with expected picks (see module doc). `market` is the market order
    (fantasy.model.valuation.market_order); `my_plan` (player ids, in pick order) is used for my team's
    picks while those players are still available, then market order. `reserved` players are never
    taken by other teams (e.g. the candidate I am evaluating for my next pick)."""
    rng = np.random.default_rng(seed)
    new = state.copy()
    taken = {p for ps in new.rosters.values() for p in ps}
    mrank = np.arange(1, len(market) + 1, dtype=float)
    # same opponent model as `draft simulate`: players without a market ADP are noisier (season-sim-0.3.0)
    has_adp = values["adp"].reindex(market).notna().to_numpy() if "adp" in values.columns else True
    sampled = sample_market_positions(mrank, rng, sd_mult_for(has_adp), noise)
    order = [market[i] for i in np.argsort(sampled, kind="stable")]
    slots = settings.starting_slots
    pos = {pid: list(values.loc[pid, "positions"]) if pid in values.index else [] for pid in order}
    left = {t: sum(1 for _, s in remaining if s == t) for t in new.rosters}
    plan = list(my_plan or [])
    for _, slot in remaining:
        roster = new.rosters.setdefault(slot, [])
        left[slot] -= 1
        choice = None
        if slot == new.my_team:
            while plan and choice is None:
                cand = plan.pop(0)
                if cand not in taken:
                    choice = cand
        if choice is None:
            cur = [pos.get(p) or (list(values.loc[p, "positions"]) if p in values.index else []) for p in roster]
            fallback = None
            for pid in order:
                if pid in taken or (reserved and slot != new.my_team and pid in reserved):
                    continue
                fallback = fallback or pid
                f, _ = max_starting_matching(cur + [pos[pid]], slots)
                if len(slots) - f <= left[slot]:
                    choice = pid
                    break
            choice = choice or fallback
        if choice is None:
            break
        taken.add(choice)
        roster.append(choice)
        new.projected.setdefault(slot, []).append(choice)
    n = sum(len(v) for v in new.projected.values())
    new.source = state.source + f"+expected_picks({n})"
    return new
