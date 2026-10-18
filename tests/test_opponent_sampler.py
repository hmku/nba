"""One opponent (noisy-ADP) sampler shared by `draft simulate`, `sim` roster completion and the research
rooms (tech-debt F4)."""

from __future__ import annotations

import numpy as np

from fantasy.draft.availability import NO_ADP_SD_MULT, adp_sd, sample_market_positions, sd_mult_for


def test_formula_and_rng_consumption_match_the_inline_version():
    mr = np.arange(1, 301, dtype=float)
    sd_mult = sd_mult_for(np.arange(300) % 3 != 0)
    a = sample_market_positions(mr, np.random.default_rng(5), sd_mult, 1.3)
    rng = np.random.default_rng(5)
    b = mr + rng.normal(0, 1, len(mr)) * adp_sd(mr) * sd_mult * 1.3  # the pre-refactor inline expression
    assert np.array_equal(a, b)


def test_no_adp_players_are_noisier():
    assert list(sd_mult_for([True, False])) == [1.0, NO_ADP_SD_MULT]
    mr = np.full(20000, 100.0)
    rng = np.random.default_rng(0)
    with_adp = sample_market_positions(mr, rng, 1.0) - 100
    without = sample_market_positions(mr, np.random.default_rng(0), sd_mult_for(np.zeros(20000, bool))) - 100
    assert np.allclose(without, with_adp * NO_ADP_SD_MULT)


def test_sim_roster_completion_uses_the_no_adp_multiplier(monkeypatch):
    """complete_rosters passes per-player sd multipliers (market order aligned) to the shared sampler."""
    import pandas as pd

    from fantasy.league.settings import LeagueSettings
    from fantasy.sim import league as league_mod
    from fantasy.sim.league import LeagueState, complete_rosters

    seen = {}

    def spy(market_rank, rng, sd_mult=1.0, noise=1.0):
        seen["sd_mult"] = np.asarray(sd_mult)
        return sample_market_positions(market_rank, rng, sd_mult, noise)

    monkeypatch.setattr(league_mod, "sample_market_positions", spy)
    market = ["a", "b", "c", "d"]
    values = pd.DataFrame({"adp": [1.0, np.nan, 3.0, np.nan], "positions": [["PG"], ["C"], ["SF"], ["PF"]]},
                          index=["a", "b", "c", "d"])
    state = LeagueState(rosters={1: [], 2: []}, my_team=1, source="test")
    complete_rosters(state, [(1, 1), (2, 2)], values.loc[["d", "c", "b", "a"]], market, LeagueSettings())
    assert list(seen["sd_mult"]) == [1.0, NO_ADP_SD_MULT, 1.0, NO_ADP_SD_MULT]
