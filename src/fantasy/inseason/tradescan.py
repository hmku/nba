"""T-008: trade scan. Enumerate 1-for-1 and 2-for-1 trades with every other team, screen them cheaply, then run
paired position-aware season sims for the shortlist under two projection sets.

1. **Enumerate** for each partner: 1-for-1 (13 x 13), I give 2 / get 1 (78 x 13) and I give 1 / get 2 (13 x 78).
   Rosters go back to 13: a short roster adds the best *realistic* free agent of the latest Yahoo capture (not
   tagged out; best by default-blend value), a long roster drops its lowest-value player.
2. **Screen** (cheap, deterministic): an all-play category proxy. Each team's mean and sd of every weekly category
   come from the base season sim (regular weeks). A trade changes a team's means by the change in a linear weekly
   line, sum_i start_share(rank_i) x games/week_i x per-game line_i (percentages from makes/attempts), where
   games/week = the NBA team's regular-season games per fantasy week x gp/82 and start_share(rank) is measured on
   the base league by the position-aware lineup Monte Carlo (`lineup.simulate_lineups`, gp/82 iid availability:
   a PRIOR on availability, the lineup rule itself is exact). Score = expected categories won per week against the
   average other team, sum_c Phi(sign_c (mu_me - mu_u) / sqrt(sd_me^2 + sd_u^2)) averaged over u. Keep trades that
   raise my score and that the partner might accept: by rankings (sum of valuation value given minus received,
   how a human reading rankings judges it) the partner loses at most `naive_tol`, AND his proxy score falls by
   at most `partner_tol` (both PRIORS); then the top K per partner by my proxy gain.
3. **Simulate** the shortlist: paired position-aware season sims (common random numbers), under the default blend
   projections and under the conservative `market` projections (`fantasy data alt-projections --blend market`).
   Both teams' ΔP(playoffs), ΔP(title), Δ category wins ± paired SE.
4. **Flag** `helps_both`: the trade helps me (season verdict "helps": one of ΔP(playoffs)/ΔP(title) > 2 SE, neither
   < -2 SE) under BOTH projection sets.
5. **Decomposition hooks**: games of the players in and out in each playoff week (schedule effect; the reviewer
   found about half of the Murray title gain came from Denver's 4-game playoff weeks), regular-season games per
   week, and the blend - market difference (projection disagreement).

Trades are simulated as effective for the whole simulated season (the 2-day review is ignored): the error is at most
the few days of week 1 before the trade would clear.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import norm

from fantasy.inseason.lineup import PlayerSpec, simulate_lineups
from fantasy.inseason.streamscan import season_verdict
from fantasy.inseason.trade import Trade, apply_trade, naive_value
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import SEASON_GAMES, STATS, parse_positions
from fantasy.sim.engine import SeasonSimulator, SimResult
from fantasy.sim.league import LeagueState
from fantasy.sim.report import paired_delta
from fantasy.sim.schedule import FantasyWeek, NBASchedule

SI = {s: i for i, s in enumerate(STATS)}


@dataclass
class ScanParams:
    top_per_team: int = 3
    max_trades: int = 33
    partner_tol: float = 0.05  # PRIOR: partner's proxy may drop this much (cats/week) and still be "acceptable"
    naive_tol: float = 0.5  # PRIOR: partner may lose this much summed valuation value ("by rankings")
    kinds: tuple[str, ...] = ("1-1", "2-1", "1-2")
    sims: int = 2000
    seed: int = 0
    sig: float = 2.0
    start_share_sims: int = 40
    partners: tuple[int, ...] | None = None  # None = every other team

    def to_dict(self) -> dict:
        return {**asdict(self), "kinds": list(self.kinds),
                "partners": list(self.partners) if self.partners else None}


# ---------------------------------------------------------------- screen
def games_per_week(nba: NBASchedule, calendar: list[FantasyWeek], regular: list[int]) -> dict[str, float]:
    days = [d for w in calendar if w.week in regular for d in w.days()]
    idx, M = nba.plays_matrix(days)
    n_weeks = max(1, len(regular))
    return {t: float(M[i].sum()) / n_weeks for t, i in idx.items()}


def start_shares(league: LeagueState, proj: pd.DataFrame, values: pd.DataFrame, settings: LeagueSettings,
                 nba: NBASchedule, days: list[date], n_sims: int = 40, seed: int = 0) -> np.ndarray:
    """Mean share of a player's available games that he starts, by roster rank (0 = best value), over all teams."""
    from fantasy.inseason.lineup import team_days

    plays = team_days(nba.games, days)
    val = values["value"]
    num, den = np.zeros(20), np.zeros(20)
    for t in league.team_ids:
        specs = []
        for p in league.rosters[t]:
            if p not in proj.index:
                continue
            r = proj.loc[p]
            out = str(r["status"]) in ("out", "out_for_season")
            specs.append(PlayerSpec(p, str(r["name"]), str(r["team"]), parse_positions(r["positions"]),
                                    float(val.get(p, -99.0)), 0.0 if out else min(1.0, float(r["gp"]) / SEASON_GAMES)))
        res = simulate_lineups(specs, settings.starting_slots, days, plays, n_sims=n_sims, seed=seed)
        for k, row in enumerate(res.per_player()):
            if k < 20 and row["exp_available"] > 0:
                num[k] += row["exp_starts"]
                den[k] += row["exp_available"]
    sh = np.where(den > 0, num / np.maximum(den, 1e-9), 0.0)
    last = max((k for k in range(20) if den[k] > 0), default=0)
    sh[last + 1:] = sh[last] if last else 0.0
    return sh


