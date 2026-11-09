"""Re-run the season simulator with week-start inputs (git versions) and dump per-matchup predictions for weeks 1-3.
Output: preds.json = {week: {team_slot: {cat: {mean, sd}}, matchups: [{a,b,p_win,p_tie,cat_win{a's},cat_tie}]}}"""
import json, subprocess, sys
from pathlib import Path
import numpy as np
from fantasy.sim.cli import _setup, _params
from fantasy.sim.report import head_to_head

SP = Path(__file__).parent
REPO = Path("/Users/harrisonku/dev/nba")
# commit used for each week's start (Monday-morning daily routine; W1 = pre-first-game)
WEEKS = {1: ("4bb561a", "data/processed/projections_2026_27.csv"),
         2: ("f36d3ef", "data/processed/projections_2026_27.inseason.csv"),
         3: ("8e1d8ae", "data/processed/projections_2026_27.inseason.csv")}
SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
STREAM = (sys.argv[2] != "nostream") if len(sys.argv) > 2 else True
out = {}
for w, (sha, projrel) in WEEKS.items():
    lf = SP / f"inputs/rosters_w{w}.json"; pf = SP / f"inputs/proj_w{w}.csv"
    lf.write_text(subprocess.run(["git", "show", f"{sha}:data/manual/league_rosters_2026-27.json"], cwd=REPO,
                                 capture_output=True, text=True, check=True).stdout)
    pf.write_text(subprocess.run(["git", "show", f"{sha}:{projrel}"], cwd=REPO, capture_output=True, text=True,
                                 check=True).stdout)
    st = _setup(True, None, lf, False, "market", 0, None, None, pf, None, True, None, None)
    res = st.ctx.simulator(_params(SIMS, 0, STREAM, True, 7.0, True)).run(st.league)
    wi = res.week_index(w)
    teams = {}
    for t in res.team_ids:
        x = res.totals[:, wi, res.tindex(t)]
        teams[t] = {c: {"mean": float(x[:, k].mean()), "sd": float(x[:, k].std(ddof=1))} for k, c in enumerate(res.cats)}
        teams[t]["games"] = float(res.games[:, wi, res.tindex(t)].mean())
        teams[t]["games_sd"] = float(res.games[:, wi, res.tindex(t)].std(ddof=1))
    mus = []
    seen = set()
    for t in res.team_ids:
        o = res.matchups.opponent(t, w)
        if o is None or (o, t) in seen:
            continue
        seen.add((t, o))
        h = head_to_head(res, t, o, [w])
        mus.append({"a": t, "b": o, "p_win": h["p_win"], "p_tie": h["p_tie"], "p_loss": h["p_loss"],
                    "exp_cats": h["exp_cats_won"],
                    "cat_win": dict(zip(res.cats, map(float, h["cat_win"]))),
                    "cat_tie": dict(zip(res.cats, map(float, h["cat_tie"])))})
    # correlation of category totals within a team-week (pooled across teams), for the report
    X = res.totals[:, wi]  # (N,T,C)
    Z = (X - X.mean(0)) / X.std(0)
    corr = np.mean([np.corrcoef(Z[:, i, :].T) for i in range(Z.shape[1])], axis=0)
    out[w] = {"commit": sha, "proj": projrel, "sims": SIMS, "streaming": STREAM, "cats": res.cats,
              "teams": teams, "matchups": mus, "corr": corr.round(3).tolist(),
              "warnings": (st.ctx.warnings + st.league.warnings + res.warnings)[:10]}
    print(w, sha, [(m["a"], m["b"], round(m["p_win"], 3)) for m in mus], file=sys.stderr)
name = "preds.json" if STREAM else "preds_nostream.json"
(SP / name).write_text(json.dumps(out, indent=1))
