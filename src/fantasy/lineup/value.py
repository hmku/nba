"""Matchup-aware weekly category model: margins, category weights, analytic and Monte Carlo evaluation of a plan.

A *plan* for a team is a boolean matrix X (players x remaining days): X[i, d] = player i is in a starting slot on day
d. P[i, d] = P(player i plays on day d | started) (0 when his NBA team has no game). Per-game lines S are
per-game-played projections (data/processed/projections_*.csv).

Weekly total of raw stat s for a team = accumulated + sum_i G_i * (true per-game mean) + game noise, where
G_i = sum_d X[i,d] * Bernoulli(P[i,d]) are his games. Normal approximation (moments):
  mean  = acc + sum_i k_i S_is,                       k_i = sum_d X P,  q_i = sum_d X P (1 - P)
  var   = sum_i [ k_i var_game(S_is)                  per-game noise, fantasy.model.noise (FITTED)
                + q_i S_is^2                          Bernoulli games-played noise
                + (k_i S_is)^2 * (vol_i^2 + sd_s^2) ] season-level projection error (FITTED, sim.calibration)
PTS (lineup-0.4.1): the game-noise and season-error terms are derived from the components the Monte Carlo builds PTS
from (2 FGM + 3PM + FTM, rescaled), see pts_var_terms.
FG% / FT%: delta method on M - p_team * A (makes minus team % times attempts), as in fantasy.model.catmodel, plus
the same Bernoulli and projection-error terms (FG%/FT% shift sds from sim.calibration); var(pct) = var / A^2.

Category margin (me - opponent) ~ Normal(mu_c, sigma_c^2), sigma_c^2 = var_me + var_opp. Expected category score
E_c = Phi(sign_c mu_c / sigma_c) (sign -1 for TO; with integer counts this is ~ P(win) + P(tie)/2, the Yahoo
H2H-categories credit). Objective: expected categories won, sum_c E_c -- in this league ("head" scoring) every
category counts as a win/loss in the standings, so this is the regular-season objective, not P(win matchup).

Category weights (gradient of the objective w.r.t. my weekly totals): w_c = sign_c phi(z_c) / sigma_c. A player's
per-game marginal value = sum_c w_c * delta_c, delta_c = S_c for counts and (S_makes - p_me S_att) / A_me for
percentages (linearised at the projected totals). The variance a player adds is ignored in the gradient (second
order); the plan is still evaluated with full moments and by Monte Carlo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from fantasy.league.settings import Category
from fantasy.model.catmodel import CategoryModel
from fantasy.model.lines import STATS
from fantasy.model.noise import game_var, game_var_matrix, ratio_var
from fantasy.sim import calibration as cal

SI = {s: i for i, s in enumerate(STATS)}
_EPS = 1e-9


def error_sds(mpg: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(vol_sd, fg_pct_sd, ft_pct_sd) per player by projected mpg (cal.ERROR_BY_MPG, FITTED)."""
    vol, fg, ft = np.zeros(len(mpg)), np.zeros(len(mpg)), np.zeros(len(mpg))
    for i, m in enumerate(mpg):
        for lo, v, f, t in cal.ERROR_BY_MPG:
            if (m if np.isfinite(m) else 0.0) >= lo:
                vol[i], fg[i], ft[i] = v, f, t
                break
    return vol, fg, ft


def _stat_sd(stat: str) -> float:
    """Stat-specific season factor sd (FITTED, cal.STAT_SD); makes follow their attempts, pts the volume factor."""
    return {"fgm": cal.STAT_SD["fga"], "ftm": cal.STAT_SD["fta"]}.get(stat, cal.STAT_SD.get(stat, 0.0))


@dataclass
class TeamWeek:
    """One team's remaining week: lines, P(plays) per day, accumulated raw totals."""

    ids: list[str]
    S: np.ndarray  # (n, n_stats) per-game-played means
    P: np.ndarray  # (n, D) P(plays | started), 0 without a game
    acc: np.ndarray  # (n_stats,) raw totals already banked this week
    vol: np.ndarray  # (n,) projection-error sds
    fg_sd: np.ndarray
    ft_sd: np.ndarray

    @property
    def n_days(self) -> int:
        return self.P.shape[1]


