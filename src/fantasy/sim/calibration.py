"""Fitted simulator constants (docs/research/sim_calibration.md, 2026-10-18).

Data: NocturneBear/NBA-Data-2010-2024 regular-season box scores (2010-11..2023-24, incl. DNP rows) and the
sumitrodatta Basketball-Reference mirror (player totals 2011-2026). Values are for "rostered" players
(>= 24 projected or realised minutes per game), the population a 12-team league rosters. SEs are cluster
bootstraps (by player or by season, see the note).

These are simulator-only. The per-game noise variance is NOT here: it lives in fantasy.model.noise and is shared
with the draft valuation and optimizer.
"""

from __future__ import annotations

# Season-level projection error of the baseline projection method, out of sample (targets 2014-2024,
# rostered n = 1828 player-seasons; player-cluster bootstrap SE). Log scale for the factors; FG%/FT% in
# percentage points / 100. Sampling noise of a season mean is removed.
VOL_SD = 0.21  # common volume factor, fitted 0.207 (0.006) pooled over rostered players
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
# The error shrinks with projected minutes (n = 918 / 910 / 1319 player-seasons; SE in brackets):
# (min projected mpg, vol_sd, fg_pct_sd, ft_pct_sd), first match wins. Used when the projections carry `mpg`.
ERROR_BY_MPG = [
    (30.0, 0.153, 0.022, 0.038),  # vol 0.153 (0.008), FG 0.022 (0.001), FT 0.038 (0.003)
    (24.0, 0.247, 0.027, 0.041),  # vol 0.247 (0.008), FG 0.027 (0.001), FT 0.041 (0.002)
    (0.0, 0.330, 0.033, 0.049),  # 15-24 mpg: vol 0.330 (0.009), FG 0.033 (0.002), FT 0.049 (0.003)
]

# Game-to-game variance: fantasy.model.noise (one model shared by the draft and the simulator, noise-var-1.0).
# The constant-CV alternatives (GAME_CV_POOLED here, GAME_CV_PRIOR/GAME_CV_FITTED in model.lines) were deleted;
# git history keeps them.

# Correlation of team weekly noise given games, in the simulator's noise order (simulator-only: the draft
# objective, expected category wins, is a sum of per-category marginals, which correlation does not change). "fgm"/"ftm" stand for makes
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
