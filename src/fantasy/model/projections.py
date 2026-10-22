"""Baseline 2026-27 per-game projections (transparent, deterministic).

Pipeline (all inputs are cached files; no network):

1. Basketball-Reference totals for 2023-24, 2024-25, 2025-26 (bbref seasons 2024/2025/2026).
2. Per-minute rates = sum_s(w_s * stat_s) / sum_s(w_s * min_s) with season weights 5/3/2,
   regressed toward position-group league means with a prior of `Params.prior_min` *effective
   minutes* (see `effective_minutes`: minutes weighted by w_s / mean(w)).
   Makes and attempts are projected separately (FG%, FT% are derived, never averaged).
3. Age curve multiplier on production rates (makes and attempts scaled together so efficiency
   is unchanged; turnovers scale with usage too, so TOV per possession used is unchanged).
4. Minutes per game: recency-weighted MPG plus young-player growth (tapered to 0 at 30 mpg),
   then manual role overrides.
5. Games: fitted model (fantasy.model.availability.predict_gp, gp-ols-0.2: last three seasons' games
   and minutes, age, ironman and role hinges, recent-era calibration, separate low-minute / lost-season
   model; fitted out of sample on bbref history) and gp_sd from its residuals; the
   playoff-week availability multiplier (`playoff_mult`) from team strength and injury history.
   `Params.gp_model="shrink"` restores the baseline-0.1 rule (recency mean shrunk toward 68).
6. Manual overlays (data/manual/): team changes, injuries/status, role adjustments, rookies
   without NBA history, Yahoo ADP and position eligibility overrides.
7. Points are recomputed from makes: pts = 2*FGM + 3PM + FTM.

The output is the contract in docs/ARCHITECTURE.md ("Processed projections CSV").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from fantasy.data.ids import canonical_id
from fantasy.data.provenance import MANUAL_DIR, PROCESSED_DIR, RAW_DIR, read_provenance
from fantasy.model.availability import (
    GP_FIT,
    GP_MODEL_VERSION,
    PLAYOFF_FIT,
    PLAYOFF_MODEL_VERSION,
    gp_sd_from_model,
    is_low_minutes,
    playoff_mult,
    predict_gp,
)

# 0.2.0: fitted games-played model + calibrated gp_sd + playoff_mult column
# 0.2.1: playoff_mult refit on the league's real playoff window (Mar 1-21; playoff-mult-0.2)
# 0.2.2: gp-ols-0.2 (role hinge, recent-era calibration, low-minute / lost-season model)
# 0.3.0: review fixes (docs/research/projection_quality.md §6): TOV now follows the age curve like
#        the other usage stats (was exempt, contradicting this docstring); MPG_GROWTH tapers
#        linearly to 0 between 27 and 30 mpg (was a step at 30); prior_min / min_total_minutes
#        restated in effective minutes with the same effective strength (outputs unchanged);
#        per-row `gp_scale` column in player_overrides (default baseline01 = unchanged).
MODEL_VERSION = "baseline-0.3.0"
SEASON = "2026-27"
OUTPUT_PATH = PROCESSED_DIR / "projections_2026_27.csv"

COLUMNS = [
    "player_id", "name", "team", "positions", "age", "gp", "gp_sd", "mpg",
    "fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov",
    "adp", "status", "injury_note", "source", "playoff_mult",
]  # fmt: skip
RATE_STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"]
POSITIONS = ["PG", "SG", "SF", "PF", "C"]
STATUS_VALUES = {"healthy", "out", "gtd", "out_for_season", "unknown"}


@dataclass(frozen=True)
class Params:
    season_weights: dict[int, float] = field(
        default_factory=lambda: {2026: 5.0, 2025: 3.0, 2024: 2.0}
    )
    # Rate shrinkage: effective minutes (`effective_minutes`) of position-group prior production
    # added to every player's rates. PRIOR, unfitted: 120 reproduces the pre-0.3.0 value of 400
    # *weighted* minutes (weights 5/3/2 summing to 10), i.e. ~80 real minutes for a player with only
    # the latest season, ~40 per season for three equal seasons. Refit per stat: projection_quality.md §6.
    prior_min: float = 120.0
    gp_model: str = "fitted"  # fitted (availability.predict_gp) | shrink (baseline-0.1 rule)
    # Explicit manual gp values (overrides, rookie priors) were set on the baseline-0.1 scale, which
    # over-predicted realised games by ~10% (realised / predicted = 0.87-0.92 out of sample for
    # rotation players); with the fitted model they are multiplied by this to stay comparable.
    # 1.0 = take manual gp literally. Only applies when gp_model == "fitted" and only to rows whose
    # `gp_scale` column is blank or "baseline01"; rows marked "fitted" are taken literally.
    manual_gp_scale: float = 0.90
    gp_prior: float = 68.0  # shrink only: league prior for games played by a rotation player
    gp_prior_weight: float = 1.0  # shrink only: weight of prior in units of "seasons"
    gp_min: float = 0.0
    gp_max: float = 80.0
    gp_sd_base: float = 7.0
    mpg_max: float = 37.5
    mpg_prior: float = 16.0  # bench-player minutes prior for small samples
    mpg_prior_games: float = 25.0
    # effective minutes (`effective_minutes`) needed to be projected at all; 225 = the pre-0.3.0 rule
    # (weighted minutes / sum(w) * 2 >= 150), i.e. 150 real minutes with only the latest season
    min_total_minutes: float = 225.0
    max_rows: int = 450


# Production multiplier by age in 2026-27 (age as of Feb 1, 2027, bbref convention).
AGE_CURVE = {19: 1.08, 20: 1.07, 21: 1.06, 22: 1.045, 23: 1.035, 24: 1.025, 25: 1.015, 26: 1.005,
             27: 1.0, 28: 1.0, 29: 0.995, 30: 0.99, 31: 0.98, 32: 0.97, 33: 0.96, 34: 0.95,
             35: 0.94, 36: 0.93, 37: 0.92, 38: 0.91, 39: 0.90, 40: 0.89, 41: 0.88}  # fmt: skip
# Additional minutes for young players on the rise (PRIOR, unfitted). Full growth below
# MPG_GROWTH_FULL_BELOW, tapering linearly to 0 at MPG_GROWTH_CAP. The 3-mpg band equals the largest
# growth, so projected mpg stays non-decreasing in historical mpg (no cliff at the cap).
MPG_GROWTH = {19: 3.0, 20: 3.0, 21: 2.5, 22: 2.0, 23: 1.0, 24: 0.5}
MPG_GROWTH_CAP = 30.0
MPG_GROWTH_FULL_BELOW = 27.0


def age_mult(age: float) -> float:
    a = int(np.clip(round(age), 19, 41))
    return AGE_CURVE[a]


def mpg_growth(mpg: float, age: float) -> float:
    """Young-player minutes growth: MPG_GROWTH[age], tapered linearly to 0 between 27 and 30 mpg."""
    full = MPG_GROWTH.get(int(round(age)), 0.0)
    band = MPG_GROWTH_CAP - MPG_GROWTH_FULL_BELOW
    return full * float(np.clip((MPG_GROWTH_CAP - mpg) / band, 0.0, 1.0))


def effective_minutes(seasons_w: pd.Series, minutes: pd.Series, weights: dict[int, float]) -> float:
    """Season-weighted minutes on a real-minute scale: sum_s (w_s / mean(w)) * min_s.

    Three equal seasons of M minutes count as 3M; a player with only the latest season (w=5 of 5/3/2)
    counts 1.5x his minutes. `Params.prior_min` and `min_total_minutes` are in these units."""
    mean_w = sum(weights.values()) / len(weights)
    return float((seasons_w * minutes).sum() / mean_w)


# ---------------------------------------------------------------- loading


def _raw(name: str) -> Path:
    return RAW_DIR / "bbref_sumitrodatta" / name


def load_totals() -> pd.DataFrame:
    """One row per (player, season): combined row for multi-team players, latest team kept."""
    t = pd.read_csv(_raw("player_totals.csv"))
    t = t[t["lg"] == "NBA"].copy()
    t["is_combined"] = t["team"].astype(str).str.fullmatch(r"\dTM")
    # latest team = last single-team row in file order (bbref lists stints chronologically)
    singles = t[~t["is_combined"]]
    last_team = singles.groupby(["player_id", "season"])["team"].last()
    combined = t[t["is_combined"]].set_index(["player_id", "season"])
    single_only = singles.set_index(["player_id", "season"])
    single_only = single_only[~single_only.index.isin(combined.index)]
    out = pd.concat([combined, single_only]).copy()
    out["last_team"] = last_team.reindex(out.index).values
    out = out.reset_index()
    out = out.rename(columns={"fg": "fgm", "ft": "ftm", "x3p": "fg3m", "trb": "reb"})
    for c in ["fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov", "mp", "g"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    return out


def load_pbp() -> pd.DataFrame:
    p = pd.read_csv(_raw("player_play_by_play.csv"))
    p["is_combined"] = p["team"].astype(str).str.fullmatch(r"\dTM")
    has_comb = p.groupby(["player_id", "season"])["is_combined"].transform("any")
    p = p[p["is_combined"] | ~has_comb]
    return p


def load_manual(name: str) -> pd.DataFrame:
    path = MANUAL_DIR / name
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, comment="#", skipinitialspace=True, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    if "name" in df.columns and "player_id" not in df.columns:
        df["player_id"] = df["name"].map(canonical_id)
    elif "player_id" in df.columns:
        df["player_id"] = [
            pid.strip() or canonical_id(n)
            for pid, n in zip(df["player_id"], df.get("name", ""), strict=False)
        ]
    return df


# ---------------------------------------------------------------- positions


def _pos_group(pos: str) -> str:
    pos = str(pos)
    if pos.startswith("C"):
        return "C"
    if pos.endswith("F") or pos in ("SF", "PF"):
        return "F"
    return "G"


def derive_positions(pbp: pd.DataFrame, weights: dict[int, float]) -> dict[str, str]:
    """Yahoo-style eligibility approximated from bbref play-by-play position minutes.

    A player is eligible at a position if >= 10% of his (season-weighted) minutes were played
    there, plus his bbref listed position. Yahoo is usually at least this generous; manual
    overrides in data/manual/positions_overrides.csv take precedence.
    """
    pct_cols = {
        "PG": "pg_percent",
        "SG": "sg_percent",
        "SF": "sf_percent",
        "PF": "pf_percent",
        "C": "c_percent",
    }
    p = pbp[pbp["season"].isin([s for s in weights if s >= 2025])].copy()
    p["w"] = p["season"].map(weights) * pd.to_numeric(p["mp"], errors="coerce").fillna(0)
    out: dict[str, str] = {}
    for pid, grp in p.groupby("player_id"):
        wsum = grp["w"].sum()
        listed = str(grp.sort_values("season")["pos"].iloc[-1])
        elig = set()
        if wsum > 0:
            for pos, col in pct_cols.items():
                share = (pd.to_numeric(grp[col], errors="coerce").fillna(0) * grp["w"]).sum() / wsum
                if share >= 10:
                    elig.add(pos)
        for lp in listed.split("-"):
            if lp in POSITIONS:
                elig.add(lp)
        if not elig:
            elig = {"SF"}
        out[pid] = "|".join(p_ for p_ in POSITIONS if p_ in elig)
    return out


# ---------------------------------------------------------------- core model


def baseline(params: Params | None = None) -> pd.DataFrame:
    """Statistical per-game projection for every player with enough recent minutes (no manual data yet)."""
    params = params or Params()
    w = params.season_weights
    tot = load_totals()
    tot = tot[tot["season"].isin(list(w))].copy()
    tot["w"] = tot["season"].map(w)

    career = (
        pd.read_csv(_raw("player_career_info.csv"))
        .drop_duplicates("player_id")
        .set_index("player_id")
    )
    grp_rates = _group_prior_rates(tot, max(w))
    # the GP model needs the latest weighted season to be complete: an in-progress season would put nearly
    # everyone under 500 minutes and silently switch them to the low-minute model
    latest_mp = tot.loc[tot["season"] == max(w), "mp"]
    if latest_mp.median() < 300:
        raise ValueError(f"season {max(w)} looks incomplete (median minutes {latest_mp.median():.0f}); "
                         "the games-played model needs completed seasons in season_weights")

    rows = []
    for pid, g in tot.groupby("player_id"):
        g = g.sort_values("season")
        emin = effective_minutes(g["w"], g["mp"], w)
        if emin < params.min_total_minutes:  # tiny samples
            continue
        last = g.iloc[-1]
        # age in 2026-27 = last observed age + years since
        age_now = float(last["age"]) + (2027 - int(last["season"]))
        rate = _per_minute_rates(g, emin, grp_rates.loc[_pos_group(last["pos"])], age_now, params)
        mpg = _project_mpg(g, max(w), age_now, params)
        gp, gp_sd, g1_latest = _project_gp(g, w, age_now, params)

        pg = {s: rate[s] * mpg for s in RATE_STATS}
        cinfo = career.loc[pid] if pid in career.index else None
        rows.append(
            {
                "bbref_id": pid,
                "name": last["player"],
                "team": last["last_team"] if isinstance(last["last_team"], str) else last["team"],
                "bbref_pos": last["pos"],
                "age": round(age_now, 1),
                "gp": gp,
                "gp_sd": gp_sd,
                "g1_latest": g1_latest,
                "mpg": mpg,
                **pg,
                "last_season": int(last["season"]),
                "seasons_used": ",".join(str(int(s)) for s in g["season"]),
                "effective_minutes": emin,
                "birth_date": cinfo["birth_date"] if cinfo is not None else "",
            }
        )
    df = pd.DataFrame(rows)
    df["player_id"] = df["name"].map(canonical_id)
    # Rows from earlier seasons only (e.g. retired / out of league) are dropped unless a manual
    # override re-activates them; players who sat out 2025-26 injured are re-added via manual.
    return df


def _group_prior_rates(tot: pd.DataFrame, latest_season: int) -> pd.DataFrame:
    """Position-group league mean per-minute rates (rotation players, latest season), scaled to a prior."""
    latest = tot[(tot["season"] == latest_season) & (tot["mp"] >= 500)].copy()
    latest["grp"] = latest["pos"].map(_pos_group)
    grp_rates = (
        latest.groupby("grp")[RATE_STATS].sum().div(latest.groupby("grp")["mp"].sum(), axis=0)
    )
    # replacement-ish prior: scale down production (low-minute players are below average),
    # but keep shooting percentages at the group mean (makes and attempts scaled together)
    return grp_rates * 0.85


def _per_minute_rates(
    g: pd.DataFrame, emin: float, prior: pd.Series, age_now: float, params: Params
) -> dict[str, float]:
    """Season-weighted per-minute rates regressed toward the group `prior`, then age-adjusted.

    `emin` = effective_minutes(g); the stat sums use the same w_s / mean(w) scaling, so `prior_min`
    is the prior's weight in effective minutes."""
    scale = emin / float((g["w"] * g["mp"]).sum()) if emin > 0 else 0.0  # = 1 / mean(w)
    rate = {}
    for s in RATE_STATS:
        rate[s] = (scale * (g["w"] * g[s]).sum() + params.prior_min * prior[s]) / (
            emin + params.prior_min
        )
    # keep makes <= attempts after regression
    rate["fgm"] = min(rate["fgm"], rate["fga"])
    rate["ftm"] = min(rate["ftm"], rate["fta"])
    rate["fg3m"] = min(rate["fg3m"], rate["fgm"])
    m = age_mult(age_now)
    for s in RATE_STATS:  # incl. tov: turnovers scale with usage (pre-0.3.0 exempted them; bug)
        rate[s] *= m
    return rate


