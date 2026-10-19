"""The one game-to-game noise model (fantasy.model.noise) and that every consumer uses it."""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import fantasy
from fantasy.league.settings import LeagueSettings
from fantasy.model import noise
from fantasy.model.catmodel import build_arrays
from fantasy.model.lines import GAMES_PER_WEEK, STATS, effective_lines, load_projections
from fantasy.model.noise import GAME_VAR, game_var, game_var_matrix, ratio_var
from fantasy.model.valuation import ValuationParams, _cat_scores, compute_values, valuation_params

# a generous upper end of realistic per-game means (well past any NBA season average)
M_MAX = {"fgm": 15, "fga": 30, "ftm": 12, "fta": 15, "fg3m": 6, "pts": 45, "reb": 18, "ast": 14,
         "stl": 3.5, "blk": 5, "tov": 6}


def test_covers_every_stat():
    assert set(GAME_VAR) == set(STATS) == set(M_MAX)


@pytest.mark.parametrize("stat", STATS)
def test_variance_increases_with_the_mean(stat):
    m = np.linspace(0.0, M_MAX[stat], 400)
    v = game_var(stat, m)
    assert v[0] == 0.0 and np.all(np.diff(v) > 0)
    # ... but slower than the mean squared: the CV falls with volume (low-volume players are noisier per unit)
    cv = np.sqrt(v[1:]) / m[1:]
    assert cv[0] > cv[-1]


def test_matches_fitted_values_at_reference_points():
    # var = a*m + b*m^2 with the sim_calibration.md section 2 coefficients
    assert game_var("stl", 1.0) == pytest.approx(1.036 + 0.022)
    assert game_var("blk", 2.0) == pytest.approx(2 * 1.052 + 4 * 0.065)
    assert game_var("fga", 20.0) == pytest.approx(20 * 1.430 - 400 * 0.010)
    assert game_var("fta", 6.0) == pytest.approx(6 * 2.198 - 36 * 0.009)
    assert game_var("pts", 25.0) == pytest.approx(25 * 3.342 - 625 * 0.028)
    # the review's example: steals CV of a low-minute player (~0.7 stl/g) ~ 1.2, not the old constant 0.97
    assert np.sqrt(game_var("stl", 0.7)) / 0.7 == pytest.approx(1.22, abs=0.02)


def test_vectorised_shapes_and_matrix_form():
    m = np.abs(np.random.default_rng(0).normal(5, 3, (4, 3, len(STATS))))
    V = game_var_matrix(m)
    assert V.shape == m.shape
    for j, s in enumerate(STATS):
        assert np.allclose(V[..., j], game_var(s, m[..., j]))
    assert game_var("reb", 3.0).shape == ()
    assert np.all(game_var("pts", np.array([-1.0, 0.0])) == 0.0)  # clipped
    with pytest.raises(ValueError):
        game_var_matrix(np.ones((2, 3)))


def test_ratio_var():
    a = np.array([10.0, 10.0, 0.0])
    m = np.array([5.0, 6.0, 0.0])
    r = ratio_var(m, a, 0.5, "fga")
    assert r[0] == pytest.approx(10 * 0.25)  # at p_ref only the binomial term
    assert r[1] == pytest.approx(10 * 0.6 * 0.4 + game_var("fga", 10.0) * 0.01)
    assert r[2] == 0.0


def test_ratio_var_never_negative_when_makes_exceed_attempts():
    # p = 1.5 > 1 would make the binomial term a*p*(1-p) negative; the result is clipped at 0
    r = ratio_var(np.array([6.0, 30.0]), np.array([4.0, 4.0]), 0.99, "fta")
    assert np.all(r >= 0.0) and np.all(np.isfinite(r))


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_build_arrays_rejects_non_finite_projection(proj_path, bad):
    s = LeagueSettings()
    eff = effective_lines(load_projections(proj_path).df, None)
    pid = eff.index[3]
    eff.loc[pid, "reb"] = bad
    with pytest.raises(ValueError, match=rf"catmodel.build_arrays.*{re.escape(repr(pid))}.*'reb'"):
        build_arrays(eff, s)


@pytest.mark.parametrize("stat", ["fta", "blk"])  # a ratio-category denominator and a count category
def test_cat_scores_rejects_non_finite_projection(proj_path, stat):
    s = LeagueSettings()
    eff = effective_lines(load_projections(proj_path).df, None)
    pid = eff.index[7]
    eff.loc[pid, stat] = np.nan
    with pytest.raises(ValueError, match=rf"valuation._cat_scores.*{re.escape(repr(pid))}.*'{stat}'"):
        _cat_scores(eff, eff.index[:120], s, kappa=1.0)


def test_finite_checks_pass_on_valid_projections(proj_path):
    s = LeagueSettings()
    _, eff, _ = compute_values(load_projections(proj_path), s)
    build_arrays(eff, s)  # does not raise


# ---------------------------------------------------------------- every consumer uses it
def test_no_constant_cv_path_left_in_src():
    src = Path(fantasy.__file__).resolve().parent
    pat = re.compile(r"GAME_CV|FANTASY_GAME_CV|game_cv\b|GAME_VAR")
    hits = [f"{p.relative_to(src)}:{i}" for p in src.rglob("*.py") if p.name != "noise.py"
            for i, line in enumerate(p.read_text().splitlines(), 1) if pat.search(line.split("#")[0])]
    assert hits == []


def test_catmodel_uses_noise_model(proj_path):
    s = LeagueSettings()
    proj = load_projections(proj_path)
    _, eff, _ = compute_values(proj, s)
    arr = build_arrays(eff, s)
    assert np.allclose(arr.V, game_var_matrix(eff[STATS].to_numpy()))
    S = eff[STATS].to_numpy()
    for j, c in enumerate(c for c in s.cats if c.kind == "ratio"):
        m, a = S[:, STATS.index(c.num)], S[:, STATS.index(c.den)]
        assert np.allclose(arr.R[:, j], ratio_var(m, a, m.sum() / a.sum(), c.den))


def test_valuation_and_market_truth_use_noise_model(proj_path):
    from fantasy.sim.market_truth import fixed_pool_scores

    s = LeagueSettings()
    proj = load_projections(proj_path)
    eff = effective_lines(proj.df, None)
    pool = eff.index[:120]
    g = _cat_scores(eff, pool, s, kappa=1.0)
    for key, stat in (("stl", "stl"), ("pts", "pts"), ("tov", "tov")):
        x = eff[stat]
        denom = np.sqrt(x.loc[pool].std(ddof=0) ** 2 + game_var(stat, x.loc[pool].to_numpy()).mean() / GAMES_PER_WEEK)
        expect = (x - x.loc[pool].mean()) / denom
        assert np.allclose(g[key], expect if key != "tov" else -expect)
    # market_truth's fixed-pool scores reproduce the valuation exactly (same noise model)
    pd.testing.assert_frame_equal(fixed_pool_scores(eff, eff, pool, s, 1.0), g)


def test_runs_record_the_noise_model():
    p = valuation_params(ValuationParams(), LeagueSettings())
    assert p["noise_model"] == noise.NOISE_MODEL_VERSION and "game_cv" not in p
