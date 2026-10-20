"""T-006: matchup-aware add/drop scan for THIS week, with the drop's season cost (paired season sim).

For every (free agent c, drop candidate x) pair:

* **Week** (days[0] = today .. end of the matchup week, vs the real opponent from the league schedule): the lineup
  planner's matchup model (`fantasy.lineup.value` / `plan`, lineup-0.2.0). The swap takes effect on c's
  `available_from` day (free agent: today; waiver claim: the day it processes, x keeps playing until then). Two
  numbers:
  - `week_linear_gain`: first order. Category weights w_c = dE[cats won]/d(my weekly total) at the baseline plan
    (the gradient in `lineup.value`); every player's value on a day = P(plays) x sum_c w_c delta_c; the
    position-aware daily lineups are re-solved with c in and x out; gain = sum of started values minus the
    baseline's. Only games where c fills an open slot or displaces a lower-value starter count.
  - `week_d_exp_cats` / `week_d_p_win`: the planner re-optimised with c in and x out (fixed point), evaluated
    with full moments (analytic normal model); and, for the shortlist, a paired Monte Carlo (common random
    numbers keyed by player id) giving the SE.
* **Season**: paired position-aware season simulation (`possim.PositionAwareSimulator` with windows: x until
  the effective day, c from it), ΔP(playoffs), ΔP(title), Δ category wins ± paired SE (`sim.report.paired_delta`).
  This is the full cost of losing x for the rest of the season (and includes this week, at season-rate
  availability). It is expensive, so only a shortlist gets it: half by this week's gain and half by the cheap
  `season_proxy_d_value` = valuation value(c) - value(x) (season G-score totals above replacement; its agreement
  with the sim is checked in docs/research/streaming_and_trade_scan.md).

Decision rule (`decision_rule` in the output): recommend a pair only when its *season* effect beats noise (ΔP(playoffs)
or ΔP(title) > 2 paired SE, neither < -2 SE); the season sim includes this week. A pair whose only significant gain
is this week's ΔP(win) is listed under `week_only` with its season ΔP(title) lower bound, never recommended.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from fantasy.inseason.freeagents import FreeAgent
from fantasy.league.settings import LeagueSettings
from fantasy.lineup.optimize import DaySlots
from fantasy.lineup.plan import (
    LineupParams,
    TeamSide,
    daily_plan,
    games_matrix,
    generic_values,
    optimize_week,
    p_matrix,
    team_week,
)
from fantasy.lineup.players import REST_SHARE, RosterPlayer
from fantasy.lineup.value import draw, per_game_value, score_matchup, simulate_totals
from fantasy.sim.schedule import NBASchedule

SeasonEval = Callable[[str, str | None, date], dict]


@dataclass
class StreamParams:
    n_drops: int = 3  # default drop candidates: my k lowest season values (valuation `value`)
    week_sims: int = 4000  # paired Monte Carlo of this week's matchup for the shortlist
    season_sims: int = 2000  # paired season sims per shortlisted pair
    season_top: int = 8  # pairs that get a season sim (half by week gain, half by the season proxy)
    top: int = 10  # extra (non-shortlisted) pairs listed
    seed: int = 0
    rest_share: float = REST_SHARE  # PRIOR (lineup planner): share of a healthy player's misses that are rests
    sig: float = 2.0  # a delta counts only when |delta| >= sig * SE

    def to_dict(self) -> dict:
        return asdict(self)


def team_side(players: list[RosterPlayer], proj: pd.DataFrame, nba: NBASchedule, days: list[date],
              rest_share: float, acc: dict | None = None, cut: dict[str, tuple[int, int]] | None = None) -> TeamSide:
    """TeamSide; `cut[pid] = (lo, hi)` keeps a player's games only on day indices lo <= d < hi."""
    G, _ = games_matrix(players, days, nba)
    for i, p in enumerate(players):
        if cut and p.player_id in cut:
            lo, hi = cut[p.player_id]
            G[i, :lo] = False
            G[i, hi:] = False
    P = p_matrix(players, G, rest_share)
    return TeamSide(players, G, P, team_week(players, proj, P, acc))


def mc_delta(base: TeamSide, X0: np.ndarray, alt: TeamSide, X1: np.ndarray, opp_tot: np.ndarray, cats,
             n: int, seed: int) -> dict:
    """Paired Monte Carlo of this week's matchup: same draws for every common player (keyed by player id)."""
    s0 = score_matchup(simulate_totals(base.tw, X0, draw(base.tw, n, seed, "me")), opp_tot, cats)
    s1 = score_matchup(simulate_totals(alt.tw, X1, draw(alt.tw, n, seed, "me")), opp_tot, cats)
    c0, c1 = s0.sum(axis=1), s1.sum(axis=1)
    w0, w1 = (c0 > len(cats) / 2).astype(float), (c1 > len(cats) / 2).astype(float)
    dc, dw = c1 - c0, w1 - w0
    rt = np.sqrt(n)
    return {"n_sims": n, "p_win_before": round(float(w0.mean()), 4), "exp_cats_before": round(float(c0.mean()), 3),
            "d_exp_cats": round(float(dc.mean()), 3), "d_exp_cats_se": round(float(dc.std(ddof=1) / rt), 3),
            "d_p_win": round(float(dw.mean()), 4), "d_p_win_se": round(float(dw.std(ddof=1) / rt), 4)}


