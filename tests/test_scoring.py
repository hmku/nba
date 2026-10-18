import numpy as np

from fantasy.league.settings import LeagueSettings
from fantasy.model.catmodel import CategoryModel, build_arrays
from fantasy.model.lines import STATS, load_projections
from fantasy.model.valuation import ValuationParams, compute_values


def test_settings_defaults():
    s = LeagueSettings()
    assert s.num_rounds == 13
    assert len(s.starting_slots) == 10
    assert s.categories == ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
    assert "categories" in s.unverified_fields()


def test_values_sorted_and_replacement_near_zero(proj_path):
    values, eff, repl = compute_values(load_projections(proj_path), LeagueSettings())
    assert values["value"].is_monotonic_decreasing
    pool = 12 * 13
    assert abs(values["value"].iloc[pool : pool + 12].mean()) < 1e-9
    assert set(STATS) <= set(repl.index)


def test_ratio_category_uses_volume(proj_path):
    """Same FG% above average is worth more on higher volume (makes/attempts, not averaged %)."""
    proj = load_projections(proj_path)
    df = proj.df.copy()
    a, b = df.index[0], df.index[1]
    for pid, fga in ((a, 20.0), (b, 5.0)):
        df.loc[pid, ["fga", "fgm"]] = [fga, fga * 0.60]
    proj2 = type(proj)(df=df, path=proj.path, sha256="x")
    values, _, _ = compute_values(proj2, LeagueSettings())
    assert values.loc[a, "fg_pct"] > values.loc[b, "fg_pct"] > 0


def test_turnovers_are_negative(proj_path):
    values, _, _ = compute_values(load_projections(proj_path), LeagueSettings())
    proj = load_projections(proj_path).df
    hi, lo = proj["tov"].idxmax(), proj["tov"].idxmin()
    assert values.loc[hi, "tov"] < values.loc[lo, "tov"]


def test_punt_zeroes_category(proj_path):
    proj = load_projections(proj_path)
    v0, _, _ = compute_values(proj, LeagueSettings())
    v1, _, _ = compute_values(proj, LeagueSettings(), ValuationParams(punt=["ft_pct"]))
    assert not v0["value"].equals(v1["value"])


def test_average_team_wins_half(proj_path):
    s = LeagueSettings()
    values, eff, _ = compute_values(load_projections(proj_path), s)
    arrays = build_arrays(eff.loc[values.index], s)
    model = CategoryModel(s, arrays, list(values.index))
    S, V, R = model.opp_sums
    P = model.win_probs(S[None], V[None], R[None])[0]
    # the mean of league teams' sums gives ~0.5 in count cats
    assert np.allclose(P[2:], 0.5, atol=0.02)
    assert abs(model.matchup_win_prob(np.full((1, 9), 0.5))[0] - 0.5) < 1e-9


def test_matchup_prob_monotone():
    p = np.array([[0.6] * 9, [0.4] * 9])
    m = CategoryModel.matchup_win_prob(p)
    assert m[0] > 0.5 > m[1]
    assert abs(m[0] + m[1] - 1) < 1e-9
