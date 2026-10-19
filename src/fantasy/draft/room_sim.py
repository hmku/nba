"""Monte Carlo availability: P(player still on the board at each of my future picks), estimated by
simulating the other teams' picks from the current board under an explicit room model W
(`OptimizerParams(availability="sim")`, opt-in; docs/research/draft_policy.md).

Why: `fantasy.draft.availability.prob_available` treats a sampled Normal(ADP) position as the pick number,
while every simulated room (draft simulate, sim roster completion, the research rooms) sorts the samples
and uses the rank as the pick; and it has no notion of opponents who draft by their own values. Planning
against one room and being graded in another biases the optimizer towards reaching.

Room models (the same worlds the policy harness `fantasy.draft.policy_eval` grades in):
  adp    every opponent takes the next available player of one noisy market order
         (position ~ Normal(market rank, adp_sd * sd_mult), as `sample_market_positions`); the latent
         positions of players still available are drawn conditional on not having gone before the
         current pick (truncated Normal), and the room drafts by their *rank*.
  sym    every opponent is "sharp": best available by its own noisy view of our values, leaning towards
         the market (SHARP_MARKET_WEIGHT). A cheap proxy for an optimizer drafting on independently
         perturbed projections (`fantasy.sim.symmetric.perturb_projections`); the noise sd was fitted to
         the value spread of perturbed views (SHARP_SD_*), the market lean to simulated symmetric rooms
         (docs/research/draft_policy.md, availability fidelity table).
  mixed  N_SHARP sharp opponents on random seats, the rest noisy ADP (the user's description of the league).
At my own picks the simulated me takes the best available by our value (so the number of players gone by
each pick is right; the optimizer's rollouts separately discount the specific players I plan to take).
"""

from __future__ import annotations

import numpy as np
from scipy.special import ndtr, ndtri

from fantasy.draft.availability import adp_sd

ROOMS = ("adp", "mixed", "sym")
N_SHARP = 3
# sharp view noise sd as a function of our value (fitted: sd of value(perturbed view) - value over 8 views,
# by value band: 1.02 top-30, 0.84 30-80, 0.62 80-160, 0.44 160-300)
SHARP_SD_A, SHARP_SD_B, SHARP_SD_MIN, SHARP_SD_MAX = 0.55, 0.08, 0.35, 1.2
# sharp views lean towards the market: centre = (1 - w) * our value + w * (our value of the player's market-rank
# slot). Fitted on 100 simulated symmetric rooms (seeds 10000-10099, disjoint from the evaluation seeds): mean
# abs error of P(available) at my picks 24..108 over the top 150, 0.054 at w = 0 -> 0.033 at w = 0.25-0.5
SHARP_MARKET_WEIGHT = 0.3
POOL = 320  # players simulated: the best POOL by min(market rank, value rank); the rest never go this early


def sharp_sd(value: np.ndarray) -> np.ndarray:
    return np.clip(SHARP_SD_A + SHARP_SD_B * value, SHARP_SD_MIN, SHARP_SD_MAX)


def simulate_availability(
    market_rank: np.ndarray, sd_mult: np.ndarray, value: np.ndarray, available: np.ndarray,
    order_slots: list[int], current: int, targets: list[int], my_slot: int, room: str = "mixed",
    n_sims: int = 300, seed: int = 0, n_sharp: int = N_SHARP, skip_my_current: bool = False,
) -> dict[int, np.ndarray]:
    """{pick p: P(row not taken by an opponent before p)} for every target pick p >= current.

    `order_slots[k - 1]` is the slot on the clock at overall pick k; `available` marks rows on the board now.
    `skip_my_current=True` (display only): when the current pick is mine, the simulated me does not pick at it,
    so the table answers "if I pass on this player now, is he still there at my next pick?". Same random draws
    (they do not depend on this flag or on `targets`); the optimizer's decision table keeps the default False."""
    if room not in ROOMS:
        raise ValueError(f"unknown room {room!r}; choose from {ROOMS}")
    n = len(market_rank)
    out = {p: available.astype(float) for p in targets}
    later = [p for p in targets if p > current]
    if not later:
        return out
    av = np.flatnonzero(available)
    vrank = np.empty(n)
    vrank[np.argsort(-value, kind="stable")] = np.arange(1, n + 1)
    key = np.minimum(market_rank[av], vrank[av])
    pool = av[np.argsort(key, kind="stable")[:POOL]]
    m = len(pool)
    rng = np.random.default_rng([seed, current, 7919])
    # ADP drafters: latent positions conditional on surviving to the current pick, drafted by rank
    mu, sd = market_rank[pool], adp_sd(market_rank[pool]) * sd_mult[pool]
    lo = ndtr((current - 0.5 - mu) / sd)
    u = lo + rng.random((n_sims, m)) * (1.0 - lo)
    lat = mu + sd * ndtri(np.clip(u, 1e-12, 1 - 1e-12))
    # sharp drafters: own noisy view of our values
    others = sorted({s for s in order_slots if s != my_slot})
    k_sharp = {"adp": 0, "sym": len(others), "mixed": min(n_sharp, len(others))}[room]
    sharp_seat = np.zeros((n_sims, max(others) + 1), dtype=int) - 1  # seat -> sharp index in this run, -1 = ADP
    for r in range(n_sims):
        seats = rng.choice(others, k_sharp, replace=False) if k_sharp < len(others) else np.array(others)
        sharp_seat[r, seats] = np.arange(k_sharp)
    mval = np.sort(value)[::-1][np.clip(market_rank.astype(int) - 1, 0, n - 1)]
    centre = (1 - SHARP_MARKET_WEIGHT) * value + SHARP_MARKET_WEIGHT * mval
    views = centre[pool][None, None, :] + rng.normal(0.0, 1.0, (n_sims, max(k_sharp, 1), m)) * sharp_sd(value[pool])
    taken = np.zeros((n_sims, m), dtype=bool)
    lat_m = lat.copy()
    runs = np.arange(n_sims)
    last = max(later)
    for k in range(current, last):
        if k in later:
            out[k][pool] = 1.0 - taken.mean(axis=0)
        slot = order_slots[k - 1]
        if slot == my_slot:
            if skip_my_current and k == current:
                continue
            pick = np.argmax(np.where(taken, -np.inf, value[pool][None, :]), axis=1)
            taken[runs, pick] = True
            lat_m[runs, pick] = np.inf
            views[runs, :, pick] = -np.inf
            continue
        j = sharp_seat[:, slot]
        pick = np.argmin(lat_m, axis=1)
        if k_sharp:
            is_sharp = j >= 0
            if is_sharp.any():
                rs = runs[is_sharp]
                pick[rs] = np.argmax(views[rs, j[rs], :], axis=1)
        taken[runs, pick] = True
        lat_m[runs, pick] = np.inf
        views[runs, :, pick] = -np.inf
    out[last][pool] = 1.0 - taken.mean(axis=0)
    return out