def _project_mpg(g: pd.DataFrame, latest_season: int, age_now: float, params: Params) -> float:
    """Minutes: recency-weighted mpg over seasons with >= 10 games, small-sample shrinkage, age trends."""
    gg = g[g["g"] >= 10]
    if gg.empty:
        gg = g
    mpg_hist = gg["mp"] / gg["g"].clip(lower=1)
    rw = gg["season"].map({latest_season: 0.6, latest_season - 1: 0.3, latest_season - 2: 0.1})
    mpg = float((mpg_hist * rw).sum() / rw.sum())
    # small samples (e.g. late-season minutes on tanking teams, two-way players): shrink
    # minutes toward a bench prior; players with >= 80 career games (in window) are untouched
    g_total = float(g["g"].sum())
    shrink = (
        params.mpg_prior_games
        / (g_total + params.mpg_prior_games)
        * float(np.clip((80.0 - g_total) / 50.0, 0.0, 1.0))
    )
    mpg = (1 - shrink) * mpg + shrink * params.mpg_prior
    mpg += mpg_growth(mpg, age_now)
    if age_now >= 33:
        mpg -= 0.5 * (age_now - 32)
    # players whose last season is old (missed latest season entirely) keep their minutes
    return float(np.clip(mpg, 4.0, params.mpg_max))


