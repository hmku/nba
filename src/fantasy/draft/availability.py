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
NO_ADP_SD_MULT = 1.6  # players without a market ADP (valuation-rank stand-in) are noisier


def adp_sd(adp: np.ndarray) -> np.ndarray:
    return SD_FLOOR + SD_SLOPE * np.asarray(adp, dtype=float)


def sd_mult_for(has_adp: np.ndarray | list[bool]) -> np.ndarray:
    """Per-player multiplier on `adp_sd`: 1 with a market ADP, NO_ADP_SD_MULT without."""
    return np.where(np.asarray(has_adp, dtype=bool), 1.0, NO_ADP_SD_MULT)


def sample_market_positions(
    market_rank: np.ndarray, rng: np.random.Generator, sd_mult: np.ndarray | float = 1.0, noise: float = 1.0,
) -> np.ndarray:
    """One noisy market draft: position ~ Normal(market rank, adp_sd(rank) * sd_mult * noise).

    The single opponent model shared by `draft simulate`, `sim` roster completion and the research rooms
    (one rng.normal draw of len(market_rank)); sort ascending for the draft order."""
    mr = np.asarray(market_rank, dtype=float)
    return mr + rng.normal(0, 1, len(mr)) * adp_sd(mr) * sd_mult * noise


def survival(pick: float, adp: np.ndarray, sd_mult: np.ndarray | float = 1.0) -> np.ndarray:
    """P(player is drafted at a pick later than `pick`), unconditional."""
    adp = np.asarray(adp, dtype=float)
    return norm.sf((pick + 0.5 - adp) / (adp_sd(adp) * sd_mult))


def prob_available(
    at_pick: float, current_pick: float, adp: np.ndarray, sd_mult: np.ndarray | float = 1.0
) -> np.ndarray:
    """P(available at `at_pick` | available now at `current_pick`)."""
    if at_pick <= current_pick:
        return np.ones_like(np.asarray(adp, dtype=float))
    # picks before current_pick are already resolved, so condition on surviving to current_pick - 1
    s_now = survival(current_pick - 1, adp, sd_mult)
    s_then = survival(at_pick - 1, adp, sd_mult)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(s_now > 1e-12, s_then / s_now, 0.0)
    return np.clip(p, 0.0, 1.0)
