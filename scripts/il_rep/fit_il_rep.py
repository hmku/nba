"""Fit `SimParams.il_rep_availability`: share of his team's games a replacement-level player plays.

Replacement band = exactly the engine's replacement line (`fantasy.model.valuation.compute_values`): players ranked
num_teams * num_rounds + 1 .. + replacement_band (league 44495: 157-168) by 9-cat G-score with availability-weighted
lines, ranked ex ante on that season's *preseason* projections (ESPN's stored projections, the only preseason
projections we have historically; 2022-23 is an in-season ESPN update and is skipped).

Estimates (report keys):
- `forward_share` (THE FIT, 2024-25 and 2025-26 ESPN box scores): an IL replacement is picked up while he is active and
  held for about the length of an IL spell. For every game a band player played, the share of his team's games he
  plays in the next H days (14 / 28 / 56); averaged per player and start-weighted (each active moment equally likely
  to be the pickup). `IL_REP_AVAILABILITY` = start-weighted H=28 on the exact band.
- `full_season_share` (2021-22, 2023-24..2025-26, bbref totals): games played / 82 for the season (combined row for
  traded players; 0 when no NBA games). Includes absences before any pickup could happen: a lower bound, not the fit.
- `box_2025_2026`: split of missed games into absences of >= 7 days vs shorter ones, and coach's-decision DNPs.

Run: python scripts/il_rep/fit_il_rep.py > scripts/il_rep/report_il_rep.json   (no network; data/raw only)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from fantasy.data.ids import canonical_id
from fantasy.league.settings import load_settings
from fantasy.model.lines import STATS, availability, effective_lines
from fantasy.model.valuation import ValuationParams, _cat_scores

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
SEASONS = {2022: "2021_22", 2024: "2023_24", 2025: "2024_25", 2026: "2025_26"}  # bbref end-year -> ESPN file
BOX_SEASONS = (2025, 2026)
LONG_DAYS = 7  # engine il_min_spell_days
HORIZONS = (14, 28, 56)  # replacement holding periods (simulated long spells: median 28 days, il_replacement.md)
N_BOOT = 4000
RNG = np.random.default_rng(0)


def ranked(season: int, settings) -> pd.DataFrame:
    """ESPN preseason projections ranked exactly as compute_values orders them (replacement band = rows 156..167)."""
    e = pd.read_csv(RAW / "espn" / f"projections_{SEASONS[season]}.csv")
    e["player_id"] = [canonical_id(n) for n in e["name"]]
    e = e.drop_duplicates("player_id").set_index("player_id")
    df = e[STATS].astype(float).fillna(0.0)  # a few ESPN rows omit 3PM (bigs): 0
    df["name"], df["team"] = e["name"], e["team"]
    df["positions"] = [[] for _ in range(len(df))]
    df["gp"], df["gp_sd"], df["playoff_mult"] = e["gp"].astype(float), 0.0, 1.0
    df["adp"], df["status"] = np.nan, "healthy"
    p = ValuationParams()
    n_pool = settings.num_teams * settings.num_rounds
    weights = pd.Series(1.0, index=settings.categories)
    avail = availability(df, p.gp_risk_lambda, p.playoff_weight)
    eff = effective_lines(df, None, avail=avail)
    order = df.index
    for _ in range(p.iterations):  # same loop as compute_values; band = order[n_pool : n_pool + band] of the last pass
        scores = _cat_scores(eff, order[:n_pool], settings, p.kappa)
        order = (scores * weights).sum(axis=1).sort_values(ascending=False).index
        rep = df.loc[order[n_pool : n_pool + p.replacement_band], STATS].mean()
        eff = effective_lines(df, rep, p.stream_recovery, avail=avail)
    out = df.loc[order, ["name", "team", "gp"]].copy()
    out["rank"] = np.arange(1, len(out) + 1)
    out["season"] = season
    return out


def realized_games(season: int) -> pd.Series:
    t = pd.read_csv(RAW / "bbref_sumitrodatta" / "player_totals.csv")
    t = t[(t["season"] == season) & (t["lg"] == "NBA")]
    t["player_id"] = [canonical_id(n) for n in t["player"]]
    multi = t["team"].astype(str).str.fullmatch(r"TOT|\dTM")
    one = pd.concat([t[multi], t[~multi]]).drop_duplicates("player_id")  # combined row first
    return one.set_index("player_id")["g"].astype(float).clip(upper=82)


def box_split(season: int) -> pd.DataFrame:
    """Per player: games played, team games missed inside >= LONG_DAYS absences, coach's-decision DNPs."""
    p = pd.read_csv(RAW / "espn_boxscores" / f"player_games_{season}.csv.gz", parse_dates=["game_date"])
    p = p[p["regular_season"]]
    tg = pd.read_csv(RAW / "espn_boxscores" / f"team_games_{season}.csv.gz", parse_dates=["game_date"])
    tg = tg[tg["regular_season"]]
    sched = {k: np.sort(v["game_date"].to_numpy()) for k, v in tg.groupby("team")}
    start, end = tg["game_date"].min(), tg["game_date"].max()
    rows = {}
    for pid, g in p.groupby("player_id"):
        g = g.sort_values("game_date")
        played = g[g["played"]]
        n_played = len(played)
        cd = int((~g["played"] & g["dnp_reason"].astype(str).str.contains("COACH", na=False)).sum())
        long_missed = 0
        if n_played == 0:
            long_missed = 82
        else:
            dates = played["game_date"].to_numpy()
            teams = played["team"].to_numpy()
            # gaps: season start -> first game, between played games, last game -> season end
            gaps = [(np.datetime64(start) - np.timedelta64(1, "D"), dates[0], teams[0])]
            gaps += [(dates[i], dates[i + 1], teams[i + 1]) for i in range(len(dates) - 1)]
            gaps.append((dates[-1], np.datetime64(end) + np.timedelta64(1, "D"), teams[-1]))
            for a, b, tm in gaps:
                days_out = int((b - a) / np.timedelta64(1, "D")) - 1
                if days_out >= LONG_DAYS:
                    s = sched.get(tm, np.array([], dtype="datetime64[ns]"))
                    long_missed += int(((s > a) & (s < b)).sum())
        long_missed = min(long_missed, 82 - n_played)
        rows[pid] = {"box_played": n_played, "long_missed": long_missed, "cd_dnp": cd}
    return pd.DataFrame.from_dict(rows, orient="index")


