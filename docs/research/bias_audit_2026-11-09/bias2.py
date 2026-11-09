"""Part 2: heterogeneity, regression to the mean, availability, minutes (players + teams), performers, in-season."""
from __future__ import annotations

import re
import subprocess
import unicodedata
from io import StringIO

import numpy as np
import pandas as pd

REPO = "/Users/harrisonku/dev/nba"
OUT = "/private/tmp/claude-501/-Users-harrisonku-dev-nba/26127dfb-08f4-48a4-83aa-e25b95986212/scratchpad/bias"
pd.set_option("display.width", 220, "display.max_columns", 40, "display.max_rows", 200)
rep: list[str] = []


class _Fit:
    pass


def wls(y, X, w):
    """WLS with HC1 sandwich SEs; X DataFrame (with const)."""
    Xv = np.asarray(X, float); yv = np.asarray(y, float); wv = np.asarray(w, float)
    XtW = Xv.T * wv
    A = np.linalg.pinv(XtW @ Xv)
    b = A @ XtW @ yv
    e = yv - Xv @ b
    meat = (Xv.T * (wv * e) ** 2) @ Xv
    n, k = Xv.shape
    V = A @ meat @ A * n / (n - k)
    f = _Fit()
    f.params = pd.Series(b, index=X.columns)
    f.bse = pd.Series(np.sqrt(np.diag(V)), index=X.columns)
    f.tvalues = f.params / f.bse
    return f


def add_const(X):
    X = pd.DataFrame(X).copy()
    X.insert(0, "const", 1.0)
    return X


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    rep.append(s)


def gitcsv(sha, path):
    return pd.read_csv(StringIO(subprocess.check_output(["git", "-C", REPO, "show", f"{sha}:{path}"], text=True)))


def slug(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))
    return "-".join(s.split())


pre = gitcsv("d295766", "data/processed/projections_2026_27.csv").set_index("player_id")
gp02 = gitcsv("da3a51d", "data/processed/projections_2026_27.csv").set_index("player_id")
ins = pd.read_csv(f"{REPO}/data/processed/projections_2026_27.inseason.csv").set_index("player_id")
cur = pd.read_csv(f"{REPO}/data/processed/projections_2026_27.csv").set_index("player_id")
dB = pd.read_pickle(f"{OUT}/dB.pkl")
pg = pd.read_csv(f"{REPO}/data/raw/espn_boxscores/player_games_2027.csv.gz")
pg = pg[pg.regular_season]
P = pg[pg.played].copy()
tg = pd.read_csv(f"{REPO}/data/raw/espn_boxscores/team_games_2027.csv.gz")
tg = tg[tg.regular_season]
team_n = tg.groupby("team").game_id.nunique()

rook = pd.read_csv(f"{REPO}/data/manual/rookies_2026_27.csv", comment="#")
rook_ids = {slug(n) for n in rook.name}
ov = pd.read_csv(f"{REPO}/data/manual/player_overrides_2026_27.csv", comment="#")
ov["pid"] = ov.name.map(slug)
ov_ids = set(ov.pid)
ov_trade = set(ov.loc[ov.note.fillna("").str.contains("trade|signed|traded", case=False), "pid"])
ov_inj = set(ov.loc[ov.games_missed.notna() | ov.status.isin(["out", "gtd", "out_for_season"]), "pid"])
say(f"overrides: {len(ov_ids)} names, {len(ov_ids & set(pre.index))} match projection ids "
    f"(unmatched: {sorted(ov_ids - set(pre.index))[:10]}); rookies {len(rook_ids & set(pre.index))}/{len(rook_ids)}")

# ------------------------------------------------- per-game value (z-sum, 9 cats) using projected-top-200 scale
top = pre.sort_values("mpg", ascending=False).head(200)
pfg = top.fgm.sum() / top.fga.sum()
pft = top.ftm.sum() / top.fta.sum()
CNT = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"]
sd = {c: top[c].std() for c in CNT}
sd["fg"] = (top.fgm - pfg * top.fga).std()
sd["ft"] = (top.ftm - pft * top.fta).std()


def zval(df, pfx=""):
    v = sum(df[pfx + c] / sd[c] * (-1 if c == "tov" else 1) for c in CNT)
    v = v + (df[pfx + "fgm"] - pfg * df[pfx + "fga"]) / sd["fg"] + (df[pfx + "ftm"] - pft * df[pfx + "fta"]) / sd["ft"]
    return v


