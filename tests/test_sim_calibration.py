"""Calibrated simulator constants (docs/research/sim_calibration.md) and the opt-in fitted GAME_CV."""

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import fantasy
from fantasy.draft.order import draft_order
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import GAME_CV, GAME_CV_PRIOR, STATS, load_projections
from fantasy.sim import calibration as cal
from fantasy.sim.context import build_context
from fantasy.sim.engine import FACTOR_STATS, SimParams
from fantasy.sim.league import LeagueState, complete_rosters
from fantasy.sim.schedule import uniform_schedule

TEAMS = list(range(1, 13))
NOERR = dict(vol_sd=0.0, stat_sd=0.0, fg_pct_sd=0.0, ft_pct_sd=0.0)


@pytest.fixture(scope="module")
def ctx(proj_path):
    return build_context(load_projections(proj_path), LeagueSettings(), TEAMS, nba=uniform_schedule(["TST"]))


@pytest.fixture(scope="module")
def league(ctx):
    empty = LeagueState(rosters={t: [] for t in TEAMS}, my_team=1, source="test")
    rem = [(p.overall, p.slot) for p in draft_order(12, 13, "snake_3rr")]
    return complete_rosters(empty, rem, ctx.values, ctx.market, ctx.settings, seed=0)


def test_defaults_are_the_fitted_constants():
    p = SimParams()
    assert p.vol_sd == cal.VOL_SD and p.fg_pct_sd == cal.FG_PCT_SD and p.ft_pct_sd == cal.FT_PCT_SD
    assert p.stat_sd is None and p.stat_sds == cal.STAT_SD and set(cal.STAT_SD) == set(FACTOR_STATS)
    assert p.game_var == "fitted" and p.noise_corr
    C = np.array(cal.NOISE_CORR)
    assert np.allclose(C, C.T) and np.allclose(np.diag(C), 1.0)
    np.linalg.cholesky(C)  # positive definite
    assert set(cal.GAME_VAR) == set(STATS) == set(cal.GAME_CV_POOLED)


def test_draft_game_cv_default_unchanged():
    # draft-path freeze: the valuation/optimizer default stays the prior unless FANTASY_GAME_CV=fitted
    if os.environ.get("FANTASY_GAME_CV", "").lower() != "fitted":
        assert GAME_CV == GAME_CV_PRIOR


def test_fitted_game_cv_is_opt_in():
    code = "from fantasy.model.lines import GAME_CV, GAME_CV_FITTED, GAME_CV_SOURCE; print(GAME_CV_SOURCE, GAME_CV == GAME_CV_FITTED)"
    src = str(Path(fantasy.__file__).resolve().parents[1])
    env = {**os.environ, "FANTASY_GAME_CV": "fitted", "PYTHONPATH": src}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout
    assert out.split() == ["fitted", "True"]


def test_per_stat_and_scalar_stat_sd(ctx):
    pid = ctx.values.index[0]
    base = ctx.proj.df.loc[pid, STATS].to_numpy(dtype=float)
    # scalar 0 everywhere -> means equal the projection
    mu = ctx.simulator(SimParams(n_sims=50, **NOERR))._player_arrays([pid])["mu"][:, 0]
    assert np.allclose(mu, base[None, :])
    # only REB gets a stat-specific factor -> only REB varies
    sds = {s: 0.0 for s in FACTOR_STATS} | {"reb": 0.3}
    mu = ctx.simulator(SimParams(n_sims=400, vol_sd=0, stat_sd=None, stat_sds=sds, fg_pct_sd=0, ft_pct_sd=0))._player_arrays([pid])["mu"][:, 0]
    i = STATS.index("reb")
    assert np.log(mu[:, i] / base[i]).std() == pytest.approx(0.3, rel=0.15)
    others = [j for j in range(len(STATS)) if j != i]
    assert np.allclose(mu[:, others], base[None, others])


def test_game_variance_modes(ctx):
    m = np.array([[10.0, 20.0, 3.0, 4.0, 1.5, 25.0, 6.0, 4.0, 1.0, 0.6, 2.0]])
    fitted = ctx.simulator(SimParams(n_sims=1)).game_variance(m)
    a = np.array([cal.GAME_VAR[s][0] for s in STATS])
    b = np.array([cal.GAME_VAR[s][1] for s in STATS])
    assert np.allclose(fitted, a * m + b * m * m)
    cv = ctx.simulator(SimParams(n_sims=1, game_var="cv")).game_variance(m)
    assert np.allclose(cv, (m * np.array([GAME_CV[s] for s in STATS])) ** 2)
    with pytest.raises(ValueError):
        ctx.simulator(SimParams(n_sims=1, game_var="bogus"))


def _partial_corr(res, a, b):
    ia, ib = res.cats.index(a), res.cats.index(b)
    X = res.totals[..., [ia, ib]].astype(float)
    G = res.games.astype(float)[..., None]
    X, G = X - X.mean(0, keepdims=True), G - G.mean(0, keepdims=True)
    X, G = X.reshape(-1, 2), G.reshape(-1)
    R = X - np.outer(G, (G @ X) / (G @ G))
    return float(np.corrcoef(R.T)[0, 1])


def test_noise_correlation_raises_category_correlation(ctx, league):
    on = ctx.simulator(SimParams(n_sims=300, seed=2, streaming=False, **NOERR)).run(league)
    off = ctx.simulator(SimParams(n_sims=300, seed=2, streaming=False, noise_corr=False, **NOERR)).run(league)
    # REB and AST share a minutes shock (fitted noise corr 0.16); given games they are ~independent without it
    assert _partial_corr(on, "reb", "ast") > _partial_corr(off, "reb", "ast") + 0.05
    # the means are unaffected
    assert abs(on.totals.mean() - off.totals.mean()) < 0.01 * abs(off.totals.mean())