class Screen:
    """All-play category proxy anchored on a base season sim (see module docstring, step 2)."""

    def __init__(self, base: SimResult, league: LeagueState, proj: pd.DataFrame, values: pd.DataFrame,
                 settings: LeagueSettings, gpw: dict[str, float], shares: np.ndarray) -> None:
        self.cats = settings.cats
        self.sign = np.array([1.0 if c.higher_is_better else -1.0 for c in self.cats])
        wi = [base.week_index(w) for w in base.regular_weeks]
        tot = base.totals[:, wi].astype(float)  # (N, w, T, C)
        self.team_ids = list(base.team_ids)
        self.mu = tot.mean(axis=(0, 1))  # (T, C)
        self.sd = tot.std(axis=(0, 1))
        self.val = values["value"]
        self.shares = shares
        self.line: dict[str, np.ndarray] = {}
        for p in proj.index:
            r = proj.loc[p]
            g = gpw.get(str(r["team"]), 0.0) * min(1.0, float(r["gp"]) / SEASON_GAMES)
            self.line[p] = g * r[STATS].to_numpy(dtype=float)
        self.zero = np.zeros(len(STATS))
        self.base_lines = {t: self.team_line(league.rosters[t]) for t in self.team_ids}

    def team_line(self, roster: Sequence[str]) -> np.ndarray:
        ps = sorted(roster, key=lambda p: -float(self.val.get(p, -99.0)))
        out = np.zeros(len(STATS))
        for k, p in enumerate(ps):
            out += self.shares[min(k, len(self.shares) - 1)] * self.line.get(p, self.zero)
        return out

    def cat_values(self, line: np.ndarray) -> np.ndarray:
        out = []
        for c in self.cats:
            if c.kind == "count":
                out.append(line[SI[c.stat]])
            else:
                a = line[SI[c.den]]
                out.append(line[SI[c.num]] / a if a > 0 else 0.0)
        return np.array(out)

    def score(self, changed: dict[int, list[str]]) -> dict[int, float]:
        """Expected categories won per week vs the average other team, for each team in `changed`."""
        mu = self.mu.copy()
        for t, roster in changed.items():
            i = self.team_ids.index(t)
            mu[i] = self.mu[i] + self.cat_values(self.team_line(roster)) - self.cat_values(self.base_lines[t])
        out = {}
        for t in changed:
            i = self.team_ids.index(t)
            others = [j for j in range(len(self.team_ids)) if j != i]
            z = self.sign * (mu[i] - mu[others]) / np.sqrt(np.maximum(self.sd[i] ** 2 + self.sd[others] ** 2, 1e-12))
            out[t] = float(norm.cdf(z).sum(axis=1).mean())
        return out


def enumerate_trades(league: LeagueState, my_team: int, kinds: Sequence[str],
                     partners: Sequence[int] | None = None) -> list[Trade]:
    mine = league.rosters[my_team]
    out = []
    for t in league.team_ids:
        if t == my_team or (partners and t not in partners):
            continue
        theirs = league.rosters[t]
        if "1-1" in kinds:
            out += [Trade(t, [g], [r]) for g in mine for r in theirs]
        if "2-1" in kinds:
            out += [Trade(t, list(g), [r]) for g in itertools.combinations(mine, 2) for r in theirs]
        if "1-2" in kinds:
            out += [Trade(t, [g], list(r)) for g in mine for r in itertools.combinations(theirs, 2)]
    return out


def screen_trades(screen: Screen, league: LeagueState, my_team: int, values: pd.DataFrame, roster_size: int,
                  fa_pool: Sequence[str], params: ScanParams) -> tuple[list[dict], int]:
    """Proxy deltas for every enumerated trade; returns (shortlist rows, number enumerated)."""
    base = screen.score({my_team: league.rosters[my_team], **{t: league.rosters[t] for t in league.team_ids}})
    trades = enumerate_trades(league, my_team, params.kinds, params.partners)
    val = values["value"]
    by_team: dict[int, list[dict]] = {}
    for tr in trades:
        new, moves = apply_trade(league, my_team, tr, values, roster_size, fa_pool=fa_pool)
        s = screen.score({my_team: new.rosters[my_team], tr.partner: new.rosters[tr.partner]})
        d_me, d_p = s[my_team] - base[my_team], s[tr.partner] - base[tr.partner]
        naive_p = float(val.reindex(tr.give).fillna(0).sum() - val.reindex(tr.get).fillna(0).sum())
        if d_me <= 0 or d_p < -params.partner_tol or naive_p < -params.naive_tol:
            continue
        by_team.setdefault(tr.partner, []).append({"trade": tr, "league": new, "moves": moves,
                                                   "proxy_me": d_me, "proxy_partner": d_p, "naive_partner": naive_p})
    rows = []
    for _t, lst in sorted(by_team.items()):
        lst.sort(key=lambda r: -r["proxy_me"])
        rows += lst[: params.top_per_team]
    rows.sort(key=lambda r: -r["proxy_me"])
    return rows[: params.max_trades], len(trades)


