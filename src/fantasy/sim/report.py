"""Summaries of a SimResult: league table, my team's outlook, weekly matchups, opponent profiles."""

from __future__ import annotations

import numpy as np

from fantasy.league.settings import CATEGORIES
from fantasy.sim.engine import SimResult
from fantasy.sim.league import LeagueState


def _se(p: float, n: int) -> float:
    return float(np.sqrt(max(p * (1 - p), 1e-12) / n))


def _r(x: float, k: int = 4) -> float:
    return round(float(x), k)


def _sign(cats: list[str]) -> np.ndarray:
    return np.array([1.0 if CATEGORIES[c].higher_is_better else -1.0 for c in cats], dtype=np.float32)


def head_to_head(res: SimResult, a: int, b: int, weeks: list[int]) -> dict:
    """a vs b in the given weeks (all sims): category win/tie rates and matchup W/T/L probabilities."""
    wi = [res.week_index(w) for w in weeks]
    x = res.totals[:, wi, res.tindex(a)]  # (N, k, C)
    y = res.totals[:, wi, res.tindex(b)]
    d = (x - y) * _sign(res.cats)
    win, loss = d > 0, d < 0
    nw, nl = win.sum(-1), loss.sum(-1)
    return {
        "cat_win": win.mean(axis=(0, 1)), "cat_tie": (~win & ~loss).mean(axis=(0, 1)),
        "p_win": float((nw > nl).mean()), "p_tie": float((nw == nl).mean()), "p_loss": float((nw < nl).mean()),
        "exp_cats_won": float((nw + 0.5 * (len(res.cats) - nw - nl)).mean()),
        "cats_won_dist": np.bincount(nw.ravel(), minlength=len(res.cats) + 1) / nw.size,
    }


def league_table(res: SimResult, league: LeagueState) -> list[dict]:
    n = res.n
    T = len(res.team_ids)
    champ = np.bincount(res.champion, minlength=T) / n
    final = (np.bincount(res.runner_up[res.runner_up >= 0], minlength=T) / n) + champ
    rows = []
    n_po = int(res.params["playoff_teams"])
    rounds = int(np.ceil(np.log2(max(n_po, 2))))
    byes = 2**rounds - n_po
    for i, t in enumerate(res.team_ids):
        p_po = float(res.playoff[:, i].mean())
        rows.append({
            "team": t, "name": league.team_name(t), "players": len(league.rosters[t]),
            "projected_players": len(league.projected.get(t, [])),
            "p_playoffs": _r(p_po), "p_playoffs_se": _r(_se(p_po, n)),
            "p_bye": _r((res.rank[:, i] <= byes).mean()) if byes else 0.0,
            "p_final": _r(final[i]), "p_title": _r(champ[i]), "p_title_se": _r(_se(float(champ[i]), n)),
            "exp_cat_wins": _r(res.cat_wins[:, i].mean(), 2), "exp_cat_losses": _r(res.cat_losses[:, i].mean(), 2),
            "exp_rank": _r(res.rank[:, i].mean(), 2),
        })
    rows.sort(key=lambda r: (-r["p_title"], -r["p_playoffs"]))
    return rows


def team_profiles(res: SimResult, league: LeagueState) -> dict:
    """Mean weekly category value per team (regular season) with rank (1 = best; TO lower is better)."""
    wi = [res.week_index(w) for w in res.regular_weeks]
    mean = res.totals[:, wi].mean(axis=(0, 1))  # (T, C)
    sgn = _sign(res.cats)
    ranks = (-(mean * sgn)).argsort(axis=0).argsort(axis=0) + 1
    sd = mean.std(axis=0)
    z = (mean - mean.mean(axis=0)) / np.where(sd > 0, sd, 1) * sgn
    out = {}
    for i, t in enumerate(res.team_ids):
        order = np.argsort(-z[i])
        out[str(t)] = {
            "name": league.team_name(t),
            "weekly_mean": {c: _r(mean[i, j], 3) for j, c in enumerate(res.cats)},
            "rank": {c: int(ranks[i, j]) for j, c in enumerate(res.cats)},
            "z": {c: _r(z[i, j], 2) for j, c in enumerate(res.cats)},
            "strengths": [res.cats[j] for j in order[:3] if z[i, j] > 0.3],
            "weaknesses": [res.cats[j] for j in order[::-1][:3] if z[i, j] < -0.3],
            "mean_player_games_per_week": _r(res.games[:, wi, i].mean(), 1),
        }
    return out


