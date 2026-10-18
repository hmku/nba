"""Probability that a player is still on the board at a future pick.

Each player's draft position is modelled as Normal(adp, sd(adp)) where adp is the market's
expected draft slot (Yahoo ADP when known). Conditional on being available at the current pick k,
P(available at p) = S(p) / S(k) with S(x) = P(drafted after x).

sd grows with ADP (late-round picks are noisier). Constants are priors to be calibrated with
Yahoo draft results (backtesting task).
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm

SD_FLOOR = 2.5
SD_SLOPE = 0.18


def adp_sd(adp: np.ndarray) -> np.ndarray:
    return SD_FLOOR + SD_SLOPE * np.asarray(adp, dtype=float)


def survival(pick: float, adp: np.ndarray) -> np.ndarray:
    """P(player is drafted at a pick later than `pick`), unconditional."""
    adp = np.asarray(adp, dtype=float)
    return norm.sf((pick + 0.5 - adp) / adp_sd(adp))


def prob_available(at_pick: float, current_pick: float, adp: np.ndarray) -> np.ndarray:
    """P(available at `at_pick` | available now at `current_pick`)."""
    if at_pick <= current_pick:
        return np.ones_like(np.asarray(adp, dtype=float))
    # picks before current_pick are already resolved, so condition on surviving to current_pick - 1
    s_now = survival(current_pick - 1, adp)
    s_then = survival(at_pick - 1, adp)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(s_now > 1e-12, s_then / s_now, 0.0)
    return np.clip(p, 0.0, 1.0)
