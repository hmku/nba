"""Draft optimizer: choose the pick that maximizes the projected H2H strength of the *final* roster.

For each candidate at the current pick we run a lookahead rollout:
  1. add the candidate to my roster;
  2. for each of my later picks p, rank the players by the objective of
     (roster + player + expected filler for remaining picks) and add the *expected* pick
     (rollout_mode="expected", default): the best-ranked player still on the board, i.e. player i
     with probability q_i = pa_i * prod_{j ranked above i} (1 - pa_j), pa = P(available at p) from
     fantasy.draft.availability (independence approximation; leftover mass gets the filler line).
     Players' later availability is discounted by the chance I already took them.
     rollout_mode="greedy" (legacy, optimizer-0.1) instead assumes I get the single best player with
     P(available) >= `avail_threshold`, which treats a 51% player as certain and a 49% one as gone;
  3. score the completed (expected) roster with the category-win model (fantasy.model.catmodel).
"Filler" for an unfilled future pick is the average line of players the market takes around
that pick, so partial rosters are always compared at full size (`filler="market"`, default). Opt-in
research variants (docs/research/optimizer_placeholders.md; NOT defaults):
  filler="board"     the market window around p conditioned on the current board (taken players removed,
                     availability model from the current pick): E[line of the players the room takes at
                     p-4..p+2 | board];
  filler="available" the expected best-available-by-value player at p given the current board;
  refine_passes=k    fixed-point refinement: re-run each candidate's rollout k more times with the
                     placeholders for later picks replaced by the per-pick expected lines the previous pass
                     planned (complement-aware filler).

The objective is expected category wins vs the league-average opponent (Yahoo H2H categories
counts each category), optionally with punted categories zero-weighted. A strategy scan
re-runs the rollouts under several punt strategies and reports which yields the best full
9-category team. Rollouts naturally account for third-round reversal (pick gaps), scarcity,
complementarity and positional feasibility.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from fantasy.draft.availability import NO_ADP_SD_MULT as NO_ADP_SD_MULT
from fantasy.draft.availability import prob_available, sd_mult_for
from fantasy.draft.order import Pick, draft_order
from fantasy.draft.roster import max_starting_matching, unfilled_slots
from fantasy.league.settings import LeagueSettings
from fantasy.model.catmodel import CategoryModel, build_arrays
from fantasy.model.lines import ProjectionSet
from fantasy.model.valuation import ValuationParams, compute_values, market_order

# v0.2.0: expected-value rollouts (see docs/handoffs/2026-10-18-optimizer-diagnosis.md)
# v0.3.0: category model uses the one game-noise model (fantasy.model.noise, var = a*m + b*m^2) instead of
#         constant per-game CVs (valuation-0.3.0)
# v0.3.1: opt-in OptimizerParams(availability="sim", room=...) - Monte Carlo availability from the board
#         (fantasy.draft.room_sim); default (availability="adp") output unchanged; payload gains "availability"
# v0.4.0: DEFAULT availability="sim", room="mixed" (user-approved 2026-10-19, docs/research/draft_policy.md);
#         the analytic Normal(ADP) model stays reachable with availability="adp"
OPTIMIZER_VERSION = "optimizer-0.4.0"

DEFAULT_STRATEGIES: dict[str, list[str]] = {
    "balanced": [],
    "punt_ft": ["ft_pct"],
    "punt_fg": ["fg_pct"],
    "punt_tov": ["tov"],
    "punt_ast": ["ast"],
    "punt_pts": ["pts"],
    "punt_fg3m": ["fg3m"],
    "punt_ft_tov": ["ft_pct", "tov"],
}


@dataclass
class OptimizerParams:
    n_candidates: int = 14
    rollout_pool: int = 45  # players considered at each rollout step
    avail_threshold: float = 0.5  # greedy rollouts only
    rollout_mode: str = "expected"  # expected | greedy (legacy)
    min_avail: float = 0.02  # expected rollouts: ignore players less likely than this to be there
    objective: str = "cat_wins"  # cat_wins | matchup
    strategies: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_STRATEGIES))
    strategy: str | None = None  # force a strategy; None = scan
    strategy_switch_margin: float = 0.03  # require this improvement over balanced to punt
    # availability model: "sim" = Monte Carlo of the room `room` from the current board (fantasy.draft.room_sim;
    # default since optimizer-0.4.0, deterministic per board: seed = (0, current pick)) | "adp" = analytic
    # Normal(ADP) (fantasy.draft.availability; the pre-0.4.0 default, research harnesses pin it explicitly)
    availability: str = "sim"
    room: str = "mixed"  # sim only: adp | mixed | sym
    room_sims: int = 300
    # research option (docs/research/draft_policy.md, "Room-sim self-pick correction"): what the simulated me does
    # at my own picks in the sim table: best (default, unchanged) | skip | plan (fantasy.draft.room_sim)
    room_self: str = "best"
    # research options (docs/research/optimizer_placeholders.md); defaults keep optimizer-0.2.0 outputs identical
    filler: str = "market"  # market | board | available  (expected rollouts only)
    refine_passes: int = 0  # extra fixed-point passes with the previous pass's planned lines as filler


FILLER_MODES = ("market", "board", "available")
Line = tuple[np.ndarray, np.ndarray, np.ndarray]


class DraftContext:
    """Everything that's fixed for a draft: values, arrays, opponent model, market order."""

    def __init__(
        self,
        proj: ProjectionSet,
        settings: LeagueSettings,
        val_params: ValuationParams | None = None,
        values: pd.DataFrame | None = None,
        custom_rounds: list[list[int]] | None = None,
    ) -> None:
        self.settings = settings
        if values is None:
            values, eff, _ = compute_values(proj, settings, val_params)
        else:
            _, eff, _ = compute_values(proj, settings, val_params)
        self.values = values
        self.ids = list(values.index)
        self.pos = {pid: i for i, pid in enumerate(self.ids)}
        eff = eff.loc[self.ids]
        self.arrays = build_arrays(eff, settings)
        self.market = market_order(values)
        self.model = CategoryModel(settings, self.arrays, self.market)
        mrank = {pid: i + 1 for i, pid in enumerate(self.market)}
        self.market_rank = np.array([mrank[p] for p in self.ids], dtype=float)
        # players without market ADP: their slot is a model-based guess, so widen the spread
        self.sd_mult = sd_mult_for(values["adp"].notna().to_numpy())
        self.positions = [list(p) for p in values["positions"]]
        self.value = values["value"].to_numpy(dtype=float)
        self.order: list[Pick] = draft_order(settings.num_teams, settings.num_rounds, settings.draft_order_type, custom_rounds)
        self._filler = self._build_filler()
        self.value_order = np.argsort(-self.value, kind="stable")

    def _build_filler(self) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """Average components of players the market takes around each overall pick."""
        mk = [self.pos[p] for p in self.market]
        out = {}
        for pk in self.order:
            lo, hi = max(0, pk.overall - 4), min(len(mk), pk.overall + 3)
            rows = np.array(mk[lo:hi])
            out[pk.overall] = (
                self.arrays.S[rows].mean(axis=0),
                self.arrays.V[rows].mean(axis=0),
                self.arrays.R[rows].mean(axis=0),
            )
        return out

    def filler_sums(self, picks: list[int], lines: dict[int, Line] | None = None) -> Line:
        """Sum of the placeholder lines for `picks` (`lines` overrides the market filler per pick)."""
        src = self._filler if lines is None else lines
        S = np.zeros(self.arrays.S.shape[1])
        V = np.zeros_like(S)
        R = np.zeros(self.arrays.R.shape[1])
        for p in picks:
            s, v, r = src[p]
            S, V, R = S + s, V + v, R + r
        return S, V, R

    def board_filler(self, picks: list[int], current_pick: int, available: np.ndarray, mode: str,
                     pa_fn: Callable[[int, np.ndarray], np.ndarray] | None = None) -> dict[int, Line]:
        """Placeholder lines for `picks` conditioned on the board (research option, see module docstring).

        board:     weights w_i = P(avail at max(p-4, now)) - P(avail at p+3) over available players (= P(i is taken
                   in the window p-4..p+2)), normalised to an average line (the window market filler, re-centred
                   on the actual board).
        available: q_i = P(i is the highest-value available player at p) = pa_i * prod_{j valued above i}(1 - pa_j)
                   (independence approximation, as in the expected rollout); leftover mass gets the market line.
        """
        a = self.arrays
        out: dict[int, Line] = {}
        if mode == "available":
            idx = self.value_order[available[self.value_order]]
        else:
            idx = np.flatnonzero(available)
        mr, sm = self.market_rank[idx], self.sd_mult[idx]

        def pa_at(at: int) -> np.ndarray:  # P(available at `at`) for rows idx (optimizer's availability model)
            return prob_available(at, current_pick, mr, sm) if pa_fn is None else pa_fn(at, idx)

        for p in picks:
            fs, fv, fr = self._filler[p]
            if mode == "available":
                pa = pa_at(p)
                q = pa * np.concatenate([[1.0], np.cumprod(1.0 - pa)[:-1]])
                resid = max(0.0, 1.0 - float(q.sum()))
            elif mode == "board":
                lo = pa_at(max(p - 4, current_pick))
                hi = pa_at(p + 3)
                wsum = np.clip(lo - hi, 0.0, None)
                tot = float(wsum.sum())
                q = wsum / tot if tot > 1e-9 else wsum
                resid = 0.0 if tot > 1e-9 else 1.0
            else:
                raise ValueError(f"unknown filler mode {mode!r}")
            out[p] = (q @ a.S[idx] + resid * fs, q @ a.V[idx] + resid * fv, q @ a.R[idx] + resid * fr)
        return out

    def weights(self, punt: list[str]) -> np.ndarray:
        return np.array([0.0 if c.key in punt else 1.0 for c in self.model.cats])


