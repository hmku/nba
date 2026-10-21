"""Games-played (availability) model: fitted next-season GP, its uncertainty, playoff-week availability.

Everything here was fitted offline (scripts and outputs are summarised in
docs/research/availability_model.md); the constants below are the fitted values.

1. Expected games (82-game scale), gp-ols-0.2 (2026-10-21; fit script scripts/gp_model/fit_gp.py), three parts:

   main (>= 500 minutes last season), OLS on 4,366 player-seasons (targets 2013-14..2025-26; full-season
   absences of players who later returned count as 0 games, players who never returned are excluded):

     raw = c + b1*g1 + b2*g2 + b3*g3 (+ missing-season constants m2, m3)
           + a1*max(0, age-28) + a2*max(0, age-32) + a3*max(0, 24-age)*[g1 < 70]
           + h1*max(0, g1-70) + h2*max(0, mean(g1..g3)-70) + r*max(0, 30-mpg1)

   calibration (recent era): gp = raw + a + h*max(0, raw-65), fitted on rolling-origin one-year-ahead
   forecast errors 2018-19..2023-24 (each season forecast by a main fit on earlier seasons only; later
   seasons are excluded because their 0-game seasons cannot be observed yet). The forecasts ran ~1.4 games
   high in most seasons since 2018 (league games-played trend) and a bit more at the top.

   low (< 500 minutes last season, or no season at all last year = "returning from a lost season"),
   OLS on 1,315 player-seasons: gp = c + p*min(max(mp2, mp3), 3000)/1000 + b*g1 + a*max(0, age-28)
   + y*max(0, 22-age) (teenage / age-21 prospects grow into a role) + l*lost + m*mpg1.
   Between 400 and 600 minutes last season the two models are blended linearly (no jump at 500).

   g1..g3 = games in the last three seasons (most recent first) scaled to 82 games, mpg1 = minutes per
   game last season, mp2/mp3 = minutes two and three seasons back. Out of sample (fit <= 2023-24, test
   2024-25 and 2025-26) RMSE vs gp-ols-0.1: 18.58 vs 19.06 (gain 0.48 +- 0.21) and 19.93 vs 20.05
   (0.13 +- 0.19); the specification was chosen on 2019-26 rolling residuals, so the honest gain is ~0.1-0.3.
   Low-minute players: RMSE 23.5/24.3 -> 20.9/21.2, bias -9.5 -> +3 games.
2. gp_sd: residual sd of that model by predicted-gp level (linear interpolation between bucket
   means). Age and history volatility add nothing once the level is known (sd ratios 0.96-1.02).
   The residual distribution is left-skewed (median +5, 10th percentile about -25 to -35).
3. Playoff-week multiplier (league 44495: Yahoo weeks 18-20 = Mar 1-21, 2027, i.e. the 3 weeks ending
   21 days before the NBA regular season ends on Apr 11): share of team games played in that window
   relative to the whole season, fitted on 2010-11..2023-24 box scores (82-game seasons, single-team
   players, prior season >= 1000 min), window = the 21 days ending 21 days before each season's last
   regular-season day:

     mult = 0.984 - 0.019*(0.5 - team_prev_wpct)

   (playoff-mult-0.2, 2026-10-18). The first fit (playoff-mult-0.1: 0.958 - 0.077*tank
   - 0.035*max(0, 60 - g1)/10) used the 3 weeks ending 7 days before the season end (~Mar 15 - Apr 4),
   which is where late-season tanking/resting concentrates; it does not apply to this league's
   calendar. On the correct window the injury-history term is +0.018 +- 0.014 with an unstable sign
   across eras (-0.005 for 2011-17, +0.035 for 2018-24), so it is dropped (coefficient 0); the tank
   term is small and not significant but keeps its plausible sign. Age was not significant before
   either.
"""

from __future__ import annotations

import math

import numpy as np

GP_MODEL_VERSION = "gp-ols-0.2"