dB["z_act"] = zval(dB)
dB["z_pro"] = zval(dB, "p_")
dB["r_z"] = dB.z_act - dB.z_pro
pre["zpg"] = zval(pre)
pre["vrank"] = pre.zpg.rank(ascending=False)
pre["adprank"] = pre.adp.rank()

# ------------------------------------------------- player-level frame
g = dB.groupby("player_id")
pl = pd.DataFrame({"n": g.size(), "min": g["min"].mean(), "pmin": g.pmin.first(), "starter": g.starter.mean(),
                   "team_act": g.team.agg(lambda s: s.iloc[-1])})
for c in ["r_pts", "r_reb", "r_ast", "r_fg3m", "r_min", "r_z", "rr_pts", "rm_pts", "z_act", "z_pro"]:
    pl[c] = g[c].mean()
pl["fgimp"] = g.imp_fgm.sum() / g.fga.sum()  # FG% residual at actual attempts
pl = pl.join(pre[["name", "team", "age", "positions", "zpg", "vrank", "adp", "adprank", "gp", "mpg"]])
pl["rookie"] = pl.index.isin(rook_ids).astype(int)
pl["override"] = pl.index.isin(ov_ids).astype(int)
pl["ov_trade"] = pl.index.isin(ov_trade).astype(int)
pl["moved"] = (pl.team != pl.team_act).astype(int)
pl["pos"] = pl.positions.str.split("|").str[0].map({"PG": "G", "SG": "G", "SF": "F", "PF": "F", "C": "C"})
pl["tier"] = pd.cut(pl.vrank, [0, 50, 150, 1e9], labels=["top50", "51-150", "rest"])
pl["adptier"] = pd.cut(pl.adprank.fillna(999), [0, 50, 150, 1e9], labels=["top50", "51-150", "rest/undrafted"])
pl.to_csv(f"{OUT}/player_level.csv")


def wmean(df, col):
    w = df.n
    m = np.average(df[col], weights=w)
    # SE: player is the unit; weighted mean, sandwich
    se = np.sqrt(np.sum(w ** 2 * (df[col] - m) ** 2)) / w.sum()
    return m, se


say(f"\n## Heterogeneity (player level, weights = games; n={len(pl)} players). Cells: mean resid ± SE")
for grp in ["tier", "adptier", "pos", "rookie", "override", "ov_trade", "moved"]:
    parts = []
    for k, sub in pl.groupby(grp, observed=True):
        if len(sub) < 3:
            continue
        cells = []
        for col in ["r_pts", "r_min", "rr_pts", "fgimp", "r_z"]:
            m, se = wmean(sub, col)
            cells.append(f"{col}={m:+.2f}±{se:.2f}" if col != "fgimp" else f"fg%={m:+.3f}±{se:.3f}")
        parts.append(f"  {grp}={k} (n={len(sub)}): " + " ".join(cells))
    say("\n".join(parts))
for lo, hi in ((18, 23), (23, 27), (27, 31), (31, 45)):
    sub = pl[(pl.age >= lo) & (pl.age < hi)]
    cells = [f"{c}={wmean(sub, c)[0]:+.2f}±{wmean(sub, c)[1]:.2f}" for c in ["r_pts", "r_min", "rr_pts", "r_z"]]
    say(f"  age [{lo},{hi}) (n={len(sub)}): " + " ".join(cells))
sub_s = dB.groupby("starter")
for k, sub in sub_s:
    say(f"  starter={k} games={len(sub)}: r_pts {sub.r_pts.mean():+.2f} r_min {sub.r_min.mean():+.2f} "
        f"rr_pts {sub.rr_pts.mean():+.2f} (game-level; started-games are selection on role)")

say("\n## Joint WLS of player mean residual on covariates (weights=games, HC1 SE)")
X = pd.get_dummies(pl[["tier", "pos"]], drop_first=True).astype(float)
X["age_c"] = pl.age - 27
X["rookie"] = pl.rookie
X["override"] = pl.override
X["moved"] = pl.moved
X["starter"] = pl.starter
X["pmin_c"] = pl.pmin - 25
X = add_const(X)
ok = X.notna().all(axis=1)
for col in ["r_z", "r_pts", "r_min", "rr_pts", "fgimp"]:
    y = pl.loc[ok, col]
    fit = wls(y, X[ok], pl.loc[ok, "n"])
    sig = [f"{k}={fit.params[k]:+.2f}(t{fit.tvalues[k]:+.1f})" for k in fit.params.index]
    say(f"  {col}: " + " ".join(sig))
