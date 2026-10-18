"""Player valuation: league-specific category scores (G-scores) over a replacement baseline.

For each category we compute a standardized score over the draftable pool
(num_teams * num_rounds players):

  count cats:  x = effective per-game stat               (TO sign-flipped)
  ratio cats:  x = makes - p_pool * attempts              ("impact": volume-weighted %)

  G = (x - mean_pool) / sqrt(sd_pool^2 + kappa * within_var / games_per_week)

kappa=0 gives classic z-scores; kappa=1 gives G-scores (Rosenof 2024), which down-weight
categories that are noisy week to week (STL, BLK, TO, 3PM) — appropriate for H2H.
Availability enters through effective lines (see fantasy.model.lines).

The headline `value` is used for ranking/market priors and dashboards. Draft *decisions* are
made by fantasy.draft.optimizer, which evaluates whole rosters with the category-win model.
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import (
    GAME_CV,
    GAMES_PER_WEEK,
    STATS,
    ProjectionSet,
    availability,
    effective_lines,
)

# 0.2.0: availability = fitted gp (projections baseline-0.2.0) with optional gp risk penalty and a
# playoff-week tilt (playoff_weight); see docs/research/availability_model.md
# 0.2.1: projections baseline-0.2.1 (playoff_mult refit on the Mar 1-21 fantasy playoff window)
# 0.2.2: no formula change; default projections are now the 50/50 ours/ESPN blend
#        (projections-2026-27-0.3.0, adopted 2026-10-19; docs/research/projection_quality.md)
MODEL_VERSION = "valuation-0.2.2"


@dataclass
class ValuationParams:
    kappa: float = 1.0  # 0 = z-scores, 1 = G-scores
    stream_recovery: float = 0.5  # share of missed games recovered by streaming replacements
    # downside adjustment for games-played uncertainty: value gp - lambda * gp_sd (0 = expected gp)
    gp_risk_lambda: float = 0.0
    # weight of the fantasy playoff weeks (18-20) in availability; 0.15 (= 3/20 weeks) is neutral,
    # 0.30 counts a playoff week double. A prior, not fitted (playoff_mult itself is fitted).
    playoff_weight: float = 0.30
    replacement_band: int = 12  # players beyond the pool averaged to form replacement level
    punt: list[str] = field(default_factory=list)  # categories given zero weight
    iterations: int = 3


def _cat_scores(
    eff: pd.DataFrame, pool: pd.Index, settings: LeagueSettings, kappa: float
) -> pd.DataFrame:
    out = {}
    for c in settings.cats:
        if c.kind == "count":
            x = eff[c.stat]
            within = (eff[c.stat] * GAME_CV[c.stat]) ** 2
        else:
            m, a = eff[c.num], eff[c.den]
            p_pool = m.loc[pool].sum() / max(a.loc[pool].sum(), 1e-9)
            x = m - p_pool * a
            with np.errstate(divide="ignore", invalid="ignore"):
                p = np.where(a > 0, m / a, p_pool)
            within = a * p * (1 - p) + (GAME_CV[c.den] * a) ** 2 * (p - p_pool) ** 2
        mu, sd = x.loc[pool].mean(), x.loc[pool].std(ddof=0)
        denom = np.sqrt(sd**2 + kappa * within.loc[pool].mean() / GAMES_PER_WEEK)
        g = (x - mu) / max(denom, 1e-9)
        out[c.key] = g if c.higher_is_better else -g
    return pd.DataFrame(out, index=eff.index)


def compute_values(
    proj: ProjectionSet, settings: LeagueSettings, params: ValuationParams | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Returns (values table sorted by value, effective lines, replacement line)."""
    params = params or ValuationParams()
    df = proj.df
    n_pool = settings.num_teams * settings.num_rounds
    weights = pd.Series({k: (0.0 if k in params.punt else 1.0) for k in settings.categories})

    replacement: pd.Series | None = None
    avail = availability(df, params.gp_risk_lambda, params.playoff_weight)
    eff = effective_lines(df, None, avail=avail)
    order = df.index
    for _ in range(params.iterations):
        pool = order[:n_pool]
        scores = _cat_scores(eff, pool, settings, params.kappa)
        total = (scores * weights).sum(axis=1)
        order = total.sort_values(ascending=False).index
        band = order[n_pool : n_pool + params.replacement_band]
        replacement = df.loc[band, STATS].mean()
        eff = effective_lines(df, replacement, params.stream_recovery, avail=avail)

    pool = order[:n_pool]
    scores = _cat_scores(eff, pool, settings, params.kappa)
    total = (scores * weights).sum(axis=1)
    repl_value = total.loc[order[n_pool : n_pool + params.replacement_band]].mean()
    values = pd.DataFrame(
        {
            "name": df["name"],
            "team": df["team"],
            "positions": df["positions"],
            "gp": df["gp"],
            "adp": df["adp"],
            "status": df["status"],
            "value": total - repl_value,
        }
    )
    values = values.join(scores)
    values = values.sort_values("value", ascending=False)
    values["rank"] = np.arange(1, len(values) + 1)
    assert replacement is not None
    return values, eff, replacement


def market_order(values: pd.DataFrame, adp_weight: float = 1.0) -> list[str]:
    """Expected order in which the room drafts: ADP where known, else our rank (shifted later)."""
    rank = values["rank"].astype(float)
    adp = values["adp"].astype(float)
    # unknown ADP: assume the market is a bit slower than our model on that player
    est = np.where(adp.notna(), adp_weight * adp + (1 - adp_weight) * rank, rank * 1.15 + 3)
    return list(values.index[np.argsort(est, kind="stable")])


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return None


@dataclass
class ValuationRun:
    run_id: str
    created_at: str
    model_version: str
    params: dict
    inputs: dict
    values: pd.DataFrame


def run_valuation(
    proj: ProjectionSet, settings: LeagueSettings, params: ValuationParams | None = None
) -> ValuationRun:
    params = params or ValuationParams()
    values, _, replacement = compute_values(proj, settings, params)
    now = datetime.now(UTC)
    return ValuationRun(
        run_id=f"val-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}",
        created_at=now.isoformat(),
        model_version=MODEL_VERSION,
        params={**asdict(params), "categories": settings.categories},
        inputs={"projections_path": proj.path, "projections_sha256": proj.sha256, "replacement_line": replacement.round(3).to_dict()},
        values=values,
    )

