"""Draft engine and drafting policies for simulated leagues.

A `Board` holds the draft state over the rows of a `DraftContext` (available mask, picks, rosters by
slot). Policies choose a row for a slot at a pick:

* `ADPPolicy`      - next available player of a sampled room order (noisy ADP; the same process as
                     `fantasy draft simulate`); positions ignored, as in `draft simulate`.
* `OptimizerPolicy`- the current draft optimizer (`DraftOptimizer.recommend` for that slot), with
                     optional diversity: softmax over the top-k recommendation scores (temperature
                     `temp`, in expected-category-win units) or, with prob `eps`, uniform among top-k.
* `BPAPolicy`      - best available by value keeping the starting lineup completable (baseline used by
                     `draft simulate`).
* `GreedyCatPolicy`- fast proxy of our strategy: one-step category-model objective of
                     roster + candidate + expected filler for the slot's remaining picks (the inner step
                     of the optimizer's rollout, without the lookahead). ~0.3 ms per pick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from fantasy.draft.availability import adp_sd
from fantasy.draft.optimizer import DraftContext, DraftOptimizer, DraftState, OptimizerParams
from fantasy.draft.roster import max_starting_matching


@dataclass
class Board:
    ctx: DraftContext
    available: np.ndarray
    taken: dict[int, str] = field(default_factory=dict)  # overall -> player id
    rosters: dict[int, list[int]] = field(default_factory=dict)  # slot -> rows

    @classmethod
    def empty(cls, ctx: DraftContext) -> Board:
        return cls(ctx, np.ones(len(ctx.ids), dtype=bool), {},
                   {t: [] for t in range(1, ctx.settings.num_teams + 1)})

    @classmethod
    def from_taken(cls, ctx: DraftContext, taken: dict[int, str]) -> Board:
        b = cls.empty(ctx)
        slot_of = {p.overall: p.slot for p in ctx.order}
        for ov in sorted(taken):
            pid = taken[ov]
            b.taken[ov] = pid
            if pid in ctx.pos:
                r = ctx.pos[pid]
                b.available[r] = False
                b.rosters[slot_of[ov]].append(r)
        return b

    def take(self, overall: int, slot: int, row: int) -> None:
        self.available[row] = False
        self.taken[overall] = self.ctx.ids[row]
        self.rosters[slot].append(row)

    def copy(self) -> Board:
        return Board(self.ctx, self.available.copy(), dict(self.taken),
                     {t: list(r) for t, r in self.rosters.items()})


class Policy(Protocol):
    def pick(self, board: Board, overall: int, slot: int) -> int: ...


def sample_room_order(ctx: DraftContext, rng: np.random.Generator, noise: float = 1.0) -> np.ndarray:
    """Rows in the order a noisy-ADP room drafts them: position ~ Normal(market rank, sd(adp) * noise)."""
    mr = ctx.market_rank
    sampled = mr + rng.normal(0, 1, len(mr)) * adp_sd(mr) * ctx.sd_mult * noise
    return np.argsort(sampled, kind="stable")


class ADPPolicy:
    def __init__(self, room_order: np.ndarray) -> None:
        self.order = room_order

    def pick(self, board: Board, overall: int, slot: int) -> int:
        av = board.available[self.order]
        return int(self.order[int(np.argmax(av))])


class BPAPolicy:
    def pick(self, board: Board, overall: int, slot: int) -> int:
        ctx = board.ctx
        mine = board.rosters[slot]
        my_total = sum(1 for p in ctx.order if p.slot == slot)
        left_after = my_total - len(mine) - 1
        slots = ctx.settings.starting_slots
        av = np.flatnonzero(board.available)
        for r in av[np.argsort(-ctx.value[av])]:
            f, _ = max_starting_matching([ctx.positions[i] for i in mine + [int(r)]], slots)
            if len(slots) - f <= left_after:
                return int(r)
        return int(av[np.argmax(ctx.value[av])])


class OptimizerPolicy:
    def __init__(self, ctx: DraftContext, strategy: str | None = None, temp: float = 0.0, eps: float = 0.0,
                 k: int = 5, rng: np.random.Generator | None = None, params: OptimizerParams | None = None,
                 seed: int | None = None) -> None:
        """`seed` (instead of `rng`): draw the diversity noise from rng([seed, slot, overall]) so the same
        decision gets the same draw across paired drafts that differ elsewhere."""
        self.opt = DraftOptimizer(ctx, params or OptimizerParams(strategy=strategy, availability="adp"))
        self.temp, self.eps, self.k = temp, eps, k
        self.rng = rng or np.random.default_rng(0)
        self.seed = seed
        self.last: dict | None = None

    def pick(self, board: Board, overall: int, slot: int) -> int:
        rec = self.opt.recommend(DraftState(taken=dict(board.taken), my_slot=slot))
        self.last = rec
        recs = rec["recommendations"][: self.k]
        j = 0
        rng = self.rng if self.seed is None else np.random.default_rng([self.seed, slot, overall])
        if len(recs) > 1 and (self.temp > 0 or self.eps > 0):
            if self.eps > 0 and rng.random() < self.eps:
                j = int(rng.integers(len(recs)))
            elif self.temp > 0:
                s = np.array([r["score"] for r in recs])
                w = np.exp((s - s.max()) / self.temp)
                j = int(rng.choice(len(recs), p=w / w.sum()))
        return board.ctx.pos[recs[j]["player_id"]]


class GreedyCatPolicy:
    """Fast myopic proxy for our strategy (see module doc). `pool` = candidates by value."""

    def __init__(self, ctx: DraftContext, pool: int = 30) -> None:
        self.ctx = ctx
        self.pool = pool
        self.by_value = np.argsort(-ctx.value, kind="stable")
        self.picks_of: dict[int, list[int]] = {}
        for p in ctx.order:
            self.picks_of.setdefault(p.slot, []).append(p.overall)
        self.w = np.ones(len(ctx.model.cats))

    def pick(self, board: Board, overall: int, slot: int) -> int:
        ctx, a = self.ctx, self.ctx.arrays
        cand = self.by_value[board.available[self.by_value]][: self.pool]
        rows = board.rosters[slot]
        remaining = [p for p in self.picks_of[slot] if p > overall]
        fs, fv, fr = ctx.filler_sums(remaining)
        if rows:
            idx = np.array(rows)
            fs, fv, fr = fs + a.S[idx].sum(0), fv + a.V[idx].sum(0), fr + a.R[idx].sum(0)
        P = ctx.model.win_probs(fs[None] + a.S[cand], fv[None] + a.V[cand], fr[None] + a.R[cand])
        return int(cand[int(np.argmax(P.sum(axis=1)))])


def run_draft(ctx: DraftContext, policies: dict[int, Policy], board: Board | None = None,
              start: int = 1, stop: int | None = None) -> Board:
    """Run picks start..stop (overall, inclusive) with `policies[slot]`; returns the board."""
    board = board or Board.empty(ctx)
    for pk in ctx.order:
        if pk.overall < start or (stop is not None and pk.overall > stop) or pk.overall in board.taken:
            continue
        row = policies[pk.slot].pick(board, pk.overall, pk.slot)
        board.take(pk.overall, pk.slot, row)
    return board
