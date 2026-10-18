"""Fitted simulator constants (docs/research/sim_calibration.md, 2026-10-18).

Data: NocturneBear/NBA-Data-2010-2024 regular-season box scores (2010-11..2023-24, incl. DNP rows) and the
sumitrodatta Basketball-Reference mirror (player totals 2011-2026). Values are for "rostered" players
(>= 24 projected or realised minutes per game), the population a 12-team league rosters. SEs are cluster
bootstraps (by player or by season, see the note).

These are simulator-only. The draft valuation still uses `fantasy.model.lines.GAME_CV`.
"""

from __future__ import annotations

# Season-level projection error of the baseline projection method, out of sample (targets 2014-2024,
# rostered n = 1828 player-seasons; player-cluster bootstrap SE). Log scale for the factors; FG%/FT% in
# percentage points / 100. Sampling noise of a season mean is removed.
VOL_SD = 0.21  # common volume factor, fitted 0.207 (0.006)
STAT_SD = {  # stat-specific factor on top of the volume factor (equal-loading one-factor model)
    "fga": 0.15,  # 0.153 (0.006)
    "fta": 0.28,  # 0.284 (0.008)
    "fg3m": 0.37,  # 0.366 (0.019)
    "reb": 0.05,  # 0.053 (0.019)
    "ast": 0.24,  # 0.236 (0.007)
    "stl": 0.06,  # 0.061 (0.024)
    "blk": 0.18,  # 0.181 (0.013)
    "tov": 0.16,  # 0.163 (0.008)
}
FG_PCT_SD = 0.025  # 0.025 (0.001)
FT_PCT_SD = 0.039  # 0.039 (0.002)

# Game-to-game variance given that the player plays: var = a * mean + b * mean^2 (least squares across
# 2451 rostered player-seasons with >= 30 games). b is ~0, so variance is close to proportional to the
# mean (over-dispersed Poisson), not a constant CV.
GAME_VAR = {
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
# Constant-CV refit (pooled over rostered player-seasons; bootstrap SE <= 0.03). Reported for the draft
# valuation experiment (`FANTASY_GAME_CV=fitted`, fantasy.model.lines); the simulator uses GAME_VAR.
GAME_CV_POOLED = {
    "fgm": 0.442, "fga": 0.316, "ftm": 0.746, "fta": 0.702, "fg3m": 0.761, "pts": 0.416,
    "reb": 0.449, "ast": 0.512, "stl": 0.972, "blk": 1.038, "tov": 0.680,
}

# Correlation of team weekly noise given games, in the simulator's noise order. "fgm"/"ftm" stand for makes
# given attempts (fgm - p * fga). Covariance-pooled within-player game-level correlation (150,100 player-games;
# season-cluster bootstrap SE <= 0.01).
NOISE_ORDER = ["fga", "fgm", "fta", "ftm", "fg3m", "reb", "ast", "stl", "blk", "tov"]
NOISE_CORR = [
    [1.000, 0.081, 0.192, 0.011, 0.364, 0.317, 0.142, 0.138, 0.082, 0.064],
    [0.081, 1.000, 0.051, -0.003, 0.392, 0.007, 0.030, 0.035, 0.015, 0.041],
    [0.192, 0.051, 1.000, 0.034, 0.041, 0.173, 0.067, 0.076, 0.049, 0.061],
    [0.011, -0.003, 0.034, 1.000, 0.004, 0.000, 0.013, 0.004, 0.001, 0.006],
    [0.364, 0.392, 0.041, 0.004, 1.000, 0.074, 0.062, 0.048, 0.028, 0.044],
    [0.317, 0.007, 0.173, 0.000, 0.074, 1.000, 0.157, 0.063, 0.116, 0.118],
    [0.142, 0.030, 0.067, 0.013, 0.062, 0.157, 1.000, 0.100, 0.041, 0.072],
    [0.138, 0.035, 0.076, 0.004, 0.048, 0.063, 0.100, 1.000, 0.030, 0.065],
    [0.082, 0.015, 0.049, 0.001, 0.028, 0.116, 0.041, 0.030, 1.000, 0.034],
    [0.064, 0.041, 0.061, 0.006, 0.044, 0.118, 0.072, 0.065, 0.034, 1.000],
]
