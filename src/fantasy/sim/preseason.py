"""Pre-draft season projection: simulate K full drafts (me = optimizer from my slot, the other teams =
noisy ADP, exactly as `fantasy draft simulate`), play each resulting league's season with the season
simulator (n_sims split across the K leagues) and pool the results by draft slot.

Summary schema `season_sim_preseason/1` (docs/research/dashboard_data.md).
"""

from __future__ import annotations

import time

import numpy as np

from fantasy.draft.optimizer import DraftContext
from fantasy.draft.simulate import simulate_drafts
from fantasy.sim.context import SimContext
from fantasy.sim.engine import SimParams
from fantasy.sim.league import LeagueState
from fantasy.sim.report import team_profiles

SUMMARY_SCHEMA = "season_sim_preseason/1"
SCENARIO = "pre-draft projection"

EXPLANATIONS = {
    "overview": (
        "A projection of my season before a single pick is made. We simulate the full draft several times "
        "(my picks by the optimizer from slot {slot}, the other 11 teams by ADP with noise), then play out "
        "each resulting league's whole season many times and pool the results."),
    "what_is_simulated": (
        "Each season simulation draws every player's games per fantasy week (NBA schedule, random "
        "injury/rest spells), caps the games that count by the daily lineup limit (positions ignored), adds "
        "streamed replacement-level games, and draws each team's weekly category totals from a normal "
        "approximation. The 9 head-to-head categories are scored every week against the scheduled opponent, "
        "standings are built over {n_reg} regular-season weeks x 9 categories, then the top {n_po} play "
        "the playoffs in weeks {po_weeks} ({byes}{reseed})."),
    "opponents": (
        "The other rosters come from the simulated drafts: they pick by consensus ADP with noise. In the "
        "season itself every team, mine included, is treated the same: a fixed budget of streamed "
        "replacement-level games per week and no trades or waiver gems. Team N = draft slot N (pooled over "
        "different simulated rosters); real team names arrive after the draft."),
    "priors": (
        "Projections are our 2026-27 per-game projections; game-to-game variability, injury spell lengths "
        "and streaming effectiveness are uncalibrated priors. Week calendar: {calendar}. Matchups: {matchups}."),
    "league_table": (
        "Pooled over all simulated leagues and seasons, by draft slot: chance of making the playoffs, a "
        "top-2 seed (bye), reaching the final, winning the title, expected regular-season category wins "
        "and expected final rank (1 = best)."),
    "caveats": (
        "Optimistic for my team: my roster is drafted by an optimizer that uses the same projections the "
        "season is simulated with (in-sample), while opponents draft passively by ADP; real leaguemates "
        "draft and manage better. Treat the absolute P(playoffs)/P(title) as an upper-end scenario and use "
        "the numbers relatively. SEs cover simulation noise only (including which rosters the simulated "
        "drafts produced: clustered by league), not model error - title odds plausibly move several points "
        "across reasonable priors. This is a pre-draft projection; rerun on the real rosters after the draft."),
}


def _cluster_se(x: np.ndarray, k: int) -> float:
    """SE of the pooled mean of `x` over k equal-size consecutive clusters (simulated leagues):
    sd(league means) / sqrt(k). Seasons within a league share rosters, so a plain binomial SE over all
    N seasons would understate the noise; the variance of league means already includes the within-league
    noise. With k = 1 this falls back to the iid SE."""
    if k <= 1:
        return float(x.std(ddof=1) / np.sqrt(x.size)) if x.size > 1 else 0.0
    means = x.reshape(k, -1).mean(axis=1)
    return float(means.std(ddof=1) / np.sqrt(k))


