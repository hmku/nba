"""Projection bias audit (generalises docs/research/bias_audit_2026-11-09/bias*.py).

Preseason projection = the last commit touching the projections CSV before the season start (draft-time blend), its
`base.csv` ("ours") and the raw ESPN component; in-season = the projection file at each week's start commit.
Errors are actual - projected per game played; SEs clustered by player (projection error >> game noise).
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from common import (
    REPO,
    STATS,
    AuditContext,
    add_const,
    cluster_mean,
    finding,
    fmt_pm,
    git_csv,
    git_exists,
    git_text,
    md_table,
    slug,
    wls,
)

CNT = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"]
SUGGEST = {
    "level": "league-level offset in the in-season update (shrunk toward the normal early-season drift); "
             "compare with the ESPN early-season benchmark first",
    "minutes": "team minute budget / bench-minutes trim (check team totals vs 240)",
    "rate": "per-minute rate prior off at league level (in-season offset)",
}


# ----------------------------------------------------------------------------------------------- data
def load_box(ctx: AuditContext, year: int | None = None, start: Any = None, through: Any = None) -> pd.DataFrame:
    pg = ctx.boxscores(year)
    pg = pg[pg.regular_season.astype(str).str.lower().isin(["true", "1"])].copy()
    if start is not None:
        pg = pg[pg.game_date >= str(start)]
    if through is not None:
        pg = pg[pg.game_date <= str(through)]
    pg["played"] = pg.played.astype(str).str.lower().isin(["true", "1"])
    for s in STATS + ["min"]:
        pg[s] = pg[s].astype(float)
    return pg


def residual_frame(P: pd.DataFrame, proj: pd.DataFrame, label: str) -> pd.DataFrame:
    """Per player-game residuals; proj indexed by player_id with mpg + STATS (per game)."""
    from fantasy.model.noise import game_var

    pj = proj[~proj.index.duplicated()]
    d = P[P.player_id.isin(pj.index)].copy()
    d["pmin"] = d.player_id.map(pj["mpg"]).astype(float)
    for s in STATS:
        d["p_" + s] = d.player_id.map(pj[s]).astype(float)
        d["r_" + s] = d[s] - d["p_" + s]
        rate = np.where(d.pmin > 0, d["p_" + s] / d.pmin.where(d.pmin > 0, 1), 0)
        d["rr_" + s] = d[s] - rate * d["min"]  # rate error at actual minutes
        d["rm_" + s] = rate * (d["min"] - d.pmin)  # minutes error
        d["v_" + s] = game_var(s, d["p_" + s].clip(lower=0))
    d["r_min"] = d["min"] - d.pmin
    for mk, at in (("fgm", "fga"), ("ftm", "fta")):
        pct = (pj[mk] / pj[at].replace(0, np.nan)).reindex(d.player_id).to_numpy()
        d["imp_" + mk] = d[mk] - np.nan_to_num(pct) * d[at]  # makes over projected % on ACTUAL attempts
    d["label"] = label
    return d


def bias_rows(d: pd.DataFrame) -> list[dict]:
    rows = []
    for s in STATS + ["min"]:
        col = "r_" + s
        mu, se = cluster_mean(d.player_id, d[col])
        pm = d.pmin.mean() if s == "min" else d["p_" + s].mean()
        r = {"stat": s, "proj": pm, "bias": mu, "se": se, "t": _t(mu, se), "pct": mu / pm if pm else np.nan}
        if s != "min":
            r["rate"], r["rate_se"] = cluster_mean(d.player_id, d["rr_" + s])
            r["mins"], r["mins_se"] = cluster_mean(d.player_id, d["rm_" + s])
        rows.append(r)
    for mk, at, nm in (("fgm", "fga", "fg%"), ("ftm", "fta", "ft%")):
        mu, se = cluster_mean(d.player_id, d["imp_" + mk])
        att = d[at].mean()
        rows.append({"stat": nm, "proj": d["p_" + mk].sum() / d["p_" + at].sum(), "actual": d[mk].sum() / d[at].sum(),
                     "bias": mu / att, "se": se / att, "t": _t(mu, se)})
    return rows


def _t(mu: float, se: float) -> float:
    return mu / se if se > 0 else np.nan


def zscale(top: pd.DataFrame) -> dict:
    pfg, pft = top.fgm.sum() / top.fga.sum(), top.ftm.sum() / top.fta.sum()
    sd = {c: top[c].std() for c in CNT}
    sd["fg"] = (top.fgm - pfg * top.fga).std()
    sd["ft"] = (top.ftm - pft * top.fta).std()
    return {"sd": sd, "pfg": pfg, "pft": pft}


def zval(df: pd.DataFrame, sc: dict, pfx: str = "") -> pd.Series:
    """9-cat z-sum per game (projected-top-200 scale)."""
    sd = sc["sd"]
    v = sum(df[pfx + c] / sd[c] * (-1 if c == "tov" else 1) for c in CNT)
    return v + (df[pfx + "fgm"] - sc["pfg"] * df[pfx + "fga"]) / sd["fg"] + \
        (df[pfx + "ftm"] - sc["pft"] * df[pfx + "fta"]) / sd["ft"]


def _f(x: float, k: int = 2) -> str:
    return f"{x:+.{k}f}" if np.isfinite(x) else "n/a"


# ----------------------------------------------------------------------------------------------- main
def run(ctx: AuditContext) -> tuple[list[str], list[dict], dict]:
    out = ctx.out
    md: list[str] = []
    F: list[dict] = []
    head: dict[str, Any] = {}
    pre_sha = ctx.preseason_sha
    pre = git_csv(pre_sha, ctx.proj_path).drop_duplicates("player_id").set_index("player_id")
    base_p = ctx.proj_path.replace(".csv", ".base.csv")
    base = git_csv(pre_sha, base_p).drop_duplicates("player_id").set_index("player_id") \
        if git_exists(pre_sha, base_p) else None
    espn_p = f"data/raw/espn/projections_{ctx.season_tag}.csv"
    espn = git_csv(pre_sha, espn_p).drop_duplicates("player_id").set_index("player_id") \
        if git_exists(pre_sha, espn_p) else None
    asof = ctx.asof_sha
    ins = git_csv(asof, ctx.ins_path).drop_duplicates("player_id").set_index("player_id") \
        if git_exists(asof, ctx.ins_path) else None
    cur = git_csv(asof, ctx.proj_path).drop_duplicates("player_id").set_index("player_id")
    ros = json.loads(git_text(asof, ctx.rosters_path))
    rostered = {p for t in ros["teams"].values() for p in t["players"]}

    pg = load_box(ctx, start=ctx.start, through=ctx.through)
    P = pg[pg.played].copy()
    tg = ctx.teamgames()
    tg = tg[tg.regular_season.astype(str).str.lower().isin(["true", "1"]) & (tg.game_date >= str(ctx.start))
            & (tg.game_date <= str(ctx.through))]
    team_n = tg.groupby("team").game_id.nunique()
    n_days = (pd.Timestamp(ctx.through) - pd.Timestamp(P.game_date.min())).days + 1
    head["sample"] = (f"games {P.game_date.min()}..{P.game_date.max()}: {pg.game_id.nunique()} games, {len(P):,} "
                      f"player-games, {P.player_id.nunique()} players")
    md += ["## Projection bias", f"Preseason projection `{pre_sha}` (draft-time default"
           f"{'' if base is None else '; ours = base.csv'}{'' if espn is None else '; ESPN component'}); in-season "
           f"= file at each week-start commit; as-of state `{asof}`. {head['sample']}. SEs clustered by player."]

    # ---- 1. league-wide bias per game played
    dB = residual_frame(P, pre, "blend")
    tb = pd.DataFrame(bias_rows(dB))
    tb.to_csv(out / "bias_preseason.csv", index=False)
    common_ids = set(dB.player_id)
    frames = {"blend": dB}
    if base is not None:
        frames["ours"] = residual_frame(P, base, "ours")
        common_ids &= set(frames["ours"].player_id)
    if espn is not None:
        frames["espn"] = residual_frame(P, espn[espn.index.isin(pre.index)], "espn")
        common_ids &= set(frames["espn"].player_id)
    src_t: dict[str, dict[str, float]] = {k: {} for k in frames}
    for k, d in frames.items():
        dc = d[d.player_id.isin(common_ids)]
        for r in bias_rows(dc):
            src_t[k][r["stat"]] = r["t"]
            if k != "blend":
                F.append(finding(f"bias.preseason.{k}.{r['stat']}", "projection_bias", r["stat"], r["bias"], r["se"],
                                 SUGGEST["level"], source=k, sample="common"))
    rows = []
    for r in tb.to_dict("records"):
        s = r["stat"]
        pct = s.endswith("%")
        k = 4 if pct else 2
        sug = SUGGEST["level"]
        if not pct and s != "min" and abs(r.get("mins", 0) / (r.get("mins_se") or 1)) >= 3 \
                and abs(r.get("rate", 0) / (r.get("rate_se") or 1)) < 3:
            sug = SUGGEST["minutes"]
        F.append(finding(f"bias.preseason.{s}", "projection_bias", s, r["bias"], r["se"], sug,
                         proj=r["proj"], pct=r.get("pct")))
        if s not in ("min",) and not pct:
            F.append(finding(f"bias.preseason.{s}.rate", "projection_bias", s, r["rate"], r["rate_se"], SUGGEST["rate"]))
            F.append(finding(f"bias.preseason.{s}.minutes", "projection_bias", s, r["mins"], r["mins_se"],
                             SUGGEST["minutes"]))
        rows.append({"stat": s.upper(), "proj/g": f"{r['proj']:.3f}" if pct else f"{r['proj']:.2f}",
                     "bias ± SE": (f"{100 * r['bias']:+.2f} ± {100 * r['se']:.2f} pp" if pct
                                   else fmt_pm(r["bias"], r["se"], k)),
                     "%": "" if pct else f"{r['pct']:+.0%}", "t": f"{r['t']:+.1f}",
                     "minutes part": "" if pct or s == "min" else fmt_pm(r["mins"], r["mins_se"]),
                     "rate part": "" if pct or s == "min" else fmt_pm(r["rate"], r["rate_se"]),
                     **{f"{k2} t": _f(src_t[k2].get(s, np.nan), 1) for k2 in frames if k2 != "blend"}})
    head["pts_bias"] = tb.set_index("stat").loc["pts", ["bias", "se"]].tolist()
    md += ["### 1. Per game played (actual − projected), preseason", md_table(rows, list(rows[0].keys()))]

    # ---- 1b. week-start in-season projection (each week's games vs the file in force at its start)
    wk_frames = []
    for w in ctx.weeks:
        if not w.sha or not w.proj_path:
            continue
        pw = git_csv(w.sha, w.proj_path).drop_duplicates("player_id").set_index("player_id")
        Pw = P[(P.game_date >= str(w.start)) & (P.game_date <= str(w.end))]
        wk_frames.append(residual_frame(Pw, pw, f"w{w.week}").assign(week=w.week, sha=w.sha))
    if wk_frames:
        dW = pd.concat(wk_frames, ignore_index=True)
        tw = pd.DataFrame(bias_rows(dW))
        tw.to_csv(out / "bias_weekstart.csv", index=False)
        per_week = {}
        for wk, d in dW.groupby("week"):
            per_week[wk] = {r["stat"]: r for r in bias_rows(d)}
        rows = []
        for r in tw.to_dict("records"):
            s = r["stat"]
            F.append(finding(f"bias.weekstart.{s}", "projection_bias", s, r["bias"], r["se"], SUGGEST["level"]))
            pct = s.endswith("%")
            sc = 100 if pct else 1
            rows.append({"stat": s.upper(), "pooled bias ± SE": fmt_pm(sc * r["bias"], sc * r["se"]),
                         "t": f"{r['t']:+.1f}", **{f"W{wk}": _f(sc * per_week[wk][s]["bias"]) for wk in per_week}})
        head["weekstart_pts"] = tw.set_index("stat").loc["pts", ["bias", "se"]].tolist()
        md += ["### 1b. Week-start projection (what the daily routine used that week), pooled; per-week bias"
               + " (" + ", ".join(f"W{w.week} `{w.sha}` {w.proj_path.rsplit('/', 1)[-1]}" for w in ctx.weeks
                                  if w.sha and w.proj_path) + ")",
               md_table(rows, list(rows[0].keys()))]

    # ---- 2. early-season benchmark (ESPN preseason projections, same window length, prior seasons) + league env
    bench = []
    for back in (1, 2):
        yr = ctx.box_year - back
        y0 = yr - 1
        f = REPO / f"data/raw/espn/projections_{y0}_{yr % 100:02d}.csv"
        bf = REPO / f"data/raw/espn_boxscores/player_games_{yr}.csv.gz"
        if not (f.exists() and bf.exists()):
            continue
        h = load_box(ctx, yr)
        first = h.game_date.min()
        h = h[(h.game_date < str((pd.Timestamp(first) + pd.Timedelta(days=n_days)).date())) & h.played]
        e = pd.read_csv(f).drop_duplicates("player_id").set_index("player_id")
        de = residual_frame(h, e, f"espn{yr}")
        br = {r["stat"]: r for r in bias_rows(de)}
        bench.append({"season": f"{y0}-{yr % 100:02d}", "PTS": fmt_pm(br["pts"]["bias"], br["pts"]["se"]),
                      "MIN": fmt_pm(br["min"]["bias"], br["min"]["se"]),
                      "FG% pp": fmt_pm(100 * br["fg%"]["bias"], 100 * br["fg%"]["se"]),
                      "_pts": br["pts"]["bias"], "_fg": br["fg%"]["bias"]})
    if espn is not None:
        de = frames["espn"]
        br = {r["stat"]: r for r in bias_rows(de)}
        bench.insert(0, {"season": f"this season (ESPN component, {n_days} d)",
                         "PTS": fmt_pm(br["pts"]["bias"], br["pts"]["se"]), "MIN": fmt_pm(br["min"]["bias"], br["min"]["se"]),
                         "FG% pp": fmt_pm(100 * br["fg%"]["bias"], 100 * br["fg%"]["se"])})
    if bench:
        prior = [b for b in bench if "_pts" in b]
        if prior:
            normal_pts = float(np.mean([b["_pts"] for b in prior]))
            normal_fg = float(np.mean([b["_fg"] for b in prior]))
            r = tb.set_index("stat")
            F.append(finding("bias.preseason.pts.excess_vs_early_season_norm", "projection_bias", "pts",
                             r.loc["pts", "bias"] - normal_pts, r.loc["pts", "se"],
                             "bias beyond the usual early-season ESPN miss: league-level offset", norm=normal_pts))
            F.append(finding("bias.preseason.fg%.excess_vs_early_season_norm", "projection_bias", "fg%",
                             r.loc["fg%", "bias"] - normal_fg, r.loc["fg%", "se"],
                             "FG% beyond the usual early-season miss: league-level offset", norm=normal_fg))
        md += [f"### 2. Normal early-season miss (ESPN preseason projections, first {n_days} days of prior seasons)",
               md_table([{k: v for k, v in b.items() if not k.startswith("_")} for b in bench],
                        ["season", "PTS", "MIN", "FG% pp"])]
    env = []
    for yr in (ctx.box_year, ctx.box_year - 1):
        try:
            h = load_box(ctx, yr)
        except FileNotFoundError:
            continue
        h = h[h.played & (h.game_date < str((pd.Timestamp(h.game_date.min()) + pd.Timedelta(days=n_days)).date()))]
        t = h.groupby(["game_id", "team"])[["pts", "fgm", "fga", "fg3m", "tov"]].sum()
        fg3a = h.fg3a.astype(float).sum() if "fg3a" in h else np.nan
        env.append({"season": yr, "PTS/team-g": f"{t.pts.mean():.1f}", "FG%": f"{t.fgm.sum() / t.fga.sum():.3f}",
                    "3P%": f"{h.fg3m.sum() / fg3a:.3f}", "TOV/team-g": f"{t.tov.mean():.1f}"})
    if env:
        md += [f"League environment, first {n_days} days (season-end year):", md_table(env, list(env[0].keys()))]

    # ---- 3. rostered players
    dR = dB[dB.player_id.isin(rostered)]
    if len(dR):
        rr = {r["stat"]: r for r in bias_rows(dR)}
        for s in ("pts", "min", "tov", "fg%"):
            F.append(finding(f"bias.rostered.{s}", "projection_bias", s, rr[s]["bias"], rr[s]["se"],
                             SUGGEST["level"], n_players=int(dR.player_id.nunique())))
        md += [f"- **Rostered players** ({dR.player_id.nunique()} on current rosters; selection leans positive): "
               + ", ".join(f"{s.upper()} {fmt_pm(rr[s]['bias'] * (100 if s.endswith('%') else 1), rr[s]['se'] * (100 if s.endswith('%') else 1))}"
                           for s in ("pts", "min", "tov", "fg%"))]

    # ---- 4. player-level frame, heterogeneity
    sc = zscale(pre.sort_values("mpg", ascending=False).head(200))
    dB["z_act"], dB["z_pro"] = zval(dB, sc), zval(dB, sc, "p_")
    dB["r_z"] = dB.z_act - dB.z_pro
    pre["zpg"] = zval(pre, sc)
    pre["vrank"] = pre.zpg.rank(ascending=False)
    pre["adprank"] = pre.adp.rank() if "adp" in pre else np.nan
    g = dB.groupby("player_id")
    pl = pd.DataFrame({"n": g.size(), "min": g["min"].mean(), "pmin": g.pmin.first(),
                       "team_act": g.team.agg(lambda s: s.iloc[-1])})
    for c in ["r_pts", "r_reb", "r_ast", "r_fg3m", "r_min", "r_z", "rr_pts", "z_act", "z_pro"]:
        pl[c] = g[c].mean()
    pl["fgimp"] = g.imp_fgm.sum() / g.fga.sum().replace(0, np.nan)
    pl["fga_n"] = g.fga.sum()
    pl = pl.join(pre[["name", "team", "age", "positions", "zpg", "vrank", "adprank"]])
    rook_ids: set[str] = set()
    games_missed = pd.Series(dtype=float)
    ov_ids: set[str] = set()
    for path, dest in ((f"data/manual/rookies_{ctx.season_tag}.csv", rook_ids),
                       (f"data/manual/player_overrides_{ctx.season_tag}.csv", ov_ids)):
        if git_exists(pre_sha, path):
            dfm = git_csv(pre_sha, path, comment="#")
            dest |= {slug(n) for n in dfm.name}
            if "games_missed" in dfm:
                games_missed = dfm.assign(pid=dfm.name.map(slug)).set_index("pid").games_missed.dropna()
    pl["rookie"] = pl.index.isin(rook_ids).astype(int)
    pl["override"] = pl.index.isin(ov_ids).astype(int)
    pl["moved"] = (pl.team != pl.team_act).astype(int)
    pl["pos"] = pl.positions.astype(str).str.split("|").str[0].map({"PG": "G", "SG": "G", "SF": "F", "PF": "F", "C": "C"})
    pl["tier"] = pd.cut(pl.vrank, [0, 50, 150, 1e9], labels=["top50", "51-150", "rest"])
    pl["adptier"] = pd.cut(pl.adprank.fillna(999), [0, 50, 150, 1e9], labels=["top50", "51-150", "rest"])
    # team projected minutes per team game (preseason allocation)
    tmin_proj = {t: float((pre[pre.team == t].mpg * pre[pre.team == t].gp / 82).sum()) for t in team_n.index}
    pl["team_excess"] = pl.team.map(tmin_proj).sub(240).div(10)
    pl.to_csv(out / "player_level.csv")

    X = pd.get_dummies(pl[["tier", "pos"]], drop_first=True).astype(float)
    X["age_c"] = pl.age - 27
    for c in ("rookie", "override", "moved"):
        X[c] = pl[c]
    X["pmin_c"] = pl.pmin - 25
    X["team_excess"] = pl.team_excess
    X = add_const(X)
    het_rows = []
    for col, w, lab in (("r_min", pl.n, "MIN"), ("r_pts", pl.n, "PTS"), ("r_z", pl.n, "value z"),
                        ("fgimp", pl.fga_n, "FG%")):
        ok = X.notna().all(axis=1) & pl[col].notna() & (w > 0)
        if ok.sum() < X.shape[1] + 5:
            continue
        fit = wls(pl.loc[ok, col], X[ok], w[ok])
        for cov in ("team_excess", "rookie", "override", "moved", "age_c"):
            if cov in fit.params:
                F.append(finding(f"het.{cov}.{col}", "heterogeneity", lab, fit.params[cov], fit.bse[cov],
                                 {"team_excess": "team minute budget (scale projected team mpg toward 240)",
                                  "rookie": "rookie priors", "override": "manual overrides",
                                  "moved": "team-change adjustment", "age_c": "age curve"}[cov],
                                 n=int(ok.sum()), per="per +10 projected team min" if cov == "team_excess" else ""))
                het_rows.append({"covariate": cov, "outcome": lab, "estimate": fmt_pm(fit.params[cov], fit.bse[cov]),
                                 "t": f"{fit.tvalues[cov]:+.1f}"})
    tiers = []
    for k, sub in pl.groupby("adptier", observed=True):
        m, se = cluster_mean(sub.index, sub.r_min, sub.n)
        tiers.append(f"{k} {fmt_pm(m, se)} (n={len(sub)})")
    md += ["### 3. Heterogeneity (player-level WLS, weights = games, HC1; covariates tier, position, age, rookie, "
           "override, moved team, projected mpg, team projected-minute excess). ~20 tests shown: Bonferroni |t| > 3",
           md_table([r for r in het_rows if abs(float(r["t"])) >= 2] or het_rows[:4],
                    ["covariate", "outcome", "estimate", "t"]),
           "- Minutes residual by ADP tier: " + "; ".join(tiers),
           f"- Team projected minutes per game: SD {np.std(list(tmin_proj.values())):.0f}, range "
           f"{min(tmin_proj.values()):.0f}–{max(tmin_proj.values()):.0f} (should be ≈ 240)"]

    # ---- 5. regression to the mean
    rtm = []
    for a, p_, lab in (("z_act", "z_pro", "value"), ("min", "pmin", "MIN")):
        fit = wls(pl[a], add_const(pl[p_]), pl.n)
        rtm.append((lab, fit.params[p_], fit.bse[p_]))
    for s in ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov"):
        fit = wls(g[s].mean(), add_const(g["p_" + s].first()), g.size())
        rtm.append((s.upper(), fit.params.iloc[1], fit.bse.iloc[1]))
    for lab, b, se in rtm:
        F.append(finding(f"rtm.slope.{lab.lower()}", "regression_to_mean", lab, b, se,
                         "projections too extreme (shrink toward the mean)" if b < 1 else "projections too compressed",
                         null=1.0))
    md += ["### 4. Regression to the mean (slope of actual on projected; < 1 = too extreme)",
           ", ".join(f"{lab} {b:.2f} ± {se:.2f}" for lab, b, se in rtm)]

    # ---- 6. availability
    cnt = P.groupby("player_id").size()
    lastteam = pg.sort_values("game_date").groupby("player_id").team.last()
    av = pd.DataFrame(index=pre.index)
    av["team"] = lastteam.reindex(av.index).fillna(pre.team)
    av["k"] = av.team.map(team_n)
    av["share"] = (cnt.reindex(av.index).fillna(0) / av.k).clip(upper=1)
    av["p_pre"] = (pre.gp / 82).clip(upper=1)
    if ins is not None and "gp_preseason" in ins:
        av["p_cur"] = (ins.gp_preseason / 82).reindex(av.index).clip(upper=1)
    av["status"] = pre.status if "status" in pre else "healthy"
    av["gm"] = games_missed[~games_missed.index.duplicated()].reindex(av.index).fillna(0)
    av["age"] = pre.age
    av = av[av.k.notna()]
    clean = av[(av.status.fillna("healthy") == "healthy") & (av.gm == 0)]
    av.to_csv(out / "availability.csv")
    av_lines = []
    for col, lab in (("p_pre", f"preseason `{pre_sha}`"), ("p_cur", f"current prior `{asof}`")):
        if col not in av:
            continue
        for nm, sub in (("all", av), ("healthy", clean)):
            sub = sub[sub[col].notna()]
            d = sub.share - sub[col]
            se = d.std() / np.sqrt(len(d))
            F.append(finding(f"avail.{col}.{nm}", "availability", "gp_share", d.mean(), se,
                             "GP model level (expect early-season positive: injuries accumulate later)", n=len(sub)))
            av_lines.append(f"{lab} {nm}: {fmt_pm(d.mean(), se, 3)} (n={len(sub)})")
    col = "p_cur" if "p_cur" in av else "p_pre"
    spread = []
    for lo, hi in ((0, .55), (.55, .7), (.7, .8), (.8, 1.01)):
        sub = clean[(clean[col] >= lo) & (clean[col] < hi)]
        spread.append(f"[{lo:.2f},{hi:.2f}) {sub.share.mean():.2f} vs {sub[col].mean():.2f} (n={len(sub)})")
    old_ = clean[clean.age >= 33]
    d33 = old_.share - old_[col]
    md += ["### 5. Availability (games played / team games − projected gp/82; healthy = no status, no injury window)",
           "- " + "; ".join(av_lines),
           f"- Spread (healthy, {col}): actual vs projected share by projected bucket: " + "; ".join(spread)
           + f". Age 33+: {fmt_pm(d33.mean(), d33.std() / np.sqrt(max(len(d33), 1)), 3)} (n={len(d33)})."]

    # ---- 7. minutes gainers/losers, team reallocation
    m = pl[pl.n >= 4].copy()
    m["d"] = m["min"] - m.pmin
    if ins is not None:
        m["ins_mpg"] = ins.mpg.reindex(m.index)
    m.sort_values("d").to_csv(out / "minutes_changes.csv")

    def mline(df: pd.DataFrame) -> str:
        return ", ".join(f"{r['name']} {r.team_act} {r.pmin:.1f}→{r['min']:.1f}"
                         + (f" ({r.ins_mpg:.1f})" if "ins_mpg" in r and np.isfinite(r.ins_mpg) else "")
                         for _, r in df.iterrows())

    tmin = P.groupby("team")["min"].sum()
    trows = []
    for t in sorted(team_n.index):
        pj = pre[pre.team == t]
        exp_pm = (pj.mpg * pj.gp / 82).sum()
        alloc_p = (pj.mpg * pj.gp / 82) / exp_pm
        pa = P[P.team == t].groupby("player_id")["min"].sum() / tmin[t]
        allp = alloc_p.reindex(alloc_p.index.union(pa.index)).fillna(0)
        alla = pa.reindex(allp.index).fillna(0)
        dd = (alla - allp).sort_values()
        trows.append({"team": t, "games": int(team_n[t]), "proj_min_per_g": exp_pm, "act_min_per_g": tmin[t] / team_n[t],
                      "tvd": 0.5 * float(np.abs(allp - alla).sum()),
                      "unproj_share": float(pa[~pa.index.isin(pre.index)].sum()),
                      "up": f"{dd.index[-1]} {dd.iloc[-1]:+.0%}", "down": f"{dd.index[0]} {dd.iloc[0]:+.0%}"})
    T = pd.DataFrame(trows).sort_values("tvd", ascending=False)
    T.to_csv(out / "team_minutes.csv", index=False)
    md += ["### 6. Minutes (≥ 4 games; preseason mpg → actual (in-season mpg))",
           "- Gainers: " + mline(m.sort_values("d", ascending=False).head(10)),
           "- Losers: " + mline(m.sort_values("d").head(10)),
           "- Losers projected ≥ 25 mpg: " + mline(m[m.pmin >= 25].sort_values("d").head(7)),
           f"- Teams (TVD = share of minutes allocated differently from projection; mean {T.tvd.mean():.2f}): "
           + ", ".join(f"{r.team} {r.tvd:.2f} ({r.unproj_share:.0%} unproj.)" for r in T.head(8).itertuples())
           + "; most stable: " + ", ".join(f"{r.team} {r.tvd:.2f}" for r in T.tail(3).itertuples())]

    # ---- 8. over/under performers
    q = pl[pl.n >= 5].copy()
    q.sort_values("r_z").to_csv(out / "performers.csv")
    md += ["### 7. Value over/under (9-cat z per game vs preseason, ≥ 5 g)",
           "- Over: " + ", ".join(f"{r['name']} {r.r_z:+.1f}" for _, r in q.sort_values("r_z", ascending=False).head(6).iterrows())
           + " / Under: " + ", ".join(f"{r['name']} {r.r_z:+.1f}" + (" (rostered)" if i in rostered else "")
                                      for i, r in q.sort_values("r_z").head(6).iterrows())]

    # ---- 9. coverage + in-season sanity
    unm = sorted(set(P.player_id) - set(pre.index))
    um = P[P.player_id.isin(unm)].groupby("player_id")["min"].agg(["count", "sum"]).sort_values("sum", ascending=False)
    share = float(um["sum"].sum() / P["min"].sum())
    ins_unm = sorted(set(P.player_id) - set(ins.index)) if ins is not None else []
    ros_missing = sorted(rostered - set(ins.index)) if ins is not None else sorted(rostered - set(pre.index))
    um.to_csv(out / "unprojected_players.csv")
    F.append(finding("coverage.unprojected_minutes_share", "coverage", "min", share, None,
                     "population prior for unprojected players (in-season file should cover everyone with minutes)",
                     n_players=len(unm), n_inseason_missing=len(ins_unm), rostered_missing=ros_missing,
                     candidate_fix=bool(ros_missing) or share > 0.03))
    md += ["### 8. Coverage + in-season model",
           f"- {len(unm)} players with minutes have no preseason row ({share:.1%} of minutes); {len(ins_unm)} still "
           f"missing from the in-season file. Rostered without an in-season row: {ros_missing or 'none'}. Top: "
           + ", ".join(f"{i} {r['sum']:.0f}m/{r['count']}g" for i, r in um.head(6).iterrows())]
    if ins is not None and "inseason_as_of" in ins:
        as_of = str(ins.inseason_as_of.dropna().iloc[0])
        Pq = P[P.game_date < as_of]
        gq = Pq.groupby("player_id")
        obs = pd.DataFrame({"g": gq.size(), "M": gq["min"].sum(), "mpg": gq["min"].mean()})
        for s in ("reb", "ast", "fga", "stl"):
            obs[s] = gq[s].sum()
        both = obs.index.intersection(ins.index).intersection(cur.index)
        o, c, i_ = obs.loc[both], cur.loc[both], ins.loc[both]
        try:
            from fantasy.model.inseason import STAT_N0
        except Exception:  # noqa: BLE001 - optional (another agent may be editing the model)
            STAT_N0 = {}
        parts = []
        for s in ("reb", "ast", "fga", "stl"):
            if s not in STAT_N0:
                continue
            r_p, r_o, r_i = c[s] / c.mpg, o[s] / o.M, i_[s] / i_.mpg
            sel = (np.abs(r_o - r_p) > 0.05 * r_p) & (o.M > 100)
            w_imp = ((r_i - r_p) / (r_o - r_p))[sel]
            w_th = (o.M / (STAT_N0[s] + o.M))[sel]
            parts.append(f"{s} {w_imp.median():.2f} vs M/(n0+M) {w_th.median():.2f}")
        dm_o, dm_i = o.mpg - c.mpg, i_.mpg - c.mpg
        sel = (o.g >= 4) & (np.abs(dm_o) > 3)
        mv = float(np.polyfit(dm_o[sel], dm_i[sel], 1)[0]) if sel.sum() > 5 else np.nan
        z0 = ins[(ins.games_played == 0) & (ins.team_games >= 3)] if "games_played" in ins else ins.iloc[:0]
        md += [f"- In-season file ({ins.inseason_version.dropna().iloc[0] if 'inseason_version' in ins else '?'}, as of "
               f"{as_of}): median weight on observed per-minute rates: " + "; ".join(parts)
               + f". Minutes move {mv:.2f} of the observed gap (≥ 4 g, |gap| > 3). {len(z0)} players at 0 GP after ≥ 3 "
               f"team games; rostered among them: {sorted(set(z0.index) & rostered) or 'none'}."]
    return md, F, head
