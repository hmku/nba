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
4. Ratio-bench check (lineup-0.4.0, SUGGESTION ONLY): the executed plan keeps the default "everyone with a game
   starts" rule. Unless `protect_ratios` is already on, the same fixed-point procedure is re-run with
   protect_ratios=True (a player with a game whose matchup-aware value is negative -- e.g. a poor FT shooter while
   FT% is close and the counting categories are decided -- may lose to an empty bench seat). If that plan differs,
   both are compared by Monte Carlo with common random numbers over the rest of the week (paired differences); the
   result is `ratio_bench` (suggest iff it benches someone, dE[cats] > 2 paired SE and dE >= RATIO_BENCH_MIN_EFFECT
   = 0.05 expected cats, a PRIOR minimum effect: with common random numbers the paired SE alone is tiny). It never changes `target_slots` or the plan; acting
   on it needs the user's explicit OK. The gradient that picks the benches is first order (it ignores the variance a
   start adds, which benching removes); the Monte Carlo comparison uses the full simulation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import pandas as pd

from fantasy.league.settings import Category, LeagueSettings
from fantasy.lineup.optimize import DaySlots, eligible, solve_day
from fantasy.lineup.players import REST_SHARE, TAG_PRIORS, RosterPlayer
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

# 0.3.0: banked matchup totals (matchup_score) + per-category P(win)
# 0.4.0: ratio_bench -- the protect-ratios plan is solved alongside the default one and compared by paired Monte
#        Carlo (suggestion only; the executed plan / target_slots are unchanged)
LINEUP_MODEL_VERSION = "lineup-0.4.0"
# PRIOR (not calibrated): smallest gain in expected categories won worth bothering the user with. Needed because with
# common random numbers the paired SE is tiny, so a meaningless +0.0003 can clear 2 SE.
RATIO_BENCH_MIN_EFFECT = 0.05
RATIO_BENCH_RULE = (f"suggest iff the protect-ratios plan benches someone and dE[categories won] (protect minus "
                    f"default) > 2 x its paired SE (Monte Carlo, common random numbers, rest of the week) and "
                    f">= {RATIO_BENCH_MIN_EFFECT} (minimum effect, PRIOR); suggestion only, never applied")
GAME_FLOOR = 1e-5  # a player with a game is worth at least this (so he never sits for a player without one)


@dataclass
class LineupParams:
    iterations: int = 6
    n_sims: int = 20000
    seed: int = 0
    protect_ratios: bool = False  # allow benching a player with a game whose marginal value is negative
    tag_priors: dict[str, float] = field(default_factory=lambda: dict(TAG_PRIORS))
    # PRIOR: share of a healthy player's missed games that are one-game rests (P(plays) = 1 - rest_share (1 - gp/82)).
    # 1.0 = plain season-average availability gp/82 (what `fantasy sim` uses for a random week).
    rest_share: float = REST_SHARE
    locked: tuple[str, ...] = ()  # player ids whose game has started today: pinned to their current slot

    def to_dict(self) -> dict:
        return {"iterations": self.iterations, "n_sims": self.n_sims, "seed": self.seed,
                "protect_ratios": self.protect_ratios, "tag_priors": self.tag_priors,
                "rest_share": self.rest_share, "rest_share_label": "prior", "locked": list(self.locked),
                "game_floor": GAME_FLOOR, "objective": "expected_categories_won"}

    def availability_assumption(self) -> str:
        return (f"healthy players play each scheduled game with P = 1 - {self.rest_share:g} x (1 - gp/82) "
                f"(rest_share {self.rest_share:g} is a PRIOR; 1.0 = season-average availability gp/82); "
                f"tagged players' next game: {self.tag_priors} (PRIORS); O/INJ/NA/SUSP = 0")


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


