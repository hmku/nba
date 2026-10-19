"""Game-to-game noise: the ONE per-game variance model for the draft and the season simulator.

Used by the valuation's G-scores (fantasy.model.valuation), the draft optimizer's category model
(fantasy.model.catmodel), the market-implied projections (fantasy.sim.market_truth) and the season simulator
(fantasy.sim.engine).

Model (fitted, docs/research/sim_calibration.md sections 2 and 8): given that a player plays, the variance of
a raw stat in one game is a function of his per-game mean m,

    var(stat, m) = a_stat * m + b_stat * m^2      (clipped at 0)

fitted by least squares on 2,451 rostered player-seasons (>= 24 mpg, >= 30 games, 2010-11..2023-24 box
scores). The linear term dominates (over-dispersed Poisson; FTA's a is ~2.2 because free throws come in pairs), so
the variance grows roughly with the mean, not with the mean squared. b is small per unit but NOT negligible at
star volume: for pts at m = 32, a*m = 107 and b*m^2 = -29, so the variance is 78 (-27%). Out of sample, b adds
little likelihood over a*m alone (<= 0.009 per game, every stat, both splits).

Out of sample (docs/research/noise_oos_2026-10-19.py, sim_calibration.md section 8) it beats the constant-CV
model it replaced on game-level log-likelihood for every stat in both splits. Weekly variance is NOT fully
calibrated: mean weekly z^2 at 24-30 mpg is FGA 1.33 / 1.46 (split A / B), PTS 1.21 / 1.23, FGM 1.14 / 1.23,
REB 1.05 / 1.23: the model under-states weekly variance of those stats by 14-46% in that tier (REB split A
5%); the other stats are at 0.87-1.16, and the under-statement is larger at 15-24 mpg.
The constant-CV models (GAME_CV_PRIOR, GAME_CV_FITTED, GAME_CV_POOLED) were deleted in noise-var-1.0;
git history keeps them.

Applied to *effective* per-scheduled-game lines (availability-blended, fantasy.model.lines.effective_lines),
the linear term gives a * (avail * m) = avail * a * m: exactly that term's variance for a player who plays a
share `avail` of the games. The b * m^2 term evaluated at avail * m is not exact (it is scaled by avail^2, not
avail), and Bernoulli games-played variance is not modelled here.

Weekly aggregation: a team's weekly variance is the sum over its players of games * var(stat, m) (players
independent). Within-game correlation across stats (sim.calibration.NOISE_CORR) is a simulator-only feature.
It does not change expected category wins (the default draft objective), which is a sum of per-category
marginal probabilities. It WOULD change catmodel.matchup_win_prob (P(more category wins than losses)), which
assumes independent categories: the matchup win % the optimizer reports, and its objective="matchup" mode,
ignore cross-category correlation.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

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
    # clipped at 0: makes > attempts (p > 1, a bad projection row) would otherwise give a negative variance
    return np.maximum(a * p * (1 - p) + game_var(den, a) * (p - p_ref) ** 2, 0.0)


def check_finite(lines: pd.DataFrame, stats: Iterable[str], where: str) -> None:
    """Raise ValueError naming the first player and stat whose value is NaN or +/-inf."""
    cols = list(stats)
    vals = lines[cols].to_numpy(dtype=float)
    bad = ~np.isfinite(vals)
    if bad.any():
        i, j = np.argwhere(bad)[0]
        raise ValueError(
            f"{where}: non-finite projection for player {lines.index[i]!r}, stat {cols[j]!r} "
            f"({vals[i, j]}); {int(bad.sum())} bad value(s) in total. Fix the projections before valuing."
        )
