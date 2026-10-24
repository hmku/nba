"""Recency-weighted in-season minutes model (minutes-*). See docs/research/minutes_model.md.

Predicts a player's expected minutes per game in his NEXT games played from information available at prediction
time only: a regression WITHOUT intercept on two exponentially-weighted moving averages (EWMAs) of his minutes in
games played this season and his preseason projected mpg (the prior):

  e_h(0) = prior;  e_h(t) = a_h * e_h(t-1) + (1 - a_h) * m_t,  a_h = 0.5 ** (1 / half_life_h)   (games played)
  mpg    = b_short * e_short + b_long * e_long + b_prior * prior          (t >= 1; t = 0 -> prior exactly)

Each EWMA starts at the player's prior, so early in the season it is mostly the prior and the weight on this season
grows with games played (no games-played interaction needed: chosen out of sample, see the note). No intercept: the
prediction shrinks toward the player's OWN prior, not toward a population constant. Per-minute rates are not modelled
here (inseason.shrink_line keeps shrinking them toward the prior in minutes). The prediction is conditional on
playing (availability is separate).

FITTED by weighted least squares on ESPN 2024-25 box scores (half-lives by 5-fold player-clustered CV on 2024-25),
tested out of sample on 2025-26: scripts/inseason_model/fit_minutes.py -> report_minutes.json.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

MINUTES_MODEL_VERSION = "minutes-0.1.0"
MPG_MAX = 48.0


@dataclass(frozen=True)
class MinutesParams:
    hl_short: float  # half-life of the short EWMA, games played
    hl_long: float  # half-life of the long EWMA, games played
    b_short: float
    b_long: float
    b_prior: float
    target: str = "next5"

    def as_dict(self) -> dict:
        return {**asdict(self), "coef_sum": round(self.b_short + self.b_long + self.b_prior, 4)}


# FITTED (scripts/inseason_model/report_minutes.json "deployed"): train ESPN 2024-25, every prediction point t >= 1
# games played of every player-season with a prior; target = mean minutes over his next 5 games played.
FITTED: dict[str, MinutesParams] = {
    # coefficient SEs (cluster bootstrap over players): 0.0215, 0.0359, 0.0188; sum 0.9998 +- 0.0021
    "next5": MinutesParams(hl_short=1.5, hl_long=15.0, b_short=0.5606, b_long=0.4034, b_prior=0.0358, target="next5"),
}
DEFAULT_HORIZON = "next5"


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

    minutes: his games PLAYED this season in date order (minutes > 0). Columns: t, prior, e_short, e_long, mean,
    last3 (the last two for reference / baselines)."""
    p = params or default_params()
    m = np.asarray(minutes, float)
    n = len(m)
    t = np.arange(n + 1)
    cs = np.concatenate([[0.0], np.cumsum(m)])
    lo = np.maximum(t - 3, 0)
    prior = float(prior_mpg)
    return pd.DataFrame({
        "t": t,
        "prior": prior,
        "e_short": ewma(m, prior, p.hl_short),
        "e_long": ewma(m, prior, p.hl_long),
        "mean": np.where(t > 0, cs / np.maximum(t, 1), prior),
        "last3": np.where(t > 0, (cs[t] - cs[lo]) / np.maximum(t - lo, 1), prior),
    })


def predict(feats: pd.DataFrame, params: MinutesParams | None = None) -> np.ndarray:
    """Expected mpg in the next games played for each feature row (t = 0 -> the prior exactly)."""
    p = params or default_params()
    prior = feats["prior"].to_numpy(float)
    lin = (p.b_short * feats["e_short"].to_numpy(float) + p.b_long * feats["e_long"].to_numpy(float)
           + p.b_prior * prior)
    return np.where(feats["t"].to_numpy(float) > 0, np.clip(lin, 0.0, MPG_MAX), prior)


def project_mpg(minutes, prior_mpg: float, params: MinutesParams | None = None) -> float:
    """Expected mpg in his next games played given his minutes in the games played so far (empty -> prior_mpg)."""
    f = feature_panel(minutes, prior_mpg, params)
    return float(predict(f.iloc[[-1]], params)[0])


def model_meta(params: MinutesParams | None = None) -> dict:
    p = params or default_params()
    return {"version": MINUTES_MODEL_VERSION, "params": p.as_dict(),
            "formula": "t=0: prior; else b_short*EWMA(hl_short) + b_long*EWMA(hl_long) + b_prior*prior, no "
                       "intercept; EWMAs of minutes in games played, initialised at the prior",
            "report": "scripts/inseason_model/report_minutes.json", "note": "docs/research/minutes_model.md"}