def _project_gp(
    g: pd.DataFrame, w: dict[int, float], age_now: float, params: Params
) -> tuple[float, float, float]:
    """(expected games, gp_sd, games in the latest played season) under `params.gp_model`.

    Season-weighted games: missing seasons are NOT counted as zero; those are usually full-season
    injuries whose return is handled by manual overrides."""
    games = g["g"].astype(float).clip(upper=82)
    gw = g["w"]
    n_eff = gw.sum() / max(w.values())
    g_mean = float((games * gw).sum() / gw.sum())
    g1_latest = float(games.iloc[-1])
    if params.gp_model == "fitted":
        gp, gp_sd = _fitted_gp(g, max(w), age_now, params.gp_max)
    else:
        gp = (g_mean * n_eff + params.gp_prior * params.gp_prior_weight) / (
            n_eff + params.gp_prior_weight
        )
        gp = float(np.clip(gp, params.gp_min, params.gp_max))
        hist_sd = (
            float(np.sqrt(((games - g_mean) ** 2 * gw).sum() / gw.sum()))
            if len(g) > 1
            else 10.0
        )
        gp_sd = float(np.sqrt(params.gp_sd_base**2 + (0.6 * hist_sd) ** 2))
    return gp, gp_sd, g1_latest


def _fitted_gp(g: pd.DataFrame, latest: int, age: float, cap: float) -> tuple[float, float]:
    """Fitted expected games and sd from a player's season rows (`season`, `g`, `mp`).

    Calendar lags (latest, latest-1, latest-2), as in the fit. A player who missed the latest season
    entirely (re-activated by a manual override) has 0 games / 0 minutes in it, which selects the
    low-minute ("returning from a lost season") model, as do < 500 minutes.
    """
    seasons = g["season"].astype(int)
    games = dict(zip(seasons, g["g"].astype(float).clip(upper=82), strict=True))
    mins = dict(zip(seasons, g["mp"].astype(float), strict=True))
    lags = [games.get(latest - k, 0.0 if k == 0 else None) for k in range(3)]
    minutes = [mins.get(latest - k, 0.0 if k == 0 else None) for k in range(3)]
    gp = predict_gp(lags, age, minutes, cap=cap)
    return gp, gp_sd_from_model(gp, low_minutes=is_low_minutes(minutes))