def forward_share(season: int, horizon_days: int) -> pd.DataFrame:
    """Per player: share of his team's games he plays in the `horizon_days` after a game he played (i.e. after a
    moment he is active and could be picked up as a replacement), averaged over all such starts that leave a full
    window before the season ends. Games after a trade count for the new team; after a waiver count as missed."""
    p = pd.read_csv(RAW / "espn_boxscores" / f"player_games_{season}.csv.gz", parse_dates=["game_date"])
    p = p[p["regular_season"] & p["played"]]
    tg = pd.read_csv(RAW / "espn_boxscores" / f"team_games_{season}.csv.gz", parse_dates=["game_date"])
    tg = tg[tg["regular_season"]]
    sched = {k: np.sort(v["game_date"].to_numpy()) for k, v in tg.groupby("team")}
    last = tg["game_date"].max().to_datetime64()
    h = np.timedelta64(horizon_days, "D")
    rows = {}
    for pid, g in p.groupby("player_id"):
        dates = np.sort(g["game_date"].to_numpy())
        teams = g.sort_values("game_date")["team"].to_numpy()
        shares = []
        for d, tm in zip(dates, teams, strict=True):
            if d + h > last:
                break
            s = sched[tm]
            n_team = int(((s > d) & (s <= d + h)).sum())
            if n_team == 0:
                continue
            n_pl = int(((dates > d) & (dates <= d + h)).sum())
            shares.append(min(n_pl / n_team, 1.0))
        if shares:
            rows[pid] = {f"fwd{horizon_days}": float(np.mean(shares)), f"fwd{horizon_days}_n": len(shares)}
    return pd.DataFrame.from_dict(rows, orient="index")


def boot_mean(df: pd.DataFrame, col: str, weight: str | None = None) -> tuple[float, float]:
    """(Weighted) mean and cluster-bootstrap SE (clusters = players, who recur across seasons)."""
    df = df.assign(_w=df[weight].astype(float) if weight else 1.0)
    groups = [(g[col].to_numpy(), g["_w"].to_numpy()) for _, g in df.groupby(df.index)]
    k = len(groups)
    ests = []
    for _ in range(N_BOOT):
        idx = RNG.integers(0, k, k)
        v = np.concatenate([groups[i][0] for i in idx])
        w = np.concatenate([groups[i][1] for i in idx])
        ests.append((v * w).sum() / w.sum())
    return float((df[col] * df["_w"]).sum() / df["_w"].sum()), float(np.std(ests, ddof=1))


