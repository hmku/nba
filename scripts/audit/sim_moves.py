#!/usr/bin/env python
"""Team-perspective paired season sims for selected rival moves (read-only; --no-save equivalent).

For each move: league = roster snapshot before the move (data/raw/yahoo capture -> league file, draft-slot keys),
alt = same league with the move applied (trades move players between both teams), projections = the version
committed before the move day (ex-ante) and optionally the newest one (ex-post knowledge). Output: delta P(playoffs)
/ P(title) / exp cat wins for THAT team, paired via common random numbers (fantasy.sim paired_delta).

Run with the HEAD code snapshot so uncommitted model edits don't leak in:
  PYTHONPATH=<scratch>/head/src FANTASY_DB_URL=sqlite:///<scratch>/m.db python sim_moves.py --moves moves_sel.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fantasy.sim.cli import _params, _setup
from fantasy.sim.report import paired_delta  # type: ignore[attr-defined]


def league_file(snapshot: str, out: Path) -> Path:
    d = json.load(open(snapshot))
    teams = {}
    for yid, t in d["teams"].items():
        ps = [p for p in t["players"] if p.get("player_id") and not str(p["player_id"]).startswith("unknown:")]
        teams[str(t["draft_slot"])] = {
            "name": t["name"], "yahoo_team_id": int(yid), "players": [p["player_id"] for p in ps],
            "lineup": {p["player_id"]: p.get("slot") for p in ps},
            "status": {p["player_id"]: p["status"] for p in ps if p.get("status")}}
    json.dump({"source": f"premove from {snapshot}", "my_team": 1, "draft_id": "nba.l.44495-2026", "teams": teams},
              open(out, "w"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--moves", required=True)
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    moves = json.load(open(a.moves))
    res = []
    for i, m in enumerate(moves):
        lf = league_file(m["premove_snapshot"], Path(a.out).with_name(f"premove_{i}.json"))
        for label, proj in m["projections"].items():
            st = _setup(True, None, lf, False, "market", 0, None, None, Path(proj), None, True, None, None)
            alt = st.league.copy()
            t = int(m["slot"])
            for side, adds, drops in m["sides"]:  # [(slot, [adds], [drops])...]
                for p in drops:
                    if p in alt.rosters[side]:
                        alt.rosters[side].remove(p)
                for p in adds:
                    for ps in alt.rosters.values():
                        if p in ps:
                            ps.remove(p)
                    alt.rosters[side].append(p)
            sim = st.ctx.simulator(_params(a.sims, 0, True, True, 7.0, True))
            base, after = sim.run(st.league), sim.run(alt)
            d = paired_delta(base, after, t)
            row = {"move": m["label"], "team_slot": t, "proj": label, **{k: d[k] for k in ("p_playoffs", "p_title",
                                                                                          "exp_cat_wins")}}
            print(json.dumps(row), file=sys.stderr)
            res.append(row)
            json.dump(res, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
