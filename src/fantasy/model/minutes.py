"""Recency-weighted in-season minutes models (minutes-*). See docs/research/minutes_model.md.

Predicts a player's expected minutes per game in his NEXT games played from information available at prediction
time only: a regression on exponentially-weighted moving averages (EWMAs) of his minutes in games played this season
and his preseason projected mpg (the prior):

  e_h(0) = prior;  e_h(t) = a_h * e_h(t-1) + (1 - a_h) * m_t,  a_h = 0.5 ** (1 / half_life_h)   (games played)
  mpg    = const + sum_h b_h * e_h + b_shrink * (n0 * prior + sum m) / (n0 + t) + b_prior * prior        (t >= 1)

Each EWMA starts at the player's prior, so early in the season it is mostly the prior. Two fitted horizons:
- "next5" (model A, lineups / streaming): mean minutes over the next 5 games played; no intercept.
- "ros" (model B, the rest-of-season projections file): mean minutes over all remaining games played; with an
  intercept (const + slopes summing to 0.87: regression toward a population level over long horizons wins OOS).
t = 0 -> the prior exactly. For t <= fallback_t games played the old equal-weight shrink (OLD_SHRINK_N0 games) is
used (a safeguard; 0 for both fitted models because they beat it at t <= 5 out of sample). Per-minute rates are not modelled here
(inseason.shrink_line keeps shrinking them toward the prior in minutes). Predictions are conditional on playing
(availability is a separate model).

FITTED by weighted least squares on 2010-11..2024-25 box scores (half-lives by 5-fold player-clustered CV), tested out
of sample on 2025-26: scripts/inseason_model/fit_minutes.py -> report_minutes.json.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

MINUTES_MODEL_VERSION = "minutes-0.2.0"
MPG_MAX = 48.0
OLD_SHRINK_N0 = 5.2  # = inseason.STAT_N0['mpg'] (fitted, stat-shrink-0.1): the pre-minutes-model formula


@dataclass(frozen=True)
class MinutesParams:
    half_lives: tuple[float, ...]  # EWMA half-lives, games played
    b_ewma: tuple[float, ...]
    b_prior: float
    const: float = 0.0
    shrink_n0: float = 0.0  # > 0: add the equal-weight shrink term (n0 prior + sum m) / (n0 + t)
    b_shrink: float = 0.0
    target: str = "next5"
    fallback_t: int = 0  # t <= fallback_t games played: the old shrink formula

    def as_dict(self) -> dict:
        d = asdict(self)
        d["coef_sum"] = round(sum(self.b_ewma) + self.b_shrink + self.b_prior, 4)
        return d


# FITTED (scripts/inseason_model/report_minutes.json "deployed"; coefficient SEs = cluster bootstrap over players):
# fit 2010-11..2024-25 regular seasons, every prediction point t >= 1 games played of every player-season with a prior.
FITTED: dict[str, MinutesParams] = {
    # model A, next 5 games, no intercept. SEs 0.0068, 0.0095, 0.0044; sum 0.998 +- 0.0005
    "next5": MinutesParams(half_lives=(1.5, 10.0), b_ewma=(0.5103, 0.4352), b_prior=0.0525, target="next5"),
    # model B, rest of season, WITH intercept (beats every no-intercept variant OOS by > 2 SE, see the note).
    # SEs const 0.171, 0.0061, 0.0127, prior 0.012; slopes sum 0.8715 +- 0.0065
    "ros": MinutesParams(half_lives=(1.5, 10.0), b_ewma=(0.2705, 0.5727), b_prior=0.0283, const=3.6028,
                         target="ros"),
}
# fallback_t = 0 for both: each beats the old shrink at t <= 5 by > 2 SE OOS (A 0.278 +- 0.023, B 0.315 +- 0.075)
DEFAULT_HORIZON = "ros"


def default_params(horizon: str = DEFAULT_HORIZON) -> MinutesParams:
    try:
        return FITTED[horizon]
    except KeyError:
        raise ValueError(f"unknown minutes horizon {horizon!r}; choose from {sorted(FITTED)}") from None


def decay(half_life: float) -> float:
    return float(0.5 ** (1.0 / half_life))


def ewma(minutes, prior_mpg: float, half_life: float) -> np.ndarray:
    """EWMA after t = 0..n games played (index t uses games 0..t-1 only), initialised at the prior."""
    a = decay(half_life)
    m = np.asarray(minutes, float)
    e = np.empty(len(m) + 1)
    e[0] = float(prior_mpg)
    for t in range(1, len(m) + 1):
        e[t] = a * e[t - 1] + (1.0 - a) * m[t - 1]
    return e


def feature_panel(minutes, prior_mpg: float, params: MinutesParams | None = None) -> pd.DataFrame:
    """Features after t = 0..n games played, one row per t (row t uses games 0..t-1 ONLY: no look-ahead).

    minutes: his games PLAYED this season in date order (minutes > 0). Columns: t, prior, e_<half-life> per EWMA,
    shrink (equal-weight shrink with params.shrink_n0, if used), old (equal-weight shrink with OLD_SHRINK_N0)."""
    p = params or default_params()
    m = np.asarray(minutes, float)
    t = np.arange(len(m) + 1)
    cs = np.concatenate([[0.0], np.cumsum(m)])
    prior = float(prior_mpg)
    f = pd.DataFrame({"t": t, "prior": prior})
    for h in p.half_lives:
        f[f"e_{h:g}"] = ewma(m, prior, h)
    if p.shrink_n0 > 0:
        f["shrink"] = (p.shrink_n0 * prior + cs) / (p.shrink_n0 + t)
    f["old"] = (OLD_SHRINK_N0 * prior + cs) / (OLD_SHRINK_N0 + t)
    return f


def predict(feats: pd.DataFrame, params: MinutesParams | None = None) -> np.ndarray:
    """Expected mpg in the next games played for each feature row (t = 0 -> the prior exactly)."""
    p = params or default_params()
    prior = feats["prior"].to_numpy(float)
    lin = p.const + p.b_prior * prior
    for h, b in zip(p.half_lives, p.b_ewma, strict=True):
        lin = lin + b * feats[f"e_{h:g}"].to_numpy(float)
    if p.shrink_n0 > 0:
        lin = lin + p.b_shrink * feats["shrink"].to_numpy(float)
    t = feats["t"].to_numpy(float)
    out = np.where(t <= p.fallback_t, feats["old"].to_numpy(float), np.clip(lin, 0.0, MPG_MAX))
    return np.where(t > 0, out, prior)


def project_mpg(minutes, prior_mpg: float, params: MinutesParams | None = None) -> float:
    """Expected mpg in his next games played given his minutes in the games played so far (empty -> prior_mpg)."""
    f = feature_panel(minutes, prior_mpg, params)
    return float(predict(f.iloc[[-1]], params)[0])


def model_meta(params: MinutesParams | None = None) -> dict:
    p = params or default_params()
    return {"version": MINUTES_MODEL_VERSION, "params": p.as_dict(),
            "formula": "t=0: prior; t<=fallback_t: (5.2 prior + sum m)/(5.2 + t); else const + sum b_h EWMA_h + "
                       "b_shrink (n0 prior + sum m)/(n0 + t) + b_prior prior; EWMAs of minutes in games played, "
                       "initialised at the prior",
            "report": "scripts/inseason_model/report_minutes.json", "note": "docs/research/minutes_model.md"}
