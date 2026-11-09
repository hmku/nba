"""Part 3: team minute budget as a predictor, spec without endogenous starter, FG% heterogeneity, availability slope
vs a 2025-26 control (ESPN preseason gp)."""
import numpy as np, pandas as pd, subprocess
from io import StringIO
exec(open(__file__.replace("bias3.py", "bias2.py")).read().split("pre = gitcsv")[0])  # helpers (wls, add_const, say)
REPO = "/Users/harrisonku/dev/nba"
pre = gitcsv("d295766", "data/processed/projections_2026_27.csv").set_index("player_id")
pl = pd.read_csv(f"{OUT}/player_level.csv", index_col=0)
T = pd.read_csv(f"{OUT}/team_minutes.csv").set_index("team")
dB = pd.read_pickle(f"{OUT}/dB.pkl")
pl["team_excess"] = pl.team.map(T.proj_min_per_g - 240) / 10   # per +10 projected team minutes over 240
pl["fgimp"] = dB.groupby("player_id").imp_fgm.sum() / dB.groupby("player_id").fga.sum().replace(0, np.nan)
pl["fga_n"] = dB.groupby("player_id").fga.sum()
X = pd.get_dummies(pl[["tier", "pos"]], drop_first=True).astype(float)
X["age_c"] = pl.age - 27; X["rookie"] = pl.rookie; X["override"] = pl.override; X["moved"] = pl.moved
X["pmin_c"] = pl.pmin - 25; X["team_excess"] = pl.team_excess
X = add_const(X)
say("## WLS without 'starter' (+ team projected-minute excess per 10 min over 240), weights = games, HC1")
for col, w in (("r_min", pl.n), ("r_pts", pl.n), ("rr_pts", pl.n), ("r_z", pl.n), ("fgimp", pl.fga_n)):
    ok = X.notna().all(axis=1) & pl[col].notna()
    f = wls(pl.loc[ok, col], X[ok], w[ok])
    say(f"  {col}: " + " ".join(f"{k}={f.params[k]:+.3f}(t{f.tvalues[k]:+.1f})" for k in f.params.index))
f = wls(pl.r_min, add_const(pl[["team_excess"]]), pl.n)
say(f"  r_min ~ team_excess alone: {f.params['team_excess']:+.2f} mpg per +10 team min (t {f.tvalues['team_excess']:+.1f}); "
    f"team proj minutes/g: sd {T.proj_min_per_g.std():.1f}, range {T.proj_min_per_g.min():.0f}-{T.proj_min_per_g.max():.0f}")
# FG% by tier with valid fga
for k, sub in pl[pl.fgimp.notna()].groupby("tier"):
    m = np.average(sub.fgimp, weights=sub.fga_n); se = np.sqrt(np.sum(sub.fga_n**2*(sub.fgimp-m)**2))/sub.fga_n.sum()
    say(f"  FG% resid tier {k}: {m:+.4f} ± {se:.4f} (n={len(sub)})")
# availability slope, this season vs 2025-26 with ESPN preseason gp as the projection
av = pd.read_csv(f"{OUT}/availability.csv", index_col=0)
c = av[(av.status.fillna('healthy') == 'healthy') & (av.gm == 0) & av.k.notna() & av['p_gp-ols-0.2 da3a51d'].notna()]
f = wls(c.share, add_const(c[["p_gp-ols-0.2 da3a51d"]]), np.ones(len(c)))
say(f"## availability slope 2026-27 (healthy, gp-ols-0.2): share = {f.params.iloc[0]:+.2f} + {f.params.iloc[1]:.2f}·p (± {f.bse.iloc[1]:.2f}), n={len(c)}")
for yr, fn in ((2026, "projections_2025_26"), (2027, "projections_2026_27")):
    e = pd.read_csv(f"{REPO}/data/raw/espn/{fn}.csv").set_index("player_id")
    h = pd.read_csv(f"{REPO}/data/raw/espn_boxscores/player_games_{yr}.csv.gz"); h = h[h.regular_season]
    t = pd.read_csv(f"{REPO}/data/raw/espn_boxscores/team_games_{yr}.csv.gz"); t = t[t.regular_season]
    h["game_date"] = pd.to_datetime(h.game_date); t["game_date"] = pd.to_datetime(t.game_date)
    cut = t.game_date.min() + pd.Timedelta(days=20)
    he = h[(h.game_date < cut)]; te = t[t.game_date < cut]
    k = te.groupby("team").game_id.nunique()
    x = he[he.played].groupby("player_id").size()
    team = he.groupby("player_id").team.first().reindex(e.index).fillna(e.team)
    sh = (x.reindex(e.index).fillna(0) / team.map(k)).clip(upper=1)
    p = (e.gp / 82).clip(upper=1)
    ok = sh.notna() & p.notna()
    f = wls(sh[ok], add_const(p[ok].to_frame("p")), np.ones(ok.sum()))
    say(f"  ESPN gp, season {yr}: first-20-day share = {f.params.iloc[0]:+.2f} + {f.params.iloc[1]:.2f}·p (± {f.bse.iloc[1]:.2f}); "
        f"mean share {sh[ok].mean():.3f} vs gp/82 {p[ok].mean():.3f} (n={ok.sum()})")
open(f"{OUT}/part3.txt", "w").write("\n".join(rep))
