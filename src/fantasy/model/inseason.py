"""In-season (rest-of-season) projection update. See docs/research/inseason_updates.md.

The preseason projections (data/processed/projections_2026_27.csv: per-game line + gp/gp_sd) are the prior; ESPN box
scores to date (`fantasy data fetch-boxscores`, data/raw/espn_boxscores/) are the evidence. Two fitted updates:

1. Availability (ros-avail-*): the share of his team's REMAINING games a player plays, from the prior share p0 = gp/82,
   the observed share o = played/k after k team games, the current run of missed games (injuries come in spells, so
   an ongoing absence predicts more absence; an iid Beta-binomial update ignores that and over-reacts to a hot start)
   and the share of games he was dressed for but did not play (coach's decision). Per checkpoint k a weighted OLS on
   2011-2026 player timelines (scripts/inseason_model/fit_availability.py); k = 0 is a fitted calibration of the prior
   to the timeline share; between checkpoints mean and variance are interpolated linearly in k.
2. Per-game stats (stat-shrink-*): observed rates shrunk toward the prior with stat-specific stabilisation constants
   (minutes per game in games; counting stats per minute in minutes; FG%/FT% in attempts), fitted on 2012-2022
   (scripts/inseason_model/fit_stats.py).

Output keeps the projections schema so every consumer works unchanged; `gp` becomes the 82-game-equivalent
rest-of-season play rate (82 x expected share of remaining team games played: consumers use gp / 82 as the per-game
probability of playing) and `gp_sd` its sd on the same scale; `status` is refreshed (ESPN injury feed, else cleared
once a preseason-injured player is back); `playoff_mult` is renormalised to the remaining calendar. Season-to-date
columns are added (gp_preseason, games_played, team_games, missed_streak, season_gp_exp, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ROS_AVAIL_VERSION = "ros-avail-0.3"
STAT_SHRINK_VERSION = "stat-shrink-0.1"
# rest-of-season projection model (distinct from fantasy.inseason.INSEASON_VERSION, the in-season analysis tools)
INSEASON_MODEL_VERSION = f"ros-proj-0.3.0+{ROS_AVAIL_VERSION}+{STAT_SHRINK_VERSION}"
SEASON_GAMES = 82
GTD_EXPECTED_MISSED = 1.0  # PRIOR: games a preseason day-to-day (gtd) player is expected to miss at the start
RETURN_SD_PER_GAME = 0.15  # projections.py: gp_sd += 0.15 x games_missed (return-timing uncertainty), kept in-season
PLAYOFF_CALENDAR_SHARE = 3 / 20  # = lines.PLAYOFF_CALENDAR_SHARE: how consumers decode playoff_mult
GP_SD_FLOOR = 0.5  # gp_sd 0 would make consumers substitute their default sd (sim engine: 12)
INJURY_FEED_MAX_AGE_DAYS = 3
LOW_CONF_MAX_K, LOW_CONF_DGP = 3, 10.0  # early big moves are flagged low-confidence in the meta (review finding)
ENTRY_GRACE = 5  # listed within his first team's first 5 games -> on the roster since opening night
FEATURES = ["const", "p0", "o", "log_streak", "all_missed", "dnp"]

# --- availability, FITTED (scripts/inseason_model/report_avail.json, production_model): per checkpoint k >= 1
# Rhat = const + p0*p0 + o*o + log_streak*log1p(streak) + all_missed*[x=0] + dnp*(dnp/k) - level(p0), where
# level(p0) = const_0 + (p0_0 - 1) p0 is the k = 0 fit (the prior's level vs the timeline share, no out-of-sample
# support, so removed: k = 0 deploys the prior itself and the change after games is information only);
# k = 1: o, streak, all_missed are collinear with x (min-norm fit, predictions exact);
# var(R - Rhat) = a * Rhat (1 - Rhat) + c / remaining + d * log1p(streak) (NNLS); k = 0 uses the prior's sd.
ROS_AVAIL_COEF: dict[int, dict[str, float]] = {
    0: {'const': 0.0556, 'p0': 0.9762, 'o': 0.0, 'log_streak': 0.0, 'all_missed': 0.0, 'dnp': 0.0},
    1: {'const': 0.1129, 'p0': 0.6773, 'o': 0.1986, 'log_streak': -0.0594, 'all_missed': -0.0858, 'dnp': 0.1699},
    2: {'const': -0.0499, 'p0': 0.6357, 'o': 0.3966, 'log_streak': 0.0377, 'all_missed': 0.0043, 'dnp': 0.1556},
    3: {'const': -0.0261, 'p0': 0.6164, 'o': 0.3892, 'log_streak': 0.0222, 'all_missed': -0.0109, 'dnp': 0.1479},
    5: {'const': 0.0418, 'p0': 0.5941, 'o': 0.3414, 'log_streak': -0.0302, 'all_missed': -0.0012, 'dnp': 0.1249},
    10: {'const': 0.1433, 'p0': 0.519, 'o': 0.3046, 'log_streak': -0.0571, 'all_missed': -0.0223, 'dnp': 0.0396},
    15: {'const': 0.2139, 'p0': 0.4654, 'o': 0.2795, 'log_streak': -0.0664, 'all_missed': -0.0517, 'dnp': -0.0365},
    20: {'const': 0.2416, 'p0': 0.4207, 'o': 0.2907, 'log_streak': -0.0726, 'all_missed': -0.0502, 'dnp': -0.0622},
    30: {'const': 0.2802, 'p0': 0.3529, 'o': 0.311, 'log_streak': -0.0802, 'all_missed': -0.0667, 'dnp': -0.0935},
    40: {'const': 0.3526, 'p0': 0.2812, 'o': 0.2986, 'log_streak': -0.1029, 'all_missed': -0.0322, 'dnp': -0.1292},
    50: {'const': 0.3789, 'p0': 0.2134, 'o': 0.3237, 'log_streak': -0.1143, 'all_missed': 0.0432, 'dnp': -0.0707},
    60: {'const': 0.5577, 'p0': 0.111, 'o': 0.2186, 'log_streak': -0.1634, 'all_missed': 0.1143, 'dnp': -0.1552},
}  # fmt: skip
ROS_AVAIL_VAR: dict[int, dict[str, float]] = {
    0: {'a': 0.2885, 'c': 0.0, 'd': 0.0},
    1: {'a': 0.2249, 'c': 0.0, 'd': 0.0259},
    2: {'a': 0.222, 'c': 0.0, 'd': 0.0203},
    3: {'a': 0.2232, 'c': 0.0, 'd': 0.017},
    5: {'a': 0.2218, 'c': 0.0, 'd': 0.0146},
    10: {'a': 0.2146, 'c': 0.0, 'd': 0.0141},
    15: {'a': 0.216, 'c': 0.0, 'd': 0.0136},
    20: {'a': 0.2217, 'c': 0.0, 'd': 0.0116},
    30: {'a': 0.2415, 'c': 0.0, 'd': 0.0092},
    40: {'a': 0.2488, 'c': 0.0, 'd': 0.0104},
    50: {'a': 0.2772, 'c': 0.0, 'd': 0.0108},
    60: {'a': 0.2915, 'c': 0.0, 'd': 0.0104},
}  # fmt: skip
ROS_AVAIL_FIT: dict[str, str] = {
    "data": "player timelines 2011-2026 excl. 2020-21 (NocturneBear box scores to 2024, ESPN 2025-26); opening-night "
            "rosters not injured coming in; n=59811 checkpoints",
    "oos": "fit 2011-2024, test 2025-26: RMSE remaining games update/static/iid-beta-binomial "
           "k=1 17.42/18.61/17.81, k=10 15.59/17.18/18.55, "
           "k=30 11.79/13.66/13.58",
    "report": "scripts/inseason_model/report_avail.json",
}

# --- per-game stats: stabilisation constants n0 (FITTED, scripts/inseason_model/report_stats.json)
STAT_N0: dict[str, float] = {'mpg': 5.2, 'reb': 433.2, 'ast': 288.8, 'stl': 1398.6, 'blk': 635.7, 'tov': 687.5, 'fg3m': 379.6, 'fga': 205.8, 'fta': 493.9, 'fg_pct': 398.0, 'ft_pct': 115.9}  # fmt: skip
STAT_N0_SE: dict[str, float] = {'mpg': 0.4, 'reb': 29.9, 'ast': 18.3, 'stl': 124.7, 'blk': 68.4, 'tov': 42.1, 'fg3m': 26.0, 'fga': 10.2, 'fta': 28.3, 'fg_pct': 30.1, 'ft_pct': 10.9}  # fmt: skip
STAT_N0_UNITS = {"mpg": "games", "fg_pct": "fga", "ft_pct": "fta"}  # every other stat: minutes
RATE_STATS = ["fga", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"]
COUNT_COLS = ["min", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"]
STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]


# ----------------------------------------------------------------------------------------------- availability
def _level(p0: np.ndarray) -> np.ndarray:
    c = ROS_AVAIL_COEF[0]
    return c["const"] + (c["p0"] - 1.0) * p0


def _knot(k: int, p0, sd0, o, streak, x, dnp_share, rem) -> tuple[np.ndarray, np.ndarray]:
    """Deployed mean and variance of the remaining share at fitted checkpoint k (k = 0: the prior)."""
    if k == 0:
        return p0, sd0**2
    c = ROS_AVAIL_COEF[k]
    raw = (c["const"] + c["p0"] * p0 + c["o"] * o + c["log_streak"] * np.log1p(streak)
           + c["all_missed"] * (x == 0) + c["dnp"] * dnp_share)
    mu = np.clip(raw - _level(p0), 0.0, 1.0)
    v = ROS_AVAIL_VAR[k]
    var = v["a"] * mu * (1 - mu) + v["c"] / np.maximum(rem, 1) + v["d"] * np.log1p(streak)
    return mu, np.maximum(var, 1e-6)


def ros_availability(p0, sd0, k, x, streak, dnp, rem) -> tuple[np.ndarray, np.ndarray]:
    """Expected share of remaining team games played and its sd (arrays, one entry per player).

    p0, sd0: prior share (gp / 82) and its sd; k: team games on the player's timeline so far; x: games played;
    streak: games missed in a row up to now; dnp: missed games he was dressed for; rem: remaining team games.
    k = 0 returns (p0, sd0); between the fitted checkpoints mean and variance are interpolated linearly in k (the
    player's own observed shares o = x/k and dnp/k are used at both neighbouring checkpoints); beyond the last
    checkpoint the last is used.
    """
    p0, sd0, k, x, streak, dnp, rem = (np.atleast_1d(np.asarray(v, float))
                                       for v in (p0, sd0, k, x, streak, dnp, rem))
    grid = sorted(ROS_AVAIL_COEF)
    kk = np.minimum(k, grid[-1])
    hi = np.searchsorted(grid, kk, side="left")
    lo = np.maximum(hi - 1, 0)
    o = np.where(k > 0, x / np.maximum(k, 1), p0)
    dnp_share = np.where(k > 0, dnp / np.maximum(k, 1), 0.0)
    mu, var = np.empty_like(p0), np.empty_like(p0)
    for i in range(len(p0)):
        g_lo, g_hi = grid[lo[i]], grid[hi[i]]
        args = tuple(a[i:i + 1] for a in (p0, sd0, o, streak, x, dnp_share, rem))
        m_lo, v_lo = _knot(g_lo, *args)
        m_hi, v_hi = _knot(g_hi, *args)
        w = 0.0 if g_hi == g_lo else (kk[i] - g_lo) / (g_hi - g_lo)
        mu[i] = (1 - w) * m_lo[0] + w * m_hi[0]
        var[i] = (1 - w) * v_lo[0] + w * v_hi[0]
    return np.clip(mu, 0.0, 1.0), np.sqrt(var)


def player_timeline(listings: pd.DataFrame, schedules: dict[str, pd.DataFrame], start_team: str | None) -> pd.DataFrame:
    """The team games a player was on the roster for, in order, with `played`.

    listings: his box-score rows (game_id, game_date, team, played), any order (`listed` in the output: he is in that
    game's box score, i.e. dressed; a missed game that is listed is a coach's decision, not an injury); schedules: team -> that team's
    completed games (game_id, game_date) in date order; start_team: the team he was on before the first game
    (preseason projection team in production, the previous season's team in the historical fit). Rules (also used to
    build the history): a stint on his first team starts at its game 1 if it is `start_team` or he is listed within
    its first ENTRY_GRACE games, else at his first listing; a later team's stint starts at his first listing for it
    and ends the previous stint; the last stint runs to the newest completed game. No listings: every game of
    `start_team` is missed.
    """
    cols = ["game_id", "game_date", "team", "played", "listed"]
    if listings is None or listings.empty:
        s = schedules.get(str(start_team))
        if s is None:
            return pd.DataFrame(columns=cols)
        return s[["game_id", "game_date"]].assign(team=start_team, played=False, listed=False).reset_index(drop=True)
    li = listings.sort_values(["game_date", "game_id"])
    stint_id = (li["team"] != li["team"].shift()).cumsum()
    segs: list[pd.DataFrame] = []
    for j, (_, st) in enumerate(li.groupby(stint_id, sort=True)):
        team = st["team"].iloc[0]
        s = schedules.get(team)
        if s is None or s.empty:
            continue
        s = s.reset_index(drop=True)
        first_date = st["game_date"].iloc[0]
        first_pos = int(np.searchsorted(s["game_date"].to_numpy(), first_date, side="left"))
        if j == 0 and (team == start_team or first_pos < ENTRY_GRACE):
            first_pos = 0
        if segs:
            segs[-1] = segs[-1][segs[-1]["game_date"] < first_date]
        segs.append(s.iloc[first_pos:][["game_id", "game_date"]].assign(team=team))
    if not segs:
        return pd.DataFrame(columns=cols)
    tl = pd.concat(segs, ignore_index=True)
    played = set(li.loc[li["played"].astype(bool), "game_id"].astype(str))
    tl["played"] = tl["game_id"].astype(str).isin(played)
    tl["listed"] = tl["game_id"].astype(str).isin(set(li["game_id"].astype(str)))  # in the box (dressed), any minutes
    return tl[cols]


def timeline_features(tl: pd.DataFrame) -> tuple[int, int, int, int]:
    """(k team games, x played, current streak of missed games, dnp = missed games he was dressed for)."""
    p = tl["played"].to_numpy(bool)
    k = len(p)
    hits = np.flatnonzero(p)
    streak = k if len(hits) == 0 else k - 1 - int(hits[-1])
    listed = tl["listed"].to_numpy(bool) if "listed" in tl else np.zeros(k, bool)
    return k, int(p.sum()), streak, int((listed & ~p).sum())


# ----------------------------------------------------------------------------------------------------- stats
def shrink_line(prior: pd.Series, tot: pd.Series, g: float) -> dict[str, float]:
    """Posterior per-game line from the prior per-game line (`prior`: mpg + STATS) and season-to-date totals
    (`tot`: COUNT_COLS) over `g` games played. Makes come from shrunk percentages x shrunk attempts; PTS is the
    prior's points-per-scoring identity applied to the shrunk makes (2 FGM + 3PM + FTM, rescaled like the sim)."""
    n0 = STAT_N0
    mpg_p = float(prior.get("mpg", 0.0) or 0.0)
    M = float(tot.get("min", 0.0))
    mpg_o = M / g if g > 0 else mpg_p
    mpg = (n0["mpg"] * mpg_p + g * mpg_o) / (n0["mpg"] + g)
    out: dict[str, float] = {"mpg": mpg}
    for s in RATE_STATS:
        r_p = float(prior[s]) / mpg_p if mpg_p > 0 else 0.0
        r_o = float(tot.get(s, 0.0)) / M if M > 0 else r_p
        out[s] = (n0[s] * r_p + M * r_o) / (n0[s] + M) * mpg
    for pct, make, att in (("fg_pct", "fgm", "fga"), ("ft_pct", "ftm", "fta")):
        p_p = float(prior[make]) / float(prior[att]) if float(prior[att]) > 0 else 0.0
        a_o = float(tot.get(att, 0.0))
        p_o = float(tot.get(make, 0.0)) / a_o if a_o > 0 else p_p
        p = (n0[pct] * p_p + a_o * p_o) / (n0[pct] + a_o)
        out[make] = p * out[att]
    raw_prior = 2 * float(prior["fgm"]) + float(prior["fg3m"]) + float(prior["ftm"])
    ratio = float(prior["pts"]) / raw_prior if raw_prior > 0 else 1.0
    out["pts"] = (2 * out["fgm"] + out["fg3m"] + out["ftm"]) * ratio
    return out


# -------------------------------------------------------------------------------------------------- update
@dataclass(frozen=True)
class UpdateSummary:
    as_of: str
    players: int
    updated: int
    max_team_games: int
    warnings: list[str] = field(default_factory=list)
    unlisted: list[str] = field(default_factory=list)  # projected, team played >= 3 games, never in a box score
    unmatched: list[str] = field(default_factory=list)  # played in a box score, no projection row


def playoff_mult_remaining(m: np.ndarray, s_rem: float, s_season: float) -> np.ndarray:
    """Playoff-week availability multiplier relative to the REMAINING-season average.

    `m` is relative to the full-season average with playoff share `s_season`; keeping the ratio of playoff-week to
    other-week availability fixed, rho = m (1 - s) / (1 - s m) and m_rem = rho / (s_rem rho + 1 - s_rem)
    (m_rem = m at s_rem = s; 1 once only playoff weeks remain)."""
    m = np.asarray(m, float)
    rho = m * (1 - s_season) / np.maximum(1 - s_season * m, 1e-9)
    return rho / (s_rem * rho + 1 - s_rem)


def update_projections(prior: pd.DataFrame, player_games: pd.DataFrame, team_games: pd.DataFrame, as_of: str,
                       games_missed: dict[str, float] | None = None, skip_window: dict[str, float] | None = None,
                       injuries: pd.DataFrame | None = None, playoff_share_remaining: float | None = None,
                       playoff_share_season: float | None = None) -> tuple[pd.DataFrame, UpdateSummary]:
    """Rest-of-season projections as of `as_of` (ISO date, games on that date included).

    prior: preseason projections (player_id, team, gp, gp_sd, mpg, STATS, status, ...); player_games / team_games: the
    ESPN ingester's tables (regular-season rows used). Players without box-score rows keep their prior stats; their
    availability is updated from their team's games (all missed).

    games_missed: preseason `games_missed` overrides (player_id -> m opening games expected out; the preseason gp is
    gp_healthy x (82 - m) / 82, gp_sd = sd_healthy + 0.15 m). Missing those games is not news: the update uses the
    healthy prior gp x 82 / (82 - m), skips the window (the first m team games, or up to his first game if he returns
    earlier), excludes the window's remaining games from the expected remaining games and keeps the return-timing
    sd 0.15 x (games of the window still to come). skip_window: the same window skip WITHOUT rescaling the prior
    (preseason day-to-day players, GTD_EXPECTED_MISSED, a prior). The historical fit had no preseason injury info.
    injuries: current feed (player_id, status, injury_note) -> `status`/`injury_note` (absent = healthy); without it a
    preseason out/gtd is cleared once the player has played and is not in a missed streak.
    playoff_share_remaining / _season: fantasy-playoff share of the remaining / full calendar -> `playoff_mult`
    renormalised to the remaining-season average (playoff_mult_remaining).
    """
    games_missed, skip_window = games_missed or {}, skip_window or {}
    tg = team_games[(team_games["regular_season"].astype(bool)) & (team_games["game_date"].astype(str) <= as_of)]
    tg = tg.assign(game_id=tg["game_id"].astype(str), game_date=tg["game_date"].astype(str))
    schedules = {t: d.sort_values(["game_date", "game_id"])[["game_id", "game_date"]].reset_index(drop=True)
                 for t, d in tg.groupby("team")}
    pg = player_games[(player_games["regular_season"].astype(bool))
                      & (player_games["game_date"].astype(str) <= as_of)].copy()
    pg["game_id"] = pg["game_id"].astype(str)
    pg["game_date"] = pg["game_date"].astype(str)
    pg[COUNT_COLS] = pg[COUNT_COLS].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    pg["played"] = pg["played"].astype(bool) & (pg["min"] > 0)
    pg = pg[pg["player_id"].astype(str).str.len() > 0]
    by_player = dict(tuple(pg.groupby("player_id")))
    df = prior.copy().reset_index(drop=True)
    for c in ("gp", "gp_sd", "mpg", *STATS):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    if "status" not in df:
        df["status"] = ""
    rows = []
    for r in df.itertuples(index=False):
        mine = by_player.get(r.player_id)
        tl = player_timeline(mine, schedules, str(r.team))
        k_all, x_all, streak_all, dnp_all = timeline_features(tl)
        m_gm = float(games_missed.get(r.player_id, 0.0) or 0.0)
        m = m_gm or float(skip_window.get(r.player_id, 0.0) or 0.0)
        first_play = int(np.argmax(tl["played"].to_numpy(bool))) if x_all else k_all
        w = int(min(np.ceil(m), first_play, k_all))  # expected-out games already elapsed (uninformative)
        k, x, streak, dnp = timeline_features(tl.iloc[w:])
        team_now = str(tl["team"].iloc[-1]) if len(tl) else str(r.team)
        rem = max(SEASON_GAMES - len(schedules.get(team_now, [])), 0)
        left = 0.0 if x_all else min(max(0.0, m - k_all), rem)  # expected-out games still to come
        gp_h = min(float(r.gp) * SEASON_GAMES / max(SEASON_GAMES - m_gm, 1.0), 80.0) if m_gm else float(r.gp)
        # healthy prior sd (preseason: gp_sd = sd_healthy + 0.15 m, see RETURN_SD_PER_GAME), on the share scale
        # chosen so that before any game the output reproduces the preseason gp_sd exactly
        sd_h0 = max(float(r.gp_sd) - RETURN_SD_PER_GAME * m_gm, 0.0) / (SEASON_GAMES - m_gm if m_gm else 82.0)
        rows.append({"k": k, "x": x, "streak": streak, "dnp": dnp, "k_all": k_all, "x_all": x_all,
                     "streak_all": streak_all, "dnp_all": dnp_all, "rem": rem, "left": left,
                     "p0": min(max(gp_h / 82, 0.0), 1.0), "sd0": sd_h0,
                     "team_now": team_now, "listed_any": mine is not None and len(mine) > 0, "m": m})
    feat = pd.DataFrame(rows)
    rem = feat["rem"].to_numpy(float)
    avail = rem - feat["left"].to_numpy(float)
    mu_h, sd_h = ros_availability(feat["p0"], feat["sd0"], feat["k"], feat["x"], feat["streak"], feat["dnp"],
                                  np.maximum(avail, 1))
    frac = np.where(rem > 0, avail / np.maximum(rem, 1), 0.0)
    mu = mu_h * frac
    sd = sd_h * frac + RETURN_SD_PER_GAME * feat["left"].to_numpy(float) / np.maximum(rem, 1)
    out = df.copy()
    out["gp_preseason"], out["gp_sd_preseason"] = df["gp"], df["gp_sd"]
    out["team_games"], out["games_played"] = feat["k_all"], feat["x_all"]
    out["missed_streak"], out["dnp_dressed"] = feat["streak_all"], feat["dnp_all"]
    out["ros_team_games"] = feat["rem"]
    out["ros_share"] = np.round(mu, 4)
    out["ros_games"] = np.round(mu * rem, 1)
    out["season_gp_exp"] = np.round(feat["x_all"] + mu * rem, 1)
    out["gp"] = np.round(mu * SEASON_GAMES, 1)
    out["gp_sd"] = np.maximum(np.round(sd * SEASON_GAMES, 1), GP_SD_FLOOR)
    moved = feat["team_now"] != df["team"].astype(str)
    out.loc[moved, "team"] = feat.loc[moved, "team_now"]
    # status: current injury feed, else clear a preseason out/gtd once he is back
    out["status_preseason"] = df["status"]
    if injuries is not None:
        st = injuries.drop_duplicates("player_id").set_index("player_id")
        out["status"] = out["player_id"].map(st["status"]).fillna("healthy")
        if "injury_note" in st:
            out["injury_note"] = out["player_id"].map(st["injury_note"]).fillna("")
    else:
        back = df["status"].isin(["out", "gtd"]) & (feat["x_all"] > 0) & (feat["streak_all"] == 0)
        out.loc[back, "status"] = "healthy"
    if playoff_share_remaining is not None and "playoff_mult" in out:
        pm = pd.to_numeric(out["playoff_mult"], errors="coerce").fillna(1.0).to_numpy()
        out["playoff_mult_preseason"] = pm
        s_season = playoff_share_season if playoff_share_season is not None else PLAYOFF_CALENDAR_SHARE
        out["playoff_mult"] = np.round(playoff_mult_remaining(pm, playoff_share_remaining, s_season), 4)
    updated = 0
    for i, r in df.iterrows():
        mine = by_player.get(r["player_id"])
        if mine is None or not mine["played"].any():
            continue
        played = mine[mine["played"]]
        line = shrink_line(r, played[COUNT_COLS].sum(), float(len(played)))
        line["fg3m"] = min(line["fg3m"], line["fgm"])
        for c, v in line.items():
            out.at[i, c] = round(v, 3)
        updated += 1
    out["inseason_as_of"] = as_of
    out["inseason_version"] = INSEASON_MODEL_VERSION
    team_n = {t: len(s) for t, s in schedules.items()}
    unlisted = sorted(out.loc[~feat["listed_any"] & (feat["m"] == 0)
                              & (df["team"].map(team_n).fillna(0) >= 3), "player_id"])
    known = set(df["player_id"])
    unmatched = sorted({p for p in pg.loc[pg["played"], "player_id"] if p not in known})
    summary = UpdateSummary(as_of=as_of, players=len(out), updated=updated,
                            max_team_games=int(max(team_n.values(), default=0)), unlisted=unlisted,
                            unmatched=unmatched)
    return out, summary


# ---------------------------------------------------------------------------------------------------- write
def _sha(path) -> str:
    import hashlib
    from pathlib import Path

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_injury_feed(as_of: str) -> tuple[pd.DataFrame | None, str]:
    """Current ESPN injury report (`fantasy data fetch-injuries`) as (player_id, status, injury_note), or None when it
    is missing or older than INJURY_FEED_MAX_AGE_DAYS before `as_of`; plus a note for the meta."""
    from datetime import date

    from fantasy.data.injuries import model_status, parse_espn
    from fantasy.data.provenance import RAW_DIR, read_provenance

    path = RAW_DIR / "espn" / "injuries.json"
    if not path.exists():
        return None, "no injury feed (run `fantasy data fetch-injuries`): preseason status kept unless back"
    fetched = str((read_provenance(path) or {}).get("fetched_at") or "")[:10]
    age = (date.fromisoformat(as_of) - date.fromisoformat(fetched)).days if fetched else None
    if age is None or not 0 <= age <= INJURY_FEED_MAX_AGE_DAYS:  # stale, or fetched after as_of (backfill: no leak)
        return None, f"injury feed fetched {fetched or '?'} is stale for {as_of}: preseason status kept unless back"
    inj = parse_espn(path)
    if inj.empty:
        return inj.assign(status=[], injury_note=[]), f"injury feed {fetched}: empty"
    inj["status"] = [model_status(a, b) for a, b in zip(inj["status"], inj["fantasy_status"], strict=True)]
    inj["injury_note"] = (inj["injury"].astype(str) + ": " + inj["comment"].astype(str).str[:120]).str.strip(": ")
    return inj[["player_id", "status", "injury_note"]], f"injury feed fetched {fetched}"


def playoff_share_remaining(as_of: str) -> tuple[float, float]:
    """(fantasy-playoff share of the NBA games after `as_of`, share of the whole season) from the NBA schedule."""
    from datetime import date, timedelta

    from fantasy.data.provenance import RAW_DIR
    from fantasy.league.settings import load_settings
    from fantasy.model.projections import (
        _raw,  # noqa: F401  (keeps the raw-dir convention in one place)
    )

    sched = pd.read_csv(RAW_DIR / "bbref_schedule" / "nba_2027_games.csv")
    end = date.fromisoformat(load_settings().playoff_end_date)
    start = end - timedelta(days=20)
    d = pd.to_datetime(sched["date"]).dt.date
    in_po = (d >= start) & (d <= end)
    after = d > date.fromisoformat(as_of)
    s_rem = float((in_po & after).sum() / max(after.sum(), 1))
    return s_rem, float(in_po.mean())


def write_inseason(as_of: str, season: int = 2027, prior_path=None, out_path=None) -> dict:
    """Read the preseason projections and the season's ESPN box scores, write the rest-of-season projections
    (default data/processed/projections_2026_27.inseason.csv, the default `load_projections()` file from then on)
    and a .meta.json next to them. Returns the meta dict (+ the biggest availability movers)."""
    import json
    import subprocess
    from pathlib import Path

    from fantasy.data.boxscores import (
        load_player_games,
        load_team_games,
        player_games_path,
        team_games_path,
    )
    from fantasy.league.settings import REPO_ROOT
    from fantasy.model.lines import DEFAULT_PROJECTIONS, INSEASON_PROJECTIONS
    from fantasy.model.projections import load_manual

    prior_path = Path(prior_path or DEFAULT_PROJECTIONS)
    out_path = Path(out_path or INSEASON_PROJECTIONS)
    warnings: list[str] = []
    prior = pd.read_csv(prior_path)
    pg, tg = load_player_games(season), load_team_games(season)
    ov_path = REPO_ROOT / "data" / "manual" / "player_overrides_2026_27.csv"
    ov = load_manual("player_overrides_2026_27.csv")
    gm = pd.to_numeric(ov.get("games_missed", pd.Series(dtype=float)), errors="coerce")
    games_missed = {pid: float(v) for pid, v in zip(ov["player_id"], gm, strict=True) if v and v > 0}
    base_meta = REPO_ROOT / "data" / "processed" / "projections_2026_27.base.meta.json"
    if base_meta.exists():
        built = {i["path"]: i["sha256"] for i in json.loads(base_meta.read_text()).get("inputs", [])}
        if built.get("data/manual/player_overrides_2026_27.csv") not in (None, _sha(ov_path)):
            warnings.append("player_overrides changed since the preseason build: games_missed windows may not match "
                            "the prior (rebuild with `fantasy data build-projections` or revert the in-season edit)")
    # PRIOR (not fitted): a player listed day-to-day before the opener is expected to miss ~1 game, so missing the
    # opener is not news (the historical fit had no preseason injury status); his prior is not rescaled
    gtd = prior.loc[prior.get("status", pd.Series("", index=prior.index)).astype(str) == "gtd", "player_id"]
    skip = {pid: GTD_EXPECTED_MISSED for pid in gtd if pid not in games_missed}
    injuries, inj_note = load_injury_feed(as_of)
    s_rem, s_sched = playoff_share_remaining(as_of)
    # rescale relative to the full-season share that consumers decode playoff_mult with (lines.PLAYOFF_CALENDAR_SHARE)
    s_rem_cal = s_rem * PLAYOFF_CALENDAR_SHARE / s_sched if s_sched > 0 else s_rem
    out, summ = update_projections(prior, pg, tg, as_of, games_missed=games_missed, skip_window=skip,
                                   injuries=injuries, playoff_share_remaining=min(s_rem_cal, 1.0),
                                   playoff_share_season=PLAYOFF_CALENDAR_SHARE)
    warnings += summ.warnings
    if summ.unlisted:
        warnings.append(f"{len(summ.unlisted)} projected players never in a box score although their team played "
                        f">= 3 games (injured, waived or an id mismatch?): {', '.join(summ.unlisted[:15])}")
    if summ.unmatched:
        warnings.append(f"{len(summ.unmatched)} players in box scores without a projection row (not projected): "
                        f"{', '.join(summ.unmatched[:15])}")
    out.to_csv(out_path, index=False, lineterminator="\n")
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
    except Exception:  # pragma: no cover - not a git checkout
        sha = "unknown"
    seen = out[out["team_games"] > 0]
    movers = seen.reindex((seen["gp"] - seen["gp_preseason"]).abs().sort_values(ascending=False).index).head(10)
    meta = {
        "model_version": INSEASON_MODEL_VERSION,
        "as_of": as_of,
        "git_sha": sha,
        "output": str(out_path),
        "prior": str(prior_path),
        "prior_sha256": _sha(prior_path),
        "rows": len(out),
        "players_with_box_scores": summ.updated,
        "max_team_games": summ.max_team_games,
        "gp_semantics": "gp = 82 x expected share of remaining team games played (per-game play probability x 82); "
                        "season_gp_exp = games played + expected remaining games",
        "availability": {"version": ROS_AVAIL_VERSION, **ROS_AVAIL_FIT},
        "stats": {"version": STAT_SHRINK_VERSION, "n0": STAT_N0, "n0_se": STAT_N0_SE,
                  "units": "mpg: games; fg_pct: fga; ft_pct: fta; others: minutes",
                  "report": "scripts/inseason_model/report_stats.json"},
        "games_missed_overrides": len(games_missed),
        "gtd_window_prior": {"players": len(skip), "games": GTD_EXPECTED_MISSED},
        "status": inj_note,
        "playoff_share": {"remaining_schedule": round(s_rem, 4), "season_schedule": round(s_sched, 4),
                          "remaining_used": round(min(s_rem_cal, 1.0), 4), "season_used": PLAYOFF_CALENDAR_SHARE},
        "latest_box_score_date": str(pg["game_date"].max()),
        "warnings": warnings,
        "inputs": [{"path": str(p), "sha256": _sha(p)}
                   for p in (prior_path, player_games_path(season), team_games_path(season), ov_path)],
        "sha256": _sha(out_path),
    }
    meta_path = out_path.with_name(out_path.stem + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, default=str) + "\n")
    early = out[(out["team_games"] <= LOW_CONF_MAX_K) & ((out["gp"] - out["gp_preseason"]).abs() > LOW_CONF_DGP)]
    meta["low_confidence"] = {
        "rule": f"|gp - gp_preseason| > {LOW_CONF_DGP:g} after <= {LOW_CONF_MAX_K} team games: check the reason (injury news, "
                "waived/not with team, coach's decision) before acting; history mixes known-injured players into "
                "early absences, so the fitted drop may be too large for a healthy-listed player",
        "players": sorted(early["player_id"]),
    }
    meta["movers"] = [{"player_id": r.player_id, "gp_preseason": r.gp_preseason, "gp": r.gp,
                       "played": int(r.games_played), "team_games": int(r.team_games)} for r in movers.itertuples()]
    return meta
