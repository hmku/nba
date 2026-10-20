"""Calibration helpers for the draft room model (T-016): one-step pick likelihoods, a censored (Tobit) fit
of the ADP spread, binary-forecast scores and a cluster bootstrap.

Research-only and opt-in: nothing on the draft path imports this module, and it changes no defaults.
Used by `docs/research/draft_calibration_2026.py` (results: `docs/research/draft_calibration_2026.md`).

Conventions match `fantasy.draft.availability` / `fantasy.draft.room_sim`:
  - an ADP drafter at overall pick k takes the available player with the smallest latent position,
    latent ~ Normal(market_rank, adp_sd(market_rank) * sd_mult) truncated to > k - 0.5;
  - a sharp drafter takes the available player with the largest noisy value view,
    view ~ Normal(centre, sharp_sd(value)).
Both "who goes first" probabilities are computed by numerical integration (no Monte Carlo floor), so a
big reach gets a small but finite likelihood.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from scipy.integrate import trapezoid
from scipy.optimize import minimize
from scipy.special import log_ndtr

from fantasy.draft.availability import SD_FLOOR, SD_SLOPE, adp_sd

LOG_2PI = float(np.log(2 * np.pi))


def _norm_logpdf(z: np.ndarray) -> np.ndarray:
    return -0.5 * z * z - 0.5 * LOG_2PI


def p_first_min(mu: np.ndarray, sd: np.ndarray, lo: float, n_grid: int = 6000) -> np.ndarray:
    """P(player i has the smallest latent) for independent Normal(mu_i, sd_i) truncated to (lo, inf).

    The one-step pick distribution of an ADP drafter at overall pick k (lo = k - 0.5)."""
    mu, sd = np.asarray(mu, float), np.asarray(sd, float)
    log_s_lo = log_ndtr(-(lo - mu) / sd)  # log P(latent > lo)
    hi = lo + max(20.0, float(np.min(mu + 8 * sd) - lo))  # beyond the smallest mu + 8 sd nobody is still "first"
    x = np.linspace(lo, hi, n_grid)
    z = (x[None, :] - mu[:, None]) / sd[:, None]
    log_s = log_ndtr(-z) - log_s_lo[:, None]  # log survival of the truncated latent
    total = log_s.sum(axis=0)
    log_f = _norm_logpdf(z) - np.log(sd)[:, None] - log_s_lo[:, None]
    dens = np.exp(log_f + total[None, :] - log_s)
    p = trapezoid(dens, x, axis=1)
    return p / p.sum()


def p_first_max(centre: np.ndarray, sd: np.ndarray, n_grid: int = 6000) -> np.ndarray:
    """P(player i has the largest view) for independent Normal(centre_i, sd_i): a sharp drafter's pick."""
    c, sd = np.asarray(centre, float), np.asarray(sd, float)
    lo, hi = float(np.max(c - 8 * sd)), float(np.max(c + 8 * sd))
    v = np.linspace(lo, hi, n_grid)
    z = (v[None, :] - c[:, None]) / sd[:, None]
    log_cdf = log_ndtr(z)
    total = log_cdf.sum(axis=0)
    dens = np.exp(_norm_logpdf(z) - np.log(sd)[:, None] + total[None, :] - log_cdf)
    p = trapezoid(dens, v, axis=1)
    return p / p.sum()


