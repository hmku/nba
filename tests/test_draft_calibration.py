"""Research helpers for T-016 draft-room calibration (fantasy.draft.calibration). Synthetic data only."""

from __future__ import annotations

import numpy as np
import pytest

from fantasy.draft.calibration import (
    binary_scores,
    cluster_bootstrap,
    p_first_max,
    p_first_min,
    reliability_table,
    simulate_adp_draft,
    tobit_sd_fit,
)


def test_p_first_min_matches_monte_carlo() -> None:
    mu = np.array([10.0, 11.0, 12.5, 15.0, 30.0])
    sd = np.array([2.0, 3.0, 2.5, 4.0, 6.0])
    lo = 9.5
    p = p_first_min(mu, sd, lo)
    assert p.sum() == pytest.approx(1.0)
    rng = np.random.default_rng(0)
    n = 200_000
    x = rng.normal(mu, sd, size=(n * 3, len(mu)))
    # truncate each latent to (lo, inf) by rejection per column
    cols = []
    for j in range(len(mu)):
        c = x[:, j][x[:, j] > lo][:n]
        assert len(c) == n
        cols.append(c)
    x = np.stack(cols, axis=1)
    mc = np.bincount(np.argmin(x, axis=1), minlength=len(mu)) / n
    assert np.allclose(p, mc, atol=0.006)


def test_p_first_max_matches_monte_carlo() -> None:
    c = np.array([3.0, 2.6, 2.0, 0.5])
    sd = np.array([0.8, 1.0, 0.5, 1.2])
    p = p_first_max(c, sd)
    assert p.sum() == pytest.approx(1.0)
    rng = np.random.default_rng(1)
    v = rng.normal(c, sd, size=(200_000, len(c)))
    mc = np.bincount(np.argmax(v, axis=1), minlength=len(c)) / len(v)
    assert np.allclose(p, mc, atol=0.006)


def test_simulate_adp_draft_shapes_and_zero_noise() -> None:
    mr = np.arange(1, 41, dtype=float)
    rng = np.random.default_rng(2)
    pk = simulate_adp_draft(mr, 24, rng)
    drafted = pk[~np.isnan(pk)]
    assert sorted(drafted.tolist()) == list(range(1, 25))
    assert np.isnan(pk).sum() == 16
    exact = simulate_adp_draft(mr, 24, rng, scale=0.0)
    assert np.array_equal(exact[:24], np.arange(1, 25)) and np.isnan(exact[24:]).all()


def test_tobit_recovers_parameters_on_normal_data() -> None:
    rng = np.random.default_rng(3)
    mr = np.tile(np.arange(1, 301, dtype=float), 8)
    a, b, bias, cut = 2.0, 0.12, 1.0, 156
    latent = mr + bias + rng.normal(0, 1, len(mr)) * (a + b * mr)
    pick = np.where(latent <= cut + 0.5, latent, np.nan)
    fit = tobit_sd_fit(mr, pick, cut)
    assert fit["converged"]
    assert fit["slope"] == pytest.approx(b, abs=4 * fit["se"]["slope"] + 0.005)
    assert fit["floor"] == pytest.approx(a, abs=4 * fit["se"]["floor"] + 0.2)
    assert fit["bias"] == pytest.approx(bias, abs=4 * fit["se"]["bias"] + 0.2)
    assert fit["n_censored"] == int(np.isnan(pick).sum())


def test_scores_reliability_bootstrap() -> None:
    p = np.array([0.0, 1.0, 0.5, 0.5])
    y = np.array([0.0, 1.0, 1.0, 0.0])
    s = binary_scores(p, y)
    assert s["brier"] == pytest.approx(0.125) and s["base_rate"] == 0.5 and s["n"] == 4
    rows = reliability_table(p, y, edges=(0.0, 0.4, 0.6, 1.0))
    assert [r["n"] for r in rows] == [1, 2, 1]
    assert rows[1]["observed"] == pytest.approx(0.5)
    vals = np.arange(20, dtype=float)
    clusters = np.repeat(np.arange(5), 4)
    bs = cluster_bootstrap(lambda i: float(vals[i].mean()), clusters, n_boot=500, seed=0)
    assert bs["n_clusters"] == 5 and bs["estimate"] == pytest.approx(9.5)
    assert bs["ci95"][0] < 9.5 < bs["ci95"][1]