def pts_var_terms(tw: TeamWeek) -> tuple[np.ndarray, np.ndarray]:
    """PTS variance pieces consistent with `simulate_totals`, which builds PTS = scale (2 FGM + 3PM + FTM) from the
    components (lineup-0.4.1; 0.4.0 used the fitted PTS game variance and only the volume factor, ~10% low vs MC).

    Returns per player (game-noise variance per game, season-error variance per game^2), linearised at the
    projected line: game noise = scale^2 w' C w over cal.NOISE_ORDER with w = (fga: 2 p_fg sd_fga, fgm: 2 sd_bin_fg,
    fta: p_ft sd_fta, ftm: sd_bin_ft, fg3m: sd_3pm) (C = cal.NOISE_CORR, sd_* = sqrt(fantasy.model.noise.game_var));
    season error = PTS^2 vol^2 + scale^2 [(2 FGM sd_fga)^2 + (3PM sd_3pm)^2 + (FTM sd_fta)^2 + (2 FGA fg_sd)^2
    + (FTA ft_sd)^2] (stat factors cal.STAT_SD, % shifts fg_sd / ft_sd)."""
    S = tw.S
    fga, fgm, fta, ftm, f3 = (S[:, SI[s]] for s in ("fga", "fgm", "fta", "ftm", "fg3m"))
    with np.errstate(divide="ignore", invalid="ignore"):
        p_fg = np.clip(np.where(fga > 0, fgm / np.maximum(fga, _EPS), 0.0), 0.0, 1.0)
        p_ft = np.clip(np.where(fta > 0, ftm / np.maximum(fta, _EPS), 0.0), 0.0, 1.0)
        raw = 2 * fgm + f3 + ftm
        scale = np.where(raw > 0, S[:, SI["pts"]] / np.maximum(raw, _EPS), 0.0)
    zi = {s: j for j, s in enumerate(cal.NOISE_ORDER)}
    W = np.zeros((S.shape[0], len(cal.NOISE_ORDER)))
    W[:, zi["fga"]] = 2 * p_fg * np.sqrt(game_var("fga", fga))
    W[:, zi["fgm"]] = 2 * np.sqrt(fga * p_fg * (1 - p_fg))
    W[:, zi["fta"]] = p_ft * np.sqrt(game_var("fta", fta))
    W[:, zi["ftm"]] = np.sqrt(fta * p_ft * (1 - p_ft))
    W[:, zi["fg3m"]] = np.sqrt(game_var("fg3m", f3))
    C = np.array(cal.NOISE_CORR)
    g_var = scale**2 * np.einsum("ij,jk,ik->i", W, C, W)
    sd = cal.STAT_SD
    season = (S[:, SI["pts"]] ** 2 * tw.vol**2
              + scale**2 * ((2 * fgm * sd["fga"]) ** 2 + (f3 * sd["fg3m"]) ** 2 + (ftm * sd["fta"]) ** 2
                            + (2 * fga * tw.fg_sd) ** 2 + (fta * tw.ft_sd) ** 2))
    return g_var, season


@dataclass
class TeamMoments:
    mean: np.ndarray  # (n_cats,) category value (pct for ratio cats)
    var: np.ndarray  # (n_cats,)
    makes: dict[str, float]  # ratio cat key -> projected makes total
    atts: dict[str, float]


def team_moments(tw: TeamWeek, X: np.ndarray, cats: list[Category]) -> TeamMoments:
    Xf = X.astype(float)
    k = (Xf * tw.P).sum(axis=1)
    q = (Xf * tw.P * (1 - tw.P)).sum(axis=1)
    V = game_var_matrix(tw.S)
    mean, var = [], []
    makes, atts = {}, {}
    for c in cats:
        if c.kind == "count":
            s = SI[c.stat_col]
            x = tw.S[:, s]
            mean.append(tw.acc[s] + (k * x).sum())
            if c.stat_col == "pts":
                g_var, season_var = pts_var_terms(tw)
                var.append((k * g_var + q * x**2 + k**2 * season_var).sum())
                continue
            ln_sd2 = tw.vol ** 2 + _stat_sd(c.stat_col) ** 2
            var.append((k * V[:, s] + q * x**2 + (k * x) ** 2 * ln_sd2).sum())
        else:
            mi, ai = SI[c.num_col], SI[c.den_col]
            m, a = tw.S[:, mi], tw.S[:, ai]
            M = tw.acc[mi] + (k * m).sum()
            A = tw.acc[ai] + (k * a).sum()
            p = M / A if A > _EPS else 0.0
            pct_sd = tw.fg_sd if c.key == "fg_pct" else tw.ft_sd
            impact = m - p * a
            v = (k * ratio_var(m, a, p, c.den_col) + q * impact**2
                 + (k * impact) ** 2 * (tw.vol**2 + _stat_sd(c.den_col) ** 2) + (k * a * pct_sd) ** 2).sum()
            mean.append(p)
            var.append(v / A**2 if A > _EPS else 0.0)
            makes[c.key], atts[c.key] = M, A
    return TeamMoments(np.array(mean), np.array(var), makes, atts)