# ---------------------------------------------------------------- decomposition hooks
def week_games(nba: NBASchedule, calendar: list[FantasyWeek], team: str, weeks: Sequence[int]) -> list[int]:
    out = []
    for w in calendar:
        if w.week in weeks:
            idx, M = nba.plays_matrix(w.days())
            out.append(int(M[idx[team]].sum()) if team in idx else 0)
    return out


def schedule_hooks(tr: Trade, proj: pd.DataFrame, nba: NBASchedule, calendar: list[FantasyWeek],
                   playoff_weeks: Sequence[int], gpw: dict[str, float]) -> dict:
    """Playoff-week games (raw schedule) and regular-season games/week of the players in and out."""
    def info(p: str) -> dict:
        team = str(proj.loc[p, "team"]) if p in proj.index else ""
        g = week_games(nba, calendar, team, playoff_weeks)
        return {"player_id": p, "name": str(proj.loc[p, "name"]) if p in proj.index else p, "nba_team": team,
                "playoff_week_games": g, "regular_games_per_week": round(gpw.get(team, 0.0), 2)}
    give, get = [info(p) for p in tr.give], [info(p) for p in tr.get]
    n = len(playoff_weeks)
    tot = lambda xs: [sum(x["playoff_week_games"][k] for x in xs) for k in range(n)]  # noqa: E731
    return {"playoff_weeks": list(playoff_weeks), "give": give, "get": get,
            "d_playoff_games_by_week": [b - a for a, b in zip(tot(give), tot(get), strict=True)],
            "note": "raw schedule counts (before availability); the trade's net playoff-week games (get - give) is "
                    "the schedule part of a title gain that the regular season does not show"}


# ---------------------------------------------------------------- simulate
def simulate_shortlist(rows: list[dict], league: LeagueState, my_team: int, sims: dict[str, SeasonSimulator],
                       sig: float = 2.0) -> None:
    """Adds `sim[name] = {me, partner}` paired deltas and verdicts for every projection set (in place)."""
    for name, sim in sims.items():
        base = sim.run(league)
        for r in rows:
            alt = sim.run(r["league"])
            me, pt = paired_delta(base, alt, my_team), paired_delta(base, alt, r["trade"].partner)
            r.setdefault("sim", {})[name] = {"me": me, "partner": pt, "verdict": season_verdict(me, sig)}


def trade_rows(rows: list[dict], values: pd.DataFrame, proj: pd.DataFrame, nba: NBASchedule,
               calendar: list[FantasyWeek], playoff_weeks: Sequence[int], gpw: dict[str, float],
               league: LeagueState, sets: Sequence[str]) -> list[dict]:
    names = values["name"]
    out = []
    for r in rows:
        tr = r["trade"]
        sim = r.get("sim", {})
        both = bool(sets) and all(sim.get(s, {}).get("verdict") == "helps" for s in sets)
        row = {
            "trade": tr.label(names), "partner": tr.partner, "partner_name": league.team_name(tr.partner),
            "give": [str(names.get(p, p)) for p in tr.give], "get": [str(names.get(p, p)) for p in tr.get],
            "give_ids": list(tr.give), "get_ids": list(tr.get),
            "refill_moves": {str(t): {k: [str(names.get(p, p)) for p in v] for k, v in m.items()}
                             for t, m in r["moves"].items()},
            "screen": {"proxy_d_cats_per_week_me": round(r["proxy_me"], 4),
                       "proxy_d_cats_per_week_partner": round(r["proxy_partner"], 4),
                       "naive_value_partner_gain": round(r["naive_partner"], 3)},
            "naive": {"give": naive_value(values, tr.give), "get": naive_value(values, tr.get)},
            "sim": sim,
            "helps_both": both,
            "decomposition": {"schedule": schedule_hooks(tr, proj, nba, calendar, playoff_weeks, gpw)},
        }
        if len(sets) == 2 and all(s in sim for s in sets):
            a, b = sets
            row["decomposition"]["projection_disagreement"] = {
                k: round(sim[a]["me"][k]["delta"] - sim[b]["me"][k]["delta"], 4) for k in ("p_playoffs", "p_title")}
        out.append(row)

    def key(x: dict) -> tuple:
        ts = [x["sim"][s]["me"]["p_title"]["delta"] for s in sets if s in x["sim"]]
        return (not x["helps_both"], -(min(ts) if ts else -1.0))

    out.sort(key=key)
    return out
