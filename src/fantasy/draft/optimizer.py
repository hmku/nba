"""Draft optimizer: choose the pick that maximizes the projected H2H strength of the *final* roster.

For each candidate at the current pick we run a lookahead rollout:
  1. add the candidate to my roster;
  2. for each of my later picks p, consider only players likely still available at p
     (P(available) >= `avail_threshold`, from fantasy.draft.availability), and greedily add the
     one that maximizes the objective of (roster + player + expected filler for remaining picks);
  3. score the completed roster with the category-win model (fantasy.model.catmodel).
"Filler" for an unfilled future pick is the average line of players the market takes around
that pick, so partial rosters are always compared at full size.

The objective is expected category wins vs the league-average opponent (Yahoo H2H categories
counts each category), optionally with punted categories zero-weighted. A strategy scan
re-runs the rollouts under several punt strategies and reports which yields the best full
9-category team. Rollouts naturally account for third-round reversal (pick gaps), scarcity,
complementarity and positional feasibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from fantasy.draft.availability import prob_available
from fantasy.draft.order import Pick, draft_order
from fantasy.draft.roster import max_starting_matching, unfilled_slots
from fantasy.league.settings import LeagueSettings
from fantasy.model.catmodel import CategoryModel, build_arrays
from fantasy.model.lines import ProjectionSet
from fantasy.model.valuation import ValuationParams, compute_values, market_order

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
    avail_threshold: float = 0.5
    objective: str = "cat_wins"  # cat_wins | matchup
    strategies: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_STRATEGIES))
    strategy: str | None = None  # force a strategy; None = scan
    strategy_switch_margin: float = 0.03  # require this improvement over balanced to punt


class DraftContext:
    """Everything that's fixed for a draft: values, arrays, opponent model, market order."""

    def __init__(
        self,
        proj: ProjectionSet,
        settings: LeagueSettings,
        val_params: ValuationParams | None = None,
        values: pd.DataFrame | None = None,
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
        self.positions = [list(p) for p in values["positions"]]
        self.value = values["value"].to_numpy(dtype=float)
        self.order: list[Pick] = draft_order(settings.num_teams, settings.num_rounds, settings.draft_order_type)
        self._filler = self._build_filler()

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

    def filler_sums(self, picks: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        S = np.zeros(self.arrays.S.shape[1])
        V = np.zeros_like(S)
        R = np.zeros(self.arrays.R.shape[1])
        for p in picks:
            s, v, r = self._filler[p]
            S, V, R = S + s, V + v, R + r
        return S, V, R

    def weights(self, punt: list[str]) -> np.ndarray:
        return np.array([0.0 if c.key in punt else 1.0 for c in self.model.cats])


@dataclass
class DraftState:
    taken: dict[int, str]  # overall pick -> player_id
    my_slot: int

    def current_pick(self, n_total: int) -> int:
        for k in range(1, n_total + 1):
            if k not in self.taken:
                return k
        return n_total + 1


class DraftOptimizer:
    def __init__(self, ctx: DraftContext, params: OptimizerParams | None = None) -> None:
        self.ctx = ctx
        self.params = params or OptimizerParams()

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
        """Greedy completion of `roster` over `future_picks`. Returns (rows added, objective, P)."""
        roster = list(roster)
        avail = available.copy()
        added: list[int] = []
        mrank = self.ctx.market_rank
        for j, p in enumerate(future_picks):
            remaining = future_picks[j + 1 :]
            pa = prob_available(p, current_pick, mrank)
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
        ctx, prm = self.ctx, self.params
        n_total = len(ctx.order)
        current = state.current_pick(n_total)
        taken_ids = set(state.taken.values())
        unknown = sorted(t for t in taken_ids if t not in ctx.pos)
        available = np.ones(len(ctx.ids), dtype=bool)
        for t in taken_ids:
            if t in ctx.pos:
                available[ctx.pos[t]] = False
        my_all = self.my_picks(state.my_slot)
        my_roster_ids = [state.taken[p] for p in my_all if p in state.taken]
        roster = [ctx.pos[p] for p in my_roster_ids if p in ctx.pos]
        future = [p for p in my_all if p >= current and p not in state.taken]
        on_clock_slot = ctx.order[current - 1].slot if current <= n_total else None
        my_turn = on_clock_slot == state.my_slot
        warnings: list[str] = []
        if unknown:
            warnings.append(f"{len(unknown)} drafted player(s) not in projections: {', '.join(unknown[:5])}")
        unverified = ctx.settings.unverified_fields()
        if unverified:
            warnings.append("League settings not yet verified from Yahoo: " + ", ".join(unverified))
        if not future:
            return {"draft_id": draft_id, "as_of_pick": current - 1, "for_pick": None, "my_next_picks": [],
                    "recommendations": [], "warnings": warnings + ["No remaining picks for my slot."],
                    "roster": self._roster_payload(state, my_all), "generated_at": _now()}

        decision_pick, later = future[0], future[1:]
        # candidates: best by value + best by market among available
        avail_idx = np.flatnonzero(available)
        by_val = avail_idx[np.argsort(-ctx.value[avail_idx])][: prm.n_candidates]
        by_mkt = avail_idx[np.argsort(ctx.market_rank[avail_idx])][:4]
        if decision_pick > current:
            # not on the clock yet: only consider players with a real chance of lasting to my pick
            p_dec = prob_available(decision_pick, current, ctx.market_rank[avail_idx])
            avail_idx = avail_idx[p_dec >= 0.15]
            by_val = avail_idx[np.argsort(-ctx.value[avail_idx])][: prm.n_candidates]
            by_mkt = avail_idx[np.argsort(ctx.market_rank[avail_idx])][:4]
        cands = list(dict.fromkeys([*by_val.tolist(), *by_mkt.tolist()]))
        cands = [c for c in cands if self._feasible_rows(roster, np.array([c]), len(later))[0]] or cands

        # --- strategy scan
        strategies = prm.strategies if prm.strategy is None else {prm.strategy: prm.strategies[prm.strategy]}
        w_full = ctx.weights([])
        strat_results: dict[str, dict] = {}
        per_strategy_cands: dict[str, list[dict]] = {}
        for name, punt in strategies.items():
            w = ctx.weights(punt)
            rows = []
            for c in cands:
                av = available.copy()
                av[c] = False
                added, obj, P = self.rollout(roster + [c], av, later, current, w)
                full_obj = float(P.sum())
                rows.append({"row": c, "obj": obj, "P": P, "plan": added, "cat_wins": full_obj,
                             "matchup": float(ctx.model.matchup_win_prob(P[None])[0])})
            rows.sort(key=lambda r: -r["obj"])
            per_strategy_cands[name] = rows
            best = rows[0]
            strat_results[name] = {"punt": punt, "best_player": ctx.ids[best["row"]],
                                   "expected_category_wins": best["cat_wins"], "matchup_win_prob": best["matchup"]}

        def strat_score(name: str) -> float:
            r = strat_results[name]
            return r["matchup_win_prob"] if prm.objective == "matchup" else r["expected_category_wins"] / len(w_full)

        chosen = max(strat_results, key=strat_score)
        if "balanced" in strat_results and chosen != "balanced":
            if strat_score(chosen) - strat_score("balanced") < prm.strategy_switch_margin:
                chosen = "balanced"
        rows = per_strategy_cands[chosen]
        best_obj = rows[0]["obj"]
        second_obj = rows[1]["obj"] if len(rows) > 1 else best_obj
        next_pick = later[0] if later else None

        recs = []
        for r in rows:
            c = r["row"]
            pid = ctx.ids[c]
            v = ctx.values.loc[pid]
            p_next = float(prob_available(next_pick, current, ctx.market_rank[[c]])[0]) if next_pick else 0.0
            p_dec = float(prob_available(decision_pick, current, ctx.market_rank[[c]])[0])
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

        top = recs[0]
        final_team = roster + [ctx.pos[top["player_id"]]] + [ctx.pos[p] for p in top["plan"]]
        team_eval = ctx.model.evaluate_team(final_team)
        current_eval = ctx.model.evaluate_team(roster) if roster else None
        return {
            "draft_id": draft_id,
            "as_of_pick": current - 1,
            "for_pick": decision_pick,
            "on_the_clock": {"overall": current, "slot": on_clock_slot, "is_me": my_turn},
            "picks_until_mine": decision_pick - current,
            "my_next_picks": future,
            "model_run_id": model_run_id,
            "generated_at": _now(),
            "objective": prm.objective,
            "recommendations": recs,
            "strategy": {"name": chosen, "punt": strategies[chosen],
                         "notes": _strategy_note(chosen, strat_results), "scan": strat_results},
            "team_profile": {
                "categories": {k: {"win_prob": round(p, 3), "mean": round(team_eval["cat_means"][k], 3),
                                   "league_avg": round(team_eval["opp_means"][k], 3)}
                               for k, p in team_eval["cat_win_probs"].items()},
                "punts": strategies[chosen],
                "expected_category_wins": round(team_eval["expected_category_wins"], 3),
                "matchup_win_prob": round(team_eval["matchup_win_prob"], 3),
                "basis": "projected final roster if the top recommendation and its planned picks are taken",
                "current_roster_cat_win_probs": current_eval["cat_win_probs"] if current_eval else None,
            },
            "planned_picks": [{"pick": p, "player_id": pid, "name": str(ctx.values.loc[pid, "name"])}
                              for p, pid in zip(later, top["plan"], strict=False)],
            "roster": self._roster_payload(state, my_all),
            "open_starting_slots": unfilled_slots([ctx.positions[i] for i in roster], ctx.settings.starting_slots),
            "warnings": warnings,
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