@dataclass
class CatState:
    keys: list[str]
    mu: np.ndarray  # margin me - opp
    sigma: np.ndarray
    sign: np.ndarray
    me: TeamMoments
    opp: TeamMoments

    @property
    def z(self) -> np.ndarray:
        return self.sign * self.mu / self.sigma

    @property
    def p_win(self) -> np.ndarray:
        return norm.cdf(self.z)

    @property
    def expected_cats(self) -> float:
        return float(self.p_win.sum())

    @property
    def matchup_win_prob(self) -> float:
        return float(CategoryModel.matchup_win_prob(self.p_win[None, :])[0])

    def weights(self) -> np.ndarray:
        """dE[cats]/d(my category total): sign * phi(z) / sigma."""
        return self.sign * norm.pdf(self.z) / self.sigma

    def to_dict(self) -> dict:
        return {k: {"my_mean": float(self.me.mean[j]), "opp_mean": float(self.opp.mean[j]), "margin": float(self.mu[j]),
                    "sd": float(self.sigma[j]), "p_win": float(self.p_win[j]), "weight": float(self.weights()[j])}
                for j, k in enumerate(self.keys)}


def cat_state(me: TeamWeek, X_me: np.ndarray, opp: TeamWeek, X_opp: np.ndarray, cats: list[Category]) -> CatState:
    a, b = team_moments(me, X_me, cats), team_moments(opp, X_opp, cats)
    sign = np.array([1.0 if c.higher_is_better else -1.0 for c in cats])
    sigma = np.sqrt(np.maximum(a.var + b.var, 0.0))
    # floor: a settled category (no games left) gets a tiny sd so weights stay finite (and ~0)
    floor = np.array([1e-4 if c.kind == "ratio" else 0.05 for c in cats])
    return CatState([c.key for c in cats], a.mean - b.mean, np.maximum(sigma, floor), sign, a, b)


def player_deltas(S: np.ndarray, state: CatState, cats: list[Category]) -> np.ndarray:
    """(n, n_cats) per-game change of my category totals from one more game of each player."""
    out = np.zeros((S.shape[0], len(cats)))
    for j, c in enumerate(cats):
        if c.kind == "count":
            out[:, j] = S[:, SI[c.stat_col]]
        else:
            A = state.me.atts[c.key]
            p = state.me.makes[c.key] / A if A > _EPS else 0.0
            out[:, j] = (S[:, SI[c.num_col]] - p * S[:, SI[c.den_col]]) / max(A, 1.0)
    return out


def per_game_value(S: np.ndarray, state: CatState, cats: list[Category]) -> tuple[np.ndarray, np.ndarray]:
    """(value per game played (n,), per-category contributions (n, n_cats)) in expected categories won."""
    contrib = player_deltas(S, state, cats) * state.weights()[None, :]
    return contrib.sum(axis=1), contrib


# ---------------------------------------------------------------- Monte Carlo
def _key(seed: int, tag: str, pid: str) -> int:
    return int(hashlib.sha256(f"{seed}:{tag}:{pid}".encode()).hexdigest()[:12], 16)


@dataclass
class TeamDraws:
    """Random numbers for one team, keyed by player id (common random numbers across plans)."""

    U: np.ndarray  # (N, n, D) uniforms for playing
    zv: np.ndarray  # (N, n) volume factor
    zs: np.ndarray  # (N, n, n_stats) stat factors
    zp: np.ndarray  # (N, n, 2) fg/ft pct shifts
    zg: np.ndarray  # (N, n, 10) weekly game noise (cal.NOISE_ORDER, correlated)


def draw(tw: TeamWeek, n_sims: int, seed: int, tag: str) -> TeamDraws:
    n, D = tw.P.shape
    L = np.linalg.cholesky(np.array(cal.NOISE_CORR))
    U = np.empty((n_sims, n, D))
    zv = np.empty((n_sims, n))
    zs = np.empty((n_sims, n, len(STATS)))
    zp = np.empty((n_sims, n, 2))
    zg = np.empty((n_sims, n, len(cal.NOISE_ORDER)))
    for i, pid in enumerate(tw.ids):
        rng = np.random.default_rng(_key(seed, tag, pid))
        U[:, i] = rng.random((n_sims, D))
        zv[:, i] = rng.standard_normal(n_sims)
        zs[:, i] = rng.standard_normal((n_sims, len(STATS)))
        zp[:, i] = rng.standard_normal((n_sims, 2))
        zg[:, i] = rng.standard_normal((n_sims, len(cal.NOISE_ORDER))) @ L.T
    return TeamDraws(U, zv, zs, zp, zg)