ROOKIE_STAT_COLS = ["age", "gp", "mpg", "fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"]
USAGE_STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "ast", "tov"]  # scaled by `usage_mult` (role change)


GP_SCALES = {"baseline01", "fitted"}  # `gp_scale` column values; blank = baseline01


class _ManualGp:
    """Converts a hand-entered games-played value to the active GP model's scale (and notes it).

    Each manual row says which scale its `gp` was entered on (`gp_scale` column): "baseline01" (the
    default when blank: the old rule, ~10% optimistic) is multiplied by `manual_gp_scale`; "fitted"
    (entered as realistic expected games) is taken literally, so it is not haircut twice."""

    def __init__(self, params: Params, notes: dict[str, list[str]]) -> None:
        self.fitted = params.gp_model == "fitted"
        self.scale = params.manual_gp_scale if self.fitted else 1.0
        self.notes = notes

    def __call__(self, pid: str, value: float, gp_scale: str = "") -> float:
        kind = (gp_scale or "baseline01").strip()
        if kind not in GP_SCALES:
            raise ValueError(f"{pid}: gp_scale {gp_scale!r} not in {sorted(GP_SCALES)}")
        scale = self.scale if kind == "baseline01" else 1.0
        if scale != 1.0:
            self.notes.setdefault(pid, []).append(
                f"manual gp {value:g} x {scale:g} (manual_gp_scale: baseline-0.1 scale -> fitted)"
            )
        return float(value) * scale


