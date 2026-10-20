"""T-016: calibrate the draft room model on the real 2026 draft (nba.l.44495-2026, 156 picks).

Results and interpretation: docs/research/draft_calibration_2026.md. Research only; changes no defaults.

  # 1) read-only dump of the draft (SELECT only; needs FANTASY_DB_URL; output is git-ignored under var/)
  python docs/research/draft_calibration_2026.py dump var/calibration/draft_2026.json
  # 2) analysis, fully offline (unset FANTASY_DB_URL), deterministic
  python docs/research/draft_calibration_2026.py analyse var/calibration/draft_2026.json --out var/calibration/results.json

Model under test (`fantasy.draft.availability`, `fantasy.draft.room_sim`, recommend-0.2.0 default `sim:mixed x300`):
latent position ~ Normal(market_rank, (2.5 + 0.18 * market_rank) * sd_mult), ADP drafters take the smallest latent;
3 of 11 opponents are "sharp" (best available by a noisy view of our values, leaning 30 % to the market).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DRAFT_ID = "nba.l.44495-2026"


def dump(out: str) -> None:
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["FANTASY_DB_URL"])
    with eng.connect() as c:
        if eng.dialect.name == "postgresql":
            c.execute(text("SET TRANSACTION READ ONLY"))
        q = {"drafts": "select * from drafts where draft_id=:d",
             "picks": "select * from draft_picks where draft_id=:d order by overall_pick",
             "recs": "select * from draft_recommendations where draft_id=:d order by created_at"}
        data = {k: [dict(r._mapping) for r in c.execute(text(v), {"d": DRAFT_ID})] for k, v in q.items()}
        c.rollback()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(data, default=str))
    print({k: len(v) for k, v in data.items()})


# ------------------------------------------------------------------------------------------------ analysis

def _load(path: str):  # noqa: ANN202
    from fantasy.draft.optimizer import DraftContext
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections

    d = json.loads(Path(path).read_text())
    dr = d["drafts"][0]
    s = load_settings().model_copy(update={"num_teams": dr["num_teams"], "my_slot": dr["my_slot"],
                                           "draft_order_type": dr["order_type"]})
    ctx = DraftContext(load_projections(), s, custom_rounds=(dr.get("config") or {}).get("custom_rounds"))
    picks = pd.DataFrame(d["picks"]).sort_values("overall_pick").reset_index(drop=True)
    picks["row"] = [ctx.pos[p] for p in picks.player_id]  # KeyError = a pick we cannot map: fix names first
    picks["mr"] = ctx.market_rank[picks.row]
    picks["vrank"] = ctx.values["rank"].to_numpy()[picks.row]
    picks["res"] = picks.overall_pick - picks.mr
    recs = []
    for r in d["recs"]:
        p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        recs.append({"as_of": int(r["as_of_pick"]), "for_pick": int(r["for_pick"]), "created_at": r["created_at"],
                     "availability": p.get("availability"), "payload": p})
    return ctx, dr, picks, recs


def _bins(mr: np.ndarray) -> np.ndarray:
    return np.digitize(mr, [24.5, 60.5, 108.5])  # 0: 1-24, 1: 25-60, 2: 61-108, 3: 109+


BIN_NAMES = ["1-24", "25-60", "61-108", "109+"]


def _robust_sd(x: np.ndarray) -> float:
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) else float("nan")


def residuals(ctx, dr, picks) -> dict:  # noqa: ANN001
    from fantasy.draft.availability import adp_sd
    from fantasy.draft.calibration import cluster_bootstrap, simulate_adp_draft

    opp = picks[picks.team_slot != dr["my_slot"]].reset_index(drop=True)
    out: dict = {"n_picks": len(picks), "n_opponent_picks": len(opp)}
    r = opp.res.to_numpy()
    out["all"] = {"mean": float(r.mean()), "sd": float(r.std(ddof=1)), "robust_sd": _robust_sd(r),
                  "median_abs": float(np.median(np.abs(r))), "share_within_12": float(np.mean(np.abs(r) <= 12)),
                  "min": float(r.min()), "max": float(r.max()),
                  "mean_se": float(r.std(ddof=1) / np.sqrt(len(r)))}
    b = _bins(opp.mr.to_numpy())
    rows = []
    for k, name in enumerate(BIN_NAMES):
        m = b == k
        x = opp.res.to_numpy()[m]
        bs_sd = cluster_bootstrap(lambda i, x=x: float(np.std(x[i], ddof=1)), np.arange(len(x)), 2000, seed=k)
        bs_rsd = cluster_bootstrap(lambda i, x=x: _robust_sd(x[i]), np.arange(len(x)), 2000, seed=10 + k)
        rows.append({"bin": name, "n": int(m.sum()), "mean": float(x.mean()), "sd": float(x.std(ddof=1)),
                     "sd_ci95": bs_sd["ci95"], "robust_sd": _robust_sd(x), "robust_sd_ci95": bs_rsd["ci95"],
                     "prior_sd_at_mean_mr": float(adp_sd(np.array([opp.mr.to_numpy()[m].mean()]))[0])})
    out["by_market_rank"] = rows
    # posterior-predictive check: the same statistics from drafts simulated under the prior (all-ADP room, rank-based,
    # my picks' positions excluded exactly as above), for several spread scales
    order_slots = [pk.slot for pk in ctx.order]
    mine = np.array([s == dr["my_slot"] for s in order_slots])
    ppc = {}
    rng = np.random.default_rng(2026)
    for scale in (0.5, 0.6, 0.75, 1.0, 1.25):
        sims = {name: [] for name in BIN_NAMES}
        sims_r = {name: [] for name in BIN_NAMES}
        for _ in range(1000):
            pk = simulate_adp_draft(ctx.market_rank, len(order_slots), rng, ctx.sd_mult, scale)
            dm = ~np.isnan(pk)
            dm[dm] = ~mine[pk[dm].astype(int) - 1]
            res = pk[dm] - ctx.market_rank[dm]
            bb = _bins(ctx.market_rank[dm])
            for k, name in enumerate(BIN_NAMES):
                sims[name].append(np.std(res[bb == k], ddof=1))
                sims_r[name].append(_robust_sd(res[bb == k]))
        ppc[str(scale)] = {
            name: {"sim_sd_median": float(np.median(sims[name])),
                   "sim_sd_90": [float(np.percentile(sims[name], 5)), float(np.percentile(sims[name], 95))],
                   "obs_sd_percentile": float(np.mean(np.array(sims[name]) < rows[k]["sd"])),
                   "sim_robust_sd_median": float(np.median(sims_r[name])),
                   "obs_robust_sd_percentile": float(np.mean(np.array(sims_r[name]) < rows[k]["robust_sd"]))}
            for k, name in enumerate(BIN_NAMES)}
    out["ppc_all_adp_room"] = ppc
    return out


def tobit(ctx, dr, picks) -> dict:  # noqa: ANN001
    from fantasy.draft.calibration import simulate_adp_draft, tobit_sd_fit

    n = len(picks)
    mine_rows = set(picks.row[picks.team_slot == dr["my_slot"]])
    keep = np.array([i not in mine_rows for i in range(len(ctx.ids))]) & (ctx.market_rank <= 320)
    pk = np.full(len(ctx.ids), np.nan)
    pk[picks.row.to_numpy()] = picks.overall_pick.to_numpy()
    out = {"real": tobit_sd_fit(ctx.market_rank[keep], pk[keep], n, ctx.sd_mult[keep])}
    # without the 3 largest |residual| picks (heavy-tail sensitivity)
    big = picks.loc[picks.team_slot != dr["my_slot"]].assign(a=lambda x: x.res.abs()).nlargest(3, "a")
    keep2 = keep.copy()
    keep2[big.row.to_numpy()] = False
    out["real_minus_3_outliers"] = tobit_sd_fit(ctx.market_rank[keep2], pk[keep2], n, ctx.sd_mult[keep2])
    out["outliers_dropped"] = big[["overall_pick", "player_name", "mr", "res"]].to_dict("records")
    # the estimator under the prior (rank-based all-ADP room): does it recover floor 2.5 / slope 0.18?
    rng = np.random.default_rng(7)
    est = []
    for _ in range(200):
        sp = simulate_adp_draft(ctx.market_rank, n, rng, ctx.sd_mult)
        f = tobit_sd_fit(ctx.market_rank[keep], sp[keep], n, ctx.sd_mult[keep])
        est.append([f["bias"], f["floor"], f["slope"]])
    est = np.array(est)
    out["under_prior"] = {k: {"median": float(np.median(est[:, i])), "p05": float(np.percentile(est[:, i], 5)),
                              "p95": float(np.percentile(est[:, i], 95)),
                              "obs_percentile": float(np.mean(est[:, i] < out["real"][k]))}
                          for i, k in enumerate(["bias", "floor", "slope"])}
    return out


FLOORS = (0.5, 1.0, 1.5, 2.0, 2.5)
SLOPES = (0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.18)
PRIOR = (2.5, 0.18)


def _key(a: float, b: float) -> str:
    return f"p_adp_{a:g}_{b:g}"


def one_step(ctx, dr, picks) -> dict:  # noqa: ANN001
    """Per opponent pick: likelihood of the actual pick under an ADP drafter with sd = (floor + slope * market_rank)
    * sd_mult on a (floor, slope) grid, and under a sharp drafter (room_sim's sharp model); mixture weight of sharp
    drafters and per-manager Bayes factors."""
    from fantasy.draft.calibration import p_first_max, p_first_min
    from fantasy.draft.room_sim import SHARP_MARKET_WEIGHT, sharp_sd

    n_all = len(ctx.ids)
    value = ctx.value
    mval = np.sort(value)[::-1][np.clip(ctx.market_rank.astype(int) - 1, 0, n_all - 1)]
    centre = (1 - SHARP_MARKET_WEIGHT) * value + SHARP_MARKET_WEIGHT * mval
    combos = [(a, b) for a in FLOORS for b in SLOPES]
    taken = np.zeros(n_all, dtype=bool)
    rows = []
    for _, p in picks.iterrows():
        k, row = int(p.overall_pick), int(p.row)
        if p.team_slot != dr["my_slot"]:
            av = np.flatnonzero(~taken & (ctx.market_rank <= 400))
            if row not in av:
                av = np.append(av, row)
            j = int(np.flatnonzero(av == row)[0])
            mu, sdm = ctx.market_rank[av], ctx.sd_mult[av]
            p_adp = {_key(a, b): float(p_first_min(mu, (a + b * mu) * sdm, k - 0.5, n_grid=4000)[j]) for a, b in combos}
            ps = p_first_max(centre[av], sharp_sd(value[av]))
            # ranks among available: by market (1 = next in the market order) and by our value
            rows.append({"pick": k, "slot": int(p.team_slot), "player": p.player_name,
                         "market_rank_among_avail": int(np.sum(ctx.market_rank[av] < ctx.market_rank[row]) + 1),
                         "value_rank_among_avail": int(np.sum(value[av] > value[row]) + 1),
                         "p_sharp": float(ps[j]), **p_adp})
        taken[row] = True
    df = pd.DataFrame(rows)
    floor = 1e-6
    lg = {c: np.log(np.maximum(df[_key(*c)].to_numpy(), floor)) for c in combos}
    ll_adp = {f"{a:g}_{b:g}": float(lg[(a, b)].sum()) for a, b in combos}
    ll_sharp = float(np.log(np.maximum(df.p_sharp, floor)).sum())
    (a_hat, b_hat) = max(combos, key=lambda c: lg[c].sum())
    ll_hat = float(lg[(a_hat, b_hat)].sum())
    ll_prior = float(lg[PRIOR].sum())
    region = [f"{a:g}_{b:g}" for a, b in combos if 2 * (ll_hat - lg[(a, b)].sum()) <= 5.99]  # joint 95 % (2 df)
    prof_b = {f"{b:g}": max(float(lg[(a, b)].sum()) for a in FLOORS) for b in SLOPES}
    ci_b = [float(b) for b in prof_b if 2 * (ll_hat - prof_b[b]) <= 3.84]
    ws = np.round(np.linspace(0, 1, 21), 2)
    best = df[_key(a_hat, b_hat)].to_numpy()
    prof_w = {float(w): float(np.log(np.maximum(w * df.p_sharp + (1 - w) * best, floor)).sum()) for w in ws}
    w_hat = max(prof_w, key=prof_w.get)
    ci_w = [w for w in prof_w if 2 * (prof_w[w_hat] - prof_w[w]) <= 3.84]
    prior_mixed = float(np.log(np.maximum(3 / 11 * df.p_sharp + 8 / 11 * df[_key(*PRIOR)], floor)).sum())
    # per-pick log-likelihood gain of the fitted spread over the prior, with an SE over picks (picks ~ independent
    # given the board), so the LR is not read as more certain than 143 picks allow
    gain = lg[(a_hat, b_hat)] - lg[PRIOR]
    out = {"n": len(df), "loglik_adp_grid": ll_adp, "loglik_sharp_only": ll_sharp, "loglik_prior_adp": ll_prior,
           "loglik_prior_mixed_w0.27": prior_mixed,
           "mle": {"floor": a_hat, "slope": b_hat, "loglik": ll_hat},
           "lr_vs_prior": 2 * (ll_hat - ll_prior),
           "per_pick_gain_vs_prior": {"mean": float(gain.mean()), "se": float(gain.std(ddof=1) / np.sqrt(len(gain))),
                                      "share_picks_better": float(np.mean(gain > 0))},
           "joint_95_region": region, "profile_slope": prof_b, "ci95_slope_grid": [min(ci_b), max(ci_b)],
           "profile_w_at_mle_spread": prof_w, "w_hat": float(w_hat), "ci95_w_grid": [min(ci_w), max(ci_w)],
           "share_next_in_market_order": float(np.mean(df.market_rank_among_avail == 1)),
           "share_top3_market": float(np.mean(df.market_rank_among_avail <= 3)),
           "share_top3_value": float(np.mean(df.value_rank_among_avail <= 3)),
           "median_market_rank_among_avail": float(df.market_rank_among_avail.median()),
           "median_value_rank_among_avail": float(df.value_rank_among_avail.median()),
           "n_p_prior_below_1pct": int(np.sum(df[_key(*PRIOR)] < 0.01)),
           "worst_prior_picks": df.nsmallest(6, _key(*PRIOR))[["pick", "slot", "player", _key(*PRIOR), _key(a_hat, b_hat),
                                                             "p_sharp", "market_rank_among_avail",
                                                             "value_rank_among_avail"]].to_dict("records")}
    # per manager: log Bayes factor sharp vs ADP drafter (fitted and prior spread), posterior with the 3/11 prior
    per = []
    for slot, g in df.groupby("slot"):
        ls = np.log(np.maximum(g.p_sharp, floor)).sum()
        lbf = float(ls - np.log(np.maximum(g[_key(a_hat, b_hat)], floor)).sum())
        lbf1 = float(ls - np.log(np.maximum(g[_key(*PRIOR)], floor)).sum())
        post = 1 / (1 + np.exp(-(lbf1 + np.log(3 / 8))))
        per.append({"slot": int(slot), "n": len(g), "mean_res": float(picks[picks.team_slot == slot].res.mean()),
                    "median_market_rank_among_avail": float(g.market_rank_among_avail.median()),
                    "median_value_rank_among_avail": float(g.value_rank_among_avail.median()),
                    "log_bf_sharp_vs_adp_fitted": lbf, "log_bf_sharp_vs_adp_prior": lbf1,
                    "p_sharp_post_prior_spread": float(post)})
    out["per_manager"] = per
    out["picks"] = df.to_dict("records")
    return out


def stored_predictions(ctx, dr, picks, recs, fit: tuple[float, float] | None = None) -> dict:  # noqa: ANN001
    """Calibration of the availability numbers the live model showed (draft_recommendations), plus a replay of the
    same model for every player on the board at the same decision points, against the old analytic model."""
    from fantasy.draft.availability import adp_sd, prob_available
    from fantasy.draft.calibration import binary_scores, cluster_bootstrap, reliability_table
    from fantasy.draft.room_sim import simulate_availability

    pick_at = dict(zip(picks.row, picks.overall_pick, strict=True))
    slot_at = dict(zip(picks.row, picks.team_slot, strict=True))
    order_slots = [pk.slot for pk in ctx.order]
    my_picks = [i + 1 for i, s in enumerate(order_slots) if s == dr["my_slot"]]

    def next_mine(p: int) -> int | None:
        later = [q for q in my_picks if q > p]
        return later[0] if later else None

    def gone_between(row: int, a: int, b: int) -> bool:
        """Taken by an OPPONENT at an overall pick in [a, b)."""
        k = pick_at.get(row)
        return k is not None and a <= k < b and slot_at[row] != dr["my_slot"]

    # latest stored rec per (as_of, for_pick) that used the live default room
    latest: dict = {}
    for r in recs:
        if r["availability"] and str(r["availability"]).startswith("sim:mixed"):
            latest[(r["as_of"], r["for_pick"])] = r
    pairs = []  # (cluster, kind, p, y, row, from, to)
    for (as_of, fp), r in sorted(latest.items()):
        current = as_of + 1
        taken_now = pick_at.get
        for c in r["payload"]["recommendations"]:
            row = ctx.pos[c["player_id"]]
            if current == fp:  # on the clock: "if I pass now, is he there at my next pick?"
                nxt = next_mine(fp)
                if nxt is None or nxt == fp + 1 or taken_now(row) == fp:
                    continue
                pairs.append((fp, "next", c["prob_available_next"], 0.0 if gone_between(row, fp + 1, nxt) else 1.0,
                              row, fp, nxt))
            else:  # prepared early: available at my decision pick?
                pairs.append((fp, "at_pick", c["prob_available_at_pick"],
                              0.0 if gone_between(row, current, fp) else 1.0, row, current, fp))
    st = pd.DataFrame(pairs, columns=["cluster", "kind", "p", "y", "row", "a", "b"])
    st["p_analytic"] = [float(prob_available(b, a, ctx.market_rank[[row]], ctx.sd_mult[[row]])[0])
                        for row, a, b in zip(st.row, st.a, st.b, strict=True)]
    out: dict = {"stored": {}}
    for kind, g in st.groupby("kind"):
        out["stored"][kind] = {"model": binary_scores(g.p, g.y), "analytic_adp": binary_scores(g.p_analytic, g.y),
                               "n_decisions": int(g.cluster.nunique()),
                               "brier_diff_model_minus_analytic": cluster_bootstrap(
                                   lambda i, g=g: float(np.mean((g.p.to_numpy()[i] - g.y.to_numpy()[i]) ** 2
                                                                - (g.p_analytic.to_numpy()[i] - g.y.to_numpy()[i]) ** 2)),
                                   g.cluster.to_numpy(), 4000, seed=5),
                               "reliability": reliability_table(g.p.to_numpy(), g.y.to_numpy())}
    # replay: every board player at the on-the-clock decisions with a non-adjacent next pick
    rep = []
    taken = np.zeros(len(ctx.ids), dtype=bool)
    order_by_pick = picks.sort_values("overall_pick")
    for fp in my_picks:
        nxt = next_mine(fp)
        if nxt is None or nxt == fp + 1:
            continue
        taken[:] = False
        taken[order_by_pick.row[order_by_pick.overall_pick < fp].to_numpy()] = True
        avail = ~taken
        tabs = {room: simulate_availability(ctx.market_rank, ctx.sd_mult, ctx.value, avail, order_slots, fp, [nxt],
                                            dr["my_slot"], room=room, n_sims=300, skip_my_current=True)
                for room in ("mixed", "adp", "sym")}
        if fit is not None:  # ADP room with the spread fitted on this same draft (in-sample)
            mult_fit = ctx.sd_mult * (fit[0] + fit[1] * ctx.market_rank) / adp_sd(ctx.market_rank)
            tabs["adp_fit"] = simulate_availability(ctx.market_rank, mult_fit, ctx.value, avail, order_slots, fp, [nxt],
                                                    dr["my_slot"], room="adp", n_sims=3000, seed=2,
                                                    skip_my_current=True)
        tabs2k = simulate_availability(ctx.market_rank, ctx.sd_mult, ctx.value, avail, order_slots, fp, [nxt],
                                       dr["my_slot"], room="mixed", n_sims=3000, seed=1, skip_my_current=True)
        for row in np.flatnonzero(avail & (ctx.market_rank <= 260)):
            if pick_at.get(row) == fp:
                continue
            rep.append({"cluster": fp, "row": int(row), "mr": float(ctx.market_rank[row]),
                        "p_mixed": float(tabs["mixed"][nxt][row]), "p_adp_room": float(tabs["adp"][nxt][row]),
                        "p_sym": float(tabs["sym"][nxt][row]), "p_mixed_3k": float(tabs2k[nxt][row]),
                        "p_adp_fit": float(tabs["adp_fit"][nxt][row]) if "adp_fit" in tabs else float("nan"),
                        "p_analytic": float(prob_available(nxt, fp, ctx.market_rank[[row]], ctx.sd_mult[[row]])[0]),
                        "y": 0.0 if gone_between(row, fp + 1, nxt) else 1.0})
    rp = pd.DataFrame(rep)
    contested = rp[(rp.p_mixed_3k > 0.02) & (rp.p_mixed_3k < 0.98)]
    out["replay"] = {"n_all": len(rp), "n_contested": len(contested), "decisions": sorted(rp.cluster.unique().tolist())}
    for name in ("p_mixed", "p_mixed_3k", "p_adp_room", "p_sym", "p_analytic", "p_adp_fit"):
        out["replay"][name] = binary_scores(contested[name], contested.y)
    cl = contested.cluster.to_numpy()

    def diff(a: str, b: str):  # noqa: ANN202
        da = (contested[a].to_numpy() - contested.y.to_numpy()) ** 2 - (contested[b].to_numpy() - contested.y.to_numpy()) ** 2
        return cluster_bootstrap(lambda i: float(np.mean(da[i])), cl, 4000, seed=3)

    out["replay"]["brier_diff_mixed_minus_analytic"] = diff("p_mixed_3k", "p_analytic")
    out["replay"]["brier_diff_mixed_minus_adp_room"] = diff("p_mixed_3k", "p_adp_room")
    out["replay"]["brier_diff_mixed_minus_sym"] = diff("p_mixed_3k", "p_sym")
    if fit is not None:
        out["replay"]["brier_diff_adp_fit_minus_mixed"] = diff("p_adp_fit", "p_mixed_3k")
        out["replay"]["reliability_adp_fit"] = reliability_table(contested.p_adp_fit.to_numpy(), contested.y.to_numpy())
    out["replay"]["reliability_mixed_3k"] = reliability_table(contested.p_mixed_3k.to_numpy(), contested.y.to_numpy())
    out["replay"]["reliability_analytic"] = reliability_table(contested.p_analytic.to_numpy(), contested.y.to_numpy())
    # reproduce check: replayed mixed (300 sims, seed 0) vs the stored numbers for the same player/decision
    chk = st[st.kind == "next"].merge(rp, left_on=["cluster", "row"], right_on=["cluster", "row"], how="inner")
    out["replay"]["stored_vs_replay_max_abs_diff"] = float((chk.p - chk.p_mixed).abs().max()) if len(chk) else None
    out["replay"]["stored_vs_replay_n"] = len(chk)
    return out


def manager_tendencies(dr, picks, fit: tuple[float, float]) -> dict:  # noqa: ANN001
    """Reach/wait tendencies per manager: residuals standardised by the fitted spread, Kruskal-Wallis test of
    'all managers draw from the same residual distribution' (12-13 picks each, so only large differences show)."""
    from scipy.stats import kruskal

    opp = picks[picks.team_slot != dr["my_slot"]].copy()
    opp["z"] = opp.res / (fit[0] + fit[1] * opp.mr)
    rows = []
    for slot, g in opp.groupby("team_slot"):
        z = g.z.to_numpy()
        rows.append({"slot": int(slot), "n": len(g), "mean_res": float(g.res.mean()), "median_res": float(g.res.median()),
                     "median_z": float(np.median(z)), "mean_z": float(z.mean()), "mean_z_se": float(z.std(ddof=1) / np.sqrt(len(z))),
                     "n_reach_z_lt_-1": int(np.sum(z < -1)), "n_wait_z_gt_1": int(np.sum(z > 1))})
    h, pv = kruskal(*[g.z.to_numpy() for _, g in opp.groupby("team_slot")])
    return {"per_manager": rows, "kruskal_h": float(h), "kruskal_p": float(pv)}


def analyse(path: str, out_path: str | None) -> dict:
    ctx, dr, picks, recs = _load(path)
    res = {"draft_id": dr["draft_id"], "residuals": residuals(ctx, dr, picks), "tobit": tobit(ctx, dr, picks),
           "one_step": one_step(ctx, dr, picks)}
    fit = (res["one_step"]["mle"]["floor"], res["one_step"]["mle"]["slope"])
    res["availability"] = stored_predictions(ctx, dr, picks, recs, fit)
    res["managers"] = manager_tendencies(dr, picks, fit)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(res, indent=1, default=float))
    return res


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("out")
    a = sub.add_parser("analyse")
    a.add_argument("dump")
    a.add_argument("--out")
    args = ap.parse_args(argv)
    if args.cmd == "dump":
        dump(args.out)
    else:
        res = analyse(args.dump, args.out)
        slim = {k: v for k, v in res.items()}
        slim["one_step"] = {k: v for k, v in res["one_step"].items() if k != "picks"}
        print(json.dumps(slim, indent=1, default=float))


if __name__ == "__main__":
    main(sys.argv[1:])
