"""Games-played (availability) model: fitted next-season GP, its uncertainty, playoff-week availability.

Everything here was fitted offline (scripts and outputs are summarised in
docs/research/availability_model.md); the constants below are the fitted values.

1. Expected games (82-game scale), OLS on 4,366 player-seasons (targets 2013-14..2025-26, players
   with >= 500 minutes the season before; full-season absences of players who later returned
   count as 0 games, players who never returned are excluded):

     gp = c + b1*g1 + b2*g2 + b3*g3 (+ missing-season constants m2, m3)
          + a1*max(0, age-28) + a2*max(0, age-32) + a3*max(0, 24-age)
          + h1*max(0, g1-70) + h2*max(0, mean(g1..g3)-70)

   g1..g3 = games in the last three seasons (most recent first) scaled to 82 games. Out of
   sample (fit <= 2023-24, test 2024-25 and 2025-26) it cuts GP RMSE by ~1.8-1.9 games and removes
   the +8 game optimism of the old "recency mean shrunk toward 68" (baseline-0.1).
2. gp_sd: residual sd of that model by predicted-gp level (linear interpolation between bucket
   means). Age and history volatility add nothing once the level is known (sd ratios 0.96-1.02).
   The residual distribution is left-skewed (median +5, 10th percentile about -25 to -35).
3. Playoff-week multiplier (Yahoo weeks 18-20, the 3 weeks ending a week before the NBA regular
   season ends): share of team games played in that window relative to the whole season, fitted on
   2010-11..2023-24 box scores (82-game seasons, single-team players, prior season >= 1000 min):

     mult = 0.958 - 0.077*(0.5 - team_prev_wpct) - 0.035*max(0, 60 - g1)/10

   Age was tested and does not matter here (+0.003 +- 0.013 per 4 years over 30).
"""

from __future__ import annotations

import math

import numpy as np

GP_MODEL_VERSION = "gp-ols-0.1"

GP_COEF: dict[str, float] = {
    "const": 27.5019,
    "g1": 0.2893,
    "g2": 0.1166,
    "m2": 5.9324,  # added when the player has no season two years back
    "g3": 0.0676,
    "m3": 4.2027,  # added when the player has no season three years back
    "age_gt28": -0.9456,
    "age_gt32": 0.7461,
    "age_lt24": 0.5469,
    "g1_gt70": 0.1916,
    "gavg_gt70": 0.3813,
}
GP_FIT = {
    "data": "sumitrodatta bbref mirror (Player Totals, Team Summaries), full history",
    "train_targets": "2014-2026 (bbref season labels), players with >= 500 min in T-1",
    "n": 4366,
    "oos": "fit T<=2024: RMSE 2025 21.00->19.06 (gain 1.94 +- 0.31), 2026 21.87->20.05 (1.82 +- 0.32)",
}
# residual sd by predicted gp (bucket mean prediction -> bucket residual sd)
GP_SD_KNOTS_X = (42.7, 49.3, 55.2, 60.3, 65.2, 70.1)
GP_SD_KNOTS_Y = (23.9, 22.3, 21.2, 18.8, 17.5, 15.3)

PLAYOFF_COEF = {"const": 0.958, "tank": -0.077, "injury": -0.035}
PLAYOFF_MULT_RANGE = (0.75, 1.0)

# 2025-26 regular-season records (bbref Team Summaries via the sumitrodatta mirror, fetched
# 2026-10-18). The prior-season record of a player's current team is the "tank" proxy the
# playoff-week multiplier was fitted with.
TEAM_RECORD_2025_26: dict[str, tuple[int, int]] = {
    "ATL": (46, 36), "BOS": (56, 26), "BRK": (20, 62), "CHI": (31, 51), "CHO": (44, 38),
    "CLE": (52, 30), "DAL": (26, 56), "DEN": (54, 28), "DET": (60, 22), "GSW": (37, 45),
    "HOU": (52, 30), "IND": (19, 63), "LAC": (42, 40), "LAL": (53, 29), "MEM": (25, 57),
    "MIA": (43, 39), "MIL": (32, 50), "MIN": (49, 33), "NOP": (26, 56), "NYK": (53, 29),
    "OKC": (64, 18), "ORL": (45, 37), "PHI": (45, 37), "PHO": (45, 37), "POR": (42, 40),
    "SAC": (22, 60), "SAS": (62, 20), "TOR": (46, 36), "UTA": (22, 60), "WAS": (17, 65),
}  # fmt: skip
TEAM_ALIASES = {"BKN": "BRK", "CHA": "CHO", "PHX": "PHO", "NOH": "NOP", "GS": "GSW", "SA": "SAS",
                "NY": "NYK", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}  # fmt: skip


def _nan(x: float | None) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def predict_gp(lags: list[float | None], age: float, cap: float = 80.0) -> float:
    """Expected games (82-game scale) from the last three seasons' games, most recent first.

    `lags[0]` must be present (the latest season the player played); missing older seasons
    (rookie years, seasons out of the league) use the fitted missing-season constants.
    """
    g = [None if _nan(v) else float(min(v, 82.0)) for v in (list(lags) + [None, None, None])[:3]]
    if g[0] is None:
        raise ValueError("predict_gp needs the most recent season's games")
    c = GP_COEF
    y = c["const"] + c["g1"] * g[0]
    y += c["g2"] * g[1] if g[1] is not None else c["m2"]
    y += c["g3"] * g[2] if g[2] is not None else c["m3"]
    y += c["age_gt28"] * max(0.0, age - 28) + c["age_gt32"] * max(0.0, age - 32)
    y += c["age_lt24"] * max(0.0, 24 - age)
    present = [v for v in g if v is not None]
    y += c["g1_gt70"] * max(0.0, g[0] - 70) + c["gavg_gt70"] * max(0.0, float(np.mean(present)) - 70)
    return float(np.clip(y, 0.0, cap))


def gp_sd_from_model(gp: float) -> float:
    """Calibrated sd of realised games around the fitted expectation."""
    return float(np.interp(gp, GP_SD_KNOTS_X, GP_SD_KNOTS_Y))


def team_prev_wpct(team: str | None) -> float:
    t = TEAM_ALIASES.get(str(team or "").upper(), str(team or "").upper())
    rec = TEAM_RECORD_2025_26.get(t)
    return 0.5 if rec is None else rec[0] / (rec[0] + rec[1])


def playoff_mult(team: str | None, g1: float | None) -> float:
    """Expected availability in fantasy playoff weeks relative to the season average.

    `g1`: games (82 scale) in the player's latest season; None (rookies) means no injury signal.
    """
    c = PLAYOFF_COEF
    m = c["const"] + c["tank"] * (0.5 - team_prev_wpct(team))
    if not _nan(g1):
        m += c["injury"] * max(0.0, 60.0 - float(g1)) / 10.0
    return float(np.clip(m, *PLAYOFF_MULT_RANGE))