@dataclass
class DraftState:
    taken: dict[int, str]  # overall pick -> player_id
    my_slot: int

    def current_pick(self, n_total: int) -> int:
        """The pick after the latest recorded one. Unrecorded earlier picks (sync gaps) are reported
        as warnings but must not move the clock backwards."""
        return min(max(self.taken, default=0) + 1, n_total + 1)

    def gaps(self) -> list[int]:
        return [k for k in range(1, max(self.taken, default=0)) if k not in self.taken]


class DraftOptimizer:
    def __init__(self, ctx: DraftContext, params: OptimizerParams | None = None) -> None:
        self.ctx = ctx
        self.params = params or OptimizerParams()
        if self.params.availability not in ("adp", "sim"):
            raise ValueError(f"unknown availability model {self.params.availability!r} (adp | sim)")
        if self.params.room_self not in ("best", "skip", "plan"):
            raise ValueError(f"unknown room_self {self.params.room_self!r} (best | skip | plan)")
        self._table: dict[int, np.ndarray] | None = None  # sim availability for the board being scored
        self._filler_cache: dict[tuple, dict[int, Line]] = {}  # research board fillers, reset per recommend

    def _pa(self, at_pick: int, current: int, idx: np.ndarray | None = None) -> np.ndarray:
        """P(available at `at_pick` | on the board now), for all rows or rows `idx`."""
        if self._table is not None and at_pick in self._table:
            t = self._table[at_pick]
            return t if idx is None else t[idx]
        ctx = self.ctx
        if idx is None:
            return prob_available(at_pick, current, ctx.market_rank, ctx.sd_mult)
        return prob_available(at_pick, current, ctx.market_rank[idx], ctx.sd_mult[idx])

    def _sim_table(self, available: np.ndarray, current: int, targets: list[int], my_slot: int,
                   skip_my_current: bool = False, state: DraftState | None = None) -> dict[int, np.ndarray]:
        from fantasy.draft.room_sim import simulate_availability

        ctx, prm = self.ctx, self.params
        if prm.room_self == "best":
            return simulate_availability(ctx.market_rank, ctx.sd_mult, ctx.value, available,
                                         [pk.slot for pk in ctx.order], current, targets, my_slot,
                                         room=prm.room, n_sims=prm.room_sims, skip_my_current=skip_my_current)
        plan = None
        if prm.room_self == "plan" and state is not None:  # first pass with "skip" -> the top candidate's plan
            first = DraftOptimizer(ctx, replace(prm, room_self="skip")).recommend(state)
            plan = {pp["pick"]: ctx.pos[pp["player_id"]] for pp in first["planned_picks"]}
        return simulate_availability(ctx.market_rank, ctx.sd_mult, ctx.value, available,
                                     [pk.slot for pk in ctx.order], current, targets, my_slot,
                                     room=prm.room, n_sims=prm.room_sims, skip_my_current=skip_my_current,
                                     self_pick=prm.room_self, plan=plan)

    # ------------------------------------------------------------------ helpers
    def _objective(self, P: np.ndarray, w: np.ndarray) -> np.ndarray:
        if self.params.objective == "matchup":
            return self.ctx.model.matchup_win_prob(np.where(w > 0, P, 0.0))
        return (P * w).sum(axis=-1)

    def _score_batch(
        self, base: tuple[np.ndarray, np.ndarray, np.ndarray], rows: np.ndarray, w: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        a = self.ctx.arrays
        S = base[0][None, :] + a.S[rows]
        V = base[1][None, :] + a.V[rows]
        R = base[2][None, :] + a.R[rows]
        P = self.ctx.model.win_probs(S, V, R)
        return self._objective(P, w), P

    def _sums(self, rows: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        a = self.ctx.arrays
        idx = np.array(rows, dtype=int)
        if len(idx) == 0:
            return np.zeros(a.S.shape[1]), np.zeros(a.S.shape[1]), np.zeros(a.R.shape[1])
        return a.S[idx].sum(0), a.V[idx].sum(0), a.R[idx].sum(0)

    def _feasible_rows(self, roster: list[int], cand: np.ndarray, picks_left_after: int) -> np.ndarray:
        slots = self.ctx.settings.starting_slots
        filled, _ = max_starting_matching([self.ctx.positions[i] for i in roster], slots)
        if len(slots) - filled <= picks_left_after:
            return np.ones(len(cand), dtype=bool)  # any player keeps the roster completable
        ok = np.zeros(len(cand), dtype=bool)
        for k, r in enumerate(cand):
            f, _ = max_starting_matching([self.ctx.positions[i] for i in roster + [int(r)]], slots)
            ok[k] = len(slots) - f <= picks_left_after
        return ok

    def my_picks(self, my_slot: int) -> list[int]:
        return [p.overall for p in self.ctx.order if p.slot == my_slot]

    # ------------------------------------------------------------------ rollout
    def rollout(
        self, roster: list[int], available: np.ndarray, future_picks: list[int], current_pick: int, w: np.ndarray
    ) -> tuple[list[int], float, np.ndarray]:
        if self.params.rollout_mode == "greedy":
            return self.rollout_greedy(roster, available, future_picks, current_pick, w)
        return self.rollout_expected(roster, available, future_picks, current_pick, w)

    def rollout_expected(
        self, roster: list[int], available: np.ndarray, future_picks: list[int], current_pick: int, w: np.ndarray
    ) -> tuple[list[int], float, np.ndarray]:
        """Expected completion of `roster`: at each future pick, mix the ranked candidates by the
        probability each is the best one left. Returns (most likely pick per future pick, objective, P)."""
        added, obj, P, _ = self._rollout_expected_lines(roster, available, future_picks, current_pick, w)
        return added, obj, P

    def _rollout_expected_lines(
        self, roster: list[int], available: np.ndarray, future_picks: list[int], current_pick: int, w: np.ndarray,
        fill: dict[int, Line] | None = None, resid_fill: dict[int, Line] | None = None,
    ) -> tuple[list[int], float, np.ndarray, dict[int, Line]]:
        """`rollout_expected` that also returns the expected line it planned for each future pick.

        `fill` overrides the placeholder line of later picks while ranking (None = market filler);
        `resid_fill` the line used for the probability mass no ranked candidate covers (None = market)."""
        ctx, a = self.ctx, self.ctx.arrays
        resid_src = ctx._filler if resid_fill is None else resid_fill
        lines: dict[int, Line] = {}
        roster = list(roster)
        not_mine = available.astype(float)  # P(player not already taken by me in this rollout)
        S, V, R = self._sums(roster)
        added: list[int] = []
        for j, p in enumerate(future_picks):
            remaining = future_picks[j + 1 :]
            pa = self._pa(p, current_pick) * not_mine
            cand = np.flatnonzero(pa >= self.params.min_avail)
            if len(cand) == 0:
                break
            cand = cand[np.argsort(-ctx.value[cand])][: self.params.rollout_pool]
            ok = self._feasible_rows(roster, cand, len(remaining))
            if ok.any():
                cand = cand[ok]
            fsum = ctx.filler_sums(remaining, fill)
            obj, _ = self._score_batch((S + fsum[0], V + fsum[1], R + fsum[2]), cand, w)
            ranked = cand[np.argsort(-obj, kind="stable")]
            pr = pa[ranked]
            q = pr * np.concatenate([[1.0], np.cumprod(1.0 - pr)[:-1]])
            resid = max(0.0, 1.0 - float(q.sum()))
            fs, fv, fr = resid_src[p]
            qs, qv, qr = q @ a.S[ranked], q @ a.V[ranked], q @ a.R[ranked]
            lines[p] = (qs + resid * fs, qv + resid * fv, qr + resid * fr)
            S = S + qs + resid * fs  # same association as optimizer-0.2.0 (default outputs bit-identical)
            V = V + qv + resid * fv
            R = R + qr + resid * fr
            not_mine[ranked] *= 1.0 - q
            q_rep = np.where(np.isin(ranked, added), -1.0, q)
            likely = int(ranked[int(np.argmax(q_rep))])
            roster.append(likely)  # representative pick: positional feasibility and the plan
            added.append(likely)
        P = ctx.model.win_probs(S[None], V[None], R[None])[0]
        return added, float(self._objective(P[None], w)[0]), P, lines

    def candidate_rollout(
        self, roster: list[int], available: np.ndarray, future_picks: list[int], current_pick: int, w: np.ndarray,
    ) -> tuple[list[int], float, np.ndarray, list[float]]:
        """The rollout used to score one candidate, honouring `filler` and `refine_passes`.

        Returns (plan, objective, P, objective after each pass). With the defaults this is exactly `rollout`."""
        prm = self.params
        if prm.rollout_mode != "expected" or (prm.filler == "market" and prm.refine_passes <= 0):
            added, obj, P = self.rollout(roster, available, future_picks, current_pick, w)
            return added, obj, P, [obj]
        if prm.filler not in FILLER_MODES:
            raise ValueError(f"unknown filler mode {prm.filler!r}; expected one of {FILLER_MODES}")
        base: dict[int, Line] | None = None
        if prm.filler != "market":
            key = (current_pick, prm.filler, tuple(future_picks), available.tobytes())
            base = self._filler_cache.get(key)
            if base is None:
                base = self.ctx.board_filler(future_picks, current_pick, available, prm.filler,
                                             lambda at, idx: self._pa(at, current_pick, idx))
                self._filler_cache[key] = base
        added, obj, P, lines = self._rollout_expected_lines(roster, available, future_picks, current_pick, w, base, base)
        objs = [obj]
        for _ in range(max(0, prm.refine_passes)):
            fill = dict(base) if base is not None else {p: self.ctx._filler[p] for p in future_picks}
            fill.update(lines)
            added, obj, P, lines = self._rollout_expected_lines(roster, available, future_picks, current_pick, w,
                                                                fill, base)
            objs.append(obj)
        return added, obj, P, objs

    def rollout_greedy(
        self, roster: list[int], available: np.ndarray, future_picks: list[int], current_pick: int, w: np.ndarray
    ) -> tuple[list[int], float, np.ndarray]:
        """Greedy completion of `roster` over `future_picks`. Returns (rows added, objective, P)."""
        roster = list(roster)
        avail = available.copy()
        added: list[int] = []
        for j, p in enumerate(future_picks):
            remaining = future_picks[j + 1 :]
            pa = self._pa(p, current_pick)
            cand_mask = avail & (pa >= self.params.avail_threshold)
            cand = np.flatnonzero(cand_mask)
            if len(cand) == 0:
                cand = np.flatnonzero(avail)
            if len(cand) == 0:
                break
            cand = cand[np.argsort(-self.ctx.value[cand])][: self.params.rollout_pool]
            ok = self._feasible_rows(roster, cand, len(remaining))
            if ok.any():
                cand = cand[ok]
            base_r = self._sums(roster)
            fill = self.ctx.filler_sums(remaining)
            base = (base_r[0] + fill[0], base_r[1] + fill[1], base_r[2] + fill[2])
            obj, _ = self._score_batch(base, cand, w)
            best = int(cand[int(np.argmax(obj))])
            roster.append(best)
            added.append(best)
            avail[best] = False
        S, V, R = self._sums(roster)
        P = self.ctx.model.win_probs(S[None], V[None], R[None])[0]
        return added, float(self._objective(P[None], w)[0]), P

    # ------------------------------------------------------------------ public API
    def recommend(self, state: DraftState, draft_id: str = "local", model_run_id: str | None = None) -> dict:
        """Rank candidates for my next pick on the board `state` (strategy scan + rollouts) and build the payload."""
        ctx, prm = self.ctx, self.params
        n_total = len(ctx.order)
        current = state.current_pick(n_total)
        available, unknown = self._available(state.taken)
        my_all = self.my_picks(state.my_slot)
        my_roster_ids = [state.taken[p] for p in my_all if p in state.taken]
        roster = [ctx.pos[p] for p in my_roster_ids if p in ctx.pos]
        future = [p for p in my_all if p >= current and p not in state.taken]
        on_clock_slot = ctx.order[current - 1].slot if current <= n_total else None
        my_turn = on_clock_slot == state.my_slot
        warnings = self._board_warnings(state, unknown)
        if not future:
            return {"draft_id": draft_id, "as_of_pick": current - 1, "for_pick": None, "my_next_picks": [],
                    "recommendations": [], "warnings": warnings + ["No remaining picks for my slot."],
                    "roster": self._roster_payload(state, my_all), "generated_at": _now()}

        decision_pick, later = future[0], future[1:]
        self._table = (self._sim_table(available, current, future, state.my_slot, state=state)
                       if prm.availability == "sim" else None)
        cands = self._candidates(available, current, decision_pick, roster, len(later))
        strategies, strat_results, per_strategy_cands = self._scan_strategies(cands, available, roster, later,
                                                                              current)
        chosen = self._choose_strategy(strat_results)
        rows = per_strategy_cands[chosen]
        next_pick = later[0] if later else None
        pa_disp = self._display_availability(available, current, future, state.my_slot, my_turn, next_pick)
        recs = self._recommendation_rows(rows, pa_disp, decision_pick, next_pick)
        research = prm.rollout_mode == "expected" and (prm.filler != "market" or prm.refine_passes > 0)
        if research:
            for rec_, r in zip(recs, rows, strict=True):
                rec_["score_passes"] = [round(x, 4) for x in r["obj_passes"]]
        top = recs[0]
        out = {
            "draft_id": draft_id,
            "as_of_pick": current - 1,
            "for_pick": decision_pick,
            "on_the_clock": {"overall": current, "slot": on_clock_slot, "is_me": my_turn},
            "picks_until_mine": decision_pick - current,
            "my_next_picks": future,
            "model_run_id": model_run_id,
            "generated_at": _now(),
            "objective": prm.objective,
            "optimizer_version": OPTIMIZER_VERSION,
            "rollout_mode": prm.rollout_mode,
            "availability": prm.availability if prm.availability == "adp" else f"sim:{prm.room}x{prm.room_sims}"
                            + ("" if prm.room_self == "best" else f":self={prm.room_self}"),
            "recommendations": recs,
            "strategy": {"name": chosen, "punt": strategies[chosen],
                         "notes": _strategy_note(chosen, strat_results), "scan": strat_results},
            "team_profile": self._team_profile(roster, top, strategies[chosen]),
            "planned_picks": [{"pick": p, "player_id": pid, "name": str(ctx.values.loc[pid, "name"])}
                              for p, pid in zip(later, top["plan"], strict=False)],
            "roster": self._roster_payload(state, my_all),
            "open_starting_slots": unfilled_slots([ctx.positions[i] for i in roster], ctx.settings.starting_slots),
            "warnings": warnings,
        }
        if research:  # research options only: default payloads stay identical to optimizer-0.2.0
            out["filler"] = {"mode": prm.filler, "refine_passes": prm.refine_passes,
                             "convergence": _convergence(rows),
                             "evidence": "docs/research/optimizer_placeholders.md"}
        return out

    def _available(self, taken: dict[int, str]) -> tuple[np.ndarray, list[str]]:
        """(availability mask over ctx rows, sorted drafted ids that are not in the projections)."""
        ctx = self.ctx
        taken_ids = set(taken.values())
        unknown = sorted(t for t in taken_ids if t not in ctx.pos)
        available = np.ones(len(ctx.ids), dtype=bool)
        for t in taken_ids:
            if t in ctx.pos:
                available[ctx.pos[t]] = False
        return available, unknown

    def _board_warnings(self, state: DraftState, unknown: list[str]) -> list[str]:
        warnings: list[str] = []
        if state.gaps():
            warnings.append(f"Picks {state.gaps()} not recorded: some listed players may already be gone; sync the board.")
        if unknown:
            warnings.append(f"{len(unknown)} drafted player(s) not in projections: {', '.join(unknown[:5])}")
        unverified = self.ctx.settings.unverified_fields()
        if unverified:
            warnings.append("League settings not yet verified from Yahoo: " + ", ".join(unverified))
        return warnings

    def _candidates(self, available: np.ndarray, current: int, decision_pick: int, roster: list[int],
                    n_later: int) -> list[int]:
        """Rows to score: best by value + best by market among available (only feasible ones, if any are)."""
        ctx, prm = self.ctx, self.params
        avail_idx = np.flatnonzero(available)
        if decision_pick > current:
            # not on the clock yet: only consider players with a real chance of lasting to my pick
            p_dec = self._pa(decision_pick, current, avail_idx)
            avail_idx = avail_idx[p_dec >= 0.15]
        by_val = avail_idx[np.argsort(-ctx.value[avail_idx])][: prm.n_candidates]
        by_mkt = avail_idx[np.argsort(ctx.market_rank[avail_idx])][:4]
        cands = list(dict.fromkeys([*by_val.tolist(), *by_mkt.tolist()]))
        return [c for c in cands if self._feasible_rows(roster, np.array([c]), n_later)[0]] or cands

    def _scan_strategies(
        self, cands: list[int], available: np.ndarray, roster: list[int], later: list[int], current: int,
    ) -> tuple[dict[str, list[str]], dict[str, dict], dict[str, list[dict]]]:
        """Score every candidate under each punt strategy. Returns (strategies scanned, best result per strategy,
        candidate rows per strategy sorted by objective)."""
        ctx, prm = self.ctx, self.params
        strategies = prm.strategies if prm.strategy is None else {prm.strategy: prm.strategies[prm.strategy]}
        strat_results: dict[str, dict] = {}
        per_strategy_cands: dict[str, list[dict]] = {}
        self._filler_cache = {}
        for name, punt in strategies.items():
            w = ctx.weights(punt)
            rows: list[dict] = []
            for c in cands:
                av = available.copy()
                av[c] = False
                added, obj, P, objs = self.candidate_rollout(roster + [c], av, later, current, w)
                full_obj = float(P.sum())
                rows.append({"row": c, "obj": obj, "P": P, "plan": added, "cat_wins": full_obj,
                             "matchup": float(ctx.model.matchup_win_prob(P[None])[0]), "obj_passes": objs})
            rows.sort(key=lambda r: -r["obj"])
            per_strategy_cands[name] = rows
            best = rows[0]
            strat_results[name] = {"punt": punt, "best_player": ctx.ids[best["row"]],
                                   "expected_category_wins": best["cat_wins"], "matchup_win_prob": best["matchup"]}
        return strategies, strat_results, per_strategy_cands

    def _choose_strategy(self, strat_results: dict[str, dict]) -> str:
        """Best-scoring strategy; a punt must beat balanced by `strategy_switch_margin`."""
        prm = self.params
        n_cats = len(self.ctx.weights([]))

        def strat_score(name: str) -> float:
            r = strat_results[name]
            return r["matchup_win_prob"] if prm.objective == "matchup" else r["expected_category_wins"] / n_cats

        chosen = max(strat_results, key=strat_score)
        if "balanced" in strat_results and chosen != "balanced":
            if strat_score(chosen) - strat_score("balanced") < prm.strategy_switch_margin:
                chosen = "balanced"
        return chosen

    def _display_availability(self, available: np.ndarray, current: int, future: list[int], my_slot: int,
                              my_turn: bool, next_pick: int | None) -> Callable[[int, int], float]:
        """P(row available at pick) as displayed.

        The decision table lets the simulated me take the best-value player at my current pick (so he shows P = 0
        at every later pick, even back-to-back); for display, answer "if I pass on him now, is he there at my next
        pick?" from a table that skips my own current pick (same seeds). Decision path, scores and ranking are
        unchanged."""
        disp = self._table
        if self._table is not None and my_turn and next_pick and self.params.room_self == "best":
            disp = self._sim_table(available, current, future, my_slot, skip_my_current=True)  # non-best: skips

        def pa_disp(at: int, c: int) -> float:
            if disp is not None and at in disp:
                return float(disp[at][c])
            return float(self._pa(at, current, np.array([c]))[0])

        return pa_disp

    def _recommendation_rows(self, rows: list[dict], pa_disp: Callable[[int, int], float], decision_pick: int,
                             next_pick: int | None) -> list[dict]:
        """One payload row per scored candidate (in score order), with its reason."""
        ctx = self.ctx
        best_obj = rows[0]["obj"]
        second_obj = rows[1]["obj"] if len(rows) > 1 else best_obj
        recs = []
        for r in rows:
            c = r["row"]
            pid = ctx.ids[c]
            v = ctx.values.loc[pid]
            p_next = pa_disp(next_pick, c) if next_pick else 0.0
            p_dec = pa_disp(decision_pick, c)
            marginal = r["obj"] - (second_obj if r["obj"] == best_obj else best_obj)
            recs.append({
                "player_id": pid,
                "name": str(v["name"]),
                "team": _s(v.get("team")),
                "positions": list(v["positions"]),
                "score": round(r["obj"], 4),
                "value": round(float(v["value"]), 3),
                "rank": int(v["rank"]),
                "market_rank": int(ctx.market_rank[c]),
                "marginal_value": round(marginal, 4),
                "prob_available_next": round(p_next, 3),
                "prob_available_at_pick": round(p_dec, 3),
                "adp": None if pd.isna(v["adp"]) else float(v["adp"]),
                "cat_impact": {k: round(float(v[k]), 3) for k in ctx.settings.categories},
                "projected_cat_win_probs": {c2.key: round(float(p), 3) for c2, p in zip(ctx.model.cats, r["P"], strict=True)},
                "expected_category_wins": round(r["cat_wins"], 3),
                "matchup_win_prob": round(r["matchup"], 3),
                "plan": [ctx.ids[x] for x in r["plan"]],
                "reason": "",
            })
        for rec in recs:
            rec["reason"] = self._reason(rec, recs[0], next_pick)
        return recs

    def _team_profile(self, roster: list[int], top: dict, punts: list[str]) -> dict:
        """Category profile of the projected final roster (current roster + top recommendation + its plan)."""
        ctx = self.ctx
        final_team = roster + [ctx.pos[top["player_id"]]] + [ctx.pos[p] for p in top["plan"]]
        team_eval = ctx.model.evaluate_team(final_team)
        current_eval = ctx.model.evaluate_team(roster) if roster else None
        return {
            "categories": {k: {"win_prob": round(p, 3), "mean": round(team_eval["cat_means"][k], 3),
                               "league_avg": round(team_eval["opp_means"][k], 3)}
                           for k, p in team_eval["cat_win_probs"].items()},
            "punts": punts,
            "expected_category_wins": round(team_eval["expected_category_wins"], 3),
            "matchup_win_prob": round(team_eval["matchup_win_prob"], 3),
            "basis": "projected final roster if the top recommendation and its planned (most likely) picks are taken",
            "current_roster_cat_win_probs": current_eval["cat_win_probs"] if current_eval else None,
        }

    def _roster_payload(self, state: DraftState, my_all: list[int]) -> list[dict]:
        out = []
        for p in my_all:
            pid = state.taken.get(p)
            if pid is None:
                continue
            row = self.ctx.values.loc[pid] if pid in self.ctx.pos else None
            out.append({"player_id": pid, "overall_pick": p,
                        "name": str(row["name"]) if row is not None else pid,
                        "positions": list(row["positions"]) if row is not None else []})
        return out

    def _reason(self, rec: dict, top: dict, next_pick: int | None) -> str:
        cats = sorted(rec["cat_impact"].items(), key=lambda kv: -kv[1])
        strong = [k for k, v in cats if v >= 0.8][:3]
        weak = [k for k, v in cats if v <= -0.8][:2]
        parts = []
        if rec is top:
            parts.append(f"Best projected final roster ({rec['expected_category_wins']:.2f} exp. cat wins/9, "
                         f"{rec['matchup_win_prob']:.0%} matchup win)")
        else:
            parts.append(f"{rec['score'] - top['score']:+.3f} vs top option")
        if strong:
            parts.append("strong " + "/".join(strong))
        if weak:
            parts.append("hurts " + "/".join(weak))
        if next_pick:
            parts.append(f"{rec['prob_available_next']:.0%} chance available at #{next_pick}")
        return "; ".join(parts)


def _convergence(rows: list[dict]) -> dict:
    """Per refinement pass: max |score change| over candidates and whether the #1 changed vs the previous pass."""
    n = min(len(r["obj_passes"]) for r in rows)
    steps = []
    for k in range(1, n):
        prev = max(rows, key=lambda r: r["obj_passes"][k - 1])["row"]
        cur = max(rows, key=lambda r: r["obj_passes"][k])["row"]
        steps.append({"pass": k, "max_abs_score_change": round(max(abs(r["obj_passes"][k] - r["obj_passes"][k - 1])
                                                                   for r in rows), 4),
                      "top_changed": prev != cur})
    return {"passes": n, "steps": steps}


def _strategy_note(chosen: str, res: dict) -> str:
    bal = res.get("balanced")
    c = res[chosen]
    if chosen == "balanced" or not bal:
        return "Balanced build projects best (punting did not improve projected wins enough)."
    return (f"{chosen}: {c['expected_category_wins']:.2f} exp. cat wins vs balanced "
            f"{bal['expected_category_wins']:.2f}; matchup win {c['matchup_win_prob']:.0%} vs {bal['matchup_win_prob']:.0%}")


def _s(x: object) -> str:
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else str(x)


def _now() -> str:
    return datetime.now(UTC).isoformat()