def week_dates(res: SimResult, week: int) -> tuple[str, str]:
    w = res.weeks[res.week_index(week)]
    return w.start.isoformat(), w.end.isoformat()


def my_outlook(res: SimResult, league: LeagueState, team: int) -> dict:
    i = res.tindex(team)
    n = res.n
    T = len(res.team_ids)
    p_po = float(res.playoff[:, i].mean())
    p_title = float((res.champion == i).mean())
    assumed = set(getattr(res.matchups, "assumed_weeks", []) or [])
    weekly = []
    for w in res.regular_weeks:
        opp = res.matchups.opponent(team, w)
        if opp is None or opp not in res.team_ids:
            continue
        h = head_to_head(res, team, opp, [w])
        s, e = week_dates(res, w)
        wi = res.week_index(w)
        weekly.append({
            "week": w, "start": s, "end": e, "opponent": opp, "opponent_name": league.team_name(opp),
            "p_win": _r(h["p_win"], 3), "p_tie": _r(h["p_tie"], 3), "p_loss": _r(h["p_loss"], 3),
            "exp_cats_won": _r(h["exp_cats_won"], 2),
            "my_player_games": _r(res.games[:, wi, i].mean(), 1),
            "opp_player_games": _r(res.games[:, wi, res.tindex(opp)].mean(), 1),
            "cat_win_probs": {c: _r(p, 3) for c, p in zip(res.cats, h["cat_win"], strict=True)},
            "opponent_assumed": w in assumed,
        })
    vs = {}
    for t in res.team_ids:
        if t == team:
            continue
        h = head_to_head(res, team, t, res.regular_weeks)
        vs[str(t)] = {"name": league.team_name(t), "p_win_matchup_any_week": _r(h["p_win"], 3),
                      "exp_cats_won": _r(h["exp_cats_won"], 2),
                      "cat_win_rate": {c: _r(p + 0.5 * q, 3) for c, p, q in zip(res.cats, h["cat_win"], h["cat_tie"], strict=True)}}
    return {
        "team": team, "name": league.team_name(team),
        "p_playoffs": _r(p_po), "p_playoffs_se": _r(_se(p_po, n)),
        "p_title": _r(p_title), "p_title_se": _r(_se(p_title, n)),
        "exp_cat_wins": _r(res.cat_wins[:, i].mean(), 2), "exp_cat_losses": _r(res.cat_losses[:, i].mean(), 2),
        "exp_rank": _r(res.rank[:, i].mean(), 2),
        "final_standing_dist": {str(k): _r(v, 4) for k, v in
                                enumerate(np.bincount(res.rank[:, i], minlength=T + 1)[1:] / n, start=1)},
        "weekly_matchups": weekly,
        "vs_opponents_all_play": vs,
        "weekly_h2h_matrix": weekly_h2h_matrix(res, team),
        "roster": league.rosters[team], "projected_picks": league.projected.get(team, []),
    }


def weekly_h2h_matrix(res: SimResult, team: int, weeks: list[int] | None = None) -> list[dict]:
    """My odds against EVERY other team in each week (whoever the schedule says I play): per week the
    games-per-week schedule differs, so the same opponent can be a different matchup in week 3 and week 9.
    Useful while the league's real pairings are unknown, and for planning around a known opponent."""
    out = []
    i = res.tindex(team)
    for w in weeks or res.regular_weeks:
        wi = res.week_index(w)
        s, e = week_dates(res, w)
        vs = {}
        for t in res.team_ids:
            if t == team:
                continue
            h = head_to_head(res, team, t, [w])
            vs[str(t)] = {"p_win": _r(h["p_win"], 3), "p_tie": _r(h["p_tie"], 3),
                          "exp_cats_won": _r(h["exp_cats_won"], 2),
                          "opp_player_games": _r(res.games[:, wi, res.tindex(t)].mean(), 1),
                          "cat_win_probs": {c: _r(p, 3) for c, p in zip(res.cats, h["cat_win"], strict=True)}}
        out.append({"week": w, "start": s, "end": e, "scheduled_opponent": res.matchups.opponent(team, w),
                    "opponent_assumed": w in set(getattr(res.matchups, "assumed_weeks", []) or []),
                    "my_player_games": _r(res.games[:, wi, i].mean(), 1), "vs": vs})
    return out


