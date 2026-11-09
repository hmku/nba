"""Projection bias audit, 2026-27 weeks 1-3 (games 2026-10-20..2026-11-08). Read-only on the repo.

Inputs: preseason projections at d295766 (draft-time default = 50/50 ours/ESPN blend; base = ours), ESPN raw
2026-27 projection (component), gp-ols-0.2 GP from da3a51d, ESPN box scores 2027, current in-season projection.
Tests use the fitted per-game noise var a*m + b*m^2 (fantasy.model.noise) plus cluster-by-player SEs.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/harrisonku/dev/nba/src")
from fantasy.model.noise import game_var  # noqa: E402

REPO = "/Users/harrisonku/dev/nba"
OUT = "/private/tmp/claude-501/-Users-harrisonku-dev-nba/26127dfb-08f4-48a4-83aa-e25b95986212/scratchpad/bias"
STATS = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fgm", "fga", "ftm", "fta"]
pd.set_option("display.width", 220, "display.max_columns", 40, "display.max_rows", 200)


def gitcsv(sha, path):
    from io import StringIO
    return pd.read_csv(StringIO(subprocess.check_output(["git", "-C", REPO, "show", f"{sha}:{path}"], text=True)),
                       comment=None)


def slug(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))
    return "-".join(s.split())


pre = gitcsv("d295766", "data/processed/projections_2026_27.csv")
base = gitcsv("d295766", "data/processed/projections_2026_27.base.csv")
gp02 = gitcsv("da3a51d", "data/processed/projections_2026_27.csv")
espn = gitcsv("d295766", "data/raw/espn/projections_2026_27.csv")
ins = pd.read_csv(f"{REPO}/data/processed/projections_2026_27.inseason.csv")
cur_prior = pd.read_csv(f"{REPO}/data/processed/projections_2026_27.csv")
pg = pd.read_csv(f"{REPO}/data/raw/espn_boxscores/player_games_2027.csv.gz")
tg = pd.read_csv(f"{REPO}/data/raw/espn_boxscores/team_games_2027.csv.gz")
pg = pg[pg.regular_season]
P = pg[pg.played].copy()
for s in STATS + ["min"]:
    P[s] = P[s].astype(float)

rep: list[str] = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    rep.append(s)


say(f"box scores {pg.game_date.min()}..{pg.game_date.max()}: {pg.game_id.nunique()} games, {len(P)} player-games "
    f"played, {P.player_id.nunique()} players")

# ---------------------------------------------------------------- matching
proj_ids = set(pre.player_id)
played_ids = set(P.player_id)
unm = sorted(played_ids - proj_ids)
pre_slug = {slug(n): pid for n, pid in zip(pre.name, pre.player_id)}
name_fix = {}
for pid in unm:
    nm = P.loc[P.player_id == pid, "player_name"].iloc[0]
    cand = pre_slug.get(slug(re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", nm, flags=re.I)))
    if cand and cand not in played_ids:
        name_fix[pid] = cand
umin = P[P.player_id.isin(unm)].groupby("player_id")["min"].agg(["count", "sum"]).sort_values("sum", ascending=False)
ros = json.load(open(f"{REPO}/data/manual/league_rosters_2026-27.json"))
rostered = {p for t in ros["teams"].values() for p in t["players"]}
say(f"played, no preseason row: {len(unm)} players, {umin['sum'].sum():.0f} min "
    f"({umin['sum'].sum() / P['min'].sum():.1%} of all minutes); possible name mismatches: {name_fix or 'none'}")
say(f"  top unmatched by minutes: " + ", ".join(f"{i} {r['sum']:.0f}m/{r['count']}g" for i, r in umin.head(8).iterrows()))
say(f"  rostered (Yahoo, 12 teams) without a preseason row: {sorted(rostered - proj_ids)}; "
    f"rostered without an in-season row: {sorted(rostered - set(ins.player_id))}")
ins_unm = sorted(played_ids - set(ins.player_id))
say(f"  in-season file: {len(ins_unm)} played-but-unprojected; rostered among them: "
    f"{sorted(set(ins_unm) & rostered)}")

# ---------------------------------------------------------------- per-game residual frame
def residual_frame(proj, label):
    pj = proj.set_index("player_id")
    d = P[P.player_id.isin(pj.index)].copy()
    d["pmin"] = d.player_id.map(pj["mpg"]).astype(float)
    for s in STATS:
        d["p_" + s] = d.player_id.map(pj[s]).astype(float)
        d["r_" + s] = d[s] - d["p_" + s]
        rate = np.where(d.pmin > 0, d["p_" + s] / d.pmin, 0)
        d["rr_" + s] = d[s] - rate * d["min"]              # rate error (at actual minutes)
        d["rm_" + s] = rate * (d["min"] - d.pmin)          # minutes error
        d["v_" + s] = game_var(s, d["p_" + s].clip(lower=0))
    d["r_min"] = d["min"] - d.pmin
    d["label"] = label
    return d


def cluster_mean(d, col, w=None):
    """mean of col over player-games, SE clustered by player."""
    w = np.ones(len(d)) if w is None else np.asarray(w, float)
    x = d[col].to_numpy(float)
    mu = np.sum(w * x) / w.sum()
    g = pd.DataFrame({"p": d.player_id.values, "e": w * (x - mu)}).groupby("p")["e"].sum()
    se = np.sqrt(np.sum(g ** 2)) / w.sum() * np.sqrt(len(g) / (len(g) - 1))
    return mu, se


def bias_table(d, title):
    say(f"\n## {title}: n={len(d)} player-games, {d.player_id.nunique()} players")
    say(f"{'stat':5} {'proj/g':>7} {'bias/g':>7} {'SE':>6} {'t':>5} {'%':>6} | {'rate':>6} {'SE':>5} | "
        f"{'mins':>6} {'SE':>5} | {'z_noise':>7} {'zSE':>5} {'Ez2':>5}")
    rows = []
    for s in STATS:
        mu, se = cluster_mean(d, "r_" + s)
        mr, ser = cluster_mean(d, "rr_" + s)
        mm, sem = cluster_mean(d, "rm_" + s)
        # noise-model test: player-level sum residual / sqrt(sum var); z ~ N(0,1) if unbiased & only game noise
        g = d.groupby("player_id").agg(r=("r_" + s, "sum"), v=("v_" + s, "sum"))
        z = g.r / np.sqrt(g.v.clip(lower=1e-9))
        # pooled league z: sum r / sqrt(sum v)  (pure game-noise SE, ignores projection-error variance)
        zpool = g.r.sum() / np.sqrt(g.v.sum())
        pm = d["p_" + s].mean()
        say(f"{s:5} {pm:7.3f} {mu:+7.3f} {se:6.3f} {mu / se:+5.1f} {mu / pm:+6.1%} | {mr:+6.3f} {ser:5.3f} | "
            f"{mm:+6.3f} {sem:5.3f} | {zpool:+7.1f} {1:5.0f} {np.mean(z ** 2):5.2f}")
        rows.append(dict(stat=s, proj=pm, bias=mu, se=se, t=mu / se, pct=mu / pm, rate=mr, rate_se=ser,
                         mins=mm, mins_se=sem, zpool=zpool, ez2=np.mean(z ** 2)))
    mu, se = cluster_mean(d, "r_min")
    say(f"{'min':5} {d.pmin.mean():7.2f} {mu:+7.2f} {se:6.2f} {mu / se:+5.1f} {mu / d.pmin.mean():+6.1%}")
    # percentages from makes/attempts
    for mk, at in (("fgm", "fga"), ("ftm", "fta")):
        pa = d[mk].sum() / d[at].sum()
        pp = d["p_" + mk].sum() / d["p_" + at].sum()
        pj = d.player_id.map(d.groupby("player_id")["p_" + mk].first() / d.groupby("player_id")["p_" + at].first())
        d["imp_" + mk] = d[mk] - pj * d[at]                 # makes over projected % on ACTUAL attempts
        d["vimp_" + mk] = d[at] * pj * (1 - pj)
        mu, se = cluster_mean(d, "imp_" + mk)
        att = d[at].mean()
        g = d.groupby("player_id").agg(r=("imp_" + mk, "sum"), v=("vimp_" + mk, "sum"))
        zpool = g.r.sum() / np.sqrt(g.v.sum())
        say(f"{mk[:2]}%   actual {pa:.4f} vs proj {pp:.4f} (diff {pa - pp:+.4f}); at actual attempts: "
            f"{mu / att:+.4f} ± {se / att:.4f} (t {mu / se:+.1f}; binomial z_pool {zpool:+.1f})")
        rows.append(dict(stat=mk[:2] + "%", proj=pp, bias=mu / att, se=se / att, t=mu / se, zpool=zpool))
    return pd.DataFrame(rows)


dB = residual_frame(pre, "blend")
tb = bias_table(dB, "Preseason BLEND (d295766, draft default) per game played")
tb.to_csv(f"{OUT}/bias_blend.csv", index=False)
dO = residual_frame(base, "ours")
to = bias_table(dO, "Preseason OURS only (base.csv, d295766)")
espn2 = espn[espn.player_id.isin(pre.player_id)]
dE = residual_frame(espn2, "espn")
te = bias_table(dE, "ESPN 2026-27 projection (component)")
# same sample for all three
common = set(dB.player_id) & set(dO.player_id) & set(dE.player_id)
say(f"\ncommon sample {len(common)} players; bias/g (t) by source on the common sample:")
for s in STATS + ["min"]:
    parts = []
    for d in (dB, dO, dE):
        mu, se = cluster_mean(d[d.player_id.isin(common)], "r_" + s)
        parts.append(f"{mu:+.3f} ({mu / se:+.1f})")
    say(f"  {s:5} blend {parts[0]:>15} ours {parts[1]:>15} espn {parts[2]:>15}")
dB.to_pickle(f"{OUT}/dB.pkl")

# minutes-weighted rate bias per 36
say("\nper-36 rate bias (blend), minutes-weighted over player-games:")
for s in STATS:
    rate = np.where(dB.pmin > 0, dB["p_" + s] / dB.pmin, 0)
    mu, se = cluster_mean(dB.assign(x=dB[s] / dB["min"].clip(lower=1) - rate), "x", w=dB["min"])
    say(f"  {s:5} {36 * mu:+.3f} ± {36 * se:.3f}  (proj per36 {36 * np.average(rate, weights=dB['min']):.2f})")

# player-level (equal weight per player) bias
say("\nplayer-level (each player's mean residual, equal weight, players with >=3 g):")
gpl = dB.groupby("player_id")
cnt = gpl.size()
keep = cnt[cnt >= 3].index
for s in STATS + ["min"]:
    m = gpl["r_" + s].mean().loc[keep]
    say(f"  {s:5} {m.mean():+.3f} ± {m.std() / np.sqrt(len(m)):.3f}  (n={len(m)})")
open(f"{OUT}/part1.txt", "w").write("\n".join(rep))
