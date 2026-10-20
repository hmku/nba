"""Trade evaluation with the season simulator (paired: same seeds before/after).

A trade changes two rosters at once. Rosters are brought back to the drafted size: a team left
short adds the best undrafted player by projected value (replacement), a team over the limit drops
its lowest-value player. Both teams' P(playoffs)/P(title)/category wins are reported with paired SEs
(common random numbers), plus a naive "rankings" check (sum of projected value and ADP), because a
human trade partner will mostly judge by rankings.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from fantasy.sim.engine import SeasonSimulator, SimResult
from fantasy.sim.league import LeagueState
from fantasy.sim.report import paired_delta


@dataclass
class Trade:
    partner: int
    give: list[str]  # my players going out
    get: list[str]  # partner's players coming in

    def label(self, names: pd.Series) -> str:
        n = [str(names.get(p, p)) for p in self.give]
        m = [str(names.get(p, p)) for p in self.get]
        return f"{' + '.join(n)} -> {' + '.join(m)} (team {self.partner})"


def rostered(league: LeagueState) -> set[str]:
    return {p for ps in league.rosters.values() for p in ps}


def apply_trade(league: LeagueState, my_team: int, trade: Trade, values: pd.DataFrame, roster_size: int,
                refill_skip: int = 0, fa_pool: Sequence[str] | None = None) -> tuple[LeagueState, dict]:
    """New league with the trade applied and both rosters refilled/trimmed to `roster_size`.

    A short roster adds the best undrafted player after skipping the `refill_skip` best (0 = optimistic:
    we win the waiver claim; 11 = the 12th-best, i.e. everyone else claimed first). `fa_pool` (best first,
    e.g. the realistic free agents of the latest Yahoo capture) restricts the refill candidates."""
    mine, theirs = league.rosters[my_team], league.rosters[trade.partner]
    for p in trade.give:
        if p not in mine:
            raise ValueError(f"{p} is not on team {my_team}")
    for p in trade.get:
        if p not in theirs:
            raise ValueError(f"{p} is not on team {trade.partner}")
    new = league.copy()  # (LeagueState.with_change refuses players rostered elsewhere, so swap directly)
    new.rosters[my_team] = [p for p in mine if p not in trade.give] + list(trade.get)
    new.rosters[trade.partner] = [p for p in theirs if p not in trade.get] + list(trade.give)
    val = values["value"]
    taken = rostered(new)
    order = list(fa_pool) if fa_pool is not None else list(values.index)  # values sorted best first
    pool = [p for p in order if p not in taken][refill_skip:]
    moves: dict[int, dict[str, list[str]]] = {}
    for t in (my_team, trade.partner):
        ps = new.rosters[t]
        while len(ps) > roster_size:
            worst = min(ps, key=lambda p: float(val.get(p, -99.0)))
            ps.remove(worst)
            moves.setdefault(t, {"add": [], "drop": []})["drop"].append(worst)
        while len(ps) < roster_size and pool:
            add = pool.pop(0)
            ps.append(add)
            moves.setdefault(t, {"add": [], "drop": []})["add"].append(add)
    return new, moves


def naive_value(values: pd.DataFrame, pids: Sequence[str]) -> dict:
    v = values.reindex(list(pids))
    return {"value": round(float(v["value"].fillna(0).sum()), 3),
            "adp": [None if pd.isna(a) else round(float(a), 1) for a in v["adp"]]}


def evaluate_trades(
    sim: SeasonSimulator,
    league: LeagueState,
    my_team: int,
    trades: Sequence[Trade],
    values: pd.DataFrame,
    roster_size: int,
    base: SimResult | None = None,
    refill_skip: int = 0,
) -> tuple[SimResult, list[dict]]:
    """Simulate each trade against the same base run. Returns (base, rows)."""
    base = base or sim.run(league)
    names = values["name"]
    rows = []
    for tr in trades:
        new, moves = apply_trade(league, my_team, tr, values, roster_size, refill_skip)
        alt = sim.run(new)
        rows.append({
            "trade": tr.label(names), "partner": tr.partner, "give": list(tr.give), "get": list(tr.get),
            "refill_moves": {str(t): {k: [str(names.get(p, p)) for p in v] for k, v in m.items()} for t, m in moves.items()},
            "me": paired_delta(base, alt, my_team),
            "partner_delta": paired_delta(base, alt, tr.partner),
            "naive": {"give": naive_value(values, tr.give), "get": naive_value(values, tr.get)},
        })
    return base, rows
