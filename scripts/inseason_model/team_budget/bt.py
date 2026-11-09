"""Rolling-origin backtest of T-022 (team minute budget) and T-023 (population prior) on 2024-25 / 2025-26.

Production code throughout: fantasy.model.inseason.update_projections on the production-like prior (prep.py) at
checkpoint dates; targets = each player's games played AFTER the checkpoint (regular season). Leave-one-season-out:
POP_PRIOR and the unprojected share u(k) used for season T are fitted on the other season.
Outputs runs/<T>_<cp>_<variant>.pkl (prediction tables) via `python bt.py run`, then `python bt.py eval`.
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from fantasy.model import inseason as I  # noqa: E402
from fantasy.model import team_minutes as TM  # noqa: E402

R = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[3] / "var" / "team_budget_bt"
OUT.mkdir(parents=True, exist_ok=True)
RUNS = OUT / "runs"
RUNS.mkdir(exist_ok=True)
DAYS = [-1, 13, 27, 55, 97]  # checkpoint = first game date + d (d = -1: before any game)
C = ["min", "fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov", "pts"]
POP = json.load(open(OUT / "pop.json"))


def load(T):
    pg = pd.read_csv(R / f"data/raw/espn_boxscores/player_games_{T}.csv.gz")
    tg = pd.read_csv(R / f"data/raw/espn_boxscores/team_games_{T}.csv.gz")
    pg = pg[pg.regular_season].copy()
    tg = tg[tg.regular_season].copy()
    pg["game_date"], tg["game_date"] = pg.game_date.astype(str), tg.game_date.astype(str)
    last = date.fromisoformat(tg.game_date.max())
    end = (last - timedelta(days=21)).isoformat()
    fg = tg[tg.game_date <= end].groupby("team").size().to_dict()
    return pg, tg, fg


def checkpoints(tg):
    d0 = date.fromisoformat(tg.game_date.min())
    return [(d0 + timedelta(days=d)).isoformat() for d in DAYS]


def pop_prior_from(T_other: int) -> dict:
    r = POP[str(T_other)]
    return {"mpg": r["mpg"], **r["per_min"], "fg_pct": r["fg_pct"], "ft_pct": r["ft_pct"],
            "share": r["share_after_first"], "share_sd": r["share_after_first_player_sd"]}


def set_pop(T, mult=1.0):
    I.POP_PRIOR = pop_prior_from(2026 if T == 2025 else 2025)
    I.POP_N0 = {k: (v * mult if k != "mpg" else v) for k, v in I.STAT_N0.items()}


def run(T, as_of, prior, pg, tg, fg, **kw):
    out, summ = I.update_projections(prior, pg, tg, as_of, fantasy_games=fg, **kw)
    return out


def main_run():
    for T in (2025, 2026):
        pg, tg, fg = load(T)
        prior = pd.read_csv(OUT / f"priors_{T}.csv")
        for cp in checkpoints(tg):
            t0 = time.time()
            set_pop(T, 1.0)
            res = {"pop1": run(T, cp, prior, pg, tg, fg),
                   "nopop": run(T, cp, prior, pg, tg, fg, population_prior=False)}
            for mult in (0.25, 0.5, 2.0):
                set_pop(T, mult)
                res[f"pop{mult:g}"] = run(T, cp, prior, pg, tg, fg)
            set_pop(T, 1.0)
            # preseason (prior) budget variants: constrain the prior with share = gp/82, then update
            for a, b in ((1.0, 0.0), (0.5, 0.0), (1.0, 2.0), (0.5, 2.0)):
                for u in (0.07, 0.13):
                    p = TM.TeamMinutesParams(alpha=a, beta=b, unprojected_share=u)
                    pri2, _ = TM.constrain(prior, prior["gp"] / 82, p)
                    res[f"pre_a{a:g}_b{b:g}_u{u:g}"] = run(T, cp, pri2, pg, tg, fg)
            pickle.dump(res, open(RUNS / f"{T}_{cp}.pkl", "wb"))
            print(T, cp, f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main_run()