def matchup_report(res: SimResult, league: LeagueState, team: int, week: int, opponent: int | None = None) -> dict:
    opp = opponent or res.matchups.opponent(team, week)
    if opp is None:
        raise ValueError(f"team {team} has no scheduled opponent in week {week}; pass --opponent")
    wi = res.week_index(week)
    i, j = res.tindex(team), res.tindex(opp)
    h = head_to_head(res, team, opp, [week])
    x, y = res.totals[:, wi, i], res.totals[:, wi, j]
    cats = {}
    for k, c in enumerate(res.cats):
        cats[c] = {"label": CATEGORIES[c].label, "p_win": _r(h["cat_win"][k], 3), "p_tie": _r(h["cat_tie"][k], 3),
                   "me_mean": _r(x[:, k].mean(), 3), "opp_mean": _r(y[:, k].mean(), 3),
                   "me_p10_p90": [_r(np.percentile(x[:, k], 10), 3), _r(np.percentile(x[:, k], 90), 3)],
                   "opp_p10_p90": [_r(np.percentile(y[:, k], 10), 3), _r(np.percentile(y[:, k], 90), 3)]}
    s, e = week_dates(res, week)
    return {"week": week, "start": s, "end": e, "team": team, "name": league.team_name(team),
            "opponent": opp, "opponent_name": league.team_name(opp),
            "p_win": _r(h["p_win"], 3), "p_tie": _r(h["p_tie"], 3), "p_loss": _r(h["p_loss"], 3),
            "p_win_se": _r(_se(h["p_win"], res.n), 3),
            "exp_cats_won": _r(h["exp_cats_won"], 2),
            "cats_won_dist": [_r(v, 3) for v in h["cats_won_dist"]],
            "my_player_games": _r(res.games[:, wi, i].mean(), 1),
            "opp_player_games": _r(res.games[:, wi, j].mean(), 1),
            "categories": cats,
            "opponent_roster": league.rosters[opp]}


def season_summary(res: SimResult, league: LeagueState, team: int | None = None) -> dict:
    team = team or league.my_team
    return {
        "n_sims": res.n, "seed": res.params["seed"], "compute_seconds": round(res.seconds, 2),
        "league_source": league.source, "sources": res.sources,
        "matchup_schedule": {"source": res.matchups.source,
                             "assumed_weeks": list(getattr(res.matchups, "assumed_weeks", []) or [])},
        "regular_weeks": res.regular_weeks, "playoff_weeks": res.playoff_weeks,
        "params": res.params,
        "my_team": my_outlook(res, league, team),
        "league_table": league_table(res, league),
        "team_profiles": team_profiles(res, league),
        "warnings": list(dict.fromkeys(league.warnings + res.warnings)),
    }


def paired_delta(base: SimResult, alt: SimResult, team: int) -> dict:
    """Change in P(playoffs)/P(title)/category wins for `team` between two runs with the same seed
    (common random numbers: player and team noise streams are keyed by id), with paired SEs."""
    i, k = base.tindex(team), alt.tindex(team)
    out = {}
    for name, a, b in (
        ("p_playoffs", base.playoff[:, i].astype(float), alt.playoff[:, k].astype(float)),
        ("p_title", (base.champion == i).astype(float), (alt.champion == k).astype(float)),
        ("exp_cat_wins", base.cat_wins[:, i], alt.cat_wins[:, k]),
    ):
        d = b - a
        out[name] = {"before": _r(a.mean()), "after": _r(b.mean()), "delta": _r(d.mean()),
                     "delta_se": _r(d.std(ddof=1) / np.sqrt(len(d)))}
    return out
