"""Game-to-game noise: the ONE per-game variance model for the draft and the season simulator.

Used by the valuation's G-scores (fantasy.model.valuation), the draft optimizer's category model
(fantasy.model.catmodel), the market-implied projections (fantasy.sim.market_truth) and the season simulator
(fantasy.sim.engine).

Model (fitted, docs/research/sim_calibration.md sections 2 and 8): given that a player plays, the variance of
a raw stat in one game is a function of his per-game mean m,

    var(stat, m) = a_stat * m + b_stat * m^2      (clipped at 0)

fitted by least squares on 2,451 rostered player-seasons (>= 24 mpg, >= 30 games, 2010-11..2023-24 box
scores). b is ~0, so the variance is close to proportional to the mean (over-dispersed Poisson; FTA ~2.2
because free throws come in pairs), not to the mean squared. Out of sample it beats the constant-CV model it
replaced on every stat (higher game-level log-likelihood, weekly variance calibrated across minutes tiers).
The constant-CV models (GAME_CV_PRIOR, GAME_CV_FITTED, GAME_CV_POOLED) were deleted in noise-var-1.0;
git history keeps them.

Applied to *effective* per-scheduled-game lines (availability-blended, fantasy.model.lines.effective_lines),
the linear term gives a * (avail * m) = avail * var(m): exactly the variance of a player who plays a share
`avail` of the games. (The b * m^2 term is negligible; Bernoulli games-played variance is not modelled here.)

Weekly aggregation: a team's weekly variance is the sum over its players of games * var(stat, m) (players
independent). Within-game correlation across stats (sim.calibration.NOISE_CORR) is a simulator-only feature:
the draft objective, expected category wins, is a sum of per-category marginal probabilities, so cross-stat
correlation does not change it.
"""

from __future__ import annotations

import numpy as np

from fantasy.model.lines import STATS

# Recorded in valuation, draft_sim and season_sim run params ("noise_model").
NOISE_MODEL_VERSION = "noise-var-1.0"

# (a, b) per raw stat; values from sim_calibration.md section 2 (full 2010-24 fit, rostered >= 24 mpg).
GAME_VAR: dict[str, tuple[float, float]] = {
    "fgm": (1.377, -0.032),
    "fga": (1.430, -0.010),
    "ftm": (1.969, -0.001),
    "fta": (2.198, -0.009),
    "fg3m": (1.152, -0.004),
    "pts": (3.342, -0.028),
    "reb": (1.258, 0.014),
    "ast": (1.243, -0.002),
    "stl": (1.036, 0.022),
    "blk": (1.052, 0.065),
    "tov": (1.049, -0.020),
}
_A = np.array([GAME_VAR[s][0] for s in STATS])
_B = np.array([GAME_VAR[s][1] for s in STATS])


def game_var(stat: str, mean: np.ndarray | float) -> np.ndarray:
    """Per-game variance of raw `stat` for per-game mean(s) `mean` (any shape; vectorised)."""
    a, b = GAME_VAR[stat]
    m = np.asarray(mean, dtype=float)
    return np.maximum(a * m + b * m * m, 0.0)


def game_var_matrix(means: np.ndarray) -> np.ndarray:
    """Per-game variance of every raw stat; the last axis of `means` is fantasy.model.lines.STATS."""
    m = np.asarray(means, dtype=float)
    if m.shape[-1] != len(STATS):
        raise ValueError(f"last axis must have {len(STATS)} stats (lines.STATS), got shape {m.shape}")
    return np.maximum(_A * m + _B * m * m, 0.0)


def ratio_var(makes: np.ndarray, attempts: np.ndarray, p_ref: np.ndarray | float, den: str) -> np.ndarray:
    """Per-game variance of the percentage-category "impact" makes - p_ref * attempts (delta method).

    = attempts * p * (1 - p)           binomial makes given attempts (p = makes / attempts, p_ref if no attempts)
      + var(den, attempts) * (p - p_ref)^2   volume noise, weighted by how far the player's % is from p_ref
    """
    m = np.asarray(makes, dtype=float)
    a = np.asarray(attempts, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(a > 0, m / np.where(a > 0, a, 1.0), p_ref)
    return a * p * (1 - p) + game_var(den, a) * (p - p_ref) ** 2
