"""Market-implied ("the market is right") projection sets, for out-of-sample-style grading.

Construction (quantile map on total value, keep each player's category mix):
1. Value every player with our projections (fantasy.model.valuation.compute_values).
2. Players with a market ADP: sort them by ADP. The player with the r-th best ADP gets as *target value*
   the r-th best of OUR values among the same ADP players (so the market-implied value distribution has
   the same shape as ours, but its ranking is the ADP ranking). Players without ADP keep their value
   (the market has no opinion on them).
3. Each ADP player gets one multiplier m_i on all per-game counting stats and attempts (FGM, FGA, FTM, FTA,
   3PM, PTS, REB, AST, STL, BLK, TO). Percentages and the category *mix* are unchanged; only the volume moves.
   With the value pool held fixed, a player's value is affine in m_i (G-scores are linear in the stat
   line, the % impacts are makes - p_pool * attempts), so m_i = (target - v_i(0)) / (v_i(1) - v_i(0)).
4. Re-value with the new lines (the pool's means/sds move) and repeat a few times; m is clipped to
   [m_min, m_max] so a player is never driven to a nonsensical line.

Games played (gp, gp_sd, playoff_mult) are not changed: market disagreement about health shows up as a
per-game volume change instead (a limitation - see docs/research/optimism_decomposition.md). Neither are
positions. ADP also mixes in punt-build demand (e.g. a poor-FT% star is drafted early by punt-FT teams),
which no single projection set can represent.

`blend_projections(ours, market, w)` mixes the two lines linearly (w = weight on the market).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import (
    GAMES_PER_WEEK,
    STATS,
    ProjectionSet,
    availability,
    effective_lines,
)
from fantasy.model.noise import game_var, ratio_var
from fantasy.model.valuation import ValuationParams, compute_values


def fixed_pool_scores(
    eff: pd.DataFrame, ref: pd.DataFrame, pool: pd.Index, settings: LeagueSettings, kappa: float
) -> pd.DataFrame:
    """Category G-scores of the lines `eff`, standardized with the pool statistics of `ref` (same formula
    as fantasy.model.valuation._cat_scores; with eff = ref it reproduces it exactly)."""
    out = {}
    for c in settings.cats:
        if c.kind == "count":
            x, xr = eff[c.stat], ref[c.stat]
            within = pd.Series(game_var(c.stat, xr.to_numpy()), index=xr.index)
        else:
            mr, ar = ref[c.num], ref[c.den]
            p_pool = mr.loc[pool].sum() / max(ar.loc[pool].sum(), 1e-9)
            x = eff[c.num] - p_pool * eff[c.den]
            xr = mr - p_pool * ar
            within = pd.Series(ratio_var(mr.to_numpy(), ar.to_numpy(), p_pool, c.den), index=ar.index)
        mu, sd = xr.loc[pool].mean(), xr.loc[pool].std(ddof=0)
        denom = np.sqrt(sd**2 + kappa * within.loc[pool].mean() / GAMES_PER_WEEK)
        g = (x - mu) / max(denom, 1e-9)
        out[c.key] = g if c.higher_is_better else -g
    return pd.DataFrame(out, index=eff.index)


def _with_df(proj: ProjectionSet, df: pd.DataFrame, tag: str) -> ProjectionSet:
    h = hashlib.sha256((proj.sha256 + tag).encode()).hexdigest()
    return ProjectionSet(df=df, path=f"{proj.path}#{tag}", sha256=h)


def scale_volume(proj: ProjectionSet, m: pd.Series, tag: str) -> ProjectionSet:
    df = proj.df.copy()
    mm = m.reindex(df.index).fillna(1.0).to_numpy(dtype=float)[:, None]
    df[STATS] = df[STATS].to_numpy(dtype=float) * mm
    return _with_df(proj, df, tag)


@dataclass
class MarketTruth:
    proj: ProjectionSet
    multiplier: pd.Series  # per player (1.0 = unchanged)
    diagnostics: dict


def market_implied_projections(
    proj: ProjectionSet, settings: LeagueSettings, val_params: ValuationParams | None = None,
    iterations: int = 3, m_min: float = 0.5, m_max: float = 2.0,
) -> MarketTruth:
    vp = val_params or ValuationParams()
    n_pool = settings.num_teams * settings.num_rounds
    w = pd.Series({k: (0.0 if k in vp.punt else 1.0) for k in settings.categories})
    values0, _, _ = compute_values(proj, settings, vp)
    adp = proj.df["adp"]
    has = adp.notna()
    by_adp = adp[has].sort_values(kind="stable").index
    ours_sorted = np.sort(values0.loc[by_adp, "value"].to_numpy())[::-1]
    target = pd.Series(ours_sorted, index=by_adp)
    m = pd.Series(1.0, index=proj.df.index)
    cur = proj
    n_clip = 0
    for _ in range(iterations):
        values, eff1, repl = compute_values(cur, settings, vp)
        pool = values.index[:n_pool]
        avail = availability(cur.df, vp.gp_risk_lambda, vp.playoff_weight)
        zero = cur.df.copy()
        zero[STATS] = 0.0
        eff0 = effective_lines(zero, repl, vp.stream_recovery, avail=avail)
        s1 = (fixed_pool_scores(eff1, eff1, pool, settings, vp.kappa) * w).sum(axis=1)
        s0 = (fixed_pool_scores(eff0, eff1, pool, settings, vp.kappa) * w).sum(axis=1)
        offset = (s1 - values["value"]).median()  # replacement value subtracted by compute_values
        v1, v0 = s1 - offset, s0 - offset
        slope = (v1 - v0).loc[by_adp]
        step = (target - v0.loc[by_adp]) / slope.where(slope.abs() > 1e-6)
        step = step.fillna(1.0)
        new_m = (m.loc[by_adp] * step).clip(m_min, m_max)
        n_clip = int(((m.loc[by_adp] * step) < m_min).sum() + ((m.loc[by_adp] * step) > m_max).sum())
        m.loc[by_adp] = new_m
        cur = scale_volume(proj, m, "market-implied")
    vals, _, _ = compute_values(cur, settings, vp)
    top = by_adp[:n_pool]
    rho_all = float(spearmanr(vals.loc[by_adp, "value"], -adp.loc[by_adp]).statistic)
    rho_top = float(spearmanr(vals.loc[top, "value"], -adp.loc[top]).statistic)
    rho_ours = float(spearmanr(values0.loc[top, "value"], -adp.loc[top]).statistic)
    mt = m.loc[top]
    diag = {
        "n_adp_players": int(has.sum()), "n_pool": n_pool, "iterations": iterations, "m_bounds": [m_min, m_max],
        "n_clipped_last_iter": n_clip,
        "spearman_value_vs_adp_all_adp_players": round(rho_all, 4),
        "spearman_value_vs_adp_top_pool": round(rho_top, 4),
        "spearman_ours_vs_adp_top_pool": round(rho_ours, 4),
        "multiplier_top_pool": {"median": round(float(mt.median()), 3), "p10": round(float(mt.quantile(0.1)), 3),
                                "p90": round(float(mt.quantile(0.9)), 3),
                                "mean_abs_log": round(float(np.abs(np.log(mt)).mean()), 3)},
        "largest_up": {str(proj.df.loc[p, "name"]): round(float(m[p]), 3) for p in mt.sort_values().index[-8:][::-1]},
        "largest_down": {str(proj.df.loc[p, "name"]): round(float(m[p]), 3) for p in mt.sort_values().index[:8]},
    }
    return MarketTruth(proj=cur, multiplier=m, diagnostics=diag)


def blend_projections(ours: ProjectionSet, market: MarketTruth, weight: float = 0.5) -> ProjectionSet:
    """Linear blend of the stat lines: (1 - weight) * ours + weight * market-implied."""
    m = (1 - weight) + weight * market.multiplier
    return scale_volume(ours, m, f"blend-{weight:g}")