def p_matrix(players: list[RosterPlayer], G: np.ndarray, rest_share: float = REST_SHARE) -> np.ndarray:
    P = np.zeros(G.shape)
    for i, p in enumerate(players):
        first = True
        for d in range(G.shape[1]):
            if G[i, d]:
                P[i, d] = p.p_play(first_game=first, rest_share=rest_share)
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
    locked: tuple[str, ...] | frozenset[str] = (),
) -> tuple[np.ndarray, list[list[str]]]:
    """Solve each day; values (n, D) = expected value if started. Returns (X started (n, D), labels per day).

    Unless `protect_ratios`, values of players with a game are shifted monotonically, v - min(v over players with a
    game) + GAME_FLOOR, so they are all positive (a player with a game always beats one without) while their order
    is kept. Shifting all weights of a transversal matroid by a constant does not change which feasible set of
    starters is best among the maximum ones. `locked` players are pinned to their current slot on day 0."""
    for pid in locked:
        p = next((q for q in players if q.player_id == pid), None)
        if p is None or p.slot is None:
            raise ValueError(f"locked player {pid!r} is not on the roster or has no known current slot")
    n, D = P.shape
    start = set(slots.starting)
    X = np.zeros((n, D), dtype=bool)
    labels: list[list[str]] = []
    current: Sequence[str | None] = [p.slot for p in players]
    pos = [p.positions for p in players]
    startable = [not p.is_out for p in players]
    il_ok = [p.il_eligible for p in players]
    for d in range(D):
        has = P[:, d] > 0
        v = values[:, d]
        if not protect_ratios and has.any():
            v = v - v[has].min()
        v = np.where(has, v + GAME_FLOOR, 0.0)
        fixed = [p.slot if (d == 0 and p.player_id in locked) else None for p in players]
        lab = solve_day(pos, v, startable, il_ok, slots, current, fixed=fixed if any(fixed) else None)
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
    generic: dict[str, float], params: LineupParams, best_on_cycle: bool = False,
) -> tuple[WeekResult, np.ndarray, list[list[str]]]:
    """Fixed-point matchup-aware plan. Returns (result, baseline X, baseline labels).

    `best_on_cycle` (used by the ratio-bench check only, so the executed plan is unchanged): if the iteration does
    not converge, return the visited plan with the highest analytic expected categories instead of the last one.
    Typical cycle: benching a player locks a ratio category (sd ~ 0 -> weight ~ 0), so at the next step his value
    is ~0 and he starts again."""
    gv = generic_values(me.players, generic)
    X0, lab0 = daily_plan(me.players, gv[:, None] * me.P, me.P, slots, locked=params.locked)
    X, labels = X0, lab0
    converged = False
    it = 0
    visited = []
    for it in range(1, params.iterations + 1):  # noqa: B007
        state = cat_state(me.tw, X, opp.tw, opp_X, cats)
        pg, contrib = per_game_value(me.tw.S, state, cats)
        values = me.P * pg[:, None]
        X_new, labels = daily_plan(me.players, values, me.P, slots, params.protect_ratios, params.locked)
        visited.append((X_new, labels, values, pg, contrib, state))
        if np.array_equal(X_new, X):
            converged = True
            X = X_new
            break
        X = X_new
    if best_on_cycle and not converged and visited:
        score = [cat_state(me.tw, v[0], opp.tw, opp_X, cats).expected_cats for v in visited]
        X, labels, values, pg, contrib, state = visited[int(np.argmax(score))]
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


def _cat_list(contrib: np.ndarray, keys: list[str], sign: int) -> list[dict]:
    """Categories where a start's per-game contribution has `sign` (+1 helps, -1 hurts), largest first; small ones
    (< 5% of the largest absolute contribution) dropped."""
    top = float(np.abs(contrib).max()) if contrib.size else 0.0
    rows = [(k, float(v)) for k, v in zip(keys, contrib, strict=True) if sign * v > 0 and abs(v) >= 0.05 * top > 0]
    return [{"cat": k, "per_game": round(v, 5)} for k, v in sorted(rows, key=lambda r: -abs(r[1]))]