def main() -> None:
    settings = load_settings()
    n_pool = settings.num_teams * settings.num_rounds
    band_lo, band_hi = n_pool + 1, n_pool + ValuationParams().replacement_band
    frames = []
    for s in SEASONS:
        r = ranked(s, settings)
        g = realized_games(s)
        r["g"] = g.reindex(r.index).fillna(0.0)
        r["no_nba_games"] = ~r.index.isin(g.index)
        r["share"] = r["g"] / 82.0
        if s in BOX_SEASONS:
            r = r.join(box_split(s))
            for hz in HORIZONS:
                r = r.join(forward_share(s, hz))
        frames.append(r)
    allr = pd.concat(frames)
    band = allr[(allr["rank"] >= band_lo) & (allr["rank"] <= band_hi)].copy()
    rep: dict = {"band_ranks": [band_lo, band_hi], "seasons": sorted(SEASONS), "long_days": LONG_DAYS}

    est, se = boot_mean(band, "share")
    rep["full_season_share"] = {"mean_share": round(est, 4), "se_cluster_boot": round(se, 4), "n_player_seasons": len(band),
                      "n_players": int(band.index.nunique()), "n_no_nba_games": int(band["no_nba_games"].sum()),
                      "median_share": round(float(band["share"].median()), 4),
                      "mean_projected_gp_share": round(float((band["gp"] / 82).mean()), 4)}
    rep["by_season"] = {int(s): {"mean": round(float(v["share"].mean()), 4),
                                 "sd": round(float(v["share"].std(ddof=1)), 4), "n": len(v)}
                        for s, v in band.groupby("season")}
    # out of sample: fit on the other seasons, check the held-out one (and the canonical old->new split)
    loso = {}
    for s in SEASONS:
        tr, te = band[band["season"] != s]["share"], band[band["season"] == s]["share"]
        loso[int(s)] = {"fit_other": round(float(tr.mean()), 4), "held_out": round(float(te.mean()), 4),
                        "held_out_se": round(float(te.std(ddof=1) / np.sqrt(len(te))), 4),
                        "z": round(float((te.mean() - tr.mean()) / (te.std(ddof=1) / np.sqrt(len(te)))), 2)}
    rep["leave_one_season_out"] = loso
    tr = band[band["season"] <= 2024]["share"]
    te = band[band["season"] >= 2025]["share"]
    rep["oos_fit_2022_2024_test_2025_2026"] = {
        "fit": round(float(tr.mean()), 4), "fit_se": round(float(tr.std(ddof=1) / np.sqrt(len(tr))), 4),
        "test": round(float(te.mean()), 4), "test_se": round(float(te.std(ddof=1) / np.sqrt(len(te))), 4),
        "mse_fitted": round(float(((te - tr.mean()) ** 2).mean()), 4),
        "mse_prior_0.85": round(float(((te - 0.85) ** 2).mean()), 4)}
    # rank profile around the band (precision / smoothness check)
    prof = {}
    for lo in range(97, 241, 24):
        w = allr[(allr["rank"] >= lo) & (allr["rank"] < lo + 24)]
        prof[f"{lo}-{lo + 23}"] = {"mean": round(float(w["share"].mean()), 4),
                                    "se": round(float(w["share"].std(ddof=1) / np.sqrt(len(w))), 4), "n": len(w)}
    rep["rank_profile_share"] = prof
    wide = allr[(allr["rank"] >= n_pool - 11) & (allr["rank"] <= n_pool + 36)].copy()
    est_w, se_w = boot_mean(wide, "share")
    rep["wide_band_145_192"] = {"mean_share": round(est_w, 4), "se": round(se_w, 4), "n": len(wide)}

    # box-score decomposition (2025, 2026): what a manager who swaps out a long-injured replacement would get
    b = band[band["season"].isin(BOX_SEASONS)].copy()
    b = b[b["box_played"].notna()]
    b["share_excl_long"] = 1 - (82 - b["box_played"] - b["long_missed"]) / 82
    b["long_share"] = b["long_missed"] / 82
    b["cd_share"] = b["cd_dnp"] / 82
    e2, se2 = boot_mean(b, "share_excl_long")
    rep["box_2025_2026"] = {"n": len(b), "box_played_share": round(float((b["box_played"] / 82).mean()), 4),
                            "missed_in_long_absences_share": round(float(b["long_share"].mean()), 4),
                            "coach_decision_dnp_share": round(float(b["cd_share"].mean()), 4),
                            "share_if_long_absences_swapped": round(e2, 4), "se": round(se2, 4)}
    rep["wide_band_by_season"] = {int(k): round(float(v["share"].mean()), 4) for k, v in wide.groupby("season")}

    # forward-looking (2025, 2026): a replacement is picked up while active; share of team games over the next H days
    fwd = {}
    for name, d in (("band_157_168", band), ("wide_145_192", wide)):
        d = d[d["season"].isin(BOX_SEASONS)]
        for hz in HORIZONS:
            c = f"fwd{hz}"
            x = d[d[c].notna()]
            m, se_ = boot_mean(x, c)
            wm, wse = boot_mean(x, c, weight=f"{c}_n")
            by = {int(k): round(float(v[c].mean()), 4) for k, v in x.groupby("season")}
            wby = {int(k): round(float((v[c] * v[f"{c}_n"]).sum() / v[f"{c}_n"].sum()), 4) for k, v in x.groupby("season")}
            fwd[f"{name}_H{hz}"] = {"mean": round(m, 4), "se_cluster_boot": round(se_, 4),
                                    "start_weighted_mean": round(wm, 4), "start_weighted_se": round(wse, 4),
                                    "start_weighted_by_season": wby, "n": len(x),
                                    "n_never_active": int(d[c].isna().sum()), "by_season": by}
    rep["forward_share"] = fwd
    # the band players (audit)
    rep["band_players"] = band.rename_axis("player_id").reset_index()[["season", "rank", "player_id", "gp", "g"]].rename(
        columns={"gp": "proj_gp"}).to_dict(orient="records")
    json.dump(rep, sys.stdout, indent=1, default=float)


if __name__ == "__main__":
    main()
