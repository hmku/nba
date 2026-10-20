"""Daily and weekly lineup planner: matchup-aware weights, fixed-point iteration, baselines and evaluation.

Policy for the remaining days of the matchup week:
1. Baseline: my lineup and the opponent's are set daily by an exact position-aware assignment using a generic
   season value (valuation G-score `value`; players with a game always beat players without one).
2. From the baseline plans compute the weekly category margins (value.cat_state) and weights; a player's value on a
   day = P(plays) * per-game marginal value; re-solve every day's assignment for me. Repeat until my plan stops
   changing (fixed point, max `iterations`). The opponent keeps the generic policy (ASSUMPTION: an attentive
   manager starting his best players with games; he does not react to my lineup).
3. Evaluate my plan and two baselines (generic season-value lineups; the current Yahoo lineup held all week) by
   the analytic normal model and by Monte Carlo with common random numbers (paired SEs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from fantasy.league.settings import Category, LeagueSettings
from fantasy.lineup.optimize import DaySlots, solve_day
from fantasy.lineup.players import TAG_PRIORS, RosterPlayer
from fantasy.lineup.value import (
    CatState,
    TeamWeek,
    cat_state,
    draw,
    error_sds,
    per_game_value,
    score_matchup,
    simulate_totals,
)
from fantasy.model.lines import SEASON_GAMES, STATS, ProjectionSet
from fantasy.model.valuation import compute_values
from fantasy.sim.schedule import NBASchedule

LINEUP_MODEL_VERSION = "lineup-0.1.0"
GAME_FLOOR = 1e-5  # a player with a game is worth at least this (so he never sits for a player without one)


@dataclass
class LineupParams:
    iterations: int = 6
    n_sims: int = 20000
    seed: int = 0
    protect_ratios: bool = False  # allow benching a player with a game whose marginal value is negative
    tag_priors: dict[str, float] = field(default_factory=lambda: dict(TAG_PRIORS))

    def to_dict(self) -> dict:
        from fantasy.lineup.players import REST_SHARE
        return {"iterations": self.iterations, "n_sims": self.n_sims, "seed": self.seed,
                "protect_ratios": self.protect_ratios, "tag_priors": self.tag_priors, "rest_share": REST_SHARE,
                "game_floor": GAME_FLOOR, "objective": "expected_categories_won"}


def games_matrix(players: list[RosterPlayer], days: list[date], nba: NBASchedule) -> tuple[np.ndarray, list[str]]:
    idx, M = nba.plays_matrix(days)
    G = np.zeros((len(players), len(days)), dtype=bool)
    warnings = []
    for i, p in enumerate(players):
        if p.team in idx:
            G[i] = M[idx[p.team]]
        else:
            warnings.append(f"{p.player_id}: NBA team {p.team!r} not in the schedule -> no games")
    return G, warnings


def p_matrix(players: list[RosterPlayer], G: np.ndarray) -> np.ndarray:
    P = np.zeros(G.shape)
    for i, p in enumerate(players):
        first = True
        for d in range(G.shape[1]):
            if G[i, d]:
                P[i, d] = p.p_play(first_game=first)
                first = False
    return P


def team_week(players: list[RosterPlayer], proj: pd.DataFrame, P: np.ndarray, acc: dict | None) -> TeamWeek:
    S = np.zeros((len(players), len(STATS)))
    mpg = np.zeros(len(players))
    for i, p in enumerate(players):
        if p.player_id in proj.index:
            S[i] = proj.loc[p.player_id, STATS].to_numpy(dtype=float)
            mpg[i] = float(proj.loc[p.player_id, "mpg"]) if "mpg" in proj.columns else 30.0
    vol, fg, ft = error_sds(mpg)
    a = np.array([float((acc or {}).get(s, 0.0)) for s in STATS])
    return TeamWeek([p.player_id for p in players], S, P, a, vol, fg, ft)


def daily_plan(
    players: list[RosterPlayer], values: np.ndarray, P: np.ndarray, slots: DaySlots, protect_ratios: bool = False,
) -> tuple[np.ndarray, list[list[str]]]:
    """Solve each day; values (n, D) = expected value if started. Returns (X started (n, D), labels per day)."""
    n, D = P.shape
    start = set(slots.starting)
    X = np.zeros((n, D), dtype=bool)
    labels: list[list[str]] = []
    current = [p.slot for p in players]
    pos = [p.positions for p in players]
    startable = [not p.is_out for p in players]
    il_ok = [p.il_eligible for p in players]
    for d in range(D):
        has = P[:, d] > 0
        v = values[:, d] if protect_ratios else np.maximum(values[:, d], 0.0)
        v = np.where(has, v + GAME_FLOOR, 0.0)
        lab = solve_day(pos, v, startable, il_ok, slots, current)
        labels.append(lab)
        X[:, d] = [lab_i in start and h for lab_i, h in zip(lab, has, strict=True)]
        current = lab
    return X, labels


def generic_per_game_values(proj: ProjectionSet, settings: LeagueSettings) -> dict[str, float]:
    """Per-game G-score (valuation with availability 1: gp = 82) -- the "start your best players" ranking used by
    the opponent policy and the naive baseline (season value would penalise injury-prone players for games they
    are not missing this week)."""
    df = proj.df.copy()
    df["gp"] = float(SEASON_GAMES)
    df["gp_sd"] = 0.0
    df["playoff_mult"] = 1.0
    values, _, _ = compute_values(ProjectionSet(df=df, path=proj.path, sha256=proj.sha256), settings)
    return {str(k): float(v) for k, v in values["value"].items()}


def generic_values(players: list[RosterPlayer], generic: dict[str, float]) -> np.ndarray:
    """Positive per-game generic value preserving the order of the season value."""
    v = np.array([generic.get(p.player_id, np.nan) for p in players], dtype=float)
    lo = np.nanmin(v) if np.isfinite(v).any() else 0.0
    return np.where(np.isfinite(v), v - lo + 1e-3, 0.0)


@dataclass
class TeamSide:
    players: list[RosterPlayer]
    G: np.ndarray
    P: np.ndarray
    tw: TeamWeek


@dataclass
class WeekResult:
    X: np.ndarray
    labels: list[list[str]]
    values: np.ndarray  # (n, D) matchup-aware value if started
    per_game: np.ndarray  # (n,)
    contrib: np.ndarray  # (n, n_cats)
    state: CatState  # weights used for the final plan
    final_state: CatState  # moments of the final plan
    iterations: int
    converged: bool


def optimize_week(
    me: TeamSide, opp_X: np.ndarray, opp: TeamSide, cats: list[Category], slots: DaySlots,
    generic: dict[str, float], params: LineupParams,
) -> tuple[WeekResult, np.ndarray, list[list[str]]]:
    """Fixed-point matchup-aware plan. Returns (result, baseline X, baseline labels)."""
    gv = generic_values(me.players, generic)
    X0, lab0 = daily_plan(me.players, gv[:, None] * me.P, me.P, slots)
    X, labels = X0, lab0
    converged = False
    it = 0
    for it in range(1, params.iterations + 1):  # noqa: B007
        state = cat_state(me.tw, X, opp.tw, opp_X, cats)
        pg, contrib = per_game_value(me.tw.S, state, cats)
        values = me.P * pg[:, None]
        X_new, labels = daily_plan(me.players, values, me.P, slots, params.protect_ratios)
        if np.array_equal(X_new, X):
            converged = True
            X = X_new
            break
        X = X_new
    final = cat_state(me.tw, X, opp.tw, opp_X, cats)
    return WeekResult(X, labels, values, pg, contrib, state, final, it, converged), X0, lab0


def static_plan(me: TeamSide, slots: DaySlots) -> np.ndarray:
    """Current Yahoo lineup held all week: started iff in a starting slot now and playing."""
    start = set(slots.starting)
    on = np.array([p.slot in start and not p.is_out for p in me.players])
    return on[:, None] & (me.P > 0)


def monte_carlo(
    me: TeamSide, plans: dict[str, np.ndarray], opp: TeamSide, opp_X: np.ndarray, cats: list[Category],
    n_sims: int, seed: int,
) -> dict:
    """E[cats] and P(win matchup) per plan with SEs, and paired differences vs the first plan."""
    if n_sims <= 0:
        return {}
    dm, do = draw(me.tw, n_sims, seed, "me"), draw(opp.tw, n_sims, seed, "opp")
    opp_tot = simulate_totals(opp.tw, opp_X, do)
    out: dict = {"n_sims": n_sims, "seed": seed, "plans": {}}
    first = None
    for name, X in plans.items():
        sc = score_matchup(simulate_totals(me.tw, X, dm), opp_tot, cats)
        cats_won = sc.sum(axis=1)
        win = (cats_won > len(cats) / 2).astype(float)
        row = {"expected_cats": float(cats_won.mean()), "expected_cats_se": float(cats_won.std(ddof=1) / np.sqrt(n_sims)),
               "p_win_matchup": float(win.mean()), "p_win_matchup_se": float(win.std(ddof=1) / np.sqrt(n_sims)),
               "cat_score": {c.key: float(sc[:, j].mean()) for j, c in enumerate(cats)}}
        if first is None:
            first = (cats_won, win)
        else:
            dc, dw = first[0] - cats_won, first[1] - win
            row["optimized_minus_this"] = {
                "expected_cats": float(dc.mean()), "expected_cats_se": float(dc.std(ddof=1) / np.sqrt(n_sims)),
                "p_win_matchup": float(dw.mean()), "p_win_matchup_se": float(dw.std(ddof=1) / np.sqrt(n_sims))}
        out["plans"][name] = row
    return out


def summarize_state(st: CatState) -> dict:
    return {"expected_cats": st.expected_cats, "p_win_matchup": st.matchup_win_prob}


def day_summary(players: list[RosterPlayer], labels: list[str], P_d: np.ndarray, values_d: np.ndarray,
                slots: DaySlots, day: date, first_game: np.ndarray | None = None) -> dict:
    start = set(slots.starting)
    lineup = []
    order = {s: k for k, s in enumerate(slots.columns)}
    for i in sorted(range(len(players)), key=lambda i: (order.get(labels[i], 99), players[i].player_id)):
        p = players[i]
        lineup.append({"slot": labels[i], "player_id": p.player_id, "name": p.name, "has_game": bool(P_d[i] > 0),
                       "p_play": round(float(P_d[i]), 3), "value": round(float(values_d[i]), 5)})
    with_game = [i for i in range(len(players)) if P_d[i] > 0]
    unused = [players[i].name for i in with_game if labels[i] not in start]
    started_with_game = sum(1 for i in with_game if labels[i] in start)
    # tagged players whose tag applies today (their next game): decide at tip-off
    tip = [players[i].name for i in with_game
           if players[i].status is not None and (first_game is None or first_game[i])]
    return {"date": day.isoformat(), "decide_at_tipoff": tip, "players_with_game": len(with_game), "starting_slots": len(slots.starting),
            "started_with_game": started_with_game, "unused_starts": len(unused), "unused_players": unused,
            "empty_slots": len(slots.starting) - started_with_game, "lineup": lineup}


def changes_vs_current(players: list[RosterPlayer], labels: list[str]) -> list[dict]:
    return [{"player_id": p.player_id, "name": p.name, "from": p.slot, "to": lab}
            for p, lab in zip(players, labels, strict=True) if p.slot != lab]


def build_plan(
    me_players: list[RosterPlayer], opp_players: list[RosterPlayer], proj: pd.DataFrame, nba: NBASchedule,
    days: list[date], settings: LeagueSettings, generic: dict[str, float], acc_me: dict | None = None,
    acc_opp: dict | None = None, params: LineupParams | None = None,
) -> dict:
    """Full planner output for days[0] (today) and the rest of the week."""
    params = params or LineupParams()
    cats = settings.cats
    rs = settings.roster_slots
    slots = DaySlots(tuple(settings.starting_slots), rs.get("BN", 0), rs.get("IL", 0) + rs.get("IL+", 0))
    warnings: list[str] = []

    def side(players: list[RosterPlayer], acc: dict | None) -> TeamSide:
        G, w = games_matrix(players, days, nba)
        warnings.extend(w)
        P = p_matrix(players, G)
        return TeamSide(players, G, P, team_week(players, proj, P, acc))

    me, opp = side(me_players, acc_me), side(opp_players, acc_opp)
    ogv = generic_values(opp.players, generic)
    opp_X, _ = daily_plan(opp.players, ogv[:, None] * opp.P, opp.P, slots)
    res, X_naive, _ = optimize_week(me, opp_X, opp, cats, slots, generic, params)
    X_static = static_plan(me, slots)
    naive_state = cat_state(me.tw, X_naive, opp.tw, opp_X, cats)
    static_state = cat_state(me.tw, X_static, opp.tw, opp_X, cats)
    mc = monte_carlo(me, {"optimized": res.X, "naive_season_value": X_naive, "static_current_lineup": X_static},
                     opp, opp_X, cats, params.n_sims, params.seed)

    today = day_summary(me.players, res.labels[0], me.P[:, 0], res.values[:, 0], slots, days[0])
    start = set(slots.starting)
    tipoff = [{"player_id": p.player_id, "name": p.name, "status": p.status, "p_play": round(float(me.P[i, 0]), 3),
               "slot": res.labels[0][i],
               "note": "lineup locks at his own tip-off: re-check his status ~30 min before and swap if ruled out"}
              for i, p in enumerate(me.players) if me.P[i, 0] > 0 and p.status is not None]
    tagged = []
    for i, p in enumerate(me.players):
        if p.status is None:
            continue
        nxt = next((d for d in range(len(days)) if me.G[i, d]), None)
        tagged.append({"player_id": p.player_id, "name": p.name, "status": p.status,
                       "next_game": days[nxt].isoformat() if nxt is not None else None,
                       "p_play_next_game": round(float(me.P[i, nxt]), 3) if nxt is not None else 0.0,
                       "il_eligible": p.il_eligible})
    used_il = sum(1 for p in me.players if p.slot == "IL")
    il_moves = [{"player_id": p.player_id, "name": p.name, "status": p.status, "from": p.slot, "to": "IL"}
                for p, lab in zip(me.players, res.labels[0], strict=True) if lab == "IL" and p.slot != "IL"]
    il_moves = il_moves[: max(slots.n_il - used_il, 0)]
    per_player = [{"player_id": p.player_id, "name": p.name, "positions": list(p.positions), "team": p.team,
                   "status": p.status, "current_slot": p.slot, "games_left": int(me.G[i].sum()),
                   "has_game_today": bool(me.G[i, 0]), "p_play_today": round(float(me.P[i, 0]), 3),
                   "value_per_game": round(float(res.per_game[i]), 5),
                   "planned_starts": int(res.X[i].sum()),
                   "value_by_cat": {c.key: round(float(res.contrib[i, j]), 5) for j, c in enumerate(cats)}}
                  for i, p in enumerate(me.players)]
    out = {
        "date": days[0].isoformat(), "days": [d.isoformat() for d in days],
        "lineup": [e for e in today["lineup"] if e["slot"] in start],
        "bench": [e for e in today["lineup"] if e["slot"] == "BN"],
        "il": [e for e in today["lineup"] if e["slot"] == "IL"],
        "il_moves": il_moves,
        "changes_vs_current": changes_vs_current(me.players, res.labels[0]),
        "decide_at_tipoff": tipoff,
        "tagged_players": tagged,
        "today": {k: today[k] for k in ("players_with_game", "started_with_game", "unused_starts", "unused_players",
                                         "empty_slots")},
        "players": per_player,
        "category_weights": res.state.to_dict(),
        "expected": {
            "method": "analytic normal approximation per category; categories independent for P(win matchup)",
            "optimized": summarize_state(res.final_state),
            "naive_season_value": summarize_state(naive_state),
            "static_current_lineup": summarize_state(static_state)},
        "monte_carlo": mc,
        "fixed_point": {"iterations": res.iterations, "converged": res.converged},
        "week_plan": [day_summary(me.players, res.labels[d], me.P[:, d], res.values[:, d], slots, day,
                                  first_game=(me.G[:, :d].sum(axis=1) == 0))
                      for d, day in enumerate(days)],
        "week_totals": {"players_with_game": int((me.P > 0).sum()), "planned_starts": int(res.X.sum()),
                        "slot_days": len(slots.starting) * len(days),
                        "unused_starts": int(((me.P > 0) & ~res.X).sum()),
                        "opponent_planned_starts": int(opp_X.sum())},
        "opponent_plan": [{"player_id": p.player_id, "name": p.name, "planned_starts": int(opp_X[i].sum()),
                           "games_left": int(opp.G[i].sum()), "status": p.status}
                          for i, p in enumerate(opp.players)],
        "warnings": warnings,
        "model_version": LINEUP_MODEL_VERSION,
        "params": params.to_dict(),
    }
    return out