say("  (11 covariates x 5 outcomes = 55 tests: Bonferroni 5% ~ |t|>3.3; note 'starter' is endogenous: role gained)")

say("\n## Regression to the mean: player mean actual on projected (WLS, weights = games, HC1)")
for a, p_ in (("z_act", "z_pro"), ("min", "pmin")):
    fit = wls(pl[a], add_const(pl[p_]), pl.n)
    say(f"  {a} ~ {p_}: slope {fit.params[p_]:.3f} ± {fit.bse[p_]:.3f}, intercept {fit.params['const']:+.2f} "
        f"(slope<1 = projections spread too wide / not shrunk enough)")
for s in ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"]:
    a = g[s].mean()
    p_ = g["p_" + s].first()
    fit = wls(a, add_const(p_), g.size())
    say(f"  {s}: slope {fit.params.iloc[1]:.3f} ± {fit.bse.iloc[1]:.3f}")
# per-minute rate slope (separates minutes from rate)
for s in ["pts", "reb", "ast"]:
    a = g[s].sum() / g["min"].sum()
    p_ = g["p_" + s].first() / g.pmin.first()
    w = g["min"].sum()
    fit = wls(a, add_const(p_), w)
    say(f"  {s}/min rate: slope {fit.params.iloc[1]:.3f} ± {fit.bse.iloc[1]:.3f}")
# small-sample attenuation check: noise in the actual (LHS) does not bias the slope; the projection is the RHS.

# ------------------------------------------------- availability
say("\n## Availability: actual share of team games played vs projected gp/82")
cnt = P.groupby("player_id").size()
lastteam = pg.sort_values("game_date").groupby("player_id").team.last()
av = pd.DataFrame(index=pre.index)
av["team"] = lastteam.reindex(av.index).fillna(pre.team)
av["k"] = av.team.map(team_n)
av["x"] = cnt.reindex(av.index).fillna(0)
av["share"] = (av.x / av.k).clip(upper=1)
for lab, src in (("d295766 (draft)", pre), ("gp-ols-0.2 da3a51d", gp02)):
    av["p_" + lab] = (src.gp / 82).reindex(av.index)
av["status"] = pre.status
av["age"] = pre.age
gm = ov.set_index("pid").games_missed.dropna()
av["gm"] = gm.reindex(av.index).fillna(0)
clean = av[(av.status.fillna("healthy") == "healthy") & (av.gm == 0) & av.k.notna()]
for lab in ("d295766 (draft)", "gp-ols-0.2 da3a51d"):
    for nm, sub in (("all", av[av.k.notna()]), ("healthy, no injury window", clean)):
        d = sub.share - sub["p_" + lab]
        say(f"  {lab:20} {nm:26} n={len(sub)}: share {sub.share.mean():.3f} vs proj {sub['p_' + lab].mean():.3f}: "
            f"diff {d.mean():+.3f} ± {d.std() / np.sqrt(len(d)):.3f}")
lab = "gp-ols-0.2 da3a51d"
for lo, hi in ((18, 25), (25, 30), (30, 33), (33, 45)):
    sub = clean[(clean.age >= lo) & (clean.age < hi)]
    d = sub.share - sub["p_" + lab]
    say(f"    healthy age [{lo},{hi}) n={len(sub)}: diff {d.mean():+.3f} ± {d.std() / np.sqrt(len(d)):.3f}")
for lo, hi in ((0, .55), (.55, .7), (.7, .8), (.8, 1.01)):
    sub = clean[(clean["p_" + lab] >= lo) & (clean["p_" + lab] < hi)]
    d = sub.share - sub["p_" + lab]
    say(f"    healthy proj share [{lo},{hi}) n={len(sub)}: actual {sub.share.mean():.3f} proj "
        f"{sub['p_' + lab].mean():.3f} diff {d.mean():+.3f} ± {d.std() / np.sqrt(len(d)):.3f}")
