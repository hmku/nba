"""Baseline 2026-27 per-game projections (transparent, deterministic).

Pipeline (all inputs are cached files; no network):

1. Basketball-Reference totals for 2023-24, 2024-25, 2025-26 (bbref seasons 2024/2025/2026).
2. Per-minute rates = sum_s(w_s * stat_s) / sum_s(w_s * min_s) with season weights 5/3/2,
   regressed toward position-group league means with a prior of PRIOR_MIN minutes.
   Makes and attempts are projected separately (FG%, FT% are derived, never averaged).
3. Age curve multiplier on production rates (makes and attempts scaled together so efficiency
   is unchanged; turnovers scale with usage too).
4. Minutes per game: recency-weighted MPG plus young-player growth, then manual role overrides.
5. Games: recency-weighted games, shrunk toward a league prior; gp_sd from history + base noise.
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

MODEL_VERSION = "baseline-0.1"
SEASON = "2026-27"
OUTPUT_PATH = PROCESSED_DIR / "projections_2026_27.csv"

COLUMNS = [
    "player_id", "name", "team", "positions", "age", "gp", "gp_sd", "mpg",
    "fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov",
    "adp", "status", "injury_note", "source",
]  # fmt: skip
RATE_STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"]
POSITIONS = ["PG", "SG", "SF", "PF", "C"]
STATUS_VALUES = {"healthy", "out", "gtd", "out_for_season", "unknown"}


@dataclass(frozen=True)
class Params:
    season_weights: dict[int, float] = field(
        default_factory=lambda: {2026: 5.0, 2025: 3.0, 2024: 2.0}
    )
    prior_min: float = 400.0  # minutes of league-average (by position group) production added
    gp_prior: float = 68.0  # league prior for games played by a rotation player
    gp_prior_weight: float = 1.0  # weight of prior in units of "seasons"
    gp_min: float = 0.0
    gp_max: float = 80.0
    gp_sd_base: float = 7.0
    mpg_max: float = 37.5
    mpg_prior: float = 16.0  # bench-player minutes prior for small samples
    mpg_prior_games: float = 25.0
    min_total_minutes: float = 150.0  # weighted minutes needed to be projected at all
    max_rows: int = 450


# Production multiplier by age in 2026-27 (age as of Feb 1, 2027, bbref convention).
AGE_CURVE = {19: 1.08, 20: 1.07, 21: 1.06, 22: 1.045, 23: 1.035, 24: 1.025, 25: 1.015, 26: 1.005,
             27: 1.0, 28: 1.0, 29: 0.995, 30: 0.99, 31: 0.98, 32: 0.97, 33: 0.96, 34: 0.95,
             35: 0.94, 36: 0.93, 37: 0.92, 38: 0.91, 39: 0.90, 40: 0.89, 41: 0.88}  # fmt: skip
# Additional minutes for young players on the rise (applied only below 30 mpg).
MPG_GROWTH = {19: 3.0, 20: 3.0, 21: 2.5, 22: 2.0, 23: 1.0, 24: 0.5}


def age_mult(age: float) -> float:
    a = int(np.clip(round(age), 19, 41))
    return AGE_CURVE[a]


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

    # position-group league mean per-minute rates (rotation players, latest season)
    latest = tot[(tot["season"] == max(w)) & (tot["mp"] >= 500)].copy()
    latest["grp"] = latest["pos"].map(_pos_group)
    grp_rates = (
        latest.groupby("grp")[RATE_STATS].sum().div(latest.groupby("grp")["mp"].sum(), axis=0)
    )
    # replacement-ish prior: scale down production (low-minute players are below average),
    # but keep shooting percentages at the group mean (makes and attempts scaled together)
    grp_rates = grp_rates * 0.85

    rows = []
    for pid, g in tot.groupby("player_id"):
        g = g.sort_values("season")
        wmin = (g["w"] * g["mp"]).sum()
        if wmin / sum(w.values()) * 2 < params.min_total_minutes:  # tiny samples
            continue
        last = g.iloc[-1]
        grp = _pos_group(last["pos"])
        prior = grp_rates.loc[grp]
        rate = {}
        for s in RATE_STATS:
            rate[s] = ((g["w"] * g[s]).sum() + params.prior_min * prior[s]) / (
                wmin + params.prior_min
            )
        # keep makes <= attempts after regression
        rate["fgm"] = min(rate["fgm"], rate["fga"])
        rate["ftm"] = min(rate["ftm"], rate["fta"])
        rate["fg3m"] = min(rate["fg3m"], rate["fgm"])

        # age in 2026-27 = last observed age + years since
        age_now = float(last["age"]) + (2027 - int(last["season"]))
        m = age_mult(age_now)
        for s in ["fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk"]:
            rate[s] *= m

        # minutes: recency-weighted mpg over seasons with >= 10 games
        gg = g[g["g"] >= 10]
        if gg.empty:
            gg = g
        mpg_hist = gg["mp"] / gg["g"].clip(lower=1)
        rw = gg["season"].map({max(w): 0.6, max(w) - 1: 0.3, max(w) - 2: 0.1})
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
        if mpg < 30:
            mpg += MPG_GROWTH.get(int(round(age_now)), 0.0)
        if age_now >= 33:
            mpg -= 0.5 * (age_now - 32)
        # players whose last season is old (missed latest season entirely) keep their minutes
        mpg = float(np.clip(mpg, 4.0, params.mpg_max))

        # games: season-weighted games (missing seasons are NOT counted as zero; those are
        # usually full-season injuries whose return is handled by manual overrides)
        games = g["g"].astype(float).clip(upper=82)
        gw = g["w"]
        n_eff = gw.sum() / max(w.values())
        g_mean = float((games * gw).sum() / gw.sum())
        gp = (g_mean * n_eff + params.gp_prior * params.gp_prior_weight) / (
            n_eff + params.gp_prior_weight
        )
        gp = float(np.clip(gp, params.gp_min, params.gp_max))
        hist_sd = (
            float(np.sqrt(((games - g_mean) ** 2 * gw).sum() / gw.sum())) if len(g) > 1 else 10.0
        )
        gp_sd = float(np.sqrt(params.gp_sd_base**2 + (0.6 * hist_sd) ** 2))

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
                "mpg": mpg,
                **pg,
                "last_season": int(last["season"]),
                "seasons_used": ",".join(str(int(s)) for s in g["season"]),
                "weighted_minutes": float(wmin),
                "birth_date": cinfo["birth_date"] if cinfo is not None else "",
            }
        )
    df = pd.DataFrame(rows)
    df["player_id"] = df["name"].map(canonical_id)
    # Rows from earlier seasons only (e.g. retired / out of league) are dropped unless a manual
    # override re-activates them; players who sat out 2025-26 injured are re-added via manual.
    return df


def _apply_manual(df: pd.DataFrame, notes: dict[str, list[str]]) -> pd.DataFrame:
    df = df.set_index("player_id")
    df["status"] = "healthy"
    df["injury_note"] = ""
    df["adp"] = np.nan
    df["source"] = "bbref_baseline"

    # --- inactive players (retired / unsigned / overseas)
    inactive = load_manual("inactive_2026_27.csv")
    if not inactive.empty:
        drop = [p for p in inactive["player_id"] if p in df.index]
        df = df.drop(index=drop)

    # players without a 2025-26 season need explicit activation (else assumed out of league)
    active_ok = set(load_manual("player_overrides_2026_27.csv").get("player_id", []))
    stale = df.index[(df["last_season"] < 2026) & ~df.index.isin(active_ok)]
    df = df.drop(index=stale)

    # --- rookies / players without NBA history
    rookies = load_manual("rookies_2026_27.csv")
    if not rookies.empty:
        rk = rookies.set_index("player_id")
        for pid, r in rk.iterrows():
            if pid in df.index:
                continue
            row = {
                c: float(r[c])
                for c in [
                    "age",
                    "gp",
                    "mpg",
                    "fgm",
                    "fga",
                    "ftm",
                    "fta",
                    "fg3m",
                    "reb",
                    "ast",
                    "stl",
                    "blk",
                    "tov",
                ]
            }
            row.update(
                name=r["name"], team=r["team"], bbref_pos=r["positions"].split("|")[0],
                gp_sd=float(r.get("gp_sd") or 12.0), last_season=0, seasons_used="",
                weighted_minutes=0.0, birth_date="", status="healthy", injury_note="",
                adp=np.nan, source="manual_rookie_prior", bbref_id="",
            )  # fmt: skip
            df.loc[pid] = pd.Series(row)
            notes.setdefault(pid, []).append("rookie/no NBA history: manual conservative prior")

    # --- player overrides: team, status, gp, mpg, production multiplier
    ov = load_manual("player_overrides_2026_27.csv")
    for _, r in ov.iterrows():
        pid = r["player_id"]
        if pid not in df.index:
            notes.setdefault(pid, []).append("override for unknown player ignored")
            continue
        if r.get("team"):
            df.at[pid, "team"] = r["team"]
        if r.get("status"):
            assert r["status"] in STATUS_VALUES, (pid, r["status"])
            df.at[pid, "status"] = r["status"]
        if r.get("injury_note"):
            df.at[pid, "injury_note"] = r["injury_note"]
        if r.get("gp"):
            df.at[pid, "gp"] = float(r["gp"])
        if r.get("games_missed"):
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
            for s in ["fgm", "fga", "ftm", "fta", "fg3m", "ast", "tov"]:
                df.at[pid, s] = float(df.at[pid, s]) * um
        if r.get("note"):
            notes.setdefault(pid, []).append(r["note"])
        if not str(df.at[pid, "source"]).endswith("+manual"):
            df.at[pid, "source"] = df.at[pid, "source"] + "+manual"

    # --- ADP
    adp = load_manual("adp_yahoo_2026_27.csv")
    if not adp.empty:
        for _, r in adp.iterrows():
            if r["player_id"] in df.index and r.get("adp"):
                df.at[r["player_id"], "adp"] = float(r["adp"])
            elif r["player_id"] not in df.index:
                notes.setdefault(r["player_id"], []).append("ADP row for unknown player ignored")
    return df.reset_index()


def build(params: Params | None = None) -> tuple[pd.DataFrame, dict]:
    params = params or Params()
    notes: dict[str, list[str]] = {}
    df = baseline(params)
    pos = derive_positions(load_pbp(), params.season_weights)
    df["positions"] = df["bbref_id"].map(pos)
    df = _apply_manual(df, notes)

    # rookies: positions from manual file; others missing -> bbref listed pos
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

    # consistency
    df["fgm"] = np.minimum(df["fgm"], df["fga"])
    df["ftm"] = np.minimum(df["ftm"], df["fta"])
    df["fg3m"] = np.minimum(df["fg3m"], df["fgm"])
    df["pts"] = 2 * df["fgm"] + df["fg3m"] + df["ftm"]
    df["gp"] = df["gp"].clip(params.gp_min, 82)

    # keep the most fantasy-relevant rows: rank by simple volume proxy
    # Players with a manual override are always kept: someone deliberately projected them (often
    # injured players whose low expected gp would otherwise push them below the cut).
    df["_rel"] = df["mpg"] * df["gp"].clip(lower=20)
    df = df.sort_values(["_rel", "player_id"], ascending=[False, True])
    forced = set(load_manual("player_overrides_2026_27.csv").get("player_id", []))
    below = df.iloc[params.max_rows :]
    rescued = below[below["player_id"].isin(forced)]
    for pid in rescued["player_id"]:
        notes.setdefault(pid, []).append(
            f"kept despite max_rows={params.max_rows} volume cut (manual override)"
        )
    df = pd.concat([df.head(params.max_rows), rescued])

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
    ]
    for c in num:
        df[c] = df[c].astype(float).round(3 if c not in ("age", "gp", "gp_sd", "mpg") else 1)
    df["adp"] = df["adp"].astype(float).round(1)
    out = df[COLUMNS].sort_values("player_id").reset_index(drop=True)
    assert out["player_id"].is_unique, out[out["player_id"].duplicated()]
    return out, notes


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

NINE_CATS = ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]


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