def _drop_inactive(df: pd.DataFrame) -> pd.DataFrame:
    """Drop retired/unsigned/overseas players, and players without a 2025-26 season unless activated by an
    override row (else assumed out of the league)."""
    inactive = load_manual("inactive_2026_27.csv")
    if not inactive.empty:
        drop = [p for p in inactive["player_id"] if p in df.index]
        df = df.drop(index=drop)
    active_ok = set(load_manual("player_overrides_2026_27.csv").get("player_id", []))
    stale = df.index[(df["last_season"] < 2026) & ~df.index.isin(active_ok)]
    return df.drop(index=stale)


def _add_rookies(df: pd.DataFrame, notes: dict[str, list[str]], manual_gp: _ManualGp) -> None:
    """Rookies / players without NBA history: conservative manual priors (in place)."""
    rookies = load_manual("rookies_2026_27.csv")
    if rookies.empty:
        return
    for pid, r in rookies.set_index("player_id").iterrows():
        if pid in df.index:
            continue
        stats = {c: float(r[c]) for c in ROOKIE_STAT_COLS}
        stats["gp"] = manual_gp(pid, stats["gp"], str(r.get("gp_scale", "")))
        sd = float(r.get("gp_sd") or 12.0)
        if manual_gp.fitted:  # never tighter than the calibrated residual sd at that level
            sd = max(sd, gp_sd_from_model(stats["gp"]))
        meta = dict(
            name=r["name"], team=r["team"], bbref_pos=r["positions"].split("|")[0],
            gp_sd=sd, g1_latest=np.nan, last_season=0, seasons_used="",
            effective_minutes=0.0, birth_date="", status="healthy", injury_note="",
            adp=np.nan, source="manual_rookie_prior", bbref_id="",
        )  # fmt: skip
        df.loc[pid] = pd.Series({**stats, **meta})
        notes.setdefault(pid, []).append("rookie/no NBA history: manual conservative prior")


