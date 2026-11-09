"""Team minute budget (team-min-*): rescale projected minutes so each team's expected minutes per team game add up.

Identity: a team plays ~241.4 minutes per game (240 + overtime; FITTED below), so the players projected on a team
can expect E_t = sum_i share_i x mpg_i minutes per team game, where share_i is the expected share of the team's games
player i plays (preseason gp / 82, in-season the rest-of-season share) and mpg_i his minutes per game played. The
rest, a share u of the budget, goes to players outside the projection set (call-ups, signings, two-way players).
Unconstrained projections violate this badly (2026-27 draft file: team sums 137-312, sd 38; the 2026-11-09 bias audit
found each +10 projected team minutes above 240 cost that team's players -0.25 MIN each, t -3.5, concentrated in
bench/fringe players).

Adjustment for team t with target B_t = MINUTES_PER_TEAM_GAME x (1 - u):
    gap = alpha x (B_t - E_t)   (only if gap < 0 when direction == "trim")
    mpg_i' = mpg_i + gap x a_i / sum_j share_j a_j,   a_i = mpg_i x ((48 - mpg_i) / 48) ** beta
so sum_i share_i mpg_i' = E_t + gap exactly (before clipping to [0, MPG_CAP]; clipped mass is redistributed).
beta = 0 is proportional scaling; beta > 0 tilts the change toward low-minute (bench) players, in relative terms.
Per-minute rates are unchanged: every counting stat scales with mpg'/mpg (percentages unchanged).
Teams with fewer than `min_players` projected players are left alone (the identity needs a rotation).
alpha, beta, u: FITTED by the rolling-origin backtest in docs/research/minutes_model.md ("Team minute budget").
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

TEAM_MINUTES_VERSION = "team-min-0.1"
# FITTED: mean team minutes per team game (regular season, incl. overtime), ESPN box scores 2024-25 241.39,
# 2025-26 241.31 (ESPN minutes are whole minutes per player, so this is the box-score total the minutes model sees)
MINUTES_PER_TEAM_GAME = 241.35
MPG_CAP = 44.0
COUNT_COLS = ("fgm", "fga", "ftm", "fta", "fg3m", "fg3a", "pts", "reb", "ast", "stl", "blk", "tov")


@dataclass(frozen=True)
class TeamMinutesParams:
    alpha: float = 1.0  # share of the gap to the budget that is closed
    beta: float = 0.0  # bench tilt (0 = proportional)
    unprojected_share: float = 0.0  # u: share of the team's minutes expected to go to players outside the set
    direction: str = "both"  # both | trim (only reduce over-budget teams)
    min_players: int = 8
    minutes_per_game: float = MINUTES_PER_TEAM_GAME

    def as_dict(self) -> dict:
        return {"version": TEAM_MINUTES_VERSION, **asdict(self)}


def _weights(m: np.ndarray, beta: float) -> np.ndarray:
    return m * np.clip((48.0 - m) / 48.0, 0.0, 1.0) ** beta


def adjust_team(mpg: np.ndarray, share: np.ndarray, target: float, p: TeamMinutesParams) -> np.ndarray:
    """New mpg for one team's players (arrays aligned): closes alpha of the gap to `target` (see module doc)."""
    m = np.clip(np.asarray(mpg, float), 0.0, MPG_CAP)
    s = np.clip(np.asarray(share, float), 0.0, 1.0)
    e = float((s * m).sum())
    gap = p.alpha * (target - e)
    if p.direction == "trim" and gap > 0:
        return m
    free = s > 0  # rows with share 0 (out for the season) are not adjusted
    for _ in range(10):  # clipping at 0 / MPG_CAP: freeze the clipped players and redistribute the rest
        a = _weights(m, p.beta) * free
        den = float((s * a).sum())
        if den <= 1e-9 or abs(gap) < 1e-9:
            break
        new = m + gap * a / den
        lo, hi = new < 0.0, new > MPG_CAP
        clipped = np.clip(new, 0.0, MPG_CAP)
        gap -= float((s * (clipped - m)).sum())
        m = clipped
        if not (lo | hi).any():
            break
        free &= ~(lo | hi)
    return m