def sig_sign(d: dict, sig: float) -> int:
    """+1 / -1 if a paired delta {delta, delta_se} beats sig * SE, else 0."""
    if d["delta"] > 0 and d["delta"] >= sig * d["delta_se"]:
        return 1
    if d["delta"] < 0 and d["delta"] <= -sig * d["delta_se"]:
        return -1
    return 0


def season_verdict(me: dict, sig: float = 2.0) -> str:
    """'helps' (P(playoffs) or P(title) up beyond noise, neither down), 'hurts', 'tradeoff' or 'noise'."""
    a, b = sig_sign(me["p_playoffs"], sig), sig_sign(me["p_title"], sig)
    if a < 0 or b < 0:
        return "tradeoff" if (a > 0 or b > 0) else "hurts"
    return "helps" if (a > 0 or b > 0) else "noise"


def scan_streams(
    proj_df: pd.DataFrame,
    settings: LeagueSettings,
    nba: NBASchedule,
    me_players: list[RosterPlayer],
    opp_players: list[RosterPlayer],
    free_agents: Sequence[FreeAgent],
    days: list[date],
    generic: dict[str, float],
    season_value: pd.Series,
    params: StreamParams | None = None,
    drops: Sequence[str] | None = None,
    acc_me: dict | None = None,
    acc_opp: dict | None = None,
    season_eval: SeasonEval | None = None,
    adds_used: int = 0,
) -> dict:
    """Rank (add, drop) pairs for the rest of this matchup week (days[0] = today); season sims for a shortlist."""
    params = params or StreamParams()
    df = proj_df
    cats = settings.cats
    rs = settings.roster_slots
    slots = DaySlots(tuple(settings.starting_slots), rs.get("BN", 0), rs.get("IL", 0) + rs.get("IL+", 0))
    slots_plus = DaySlots(slots.starting, slots.n_bench + 1, slots.n_il)  # transition roster: drop + add
    lp = LineupParams(n_sims=0, rest_share=params.rest_share)
    D = len(days)
    me = team_side(me_players, df, nba, days, params.rest_share, acc_me)
    opp = team_side(opp_players, df, nba, days, params.rest_share, acc_opp)
    ogv = generic_values(opp.players, generic)
    opp_X, _ = daily_plan(opp.players, ogv[:, None] * opp.P, opp.P, slots)
    base, _, _ = optimize_week(me, opp_X, opp, cats, slots, generic, lp)
    e0, w0 = base.final_state.expected_cats, base.final_state.matchup_win_prob
    base_lin = float((base.X * base.values).sum())

    roster_cap = len(slots.starting) + slots.n_bench
    n_il_ok = sum(1 for p in me_players if p.il_eligible)
    free_spots = roster_cap - (len(me_players) - min(n_il_ok, slots.n_il))
    val = season_value
    mine = [p.player_id for p in me_players]
    if drops is None:
        drops = sorted(mine, key=lambda p: float(val.get(p, -99.0)))[: params.n_drops]
    drop_opts: list[str | None] = [*drops, *([None] if free_spots > 0 else [])]
    repl_value = float(val.iloc[min(len(val) - 1, settings.num_teams * settings.num_rounds)])

    rows: list[dict] = []
    alts: dict[int, tuple[TeamSide, np.ndarray]] = {}
    for fa in free_agents:
        if fa.is_out or fa.player_id in mine or fa.player_id not in df.index or fa.team is None:
            continue
        eff = fa.available_from(days[0])
        k = next((i for i, d in enumerate(days) if d >= eff), D)
        rp = RosterPlayer(fa.player_id, fa.name, fa.positions, fa.team, fa.status, None,
                          float(df.loc[fa.player_id, "gp"]))
        for dr in drop_opts:
            players = [*me_players, rp]
            cut = {fa.player_id: (k, D)}
            if dr is not None:
                cut[dr] = (0, k)
            alt = team_side(players, df, nba, days, params.rest_share, acc_me, cut)
            pg, _ = per_game_value(alt.tw.S, base.state, cats)  # first order: baseline weights
            v_lin = alt.P * pg[:, None]
            X_lin, _ = daily_plan(players, v_lin, alt.P, slots_plus)
            res, _, _ = optimize_week(alt, opp_X, opp, cats, slots_plus, generic, lp)
            j = len(players) - 1
            drop_val = float(val.get(dr, np.nan)) if dr is not None else repl_value
            row = {
                "add": fa.player_id, "add_name": fa.name, "add_team": fa.team, "add_positions": "|".join(fa.positions),
                "add_status": fa.status, "ownership": fa.ownership, "waiver_source": fa.waiver_source,
                "effective": eff.isoformat(),
                "drop": dr,
                "drop_name": (str(df.loc[dr, "name"]) if dr in df.index else str(dr)) if dr else "(none: open spot)",
                "add_games": int(alt.G[j].sum()), "add_starts": int(res.X[j].sum()),
                "drop_games_lost": int(me.G[mine.index(dr), k:].sum()) if dr is not None else 0,
                "week_linear_gain": round(float((X_lin * v_lin).sum()) - base_lin, 4),
                "week_d_exp_cats": round(res.final_state.expected_cats - e0, 4),
                "week_d_p_win": round(res.final_state.matchup_win_prob - w0, 4),
                "season_proxy_d_value": round(float(val.get(fa.player_id, np.nan)) - drop_val, 3),
            }
            alts[id(row)] = (alt, res.X)
            rows.append(row)
    rows.sort(key=lambda r: (-r["week_d_exp_cats"], -r["season_proxy_d_value"]))
    n1 = max(1, params.season_top // 2)
    by_season = sorted(rows, key=lambda r: -r["season_proxy_d_value"])
    short = list({id(r): r for r in rows[:n1] + by_season[: max(0, params.season_top - n1)]}.values())
    if params.week_sims > 0 and short:
        opp_tot = simulate_totals(opp.tw, opp_X, draw(opp.tw, params.week_sims, params.seed, "opp"))
        for r in short:
            alt, X1 = alts[id(r)]
            r["week_mc"] = mc_delta(me, base.X, alt, X1, opp_tot, cats, params.week_sims, params.seed)
    if season_eval is not None:
        for r in short:
            r["season"] = season_eval(r["add"], r["drop"], date.fromisoformat(r["effective"]))
            r["season_verdict"] = season_verdict(r["season"], params.sig)
    remaining = settings.max_weekly_adds - adds_used
    recs, week_only = [], []
    for r in short:
        v = r.get("season_verdict")
        wk = r.get("week_mc")
        week_sig = bool(wk and wk["d_p_win"] > 0 and wk["d_p_win"] >= params.sig * wk["d_p_win_se"])
        pick = {k: r[k] for k in ("add", "add_name", "drop", "drop_name", "ownership", "effective")}
        if v == "helps":
            recs.append(pick | {"why": "season ΔP(playoffs) or ΔP(title) beyond noise, neither down",
                                "season": r["season"], "week_mc": wk})
        elif v == "noise" and week_sig:
            s_t = r["season"]["p_title"]
            week_only.append(pick | {
                "why": "this week's ΔP(win) beyond noise; season effect not resolved (within ±2 SE)",
                "season_title_lower_bound": round(s_t["delta"] - params.sig * s_t["delta_se"], 4),
                "season": r["season"], "week_mc": wk})
    recs.sort(key=lambda x: -x["season"]["p_title"]["delta"])
    week_only.sort(key=lambda x: -x["week_mc"]["d_p_win"])
    blocked = None
    if remaining <= 0:
        blocked, recs = f"no adds left this week ({adds_used}/{settings.max_weekly_adds} used)", []
    shortlisted = {id(r) for r in short}
    listed = [r for r in rows if id(r) in shortlisted] + [r for r in rows if id(r) not in shortlisted][: params.top]
    return {
        "window": [days[0].isoformat(), days[-1].isoformat()],
        "baseline": {"exp_cats": round(e0, 3), "p_win": round(w0, 4),
                     "method": "analytic normal model (lineup-0.2.0): my matchup-aware lineup vs the opponent's "
                               "generic best-players lineup"},
        "category_weights": {k: round(v["weight"], 4) for k, v in base.state.to_dict().items()},
        "roster": {"size": len(me_players), "free_spots": free_spots, "drop_candidates": [
            {"player_id": d, "name": str(df.loc[d, "name"]) if d in df.index else d,
             "season_value": round(float(val.get(d, np.nan)), 3)} for d in drops]},
        "adds": {"max_weekly": settings.max_weekly_adds, "used": adds_used, "remaining": remaining,
                 "note": "a waiver claim counts against the week in which it is processed"},
        "n_pairs_scanned": len(rows),
        "n_season_simulated": sum(1 for r in short if "season" in r),
        "pairs": listed,
        "recommend": recs,
        "recommendation": recs[0] if recs else None,
        "week_only": week_only,
        "blocked": blocked,
        "decision_rule": (f"recommend a pair only if its season ΔP(playoffs) or ΔP(title) is > {params.sig:g} paired SE "
                          f"and neither is < -{params.sig:g} SE (the season sim includes this week); otherwise recommend "
                          "nothing. Pairs whose only significant gain is this week's ΔP(win) are listed in week_only "
                          "with the season ΔP(title) lower bound, for the user to weigh. Every add/drop needs approval."),
        "params": params.to_dict(),
    }