# main model, players with >= GP_LOW_MIN minutes last season (HC1 SEs in docs/research/availability_model.md)
GP_COEF: dict[str, float] = {
    "const": 35.9477,
    "g1": 0.2728,
    "g2": 0.0825,
    "m2": 4.8312,  # added when the player has no season two years back
    "g3": 0.0394,
    "m3": 3.8565,  # added when the player has no season three years back
    "age_gt28": -0.9234,
    "age_gt32": 0.8509,
    "age_lt24": 0.6745,  # only when g1 < 70: a young player who already plays every game can't grow into more
    "g1_gt70": 0.1028,
    "gavg_gt70": 0.3856,
    "mpg_lt30": -0.5721,  # per minute of mpg below 30 last season: bench players lose games to coaching decisions
}
# recent-era calibration of the main model: gp = raw + a + h*max(0, raw - knee)
GP_CAL = {"a": -1.436, "h": -0.156, "knee": 65.0}
# players with < GP_LOW_MIN minutes last season (or no season: lost to injury), incl. "returning from a lost season"
GP_LOW_MIN = 500.0
GP_LOW_COEF = {"const": 11.3969, "prior_k": 4.5261, "g1": 0.4784, "age_gt28": -1.4025, "age_lt22": 7.2051,
               "lost": 18.3118, "mpg1": 0.6338}
GP_BLEND_MIN = (400.0, 600.0)  # last-season minutes over which low and main model are blended linearly
GP_LOW_PRIOR_CAP = 3000.0  # prior_k = min(max(mp2, mp3), cap) / 1000
GP_LOW_SD = 23.2  # in-sample residual sd of the low-minute model (OOS RMSE 20.9-21.2: slightly conservative)
GP_FIT = {
    "data": "sumitrodatta bbref mirror (Player Totals, Team Summaries), full history; scripts/gp_model/fit_gp.py",
    "train_targets": "2014-2026 (bbref season labels); main: >= 500 min in T-1 (n=4366); low: < 500 min or lost season (n=1315)",
    "calibration": "rolling-origin forecast errors 2019-2024: a -1.44 +- 0.48, h -0.16 +- 0.17 above 65 "
                   "(2025-26 excluded: censored zeros; incl. them h = -0.30)",
    "oos": "fit T<=2024 vs gp-ols-0.1: RMSE 2025 19.06->18.58 (gain 0.48 +- 0.21), 2026 20.05->19.93 (0.13 +- 0.19); "
           "low-minute players 23.5->20.9 / 24.3->21.2; spec chosen on 2019-26 rolling residuals, honest gain ~0.1-0.3",
}
# residual sd by predicted gp (bucket mean prediction -> bucket residual sd), calibrated rolling-origin forecasts
GP_SD_KNOTS_X = (45.9, 51.4, 55.4, 58.9, 62.3, 67.0)
GP_SD_KNOTS_Y = (22.3, 22.3, 18.8, 17.7, 17.5, 17.3)

PLAYOFF_MODEL_VERSION = "playoff-mult-0.2"
# window: 21 days ending 21 days before the last regular-season day (fantasy playoffs Mar 1-21, 2027)
PLAYOFF_COEF = {"const": 0.984, "tank": -0.019, "injury": 0.0}
PLAYOFF_FIT = {
    "window": "3 weeks ending 21 days before the NBA regular season's last day",
    "data": "NocturneBear/NBA-Data-2010-2024 box scores, 82-game seasons 2011-2024, single-team players, prior mp>=1000",
    "n": 2339,
    "full_model": "const 0.982 +- 0.006, tank -0.023 +- 0.034, injury +0.018 +- 0.014 (season-cluster bootstrap)",
    "chosen": "const+tank: const 0.984 +- 0.005, tank -0.019 +- 0.034; injury dropped",
    "previous": "playoff-mult-0.1 (window ending 7 days before season end): 0.958, tank -0.077 +- 0.045, injury -0.035 +- 0.015",
}
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