inj = av[(av.gm > 0) & av.k.notna()]
exp_inj = (av.loc[inj.index, "p_" + lab] * 82 / (82 - inj.gm)) * ((inj.k - inj.gm).clip(lower=0) / inj.k)
d = inj.share - exp_inj
say(f"  injury-window players n={len(inj)}: actual share {inj.share.mean():.3f} vs expected in-window "
    f"{exp_inj.mean():.3f}: diff {d.mean():+.3f} ± {d.std() / np.sqrt(len(d)):.3f}")
# historical control: early-season share vs full-season share (2025-26)
h = pd.read_csv(f"{REPO}/data/raw/espn_boxscores/player_games_2026.csv.gz")
h = h[h.regular_season]
h["game_date"] = pd.to_datetime(h.game_date)
th = pd.read_csv(f"{REPO}/data/raw/espn_boxscores/team_games_2026.csv.gz")
th = th[th.regular_season]
th["game_date"] = pd.to_datetime(th.game_date)
cut = th.game_date.min() + pd.Timedelta(days=20)
hp = h[h.played]
early = hp[hp.game_date < cut].groupby("player_id").size()
full = hp.groupby("player_id").size()
tm = hp.groupby("player_id").team.agg(lambda s: s.mode().iloc[0])
ke = th[th.game_date < cut].groupby("team").game_id.nunique()
reg = full[full >= 41].index  # rotation-ish players, conditional on a full-ish season
e_sh = (early.reindex(reg).fillna(0) / tm[reg].map(ke)).clip(upper=1)
f_sh = full[reg] / 82
say(f"  2025-26 control (players with >=41 g): first-20-day share {e_sh.mean():.3f} vs season share "
    f"{f_sh.mean():.3f} (diff {(e_sh - f_sh).mean():+.3f}); conditioning on >=41 g, indicative only")
av.to_csv(f"{OUT}/availability.csv")

# ------------------------------------------------- minutes gainers/losers
say("\n## Minutes vs preseason projection (players with >= 4 games)")
m = pl[pl.n >= 4].copy()
m["d"] = m["min"] - m.pmin
m["ins_mpg"] = ins.mpg.reindex(m.index)
cols = ["name", "team_act", "n", "pmin", "min", "d", "ins_mpg", "starter"]
fmt = lambda df: df[cols].round(1).to_string()  # noqa: E731
say("Top-10 gainers:\n" + fmt(m.sort_values("d", ascending=False).head(10)))
say("Top-10 losers:\n" + fmt(m.sort_values("d").head(10)))
# losers among likely-relevant players (proj mpg >= 25)
say("Top-10 losers with projected mpg >= 25:\n" + fmt(m[m.pmin >= 25].sort_values("d").head(10)))

# ------------------------------------------------- team minutes
say("\n## Team minutes: projected allocation vs actual")
tmin = P.groupby("team")["min"].sum()
rows = []
for t in sorted(team_n.index):
    pj = pre[pre.team == t]
    exp_pm = (pj.mpg * pj.gp / 82).sum()               # projected minutes per team game
    alloc_p = (pj.mpg * pj.gp / 82) / exp_pm
    pa = P[P.team == t].groupby("player_id")["min"].sum() / tmin[t]
    allp = alloc_p.reindex(alloc_p.index.union(pa.index)).fillna(0)
    alla = pa.reindex(allp.index).fillna(0)
    tvd = 0.5 * np.abs(allp - alla).sum()
    unproj = pa[~pa.index.isin(pre.index)].sum()
    # biggest single shifts
    dd = (alla - allp).sort_values()
    rows.append(dict(team=t, games=team_n[t], proj_min_per_g=exp_pm, act_min_per_g=tmin[t] / team_n[t],
                     tvd=tvd, unproj_share=unproj,
                     up=f"{dd.index[-1]} {dd.iloc[-1]:+.0%}", down=f"{dd.index[0]} {dd.iloc[0]:+.0%}"))
T = pd.DataFrame(rows).sort_values("tvd", ascending=False)
T.to_csv(f"{OUT}/team_minutes.csv", index=False)
say(T.round(3).to_string(index=False))
say(f"mean TVD {T.tvd.mean():.3f}; projected team minutes/g {T.proj_min_per_g.mean():.1f} vs actual "
    f"{T.act_min_per_g.mean():.1f}")