def simulate_totals(tw: TeamWeek, X: np.ndarray, dr: TeamDraws) -> np.ndarray:
    """(N, n_stats) simulated weekly raw totals (accumulated included), the season simulator's method:
    lognormal season factors, makes = attempts x (shifted) %, game noise with the fitted within-game
    correlation, PTS = 2 FGM + 3PM + FTM rescaled to the projected PTS."""
    G = ((dr.U < tw.P[None]) & X[None].astype(bool)).sum(axis=2).astype(float)  # (N, n)
    S = tw.S
    fv = np.exp(tw.vol[None] * dr.zv - tw.vol[None] ** 2 / 2)
    m = {}
    for s in ("fga", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"):
        sd = cal.STAT_SD.get(s, 0.0)
        m[s] = S[None, :, SI[s]] * fv * np.exp(sd * dr.zs[:, :, SI[s]] - sd**2 / 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        p_fg0 = np.where(S[:, SI["fga"]] > 0, S[:, SI["fgm"]] / np.maximum(S[:, SI["fga"]], _EPS), 0.0)
        p_ft0 = np.where(S[:, SI["fta"]] > 0, S[:, SI["ftm"]] / np.maximum(S[:, SI["fta"]], _EPS), 0.0)
    p_fg = np.clip(p_fg0[None] + tw.fg_sd[None] * dr.zp[:, :, 0], 0.05, 0.95)
    p_ft = np.clip(p_ft0[None] + tw.ft_sd[None] * dr.zp[:, :, 1], 0.05, 0.99)
    zi = {s: j for j, s in enumerate(cal.NOISE_ORDER)}
    tot = {}
    for s in ("fga", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"):
        tot[s] = np.maximum(G * m[s] + np.sqrt(G * game_var(s, m[s])) * dr.zg[:, :, zi[s]], 0.0)
    fgm = tot["fga"] * p_fg + np.sqrt(tot["fga"] * p_fg * (1 - p_fg)) * dr.zg[:, :, zi["fgm"]]
    ftm = tot["fta"] * p_ft + np.sqrt(tot["fta"] * p_ft * (1 - p_ft)) * dr.zg[:, :, zi["ftm"]]
    tot["fgm"] = np.clip(fgm, 0.0, tot["fga"])
    tot["ftm"] = np.clip(ftm, 0.0, tot["fta"])
    tot["fg3m"] = np.minimum(tot["fg3m"], tot["fgm"])
    raw_pts = 2 * S[:, SI["fgm"]] + S[:, SI["fg3m"]] + S[:, SI["ftm"]]
    scale = np.where(raw_pts > 0, S[:, SI["pts"]] / np.maximum(raw_pts, _EPS), 0.0)
    tot["pts"] = (2 * tot["fgm"] + tot["fg3m"] + tot["ftm"]) * scale[None]
    out = np.stack([tot[s].sum(axis=1) for s in STATS], axis=1) + tw.acc[None]
    return out


def score_matchup(me_tot: np.ndarray, opp_tot: np.ndarray, cats: list[Category]) -> np.ndarray:
    """(N, n_cats) category score for me: 1 win, 0.5 tie, 0 loss (counts rounded, % to 3 decimals)."""
    res = np.zeros((me_tot.shape[0], len(cats)))
    for j, c in enumerate(cats):
        if c.kind == "count":
            a, b = np.round(me_tot[:, SI[c.stat_col]]), np.round(opp_tot[:, SI[c.stat_col]])
        else:
            def pct(t: np.ndarray, c: Category = c) -> np.ndarray:
                A = t[:, SI[c.den_col]]
                return np.round(np.where(A > 0, t[:, SI[c.num_col]] / np.maximum(A, _EPS), 0.0), 3)
            a, b = pct(me_tot), pct(opp_tot)
        d = (a - b) * (1 if c.higher_is_better else -1)
        res[:, j] = np.where(d > 0, 1.0, np.where(d == 0, 0.5, 0.0))
    return res