def ratio_bench(
    me: TeamSide, opp: TeamSide, opp_X: np.ndarray, cats: list[Category], slots: DaySlots, generic: dict[str, float],
    params: LineupParams, res: WeekResult, days: list[date], games: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Would benching players with a game to protect a ratio category (FG%/FT%) help this week? Suggestion only.

    Solves the protect_ratios=True plan with the same inputs and fixed point; if it differs from the default plan
    (`res`), compares both by Monte Carlo with common random numbers (paired differences, protect minus default).
    `benches` = player-days started in the default plan but benched in the protect plan, with per-game category
    contributions under the protect plan's weights: `cats_helped` = categories benching helps (his start would
    lower them, e.g. FT%), `cats_hurt` = what benching gives up (counting stats). `starts` = the reverse (rare)."""
    if params.protect_ratios:
        return {"checked": False, "reason": "the plan itself already uses protect_ratios"}
    alt, _, _ = optimize_week(me, opp_X, opp, cats, slots, generic, replace(params, protect_ratios=True),
                              best_on_cycle=True)
    base = {"checked": True, "rule": RATIO_BENCH_RULE, "delta_expected_cats": 0.0, "delta_expected_cats_se": None,
            "delta_p_win": 0.0, "delta_p_win_se": None, "benches": [], "starts": [],
            "fixed_point": {"iterations": alt.iterations, "converged": alt.converged,
                            **({} if alt.converged else {"picked": "best visited plan by analytic E[cats]"})}}
    if np.array_equal(alt.X, res.X):
        return {**base, "differs": False, "suggest": False, "method": "plans identical: Monte Carlo skipped"}
    keys = [c.key for c in cats]
    games = games or {}
    benches, starts = [], []
    for d, day in enumerate(days):
        for i, p in enumerate(me.players):
            if res.X[i, d] == alt.X[i, d]:
                continue
            row = {"date": day.isoformat(), "player_id": p.player_id, "name": p.name,
                   "game": games.get(p.player_id, {}).get(day.isoformat()), "p_play": round(float(me.P[i, d]), 3)}
            if res.X[i, d]:
                benches.append({**row, "instead_of_slot": res.labels[d][i],
                                "value_per_game": round(float(alt.per_game[i]), 5),
                                "cats_helped": _cat_list(alt.contrib[i], keys, -1),
                                "cats_hurt": _cat_list(alt.contrib[i], keys, +1)})
            else:
                starts.append({**row, "slot": alt.labels[d][i]})
    out = {**base, "differs": True, "benches": benches, "starts": starts,
           "expected_analytic": {"default": summarize_state(res.final_state),
                                 "protect_ratios": summarize_state(alt.final_state)}}
    if params.n_sims <= 0:
        a, b = alt.final_state, res.final_state
        return {**out, "suggest": False, "method": "analytic only (no Monte Carlo: --sims 0): no SE, no suggestion",
                "delta_expected_cats": a.expected_cats - b.expected_cats,
                "delta_p_win": a.matchup_win_prob - b.matchup_win_prob}
    mc = monte_carlo(me, {"default": res.X, "protect_ratios": alt.X}, opp, opp_X, cats, params.n_sims, params.seed)
    diff = mc["plans"]["protect_ratios"]["optimized_minus_this"]  # default minus protect
    dc, dc_se = -diff["expected_cats"], diff["expected_cats_se"]
    return {**out, "method": f"monte_carlo: {mc['n_sims']} paired sims (common random numbers), seed {mc['seed']}",
            "delta_expected_cats": dc, "delta_expected_cats_se": dc_se,
            "delta_p_win": -diff["p_win_matchup"], "delta_p_win_se": diff["p_win_matchup_se"],
            "suggest": suggest_rule(dc, dc_se, len(benches))}


def suggest_rule(delta: float, se: float | None, n_benches: int) -> bool:
    """RATIO_BENCH_RULE: a bench to suggest, significant (> 2 paired SE) and material (>= RATIO_BENCH_MIN_EFFECT)."""
    return bool(n_benches > 0 and se is not None and delta > 2 * se and delta >= RATIO_BENCH_MIN_EFFECT)


def summarize_state(st: CatState) -> dict:
    return {"expected_cats": st.expected_cats, "p_win_matchup": st.matchup_win_prob}


def cat_win_probs(final: CatState, mc: dict) -> dict:
    """Per-category chance to win by the end of the week under the optimized plan (banked totals included):
    the Monte Carlo category score (P(win) + P(tie)/2, ties = 0.5 as in Yahoo's standings) when sims ran, else the
    analytic Phi(z). The analytic value is always kept alongside."""
    analytic = {k: round(float(p), 4) for k, p in zip(final.keys, final.p_win, strict=True)}
    opt = (mc.get("plans") or {}).get("optimized") if mc else None
    if opt and opt.get("cat_score"):
        n = int(mc.get("n_sims") or 0)
        return {"cat_win_prob": {k: round(float(v), 4) for k, v in opt["cat_score"].items()},
                "cat_win_prob_method": f"monte_carlo: share of {n} simulated weeks the category is won (ties 0.5); "
                                       f"SE <= {0.5 / max(n, 1) ** 0.5:.4f}",
                "cat_win_prob_analytic": analytic}
    return {"cat_win_prob": analytic, "cat_win_prob_method": "analytic: Phi(z) of the normal weekly margin",
            "cat_win_prob_analytic": analytic}


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


def tipoff_notes(me: TeamSide, res: WeekResult, slots: DaySlots) -> list[dict]:
    """Tagged players with a game today, with a slot-dependent instruction for the executor at tip-off."""
    start = set(slots.starting)
    lab = res.labels[0]
    val = res.values[:, 0]
    out = []
    for i, p in enumerate(me.players):
        if me.P[i, 0] <= 0 or p.status is None:
            continue
        row = {"player_id": p.player_id, "name": p.name, "status": p.status, "p_play": round(float(me.P[i, 0]), 3),
               "slot": lab[i]}
        if lab[i] in start:
            subs = [j for j in range(len(me.players)) if lab[j] == "BN" and me.P[j, 0] > 0
                    and eligible(me.players[j].positions, lab[i])]
            sub = max(subs, key=lambda j: (val[j], me.players[j].player_id)) if subs else None
            row["swap_with"] = me.players[sub].player_id if sub is not None else None
            row["note"] = (f"starting: if he is ruled out before his tip-off, swap him out for "
                           f"{me.players[sub].name if sub is not None else 'a bench player with a game (re-run the plan)'}")
        else:
            # value if confirmed active (P = 1) vs the weakest starter with a game he can replace
            confirmed = float(res.per_game[i])
            cands = [j for j in range(len(me.players)) if lab[j] in start and me.P[j, 0] > 0
                     and eligible(p.positions, lab[j])]
            weakest = min(cands, key=lambda j: (val[j], me.players[j].player_id)) if cands else None
            if weakest is not None and confirmed > val[weakest]:
                x = me.players[weakest]
                row["swap_with"] = x.player_id
                row["note"] = (f"benched: if confirmed active, start him in place of {x.name} ({lab[weakest]}) -- "
                               f"only before {x.name}'s tip-off (value {confirmed:.4f} vs {val[weakest]:.4f})")
            else:
                row["swap_with"] = None
                row["note"] = "benched: stays benched even if confirmed active (lower value than every starter he can replace)"
        out.append(row)
    return out


def tagged_players(me: TeamSide, days: list[date]) -> list[dict]:
    """My players with a Yahoo tag: next game, P(plays it), IL eligibility."""
    out = []
    for i, p in enumerate(me.players):
        if p.status is None:
            continue
        nxt = next((d for d in range(len(days)) if me.G[i, d]), None)
        out.append({"player_id": p.player_id, "name": p.name, "status": p.status,
                    "next_game": days[nxt].isoformat() if nxt is not None else None,
                    "p_play_next_game": round(float(me.P[i, nxt]), 3) if nxt is not None else 0.0,
                    "il_eligible": p.il_eligible})
    return out


def il_moves(me: TeamSide, labels: list[str], slots: DaySlots) -> list[dict]:
    """Players today's plan puts in IL who are not there yet, capped by the free IL slots."""
    used_il = sum(1 for p in me.players if p.slot == "IL")
    moves = [{"player_id": p.player_id, "name": p.name, "status": p.status, "from": p.slot, "to": "IL"}
             for p, lab in zip(me.players, labels, strict=True) if lab == "IL" and p.slot != "IL"]
    return moves[: max(slots.n_il - used_il, 0)]


def player_rows(me: TeamSide, res: WeekResult, cats: list[Category]) -> list[dict]:
    """Per-player table: games left, today's P(plays), matchup-aware value per game and by category."""
    return [{"player_id": p.player_id, "name": p.name, "positions": list(p.positions), "team": p.team,
             "status": p.status, "current_slot": p.slot, "games_left": int(me.G[i].sum()),
             "has_game_today": bool(me.G[i, 0]), "p_play_today": round(float(me.P[i, 0]), 3),
             "value_per_game": round(float(res.per_game[i]), 5),
             "planned_starts": int(res.X[i].sum()),
             "value_by_cat": {c.key: round(float(res.contrib[i, j]), 5) for j, c in enumerate(cats)}}
            for i, p in enumerate(me.players)]


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
        P = p_matrix(players, G, params.rest_share)
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

    from fantasy.lineup.record import game_markers

    games = game_markers(nba, {p.player_id: p.team for p in me.players}, [d.isoformat() for d in days])
    rb = ratio_bench(me, opp, opp_X, cats, slots, generic, params, res, days, games)

    today = day_summary(me.players, res.labels[0], me.P[:, 0], res.values[:, 0], slots, days[0])
    start = set(slots.starting)
    tipoff = tipoff_notes(me, res, slots)
    out = {
        "date": days[0].isoformat(), "days": [d.isoformat() for d in days],
        "lineup": [e for e in today["lineup"] if e["slot"] in start],
        "bench": [e for e in today["lineup"] if e["slot"] == "BN"],
        "il": [e for e in today["lineup"] if e["slot"] == "IL"],
        "il_moves": il_moves(me, res.labels[0], slots),
        "changes_vs_current": changes_vs_current(me.players, res.labels[0]),
        "target_slots": {p.player_id: {"slot": lab, "name": p.name, "yahoo_player_id": p.yahoo_player_id}
                         for p, lab in zip(me.players, res.labels[0], strict=True)},
        "decide_at_tipoff": tipoff,
        "tagged_players": tagged_players(me, days),
        "today": {k: today[k] for k in ("players_with_game", "started_with_game", "unused_starts", "unused_players",
                                         "empty_slots")},
        "players": player_rows(me, res, cats),
        "category_weights": res.state.to_dict(),
        "expected": {
            "method": "analytic normal approximation per category; categories independent for P(win matchup)",
            "availability_assumption": params.availability_assumption(),
            "optimized": summarize_state(res.final_state),
            "naive_season_value": summarize_state(naive_state),
            "static_current_lineup": summarize_state(static_state),
            **cat_win_probs(res.final_state, mc)},
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
        "ratio_bench": rb,
        "warnings": warnings,
        "model_version": LINEUP_MODEL_VERSION,
        "params": params.to_dict(),
    }
    return out
