"""Validation for docs/research/lineup_optimizer.md: matchup-aware plan vs the naive per-game-value plan (and the
current Yahoo lineup held all week) for my team over weeks 1-17 against the real Yahoo opponents, full weeks,
rosters as drafted. Week 1 uses the Yahoo statuses of the 2026-10-20 capture; later weeks treat everyone as healthy.
Paired Monte Carlo (common random numbers), SE of the mean paired difference.

Run: python docs/research/lineup_optimizer_validation.py [n_sims]
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace

import numpy as np

from fantasy.league.settings import load_settings
from fantasy.lineup.cli import LEAGUE_FILE, newest_capture
from fantasy.lineup.plan import LineupParams, build_plan, generic_per_game_values
from fantasy.lineup.players import load_team
from fantasy.model.lines import load_projections
from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH, load_nba_schedule, load_schedule_json


def main(n_sims: int = 4000) -> None:
    settings, proj = load_settings(), load_projections()
    league = json.loads(LEAGUE_FILE.read_text())
    ms = load_schedule_json(LEAGUE_SCHEDULE_PATH)
    nba = load_nba_schedule()
    generic = generic_per_game_values(proj, settings)
    cap = newest_capture()
    ids = {int(k): v["players"] for k, v in league["teams"].items()}
    rows = []
    for fw in ms.calendar or []:
        if fw.week not in ms.weeks:
            continue
        opp_id = ms.opponent(1, fw.week)
        me, _ = load_team(1, proj.df, ids[1], cap)
        opp, _ = load_team(opp_id, proj.df, ids[opp_id], cap)
        if fw.week > 1:
            me = [replace(p, status=None, slot=None) for p in me]
            opp = [replace(p, status=None, slot=None) for p in opp]
        out = build_plan(me, opp, proj.df, nba, fw.days(), settings, generic,
                         params=LineupParams(n_sims=n_sims, seed=fw.week))
        mc = out["monte_carlo"]["plans"]
        d = mc["naive_season_value"]["optimized_minus_this"]
        rows.append((fw.week, opp_id, out["week_totals"]["players_with_game"], out["week_totals"]["unused_starts"],
                     mc["optimized"]["expected_cats"], d["expected_cats"], d["expected_cats_se"],
                     d["p_win_matchup"], d["p_win_matchup_se"]))
        print(f"week {fw.week:2d} vs {opp_id:2d}: games {rows[-1][2]:2d} unused {rows[-1][3]:2d} "
              f"E[cats] {rows[-1][4]:.3f}  opt-naive {d['expected_cats']:+.4f} ({d['expected_cats_se']:.4f})  "
              f"P(win) {d['p_win_matchup']:+.4f} ({d['p_win_matchup_se']:.4f})")
    diffs = np.array([r[5] for r in rows])
    ses = np.array([r[6] for r in rows])
    print(f"mean over {len(rows)} weeks: opt-naive E[cats] {diffs.mean():+.4f} (MC SE {np.sqrt((ses**2).sum())/len(rows):.4f}); "
          f"weeks with a difference > 2 SE: {(diffs > 2 * ses).sum()}; total unused starts {sum(r[3] for r in rows)}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4000)
