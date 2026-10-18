"""Head-to-head category model: weekly team totals and category-win probabilities.

A team's weekly total in each category is approximated as Normal. Count categories sum
per-game means; FG%/FT% are ratios of summed makes over summed attempts, with delta-method
variance. The opponent is the *league-average* team obtained by snake-drafting the player pool
in market (ADP) order, and the spread between league teams is added as extra opponent variance.

P(win cat) = Phi(sign * (mu_me - mu_opp) / sqrt(var_me + var_opp + spread))
Expected category wins = sum of P(win cat); matchup win prob = P(#wins > #losses) via the
Poisson-binomial distribution (categories treated as independent).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import GAME_CV, GAMES_PER_WEEK, STATS

STAT_IDX = {s: i for i, s in enumerate(STATS)}
USAGE = 0.85  # share of rostered players' games that make it into active lineups


@dataclass
class PlayerArrays:
    """Additive per-player components, aligned with `ids`."""

    ids: list[str]
    S: np.ndarray  # (n, n_stats) per-game means
    V: np.ndarray  # (n, n_stats) per-game variances
    R: np.ndarray  # (n, n_ratio_cats) ratio-variance components

    def index(self) -> dict[str, int]:
        return {pid: i for i, pid in enumerate(self.ids)}


def build_arrays(lines: pd.DataFrame, settings: LeagueSettings) -> PlayerArrays:
    S = lines[STATS].to_numpy(dtype=float)
    cv = np.array([GAME_CV[s] for s in STATS])
    V = (S * cv) ** 2
    ratio_cats = [c for c in settings.cats if c.kind == "ratio"]
    R = np.zeros((len(lines), len(ratio_cats)))
    for j, c in enumerate(ratio_cats):
        m, a = S[:, STAT_IDX[c.num]], S[:, STAT_IDX[c.den]]
        with np.errstate(divide="ignore", invalid="ignore"):
            p = np.where(a > 0, m / a, 0.0)
        p_league = m.sum() / max(a.sum(), 1e-9)
        R[:, j] = a * p * (1 - p) + (GAME_CV[c.den] * a) ** 2 * (p - p_league) ** 2
    return PlayerArrays(ids=list(lines.index), S=S, V=V, R=R)


class CategoryModel:
    def __init__(
        self,
        settings: LeagueSettings,
        arrays: PlayerArrays,
        market_order: list[str],
        opponent_spread: float = 1.0,
    ) -> None:
        self.settings = settings
        self.arrays = arrays
        self.cats = settings.cats
        self.ratio_cats = [c for c in self.cats if c.kind == "ratio"]
        self.sign = np.array([1.0 if c.higher_is_better else -1.0 for c in self.cats])
        self.scale = GAMES_PER_WEEK * USAGE
        self._fit_opponent(market_order, opponent_spread)

    # ------------------------------------------------------------------ team aggregation
    def team_sums(self, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """idx: (B, k) player row indices (use -1 for padding). Returns summed S, V, R."""
        idx = np.atleast_2d(idx)
        mask = (idx >= 0)[..., None]
        safe = np.where(idx >= 0, idx, 0)
        S = (self.arrays.S[safe] * mask).sum(axis=1)
        V = (self.arrays.V[safe] * mask).sum(axis=1)
        R = (self.arrays.R[safe] * mask).sum(axis=1)
        return S, V, R

    def cat_moments(self, S: np.ndarray, V: np.ndarray, R: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Weekly category mean & variance for teams given summed components. Shapes (B, n_cats)."""
        S, V, R = np.atleast_2d(S), np.atleast_2d(V), np.atleast_2d(R)
        k = self.scale
        means, vars_ = [], []
        r = 0
        for c in self.cats:
            if c.kind == "count":
                i = STAT_IDX[c.stat]
                means.append(k * S[:, i])
                vars_.append(k * V[:, i])
            else:
                m, a = S[:, STAT_IDX[c.num]], S[:, STAT_IDX[c.den]]
                a_safe = np.maximum(a, 1e-9)
                means.append(m / a_safe)
                vars_.append(R[:, r] / (k * a_safe**2))
                r += 1
        return np.stack(means, axis=1), np.stack(vars_, axis=1)

    # ------------------------------------------------------------------ opponent
    def _fit_opponent(self, market_order: list[str], spread_mult: float) -> None:
        n_teams, n_rounds = self.settings.num_teams, self.settings.num_rounds
        pos = self.arrays.index()
        order = [pos[p] for p in market_order if p in pos][: n_teams * n_rounds]
        teams: list[list[int]] = [[] for _ in range(n_teams)]
        for i, row in enumerate(order):
            rnd, k = divmod(i, n_teams)
            teams[k if rnd % 2 == 0 else n_teams - 1 - k].append(row)
        width = max(len(t) for t in teams)
        idx = np.array([t + [-1] * (width - len(t)) for t in teams])
        S, V, R = self.team_sums(idx)
        mu, var = self.cat_moments(S, V, R)
        self.league_team_means = mu  # (n_teams, n_cats), for diagnostics
        self.opp_mean = mu.mean(axis=0)
        self.opp_var = var.mean(axis=0) + spread_mult * mu.var(axis=0)
        self.opp_sums = (S.mean(axis=0), V.mean(axis=0), R.mean(axis=0))

    # ------------------------------------------------------------------ evaluation
    def win_probs(self, S: np.ndarray, V: np.ndarray, R: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
        mu, var = self.cat_moments(S, V, R)
        z = self.sign * (mu - self.opp_mean) / np.sqrt(var + self.opp_var)
        return norm.cdf(z)

    def expected_cat_wins(self, P: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
        w = np.ones(P.shape[-1]) if weights is None else weights
        return (P * w).sum(axis=-1)

    @staticmethod
    def matchup_win_prob(P: np.ndarray) -> np.ndarray:
        """P(categories won > categories lost), independent categories, no ties."""
        P = np.atleast_2d(P)
        n = P.shape[1]
        dist = np.zeros((P.shape[0], n + 1))
        dist[:, 0] = 1.0
        for j in range(n):
            p = P[:, j : j + 1]
            dist[:, 1:] = dist[:, 1:] * (1 - p) + dist[:, :-1] * p
            dist[:, 0] = dist[:, 0] * (1 - p[:, 0])
        need = n // 2 + 1
        return dist[:, need:].sum(axis=1)

    def evaluate_team(self, idx: list[int]) -> dict:
        S, V, R = self.team_sums(np.array([idx]))
        P = self.win_probs(S, V, R)[0]
        mu, _ = self.cat_moments(S, V, R)
        return {
            "cat_win_probs": {c.key: float(p) for c, p in zip(self.cats, P, strict=True)},
            "cat_means": {c.key: float(m) for c, m in zip(self.cats, mu[0], strict=True)},
            "opp_means": {c.key: float(m) for c, m in zip(self.cats, self.opp_mean, strict=True)},
            "expected_category_wins": float(P.sum()),
            "matchup_win_prob": float(self.matchup_win_prob(P[None, :])[0]),
        }
