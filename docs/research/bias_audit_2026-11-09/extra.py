"""Extra: favourite-oriented expected cats, preseason vs actual per team, my weekly z, season-sim trajectory, league FG%."""
import json, subprocess, io
import numpy as np, pandas as pd
SP = "/private/tmp/claude-501/-Users-harrisonku-dev-nba/26127dfb-08f4-48a4-83aa-e25b95986212/scratchpad/league/"
A = json.load(open(SP + "analysis.json"))
S = json.load(open(SP + "season_sims.json"))
# favourite-oriented expected cats
d = []
for m in A["matchups"]:
    ea = m["exp_cats_a"]; aa = m["score"][0] + 0.5 * (9 - sum(m["score"]))
    if m["p_win_a"] < 0.5:
        ea, aa = 9 - ea, 9 - aa
    d.append((aa, ea))
d = np.array(d); diff = d[:, 0] - d[:, 1]
print("favourite cats actual %.2f vs exp %.2f, diff %.2f +- %.2f" % (d[:, 0].mean(), d[:, 1].mean(), diff.mean(), diff.std(ddof=1) / np.sqrt(len(diff))))
# preseason (post-draft sim) vs actual
pd_ = {r["team"]: r for r in S["sim-20261020033348-d0613f"]["league_table"]}
latest = {r["team"]: r for r in S["sim-20261108122946-92a66e"]["league_table"]}
st = A["standings"]
rows = []
for t in range(1, 13):
    s = st[str(t)]
    exp3 = pd_[t]["exp_cat_wins"] / 17 * 3
    act = s["cw"] + 0.5 * s["ct"]
    rows.append((t, latest[t]["name"][:22], f"{s['W']}-{s['L']}", act, round(exp3, 1), round(act - exp3, 1), pd_[t]["exp_rank"],
                 pd_[t]["p_playoffs"], latest[t]["p_playoffs"], latest[t]["p_title"], latest[t]["exp_rank"]))
rows.sort(key=lambda r: -r[5])
print("team name rec catW exp3 diff preRank preP(po) nowP(po) nowP(title) nowRank")
for r in rows: print(r)
dd = np.array([r[5] for r in rows]); print("sd of (actual-exp) cat wins over 3 wks:", dd.std(ddof=1))
# expected sd of 3-week cat wins from binomial-ish: per week sd ~1.43 (from analysis), *sqrt(3)
print("expected sd ~", 1.43 * np.sqrt(3))
print("corr pre-exp vs actual:", np.corrcoef([r[4] for r in rows], [r[3] for r in rows])[0, 1])
# my weekly z
for w in (1, 2, 3):
    r = json.load(open(SP + f"wr/w{w}_t11.json"))["review"]
    t = r["teams"]["me"]
    print("W", w, r["final"]["score"], "games", t["games_counted"], "exp", t["games_expected_week_start"],
          {c: (v["z"], v.get("volume_effect")) for c, v in t["categories"].items()})
    print("   summary:", r["summary"]["bullets"])
# trajectory
for rid, v in sorted(S.items(), key=lambda kv: kv[1]["created_at"]):
    m = v["my_team"]
    if m: print(v["created_at"][:16], rid, m.get("p_playoffs"), m.get("p_title"), m.get("exp_rank"), v["n"])
# league-wide FG% / TOV vs projection for all played games weeks 1-3
box = pd.read_csv("/Users/harrisonku/dev/nba/data/raw/espn_boxscores/player_games_2027.csv.gz")
box["game_date"] = pd.to_datetime(box["game_date"])
box = box[(box.game_date >= "2026-10-20") & (box.game_date <= "2026-11-08") & box.played.astype(str).str.lower().isin(["true", "1"])]
proj = pd.read_csv(io.StringIO(subprocess.run(["git", "show", "d295766:data/processed/projections_2026_27.csv"], cwd="/Users/harrisonku/dev/nba", capture_output=True, text=True).stdout)).drop_duplicates("player_id").set_index("player_id")
b = box[box.player_id.isin(proj.index)]
pp = proj.loc[b.player_id]
print("league (projected players) games", len(b), "FG%% actual %.4f vs proj-implied on actual FGA %.4f" % (b.fgm.sum() / b.fga.sum(), (pp.fgm.values / pp.fga.values * b.fga.values).sum() / b.fga.sum()))
print("FT%% actual %.4f vs %.4f" % (b.ftm.sum() / b.fta.sum(), (pp.ftm.values / pp.fta.values * b.fta.values).sum() / b.fta.sum()))
for k in ["pts", "fg3m", "reb", "ast", "stl", "blk", "tov", "fga"]:
    print(k, "actual/proj per game ratio %.3f" % (b[k].sum() / pp[k].sum()))
print("all players league FG%", box.fgm.sum() / box.fga.sum())