def constrain(df: pd.DataFrame, share, p: TeamMinutesParams, team_col: str = "team",
              mpg_col: str = "mpg", extra_mpg_cols: tuple[str, ...] = ()) -> tuple[pd.DataFrame, dict]:
    """Apply the team budget to a projections table (per-game lines). Returns (new table, report).

    share: per-row expected share of team games played (same index order as df). Counting stats (COUNT_COLS present)
    scale with the row's mpg ratio, as do `extra_mpg_cols` (e.g. mpg_next5). Adds `team_min_scale` (= mpg'/mpg)."""
    out = df.copy()
    sh = np.asarray(share, float)
    mpg = pd.to_numeric(out[mpg_col], errors="coerce").fillna(0.0).to_numpy(float)
    new = mpg.copy()
    target = p.minutes_per_game * (1.0 - p.unprojected_share)
    teams = out[team_col].astype(str).to_numpy()
    rep: dict = {"target_per_team_game": round(target, 2), "teams": {}}
    for t in sorted(set(teams)):
        idx = np.flatnonzero(teams == t)
        if len(idx) < p.min_players or t in ("", "nan", "FA"):
            continue
        new[idx] = adjust_team(mpg[idx], sh[idx], target, p)
        rep["teams"][t] = {"before": round(float((sh[idx] * mpg[idx]).sum()), 1),
                           "after": round(float((sh[idx] * new[idx]).sum()), 1), "players": int(len(idx))}
    ratio = np.where(mpg > 0, new / np.where(mpg > 0, mpg, 1.0), 1.0)
    out[mpg_col] = new
    for c in (*COUNT_COLS, *extra_mpg_cols):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0) * ratio
    out["team_min_scale"] = np.round(ratio, 4)
    before = [v["before"] for v in rep["teams"].values()]
    rep["sum_before"] = {"mean": round(float(np.mean(before)), 1), "sd": round(float(np.std(before)), 1),
                         "min": round(float(np.min(before)), 1), "max": round(float(np.max(before)), 1)} if before else {}
    return out, rep


# Deployed on the PRESEASON default projections (the in-season update's prior), `fantasy data build-projections`.
# alpha, beta, direction: FITTED by the rolling-origin backtest (docs/research/minutes_model.md "Team minute budget":
# 2024-25 + 2025-26, production update code at 5 checkpoints; SELECTED in-sample on those seasons: alpha 0.25 is the only alpha that improves minutes in
# both seasons without worsening the 9-cat z error in either; beta > 0 (bench tilt) and the in-season budget on
# the rest-of-season output were worse / no gain). direction "trim" ties "both" out of sample (-0.0378 +- 0.0070 vs
# -0.0378 +- 0.0072 in-season) and avoids inflating starters on a team whose shortfall belongs to players outside the
# file (2026-27 OKC: 137 projected minutes per game, 22% of its minutes to unprojected players; "both" would have
# raised Gilgeous-Alexander 33.7 -> 38.8 mpg). unprojected_share: ESTIMATE (not fitted on this population): the
# 2026-27 share of minutes to players outside the preseason file was 5.8% over weeks 1-3; history's full-season /
# first-20-days ratio of that share is 1.45-1.8, so ~0.09 over the season (history without rookie priors: 0.13-0.16).
PRESEASON_PARAMS = TeamMinutesParams(alpha=0.25, beta=0.0, unprojected_share=0.09, direction="trim")


def apply_to_file(path, params: TeamMinutesParams = PRESEASON_PARAMS) -> dict:
    """Constrain a projections CSV in place (share = gp / 82; mpg rounded to 0.1, stats to 0.001 like the blend) and
    record the step in its .meta.json (`team_minutes` block, `model_version` += TEAM_MINUTES_VERSION, sha256)."""
    import hashlib
    import json
    from pathlib import Path

    path = Path(path)
    meta_path = path.with_name(path.stem + ".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if "team_minutes" in meta:  # idempotent: already budgeted (rebuild the file first to re-apply)
        return meta
    df = pd.read_csv(path)
    share = pd.to_numeric(df["gp"], errors="coerce").fillna(0.0).clip(0, 82) / 82.0
    out, rep = constrain(df, share, params)
    out["mpg"] = out["mpg"].astype(float).round(1)
    for c in COUNT_COLS:
        if c in out:
            out[c] = out[c].astype(float).round(3)
    out = out.drop(columns="team_min_scale")[list(df.columns)]
    out.to_csv(path, index=False, lineterminator="\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if TEAM_MINUTES_VERSION not in str(meta.get("model_version", "")):
        meta["model_version"] = f"{meta.get('model_version', '')}+{TEAM_MINUTES_VERSION}".lstrip("+")
    meta["team_minutes"] = {**params.as_dict(), "share": "gp / 82", **rep,
                            "u_note": "unprojected_share 0.09 is an ESTIMATE from 2026-27 weeks 1-3 data (5.8% of "
                                      "minutes to players outside the file x history's full/early ratio); not "
                                      "backtested (backtest used the other season's realised u)",
                            "note": "docs/research/minutes_model.md (Team minute budget)"}
    meta["sha256_before_team_minutes"] = meta.get("sha256")
    meta["sha256"] = sha
    meta_path.write_text(json.dumps(meta, indent=2, default=str) + "\n")
    return meta