def _apply_override(df: pd.DataFrame, pid: str, r: pd.Series, notes: dict[str, list[str]],
                    manual_gp: _ManualGp) -> None:
    """One player_overrides row: team, status, injury note, gp, games missed, mpg, usage, note (in place)."""
    if r.get("team"):
        df.at[pid, "team"] = r["team"]
    if r.get("status"):
        assert r["status"] in STATUS_VALUES, (pid, r["status"])
        df.at[pid, "status"] = r["status"]
    if r.get("injury_note"):
        df.at[pid, "injury_note"] = r["injury_note"]
    if r.get("gp"):
        df.at[pid, "gp"] = manual_gp(pid, float(r["gp"]), str(r.get("gp_scale", "")))
        if manual_gp.fitted:  # the manual value replaces the model's level: sd at that level (not the
            # low-minute model's sd, which belongs to its own, discarded prediction)
            df.at[pid, "gp_sd"] = gp_sd_from_model(float(df.at[pid, "gp"]))
    if r.get("games_missed"):  # multiplicative on the (fitted or manual) gp; sd widened by 0.15/game
        # (PRIOR, unfitted) - an n-game absence is not subtracted, it removes the share n/82
        df.at[pid, "gp"] = max(
            0.0, float(df.at[pid, "gp"]) * (82 - float(r["games_missed"])) / 82
        )
        df.at[pid, "gp_sd"] = float(df.at[pid, "gp_sd"]) + 0.15 * float(r["games_missed"])
    if r.get("mpg"):
        new = float(r["mpg"])
        old = float(df.at[pid, "mpg"])
        for s in RATE_STATS:
            df.at[pid, s] = float(df.at[pid, s]) * new / old
        df.at[pid, "mpg"] = new
    if r.get("usage_mult"):  # role change: scales shot volume/assists/turnovers
        um = float(r["usage_mult"])
        for s in USAGE_STATS:
            df.at[pid, s] = float(df.at[pid, s]) * um
    if r.get("note"):
        notes.setdefault(pid, []).append(r["note"])
    if not str(df.at[pid, "source"]).endswith("+manual"):
        df.at[pid, "source"] = df.at[pid, "source"] + "+manual"