def preseason(
    sctx: SimContext, dctx: DraftContext, n_leagues: int = 8, params: SimParams | None = None,
    draft_seed: int = 0, noise: float = 1.0, strategy: str | None = None,
) -> dict:
    t0 = time.perf_counter()
    params = params or SimParams()
    my = dctx.settings.my_slot
    T = dctx.settings.num_teams
    team_ids = list(range(1, T + 1))
    drafts = simulate_drafts(dctx, n_sims=n_leagues, seed=draft_seed, noise=noise, strategy=strategy,
                             keep_leagues=True)
    per = max(1, params.n_sims // n_leagues)
    if per * n_leagues != params.n_sims:
        warnings_split = [f"{params.n_sims} season sims split as {per} per league x {n_leagues} leagues = "
                          f"{per * n_leagues}."]
    else:
        warnings_split = []
    names = sctx.values["name"]
    pooled: dict[str, list[np.ndarray]] = {k: [] for k in ("playoff", "champ", "final", "rank", "cw", "cl")}
    leagues_out, profiles = [], []
    warnings: list[str] = list(warnings_split)
    if n_leagues < 30:
        warnings.append(f"draft_edge_vs_bpa comes from only {n_leagues} simulated drafts (noisy); see the draft_sim run.")
    if n_leagues < 10:
        warnings.append(f"Only {n_leagues} simulated leagues: the SEs (clustered by league) are themselves rough.")
    n_po = None
    reseed = None
    for k, sim in enumerate(drafts["sims"]):
        league = LeagueState(rosters={t: list(sim["league"][t]) for t in team_ids}, my_team=my,
                             source=f"simulated draft {k} (seed {draft_seed}, noise {noise})",
                             names={t: (f"Me (slot {t})" if t == my else f"Team {t}") for t in team_ids})
        res = sctx.simulator(SimParams(**{**params.to_dict(), "n_sims": per, "seed": params.seed + k})).run(league)
        n_po = int(res.params["playoff_teams"])
        reseed = bool(res.params.get("reseed"))
        idx = [res.tindex(t) for t in team_ids]
        champ = np.zeros((res.n, T))
        champ[np.arange(res.n), res.champion] = 1
        final = champ.copy()
        ok = res.runner_up >= 0
        final[np.flatnonzero(ok), res.runner_up[ok]] = 1
        pooled["playoff"].append(res.playoff[:, idx].astype(float))
        pooled["champ"].append(champ[:, idx])
        pooled["final"].append(final[:, idx])
        pooled["rank"].append(res.rank[:, idx].astype(float))
        pooled["cw"].append(res.cat_wins[:, idx])
        pooled["cl"].append(res.cat_losses[:, idx])
        profiles.append(team_profiles(res, league)[str(my)])
        i = res.tindex(my)
        leagues_out.append({
            "league": k, "draft_seed": draft_seed, "season_seed": params.seed + k, "n_sims": res.n,
            "my_p_playoffs": round(float(res.playoff[:, i].mean()), 4),
            "my_p_title": round(float((res.champion == i).mean()), 4),
            "my_exp_rank": round(float(res.rank[:, i].mean()), 2),
            "my_exp_cat_wins": round(float(res.cat_wins[:, i].mean()), 2),
            "my_draft_expected_category_wins": round(sim["expected_category_wins"], 3),
            "my_strategy": sim["strategy"],
            "my_roster": [{"player_id": p, "name": str(names.get(p, p))} for p in sim["league"][my]],
        })
        warnings += res.warnings
    P = {k: np.concatenate(v) for k, v in pooled.items()}
    N = P["playoff"].shape[0]
    rounds = int(np.ceil(np.log2(max(n_po or 2, 2))))
    byes = 2**rounds - (n_po or 0)
    table = []
    for j, t in enumerate(team_ids):
        table.append({
            "team": t, "name": f"Me (slot {t})" if t == my else f"Team {t}", "is_me": t == my,
            "p_playoffs": round(float(P["playoff"][:, j].mean()), 4),
            "p_bye": round(float((P["rank"][:, j] <= byes).mean()), 4) if byes else 0.0,
            "p_final": round(float(P["final"][:, j].mean()), 4),
            "p_title": round(float(P["champ"][:, j].mean()), 4),
            "exp_cat_wins": round(float(P["cw"][:, j].mean()), 2),
            "exp_rank": round(float(P["rank"][:, j].mean()), 2),
        })
    table.sort(key=lambda r: (-r["p_title"], -r["p_playoffs"]))
    j = team_ids.index(my)
    p_po, p_ti = float(P["playoff"][:, j].mean()), float(P["champ"][:, j].mean())
    ranks = P["rank"][:, j].astype(int)
    cats = list(profiles[0]["rank"])
    labels = {c.key: c.label for c in dctx.model.cats}
    my_outlook = {
        "team": my,
        "p_playoffs": round(p_po, 4), "p_playoffs_se": round(_cluster_se(P["playoff"][:, j], n_leagues), 4),
        "p_bye": next(r["p_bye"] for r in table if r["is_me"]),
        "p_final": round(float(P["final"][:, j].mean()), 4),
        "p_title": round(p_ti, 4), "p_title_se": round(_cluster_se(P["champ"][:, j], n_leagues), 4),
        "exp_rank": round(float(ranks.mean()), 2), "exp_rank_se": round(_cluster_se(P["rank"][:, j], n_leagues), 3),
        "exp_cat_wins": round(float(P["cw"][:, j].mean()), 2), "exp_cat_losses": round(float(P["cl"][:, j].mean()), 2),
        "final_standing_dist": {str(k): round(float(v), 4)
                                for k, v in enumerate(np.bincount(ranks, minlength=T + 1)[1:] / N, start=1)},
        "across_leagues": {
            "p_playoffs_min": min(x["my_p_playoffs"] for x in leagues_out),
            "p_playoffs_max": max(x["my_p_playoffs"] for x in leagues_out),
            "p_title_min": min(x["my_p_title"] for x in leagues_out),
            "p_title_max": max(x["my_p_title"] for x in leagues_out),
        },
        "category_profile": {c: {"label": labels.get(c, c),
                                 "rank_mean": round(float(np.mean([p["rank"][c] for p in profiles])), 2),
                                 "z_mean": round(float(np.mean([p["z"][c] for p in profiles])), 2)} for c in cats},
    }
    expl = dict(EXPLANATIONS)
    expl["overview"] = expl["overview"].format(slot=my)
    po_weeks = list(sctx.settings.playoff_weeks)
    n_reg = len([w for w in sctx.calendar if w.week < min(po_weeks)])
    expl["what_is_simulated"] = expl["what_is_simulated"].format(
        n_reg=n_reg, n_po=n_po, po_weeks=f"{po_weeks[0]}-{po_weeks[-1]}",
        byes=f"top {byes} seeds get a bye" if byes else "no byes",
        reseed=", re-seeded each round" if reseed else ", fixed bracket")
    expl["priors"] = expl["priors"].format(
        calendar=f"week 1 = {sctx.calendar[0].start}..{sctx.calendar[0].end}, last week ends {sctx.calendar[-1].end}",
        matchups=sctx.matchups.source)
    return {
        "schema": SUMMARY_SCHEMA, "scenario": SCENARIO,
        "n_sims": N, "n_leagues": n_leagues, "sims_per_league": per, "my_team": my, "num_teams": T,
        "draft_seed": draft_seed, "season_seed": params.seed, "noise": noise, "strategy_forced": strategy,
        "regular_weeks": [w.week for w in sctx.calendar if w.week < min(sctx.settings.playoff_weeks)],
        "playoff_weeks": list(sctx.settings.playoff_weeks), "playoff_teams": n_po,
        "my_outlook": my_outlook, "league_table": table, "leagues": leagues_out,
        "draft_edge_vs_bpa": drafts["summary"]["edge"],
        "explanations": expl,
        "warnings": list(dict.fromkeys(sctx.warnings + warnings + [
            "Pre-draft projection on simulated rosters (not real teams); optimistic for my team."])),
        "compute_seconds": round(time.perf_counter() - t0, 1),
    }