def _present(x: float | None) -> float | None:
    """`x` unchanged, or None when it is missing (None or NaN)."""
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def is_low_minutes(minutes: list[float | None]) -> bool:
    """True when the latest season (`minutes[0]`) had < GP_LOW_MIN minutes or was not played at all."""
    mp1 = _present(minutes[0]) if minutes else None
    return mp1 is None or float(mp1) < GP_LOW_MIN


def predict_gp(lags: list[float | None], age: float, minutes: list[float | None], cap: float = 80.0) -> float:
    """Expected games (82-game scale) from the last three seasons, most recent first.

    `lags`: games per season (82 scale); `minutes`: minutes per season, same order. A season the player
    sat out entirely (lost to injury) is games 0 / minutes 0 (or None); missing older seasons (rookie years,
    seasons out of the league) use the fitted missing-season constants. Players with < GP_LOW_MIN minutes
    last season use the low-minute model (prior minutes, g1, mpg, lost season, age), everyone else the main
    model plus the recent-era calibration; the two are blended linearly over GP_BLEND_MIN minutes.
    """
    lags3 = (list(lags) + [None, None, None])[:3]
    mins3 = (list(minutes) + [None, None, None])[:3]
    g = [None if v is None else float(min(v, 82.0)) for v in map(_present, lags3)]
    mp = [None if v is None else float(v) for v in map(_present, mins3)]
    mp1 = mp[0] or 0.0
    lo, hi = GP_BLEND_MIN
    low = _predict_low(g, mp, age, cap) if mp1 < hi else 0.0
    if mp1 < lo:
        return low
    main = _predict_main(g, mp, age, cap)
    w = min(1.0, (mp1 - lo) / (hi - lo))
    return w * main + (1.0 - w) * low


def _predict_low(g: list[float | None], mp: list[float | None], age: float, cap: float) -> float:
    lc = GP_LOW_COEF
    g1, mp1 = g[0] or 0.0, mp[0] or 0.0
    prior = max([v for v in mp[1:] if v is not None], default=0.0)
    lost = 1.0 if g1 <= 0 and mp1 <= 0 else 0.0
    y = (lc["const"] + lc["prior_k"] * min(prior, GP_LOW_PRIOR_CAP) / 1000 + lc["g1"] * g1
         + lc["age_gt28"] * max(0.0, age - 28) + lc["age_lt22"] * max(0.0, 22 - age)
         + lc["lost"] * lost + lc["mpg1"] * (mp1 / g1 if g1 > 0 else 0.0))
    return float(np.clip(y, 0.0, cap))


def _predict_main(g: list[float | None], mp: list[float | None], age: float, cap: float) -> float:
    if g[0] is None or g[0] <= 0:
        raise ValueError("predict_gp needs the most recent season's games when it had minutes")
    c = GP_COEF
    y = c["const"] + c["g1"] * g[0]
    y += c["g2"] * g[1] if g[1] is not None else c["m2"]
    y += c["g3"] * g[2] if g[2] is not None else c["m3"]
    y += c["age_gt28"] * max(0.0, age - 28) + c["age_gt32"] * max(0.0, age - 32)
    if g[0] < 70:
        y += c["age_lt24"] * max(0.0, 24 - age)
    present = [v for v in g if v is not None]
    y += c["g1_gt70"] * max(0.0, g[0] - 70) + c["gavg_gt70"] * max(0.0, float(np.mean(present)) - 70)
    y += c["mpg_lt30"] * max(0.0, 30.0 - float(mp[0] or 0.0) / g[0])
    raw = float(np.clip(y, 0.0, cap))
    y = raw + GP_CAL["a"] + GP_CAL["h"] * max(0.0, raw - GP_CAL["knee"])
    return float(np.clip(y, 0.0, cap))


def gp_sd_from_model(gp: float, low_minutes: bool = False) -> float:
    """Calibrated sd of realised games around the fitted expectation."""
    if low_minutes:
        return GP_LOW_SD
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
    g1 = _present(g1)
    if g1 is not None:
        m += c["injury"] * max(0.0, 60.0 - float(g1)) / 10.0
    return float(np.clip(m, *PLAYOFF_MULT_RANGE))