def _apply_overrides(df: pd.DataFrame, notes: dict[str, list[str]], manual_gp: _ManualGp) -> None:
    for _, r in load_manual("player_overrides_2026_27.csv").iterrows():
        pid = r["player_id"]
        if pid not in df.index:
            notes.setdefault(pid, []).append("override for unknown player ignored")
            continue
        _apply_override(df, pid, r, notes, manual_gp)


def _apply_adp(df: pd.DataFrame, notes: dict[str, list[str]]) -> None:
    adp = load_manual("adp_yahoo_2026_27.csv")
    for _, r in adp.iterrows():
        if r["player_id"] in df.index and r.get("adp"):
            df.at[r["player_id"], "adp"] = float(r["adp"])
        elif r["player_id"] not in df.index:
            notes.setdefault(r["player_id"], []).append("ADP row for unknown player ignored")


def _apply_manual(
    df: pd.DataFrame, notes: dict[str, list[str]], params: Params | None = None
) -> pd.DataFrame:
    """Manual data on top of the statistical baseline, in order: inactive/stale players dropped,
    rookie priors added, player overrides, Yahoo ADP. Order matters (overrides can touch rookies)."""
    manual_gp = _ManualGp(params or Params(), notes)
    df = df.set_index("player_id")
    df["status"] = "healthy"
    df["injury_note"] = ""
    df["adp"] = np.nan
    df["source"] = "bbref_baseline"
    df = _drop_inactive(df)
    _add_rookies(df, notes, manual_gp)
    _apply_overrides(df, notes, manual_gp)
    _apply_adp(df, notes)
    return df.reset_index()


def build(params: Params | None = None) -> tuple[pd.DataFrame, dict]:
    """Baseline + manual overlays -> the processed projections table (COLUMNS) and per-player notes."""
    params = params or Params()
    notes: dict[str, list[str]] = {}
    df = baseline(params)
    pos = derive_positions(load_pbp(), params.season_weights)
    df["positions"] = df["bbref_id"].map(pos)
    df = _apply_manual(df, notes, params)
    # expected availability in fantasy playoff weeks relative to the season (team after overrides)
    df["playoff_mult"] = [
        playoff_mult(t, None if pd.isna(g1) else float(g1))
        for t, g1 in zip(df["team"], df["g1_latest"], strict=True)
    ]
    _fill_positions(df)

    # consistency
    df["fgm"] = np.minimum(df["fgm"], df["fga"])
    df["ftm"] = np.minimum(df["ftm"], df["fta"])
    df["fg3m"] = np.minimum(df["fg3m"], df["fgm"])
    df["pts"] = 2 * df["fgm"] + df["fg3m"] + df["ftm"]
    df["gp"] = df["gp"].clip(params.gp_min, 82)

    df = _volume_cut(df, params.max_rows, notes)
    num = [
        "age",
        "gp",
        "gp_sd",
        "mpg",
        "fgm",
        "fga",
        "ftm",
        "fta",
        "fg3m",
        "pts",
        "reb",
        "ast",
        "stl",
        "blk",
        "tov",
        "playoff_mult",
    ]
    for c in num:
        df[c] = df[c].astype(float).round(3 if c not in ("age", "gp", "gp_sd", "mpg") else 1)
    df["adp"] = df["adp"].astype(float).round(1)
    out = df[COLUMNS].sort_values("player_id").reset_index(drop=True)
    assert out["player_id"].is_unique, out[out["player_id"].duplicated()]
    return out, notes


def _fill_positions(df: pd.DataFrame) -> None:
    """Positions (in place): missing -> bbref listed pos; rookies from the rookie file; then manual overrides."""
    miss = df["positions"].isna() | (df["positions"] == "")
    df.loc[miss, "positions"] = df.loc[miss, "bbref_pos"].map(
        lambda p: "|".join(x for x in str(p).split("-") if x in POSITIONS) or "SF"
    )
    rk = load_manual("rookies_2026_27.csv")
    if not rk.empty:
        rk_pos = rk.set_index("player_id")["positions"]
        m = df["player_id"].isin(rk_pos.index) & (df["source"] == "manual_rookie_prior")
        df.loc[m, "positions"] = df.loc[m, "player_id"].map(rk_pos)
    pov = load_manual("positions_overrides.csv")
    if not pov.empty:
        pmap = pov.set_index("player_id")["positions"]
        m = df["player_id"].isin(pmap.index)
        df.loc[m, "positions"] = df.loc[m, "player_id"].map(pmap)