# ------------------------------------------------- performers
say("\n## Biggest per-game fantasy over/under-performers (9-cat z-sum per game vs preseason, >= 5 games)")
q = pl[pl.n >= 5].copy()
q["ins_z"] = zval(ins.reindex(q.index))
c2 = ["name", "team_act", "n", "vrank", "z_pro", "z_act", "r_z", "ins_z", "pmin", "min"]
say("Over:\n" + q.sort_values("r_z", ascending=False).head(10)[c2].round(2).to_string())
say("Under:\n" + q.sort_values("r_z").head(10)[c2].round(2).to_string())

# ------------------------------------------------- in-season sanity
say("\n## In-season (ros-proj-0.5.0, as_of 2026-11-08, box scores <= 2026-11-07) sanity")
Pq = P[P.game_date <= "2026-11-07"]
gq = Pq.groupby("player_id")
obs = pd.DataFrame({"g": gq.size(), "M": gq["min"].sum(), "mpg": gq["min"].mean()})
for s in ["pts", "reb", "ast", "fga", "stl"]:
    obs[s] = gq[s].sum()
both = obs.index.intersection(ins.index).intersection(cur.index)
o = obs.loc[both]
c = cur.loc[both]
i = ins.loc[both]
n0 = {"reb": 433.2, "ast": 288.8, "fga": 205.8, "stl": 1398.6}
for s in ["reb", "ast", "fga", "stl"]:
    r_p = c[s] / c.mpg
    r_o = o[s] / o.M
    r_i = i[s] / i.mpg
    sel = (np.abs(r_o - r_p) > 0.05 * r_p) & (o.M > 100)
    w_imp = ((r_i - r_p) / (r_o - r_p))[sel]
    w_th = (o.M / (n0[s] + o.M))[sel]
    say(f"  {s}/min weight on observed: implied median {w_imp.median():.3f} vs formula M/(n0+M) median "
        f"{w_th.median():.3f} (n={sel.sum()}; max |diff| {np.abs(w_imp - w_th).max():.3f})")
dm_o = o.mpg - c.mpg
dm_i = i.mpg - c.mpg
sel = (o.g >= 4) & (np.abs(dm_o) > 3)
say(f"  minutes: for |obs-prior| > 3 mpg & >= 4 g (n={sel.sum()}): slope of (ins-prior) on (obs-prior) "
    f"{np.polyfit(dm_o[sel], dm_i[sel], 1)[0]:.2f}; median ratio {(dm_i[sel] / dm_o[sel]).median():.2f}")
sel1 = (o.g <= 2) & (np.abs(dm_o) > 8)
say(f"  1-2 game flukes with |obs-prior mpg| > 8 (n={sel1.sum()}): median moved share "
    f"{(dm_i[sel1] / dm_o[sel1]).median():.2f}; examples: " + ", ".join(
        f"{p} g{o.g[p]} prior {c.mpg[p]:.1f} obs {o.mpg[p]:.1f} ins {i.mpg[p]:.1f}" for p in dm_o[sel1].abs().sort_values(ascending=False).index[:4]))
# zero-GP players and unlisted
z0 = ins[(ins.games_played == 0) & (ins.team_games >= 3)]
say(f"  players with 0 GP after >=3 team games: {len(z0)}; their stats unchanged vs prior: "
    f"{np.allclose(z0.pts, cur.pts.reindex(z0.index), atol=1e-3)}; gp moved {z0.gp_preseason.mean():.1f} -> "
    f"{z0.gp.mean():.1f}")
say(f"    rostered among them: {sorted(set(z0.index) & set(__import__('json').load(open(REPO + '/data/manual/league_rosters_2026-27.json'))['teams'] and {p for t in __import__('json').load(open(REPO + '/data/manual/league_rosters_2026-27.json'))['teams'].values() for p in t['players']}))}")
# ins vs observed per-game z: do outperformers get moved?
q2 = q[q.n >= 5]
fit = np.polyfit(q2.r_z, q2.ins_z - zval(cur.reindex(q2.index)), 1)
say(f"  value: slope of (ins z - prior z) on (obs z - preseason z) over players >= 5 g: {fit[0]:.2f}")
open(f"{OUT}/part2.txt", "w").write("\n".join(rep))
