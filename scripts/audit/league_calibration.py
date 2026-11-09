"""League standings + simulator calibration (generalises bias_audit_2026-11-09/{sim_weekstart,analyze,extra}.py).

1. `fantasy review week --week W --team Y --json --no-ledger` for every team-week (read-only): Yahoo-final totals where
   a matchup capture exists (final.basis yahoo*), else ESPN box scores x the captured active lineup.
2. Week-start sims: the season simulator re-run on the rosters + projection file at each week's start commit
   (streaming on and off). The week-1 sim doubles as the post-draft baseline (its full-season table).
3. Calibration of matchup and category P(win) (Brier, favourite hit rate, logistic slope, cluster-bootstrap SEs,
   reliability bins), weekly totals z vs the sim's mean/SD, per-team actual vs expected category wins.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from common import (
    CATS,
    RAW,
    REPO,
    AuditContext,
    calibration,
    finding,
    fmt_pm,
    git_exists,
    git_text,
    md_table,
)
from scipy.stats import chi2

SIGN = {c: (-1.0 if c == "tov" else 1.0) for c in CATS}


# ----------------------------------------------------------------------------------------------- inputs
def fetch_reviews(ctx: AuditContext) -> dict[tuple[int, int], dict]:
    """(week, yahoo team id) -> review dict; raw JSON kept in out/reviews/w{W}_t{Y}.json."""
    d = ctx.out / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    fantasy = str(Path(sys.executable).with_name("fantasy"))
    jobs = [(w.week, y) for w in ctx.weeks for y in sorted(ctx.slot2yahoo.values())]

    def one(job: tuple[int, int]) -> tuple[tuple[int, int], dict | None]:
        w, y = job
        f = d / f"w{w}_t{y}.json"
        # always refresh (Yahoo-final captures may have arrived since the last run)
        r = subprocess.run([fantasy, "review", "week", "--week", str(w), "--team", str(y), "--json", "--no-ledger"],
                           capture_output=True, text=True, cwd=REPO, env=os.environ.copy())
        if r.returncode != 0:
            ctx.warnings.append(f"review week {w} team {y} failed: {r.stderr.strip()[-200:]}")
            return job, None
        f.write_text(r.stdout)
        return job, json.loads(f.read_text())["review"]

    with ThreadPoolExecutor(max_workers=6) as ex:
        return {k: v for k, v in ex.map(one, jobs) if v is not None}


def _sim(league_file: Path, proj_file: Path, sims: int, streaming: bool) -> Any:
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.engine import SimParams
    from fantasy.sim.league import from_json
    from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH

    league = from_json(league_file)
    settings = load_settings().model_copy(update={"num_teams": len(league.rosters)})
    sctx = build_context(load_projections(proj_file), settings, league.team_ids,
                         schedule_file=LEAGUE_SCHEDULE_PATH if LEAGUE_SCHEDULE_PATH.exists() else None,
                         nba_schedule_file=None, week1_end=None, merge_allstar=True, team_names=league.names)
    params = SimParams(n_sims=sims, seed=0, streaming=streaming, il_replacement=True, il_min_spell_days=7.0,
                       roster_cap=True)
    return league, sctx.simulator(params).run(league)


def _cats_won(res: Any, a: int, b: int, wi: int) -> np.ndarray:
    x, y = res.totals[:, wi, res.tindex(a)], res.totals[:, wi, res.tindex(b)]
    d = (x - y) * np.array([SIGN[c] for c in res.cats])
    return (d > 0).sum(-1) + 0.5 * (d == 0).sum(-1)


def week_preds(res: Any, week: int, matchups: list[tuple[int, int]]) -> dict:
    """Per-team weekly mean/SD per category + games; per-matchup P(win), category P(win), cats-won mean/var."""
    wi = res.week_index(week)
    teams = {}
    for t in res.team_ids:
        x = res.totals[:, wi, res.tindex(t)]
        teams[t] = {c: {"mean": float(x[:, k].mean()), "sd": float(x[:, k].std(ddof=1))} for k, c in enumerate(res.cats)}
        g = res.games[:, wi, res.tindex(t)]
        teams[t]["games"], teams[t]["games_sd"] = float(g.mean()), float(g.std(ddof=1))
    mus = []
    for a, b in matchups:
        x, y = res.totals[:, wi, res.tindex(a)], res.totals[:, wi, res.tindex(b)]
        d = (x - y) * np.array([SIGN[c] for c in res.cats])
        nw, nl = (d > 0).sum(-1), (d < 0).sum(-1)
        s = nw + 0.5 * (len(res.cats) - nw - nl)
        mus.append({"a": a, "b": b, "p_win": float((nw > nl).mean()), "p_tie": float((nw == nl).mean()),
                    "exp_cats": float(s.mean()), "var_cats": float(s.var(ddof=1)),
                    "cat_win": {c: float((d[:, k] > 0).mean()) for k, c in enumerate(res.cats)},
                    "cat_tie": {c: float((d[:, k] == 0).mean()) for k, c in enumerate(res.cats)}})
    return {"teams": teams, "matchups": mus}


def season_table(res: Any, league: Any) -> dict:
    from fantasy.sim.report import league_table

    return {int(r["team"]): r for r in league_table(res, league)}


def run_sims(ctx: AuditContext) -> dict | None:
    """Week-start sims (cached in out/preds.json keyed by shas + sims)."""
    cache = ctx.out / "preds.json"
    from fantasy.sim import SIM_VERSION

    key = {"sims": ctx.sims, "weeks": {w.week: w.sha for w in ctx.weeks}, "asof": ctx.asof_sha, "sim": SIM_VERSION}
    if cache.exists():
        c = json.loads(cache.read_text())
        if c.get("key") == json.loads(json.dumps(key)):
            return c
    inp = ctx.out / "inputs"
    inp.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"key": key, "stream": {}, "nostream": {}, "postdraft": {}, "asof": {}}
    for w in ctx.weeks:
        if not (w.sha and w.proj_path and git_exists(w.sha, ctx.rosters_path)):
            ctx.warnings.append(f"week {w.week}: no rosters/projections at week start; skipped in calibration")
            continue
        lf, pf = inp / f"rosters_w{w.week}.json", inp / f"proj_w{w.week}.csv"
        lf.write_text(git_text(w.sha, ctx.rosters_path))
        pf.write_text(git_text(w.sha, w.proj_path))
        for mode in ("stream", "nostream"):
            league, res = _sim(lf, pf, ctx.sims, mode == "stream")
            out[mode][str(w.week)] = {"sha": w.sha, "proj": w.proj_path, **week_preds(res, w.week, w.matchups),
                                      "warnings": res.warnings[:5]}
            if mode == "stream" and w is ctx.weeks[0]:
                out["postdraft"] = {"sha": w.sha, "table": season_table(res, league),
                                    "weeks": {str(v.week): week_preds(res, v.week, v.matchups)["matchups"]
                                              for v in ctx.weeks}}
            print(f"  sim week {w.week} {mode} @ {w.sha}: {res.seconds:.0f}s", file=sys.stderr)
    if git_exists(ctx.asof_sha, ctx.rosters_path):
        lf, pf = inp / "rosters_asof.json", inp / "proj_asof.csv"
        lf.write_text(git_text(ctx.asof_sha, ctx.rosters_path))
        pf.write_text(git_text(ctx.asof_sha, ctx.ins_path if git_exists(ctx.asof_sha, ctx.ins_path) else ctx.proj_path))
        league, res = _sim(lf, pf, ctx.sims, True)
        out["asof"] = {"sha": ctx.asof_sha, "table": season_table(res, league)}
    cache.write_text(json.dumps(out, default=float))
    return json.loads(cache.read_text())


# ----------------------------------------------------------------------------------------------- scoring
def catval(t: dict, c: str) -> float:
    if c == "fg_pct":
        return t["fgm"] / t["fga"] if t["fga"] else 0.0
    if c == "ft_pct":
        return t["ftm"] / t["fta"] if t["fta"] else 0.0
    return float(t[c])


def winner(a: dict, b: dict, c: str) -> int:
    """+1 a wins, -1 b wins, 0 tie (TOV: lower wins; percentages compared at 4 dp like Yahoo's 3-dp display)."""
    x, y = catval(a, c), catval(b, c)
    if c in ("fg_pct", "ft_pct"):
        x, y = round(x, 4), round(y, 4)
    return int(np.sign(x - y) * SIGN[c])


def outcomes(ctx: AuditContext, rev: dict) -> tuple[list[dict], list[dict]]:
    y2s = {y: s for s, y in ctx.slot2yahoo.items()}
    ms, valid = [], []
    for w in ctx.weeks:
        for a, b in w.matchups:
            ra, rb = rev.get((w.week, ctx.slot2yahoo[a])), rev.get((w.week, ctx.slot2yahoo[b]))
            if ra is None or rb is None:
                continue
            fin = next((r for r in (ra, rb) if r.get("final") and str(r["final"].get("basis", "")).startswith("yahoo")
                        and r["final"].get("complete")), None)
            if fin is not None:
                me = y2s[fin["me"]["yahoo_team_id"]]
                tot = {me: fin["final"]["totals"]["me"], (b if me == a else a): fin["final"]["totals"]["opp"]}
                rec = {me: fin["teams"]["me"]["totals"], (b if me == a else a): fin["teams"]["opp"]["totals"]}
                flips = [c for c in CATS if winner(rec[a], rec[b], c) != winner(tot[a], tot[b], c)]
                for t in (a, b):
                    valid.append({"week": w.week, "team": t, "max_abs_err": max(abs(rec[t][k] - tot[t][k]) for k in RAW),
                                  "pts_err": rec[t]["pts"] - tot[t]["pts"], "flips": flips, "matchup": (a, b)})
                basis = "yahoo_final"
            else:
                tot = {a: ra["teams"]["me"]["totals"], b: rb["teams"]["me"]["totals"]}
                basis = "reconstructed"
            res = {c: winner(tot[a], tot[b], c) for c in CATS}
            wa, wb = sum(v > 0 for v in res.values()), sum(v < 0 for v in res.values())
            ms.append({"week": w.week, "a": a, "b": b, "basis": basis, "tot": tot, "cat_res": res, "score": [wa, wb],
                       "games": {a: ra["teams"]["me"]["games_counted"], b: rb["teams"]["me"]["games_counted"]}})
    return ms, valid


def attach_preds(ms: list[dict], preds: dict, mode: str) -> list[dict]:
    out = []
    for m in ms:
        wk = preds[mode].get(str(m["week"]))
        if not wk:
            continue
        pm = next(x for x in wk["matchups"] if {x["a"], x["b"]} == {m["a"], m["b"]})
        flip = pm["a"] != m["a"]
        p_loss = 1 - pm["p_win"] - pm["p_tie"]
        m = {**m, "p_win_a": p_loss if flip else pm["p_win"], "p_tie": pm["p_tie"],
             "exp_cats_a": (9 - pm["exp_cats"]) if flip else pm["exp_cats"], "var_cats": pm["var_cats"],
             "p_cat_a": {c: (1 - pm["cat_win"][c] - pm["cat_tie"][c]) if flip else pm["cat_win"][c] for c in CATS},
             "p_cat_tie": pm["cat_tie"]}
        out.append(m)
    return out


def standings(ms: list[dict], teams: list[int]) -> dict[int, dict]:
    st = {t: {"W": 0, "L": 0, "T": 0, "cw": 0.0, "cl": 0.0, "ct": 0.0, "cat": {c: [0, 0, 0] for c in CATS},
              "weeks": []} for t in teams}
    for m in ms:
        for t, o, sgn in ((m["a"], m["b"], 1), (m["b"], m["a"], -1)):
            s = st[t]
            wins, losses = (m["score"] if sgn == 1 else m["score"][::-1])
            s["cw"] += wins
            s["cl"] += losses
            s["ct"] += 9 - wins - losses
            r = "W" if wins > losses else ("L" if wins < losses else "T")
            s[r] += 1
            s["weeks"].append(f"W{m['week']} {r} {wins}-{losses} v{o}{'' if m['basis'] == 'yahoo_final' else '*'}")
            for c in CATS:
                v = m["cat_res"][c] * sgn
                s["cat"][c][0 if v > 0 else (1 if v < 0 else 2)] += 1
    return st


def zsummary(ms: list[dict], preds: dict, mode: str) -> dict:
    rows = []
    for m in ms:
        wk = preds[mode].get(str(m["week"]))
        if not wk:
            continue
        for t in (m["a"], m["b"]):
            pr = wk["teams"][str(t)]
            r = {"games": ((m["games"][t] - pr["games"]) / pr["games_sd"], m["games"][t] - pr["games"])}
            for c in CATS:
                a = catval(m["tot"][t], c)
                r[c] = ((a - pr[c]["mean"]) / pr[c]["sd"], a - pr[c]["mean"])
            rows.append(r)
    out = {}
    for c in CATS + ["games"]:
        z = np.array([r[c][0] for r in rows])
        d = np.array([r[c][1] for r in rows])
        n = len(z)
        if n < 3:
            continue
        v = float(np.var(z, ddof=1))
        out[c] = {"n": n, "mean_diff": float(d.mean()), "mean_z": float(z.mean()), "mean_z_se": float(z.std(ddof=1) / np.sqrt(n)),
                  "var_z": v, "var_z_se": v * np.sqrt(2 / (n - 1)),
                  "var_z_90ci": [v * (n - 1) / chi2.ppf(0.95, n - 1), v * (n - 1) / chi2.ppf(0.05, n - 1)]}
    return out


# ----------------------------------------------------------------------------------------------- main
def run(ctx: AuditContext, skip_sim: bool) -> tuple[list[str], list[dict], dict]:
    md: list[str] = ["## League + simulator calibration"]
    F: list[dict] = []
    head: dict[str, Any] = {}
    if not ctx.weeks:
        return md + ["No completed weeks."], F, head
    rev = fetch_reviews(ctx)
    ms, valid = outcomes(ctx, rev)
    teams = sorted(ctx.slot2yahoo)
    ros = json.loads(git_text(ctx.asof_sha, ctx.rosters_path))
    me = int(ros.get("my_team") or teams[0])
    st = standings(ms, teams)
    n_y = sum(m["basis"] == "yahoo_final" for m in ms)
    ok = [v for v in valid if v["max_abs_err"] <= 1]
    flips = {(v["week"], v["matchup"]): v["flips"] for v in valid}
    nflip = sum(len(f) for f in flips.values())
    md += [f"**Data basis:** {n_y} of {len(ms)} matchups Yahoo-final, the rest reconstructed (`*`: box scores × "
           f"captured active lineup). Validation on {len(valid)} Yahoo-final team-weeks: {len(ok)} within ±1 on every raw "
           f"stat; {nflip} category flip(s) in {len(flips)} matchup(s) (reconstructed vs Yahoo)."]
    F.append(finding("league.reconstruction_flip_rate", "data", "cats", nflip / max(9 * len(flips), 1), None,
                     "denser lineup captures (intra-day changes are missed)", n=len(flips)))

    def wl(s: dict) -> tuple[float, float]:
        return s["W"] + 0.5 * s["T"], s["cw"] + 0.5 * s["ct"]

    order = sorted(teams, key=lambda t: (-wl(st[t])[0], -wl(st[t])[1]))
    srow = []
    for t in order:
        s = st[t]
        strong = [c for c in CATS if s["cat"][c][0] == len(s["weeks"])]
        weak = [c for c in CATS if s["cat"][c][1] == len(s["weeks"])]
        srow.append({"#": t, "team": ("**" + ctx.names.get(t, str(t)) + "**") if t == me else ctx.names.get(t, str(t)),
                     "W-L-T": f"{s['W']}-{s['L']}-{s['T']}", "cats": f"{s['cw']:.0f}-{s['cl']:.0f}-{s['ct']:.0f}",
                     "weeks": ", ".join(s["weeks"]),
                     "all-won / all-lost cats": f"{' '.join(strong) or '-'} / {' '.join(weak) or '-'}"})
    md += ["### Standings", md_table(srow, list(srow[0].keys()))]
    with open(ctx.out / "standings.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["slot", "name", "W", "L", "T", "cat_w", "cat_l", "cat_t", "weeks"])
        for t in order:
            s = st[t]
            wr.writerow([t, ctx.names.get(t, ""), s["W"], s["L"], s["T"], s["cw"], s["cl"], s["ct"], "; ".join(s["weeks"])])
    head["me"] = f"{st[me]['W']}-{st[me]['L']}-{st[me]['T']} ({st[me]['cw']:.0f}-{st[me]['cl']:.0f}-{st[me]['ct']:.0f} cats)"

    # week-review rate z (actual vs projection given games played; per-game noise)
    rz: dict[str, list[float]] = {c: [] for c in CATS}
    for r in rev.values():
        for c in CATS:
            z = r["teams"]["me"]["categories"].get(c, {}).get("z")
            if z is not None:
                rz[c].append(z)
    rparts = []
    for c, v in rz.items():
        if len(v) > 3:
            var = float(np.var(v, ddof=1))
            F.append(finding(f"reviewz.var.{c}", "week_review", c, var, var * np.sqrt(2 / (len(v) - 1)),
                             "week-review z understates weekly variance: use the sim's weekly SD or inflate", null=1.0))
            rparts.append(f"{c} {var:.2f}")
    md += ["- Week-review z variance (should be ≈ 1): " + ", ".join(rparts)]

    cache = ctx.out / "preds.json"
    if not skip_sim:
        preds = run_sims(ctx)
    else:
        preds = json.loads(cache.read_text()) if cache.exists() else None
    if not preds:
        md += ["_Calibration skipped (--skip-sim and no cached preds.json)._"]
        return md, F, head
    M = attach_preds(ms, preds, "stream")
    rng = np.random.default_rng(0)
    with open(ctx.out / "matchups.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["week", "a", "b", "basis", "cats_a", "cats_b", "p_win_a", "p_tie", "exp_cats_a"])
        for m in M:
            wr.writerow([m["week"], m["a"], m["b"], m["basis"], *m["score"], round(m["p_win_a"], 4),
                         round(m["p_tie"], 4), round(m["exp_cats_a"], 3)])

    # ---- me
    mine = [m for m in M if me in (m["a"], m["b"])]
    pdw = preds.get("postdraft", {}).get("weeks", {})
    mrows = []
    pw_sum, pw_var, act_w = 0.0, 0.0, 0.0
    for m in mine:
        a_is_me = m["a"] == me
        o = m["b"] if a_is_me else m["a"]
        pw = (m["p_win_a"] if a_is_me else 1 - m["p_win_a"] - m["p_tie"]) + 0.5 * m["p_tie"]
        ec = m["exp_cats_a"] if a_is_me else 9 - m["exp_cats_a"]
        sc = m["score"] if a_is_me else m["score"][::-1]
        pdm = next((x for x in pdw.get(str(m["week"]), []) if {x["a"], x["b"]} == {me, o}), None)
        pd_p = None if pdm is None else (pdm["p_win"] if pdm["a"] == me else 1 - pdm["p_win"] - pdm["p_tie"])
        pw_sum += pw
        pw_var += pw * (1 - pw)
        act_w += 1.0 if sc[0] > sc[1] else (0.5 if sc[0] == sc[1] else 0.0)
        mrows.append({"wk": m["week"], "opp": ctx.names.get(o, o), "result": f"{sc[0]}-{sc[1]}",
                      "P(win) week-start / post-draft": f"{pw:.2f} / {'n/a' if pd_p is None else f'{pd_p:.2f}'}",
                      "exp cats → actual": f"{ec:.2f} → {sc[0] + 0.5 * (9 - sum(sc)):.1f}"})
    if mrows:
        F.append(finding("me.wins_vs_expected", "league", "wins", act_w - pw_sum, np.sqrt(pw_var),
                         "", candidate_fix=False))
        md += [f"### Me: {act_w:.1f} wins vs {pw_sum:.2f} expected at week starts", md_table(mrows, list(mrows[0].keys()))]
        head["me_exp"] = f"{act_w:.1f} wins vs {pw_sum:.2f} expected"

    # ---- calibration
    pm_ = np.array([m["p_win_a"] + 0.5 * m["p_tie"] for m in M])
    ym_ = np.array([1.0 if m["score"][0] > m["score"][1] else (0.0 if m["score"][0] < m["score"][1] else 0.5) for m in M])
    P, Y, G, CC = [], [], [], []
    for gi, m in enumerate(M):
        for c in CATS:
            P.append(m["p_cat_a"][c] + 0.5 * m["p_cat_tie"][c])
            Y.append({1: 1.0, -1: 0.0, 0: 0.5}[m["cat_res"][c]])
            G.append(gi)
            CC.append(c)
    Pa, Ya, Ga, Ca = map(np.array, (P, Y, G, CC))
    cm = calibration(pm_, ym_, np.arange(len(pm_)), rng)
    cc = calibration(Pa, Ya, Ga, rng)
    (ctx.out / "calibration.json").write_text(json.dumps({"match": cm, "cat": cc}, indent=1, default=float))
    head["brier"] = (cm["brier"], cm["brier_if_calibrated"], cm["n"])
    for lvl, c_ in (("match", cm), ("cat", cc)):
        F.append(finding(f"calib.{lvl}.brier_gap", "calibration", lvl, c_["brier_gap"], c_["brier_gap_se"],
                         "sim over/under-confident: revisit weekly variance/correlation", n=c_["n"]))
        F.append(finding(f"calib.{lvl}.fav_gap", "calibration", lvl, c_["fav_gap"], c_["fav_gap_se"],
                         "favourites win more (+: underconfident) or less (−: overconfident) than predicted"))
        F.append(finding(f"calib.{lvl}.slope", "calibration", lvl, c_["slope"], c_["slope_se"],
                         "logistic recalibration slope ≠ 1 (<1 overconfident)", null=1.0))
    by = []
    for c in CATS:
        k = Ca == c
        pc, yc = Pa[k], Ya[k]
        fav = np.where(pc >= .5, pc, 1 - pc)
        yf = np.where(pc >= .5, yc, 1 - yc)
        se = float(np.sqrt(np.mean(fav * (1 - fav)) / len(fav)))
        F.append(finding(f"calib.cat.{c}.fav_gap", "calibration", c, float(yf.mean() - fav.mean()), se,
                         "category-specific mean/variance off (9 tests: Bonferroni |t| > 2.8)"))
        by.append(f"{c} {yf.mean():.0%} vs {fav.mean():.0%}")
    md += ["### Calibration (week-start sims " + ", ".join(f"W{k} `{v['sha']}`" for k, v in preds["stream"].items())
           + f"; {ctx.sims:,} sims)",
           f"- **Matchup P(win), n={cm['n']}:** Brier {cm['brier']:.3f} vs {cm['brier_if_calibrated']:.3f} if calibrated "
           f"(coin flip {cm['brier_coin']:.3f}); gap {fmt_pm(cm['brier_gap'], cm['brier_gap_se'], 3)}. Favourites "
           f"{cm['fav_pred']:.0%} predicted, {cm['fav_actual']:.0%} won ({fmt_pm(100 * cm['fav_gap'], 100 * cm['fav_gap_se'], 0)} pp). "
           f"Slope {cm['slope']:.2f} (90% CI {cm['slope_90ci'][0]:.2f}–{cm['slope_90ci'][1]:.2f}; > 1 underconfident).",
           f"- **Category P(win), n={cc['n']} in {cc['clusters']} clusters:** Brier {cc['brier']:.3f} vs "
           f"{cc['brier_if_calibrated']:.3f} (gap {fmt_pm(cc['brier_gap'], cc['brier_gap_se'], 3)}); favourites "
           f"{cc['fav_pred']:.1%} predicted vs {cc['fav_actual']:.1%} won (± {100 * cc['fav_gap_se']:.1f} pp); slope "
           f"{cc['slope']:.2f} (90% CI {cc['slope_90ci'][0]:.2f}–{cc['slope_90ci'][1]:.2f}, SE {cc['slope_se']:.2f}).",
           "- Reliability (favourite prob → hit rate): " + "; ".join(f"{b['bin']} → {b['actual']:.2f} (n={b['n']})"
                                                                     for b in cc["bins"]),
           "- Per category, favourites won vs predicted: " + ", ".join(by)]

    # ---- weekly totals z vs sim mean/SD
    for mode in ("stream", "nostream"):
        zs = zsummary(ms, preds, mode)
        for c, s in zs.items():
            sug = ("streaming volume (games per team-week)" if c == "games" else
                   "level shift in projections (see projection bias)")
            F.append(finding(f"simz.{mode}.{c}.mean", "sim_totals", c, s["mean_z"], s["mean_z_se"], sug,
                             mean_diff=s["mean_diff"]))
            F.append(finding(f"simz.{mode}.{c}.var", "sim_totals", c, s["var_z"], s["var_z_se"],
                             "weekly SD too narrow (>1) / too wide (<1)", null=1.0))
        md += [f"- Weekly totals vs sim (streaming {'on' if mode == 'stream' else 'off'}; {zs.get('games', {}).get('n', 0)} "
               "team-weeks; mean z ± SE [var z]): "
               + ", ".join(f"{c} {s['mean_z']:+.2f} ± {s['mean_z_se']:.2f} [{s['var_z']:.2f}]" for c, s in zs.items())
               + (f". Games: {zs['games']['mean_diff']:+.1f}/team-week" if "games" in zs else "")]
        if mode == "stream" and "games" in zs:
            head["games_gap"] = zs["games"]["mean_diff"]

    # ---- per-team actual vs post-draft expectation
    pd_ = preds.get("postdraft", {})
    if pd_:
        tab, tab_now = pd_["table"], preds.get("asof", {}).get("table", {})
        rows, tz = [], []
        for t in teams:
            e = v = 0.0
            for mlist in pd_["weeks"].values():
                x = next((m for m in mlist if t in (m["a"], m["b"])), None)
                if x is None:
                    continue
                e += x["exp_cats"] if x["a"] == t else 9 - x["exp_cats"]
                v += x["var_cats"]
            act = st[t]["cw"] + 0.5 * st[t]["ct"]
            se = np.sqrt(v)
            tz.append((act - e) / se)
            F.append(finding(f"team.{t}.cats_vs_postdraft", "team", "cats", act - e, se, "", candidate_fix=False,
                             name=ctx.names.get(t)))
            now = tab_now.get(str(t), {})
            rows.append({"#": t, "team": ctx.names.get(t, t), "cats": f"{act:.1f}", "post-draft exp": f"{e:.1f}",
                         "diff ± SE": fmt_pm(act - e, se, 1),
                         "P(po) post-draft → as-of": f"{tab[str(t)]['p_playoffs']:.0%} → "
                                                    f"{now.get('p_playoffs', float('nan')):.0%}"})
        rows.sort(key=lambda r: -float(r["diff ± SE"].split(" ")[0]))
        tz_ = np.array(tz)
        F.append(finding("team.spread_mean_t2", "calibration", "cats", float(np.mean(tz_ ** 2)), np.sqrt(2 / len(tz_)),
                         "team outcomes spread wider than the sim (manager activity not modelled?)", null=1.0))
        md += [f"### Teams vs the post-draft sim (week-1 start `{pd_['sha']}`, streaming on; as-of sim "
               f"`{preds.get('asof', {}).get('sha', 'n/a')}` is projection-only — banked results not used)",
               md_table(rows, list(rows[0].keys())),
               f"- Mean t² across teams {np.mean(tz_ ** 2):.2f} (1 if the sim's spread is right; ± {np.sqrt(2 / len(tz_)):.2f})."]
    md += ["- Validation details: " + ", ".join(f"W{v['week']} #{v['team']} max|err| {v['max_abs_err']:.0f}"
                                               + (f" flips {v['flips']}" if v["flips"] else "") for v in valid)]
    return md, F, head