def _volume_cut(df: pd.DataFrame, max_rows: int, notes: dict[str, list[str]]) -> pd.DataFrame:
    """Keep the `max_rows` most fantasy-relevant rows by a simple volume proxy (mpg x gp).

    Players with a manual override are always kept: someone deliberately projected them (often
    injured players whose low expected gp would otherwise push them below the cut)."""
    df["_rel"] = df["mpg"] * df["gp"].clip(lower=20)
    df = df.sort_values(["_rel", "player_id"], ascending=[False, True])
    forced = set(load_manual("player_overrides_2026_27.csv").get("player_id", []))
    below = df.iloc[max_rows:]
    rescued = below[below["player_id"].isin(forced)]
    for pid in rescued["player_id"]:
        notes.setdefault(pid, []).append(
            f"kept despite max_rows={max_rows} volume cut (manual override)"
        )
    return pd.concat([df.head(max_rows), rescued])


def inputs_manifest() -> list[dict]:
    files = sorted((RAW_DIR / "bbref_sumitrodatta").glob("*.csv")) + sorted(
        MANUAL_DIR.glob("*.csv")
    )
    out = []
    for f in files:
        prov = read_provenance(f) or {}
        out.append(
            {
                "path": str(f.relative_to(PROCESSED_DIR.parents[1])),
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                "url": prov.get("url", "manual"),
                "fetched_at": prov.get("fetched_at"),
            }
        )
    return out


def write(path: Path = OUTPUT_PATH, params: Params | None = None) -> dict:
    params = params or Params()
    df, notes = build(params)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {
        "model_version": MODEL_VERSION,
        "gp_model": {"version": GP_MODEL_VERSION, **GP_FIT} if params.gp_model == "fitted" else "shrink",
        "playoff_mult": {"version": PLAYOFF_MODEL_VERSION, **PLAYOFF_FIT},
        "season": SEASON,
        "output": str(path.relative_to(PROCESSED_DIR.parents[1])),
        "rows": len(df),
        "sha256": sha,
        "sources": sorted(set(df["source"])),
        "source_counts": df["source"].value_counts().to_dict(),
        "params": {
            k: (v if not isinstance(v, dict) else {str(a): b for a, b in v.items()})
            for k, v in asdict(params).items()
        },
        "inputs": inputs_manifest(),
        "notes": {k: v for k, v in sorted(notes.items())},
    }
    path.with_name(path.stem + ".meta.json").write_text(
        json.dumps(meta, indent=2, default=str) + "\n"
    )
    return meta


# ---------------------------------------------------------------- sanity check helper

def zscores(df: pd.DataFrame, top_n: int = 156) -> pd.DataFrame:
    """Simple per-game 9-cat z-scores (volume-weighted % impact), iterated twice on the top pool."""
    d = df.copy()
    pool = d
    for _ in range(2):
        lg_fg = pool["fgm"].sum() / pool["fga"].sum()
        lg_ft = pool["ftm"].sum() / pool["fta"].sum()
        d["fg_imp"] = d["fgm"] - lg_fg * d["fga"]
        d["ft_imp"] = d["ftm"] - lg_ft * d["fta"]
        pool = d.loc[pool.index] if "z" not in d else d.nlargest(top_n, "z")
        cols = {"fg_pct": "fg_imp", "ft_pct": "ft_imp", "fg3m": "fg3m", "pts": "pts", "reb": "reb",
                "ast": "ast", "stl": "stl", "blk": "blk", "tov": "tov"}  # fmt: skip
        z = 0
        for cat, c in cols.items():
            mu, sd = pool[c].mean(), pool[c].std()
            zc = (d[c] - mu) / sd
            if cat == "tov":
                zc = -zc
            d["z_" + cat] = zc
            z = z + zc
        d["z"] = z
        pool = d.nlargest(top_n, "z")
    return d.sort_values("z", ascending=False)