def tobit_sd_fit(
    market_rank: np.ndarray, pick: np.ndarray, n_picks: int, sd_mult: np.ndarray | None = None,
    x0: tuple[float, float, float] = (0.0, SD_FLOOR, SD_SLOPE),
) -> dict:
    """Censored-Normal MLE of pick ~ Normal(market_rank + bias, (floor + slope * market_rank) * sd_mult).

    `pick` is the overall pick for drafted players and NaN for players still undrafted after `n_picks`
    (censored: latent > n_picks + 0.5). Returns estimates, SEs from the numerical Hessian, and the
    log-likelihood. Descriptive: the real room drafts by *rank* of latent positions, so check the estimator
    against simulated drafts (`simulate_adp_draft`) before reading the numbers as the room's sd."""
    mr = np.asarray(market_rank, float)
    pk = np.asarray(pick, float)
    mult = np.ones_like(mr) if sd_mult is None else np.asarray(sd_mult, float)
    obs = ~np.isnan(pk)
    cut = n_picks + 0.5

    def nll(th: np.ndarray) -> float:
        bias, a, b = th
        sd = (a + b * mr) * mult
        if np.any(sd <= 0.05):
            return 1e12
        z_obs = (pk[obs] - mr[obs] - bias) / sd[obs]
        ll = np.sum(_norm_logpdf(z_obs) - np.log(sd[obs]))
        ll += np.sum(log_ndtr(-(cut - mr[~obs] - bias) / sd[~obs]))
        return -float(ll)

    res = minimize(nll, np.array(x0, float), method="Nelder-Mead",
                   options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 20000, "maxfev": 40000})
    th = res.x
    h = _num_hessian(nll, th)
    try:
        cov = np.linalg.inv(h)
        se = np.sqrt(np.clip(np.diag(cov), 0, None))
    except np.linalg.LinAlgError:
        cov, se = np.full((3, 3), np.nan), np.full(3, np.nan)
    return {"bias": float(th[0]), "floor": float(th[1]), "slope": float(th[2]),
            "se": {"bias": float(se[0]), "floor": float(se[1]), "slope": float(se[2])},
            "cov": cov.tolist(), "loglik": -float(res.fun), "n_obs": int(obs.sum()), "n_censored": int((~obs).sum()),
            "converged": bool(res.success)}


def _num_hessian(f: Callable[[np.ndarray], float], x: np.ndarray, eps: float = 1e-3) -> np.ndarray:
    k = len(x)
    h = np.zeros((k, k))
    for i in range(k):
        for j in range(k):
            ei, ej = np.eye(k)[i] * eps, np.eye(k)[j] * eps
            h[i, j] = (f(x + ei + ej) - f(x + ei - ej) - f(x - ei + ej) + f(x - ei - ej)) / (4 * eps * eps)
    return h


def simulate_adp_draft(
    market_rank: np.ndarray, n_picks: int, rng: np.random.Generator, sd_mult: np.ndarray | float = 1.0,
    scale: float = 1.0,
) -> np.ndarray:
    """Overall pick of every player in one all-ADP room (NaN = undrafted after `n_picks`).

    Same opponent model as `fantasy.draft.availability.sample_market_positions` (rank of the noisy positions);
    `scale` multiplies the spread (scale 1 = the current prior)."""
    mr = np.asarray(market_rank, float)
    pos = mr + rng.normal(0, 1, len(mr)) * adp_sd(mr) * sd_mult * scale
    order = np.argsort(pos, kind="stable")[:n_picks]
    out = np.full(len(mr), np.nan)
    out[order] = np.arange(1, len(order) + 1)
    return out


def binary_scores(p: np.ndarray, y: np.ndarray, eps: float = 1e-3) -> dict:
    """Brier score, log loss (p clipped to [eps, 1 - eps]), mean forecast and base rate."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    pc = np.clip(p, eps, 1 - eps)
    return {"n": int(len(p)), "brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))),
            "mean_p": float(np.mean(p)) if len(p) else float("nan"),
            "base_rate": float(np.mean(y)) if len(y) else float("nan")}


def reliability_table(p: np.ndarray, y: np.ndarray, edges: tuple[float, ...] = (0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0)) -> list[dict]:
    """Per forecast bin: n, mean forecast, observed frequency and its binomial SE (independence assumed)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        n = int(m.sum())
        if n == 0:
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": 0})
            continue
        f = float(y[m].mean())
        rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": n, "mean_p": float(p[m].mean()), "observed": f,
                     "se": float(np.sqrt(max(f * (1 - f), 1e-9) / n))})
    return rows


def cluster_bootstrap(
    stat: Callable[[np.ndarray], float], clusters: np.ndarray, n_boot: int = 2000, seed: int = 0,
) -> dict:
    """Percentile CI of `stat(row_mask_indices)` resampling whole clusters (e.g. decision points or managers).

    `stat` receives an index array into the original rows (with repeats) and returns a float."""
    clusters = np.asarray(clusters)
    ids = np.unique(clusters)
    members = {c: np.flatnonzero(clusters == c) for c in ids}
    rng = np.random.default_rng(seed)
    full = float(stat(np.arange(len(clusters))))
    draws = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.choice(ids, len(ids), replace=True)
        draws[b] = stat(np.concatenate([members[c] for c in pick]))
    lo, hi = np.nanpercentile(draws, [2.5, 97.5])
    return {"estimate": full, "se": float(np.nanstd(draws, ddof=1)), "ci95": [float(lo), float(hi)],
            "n_clusters": int(len(ids))}

