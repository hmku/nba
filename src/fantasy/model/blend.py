"""Blend our per-game projections with external sources (ESPN, FantasyPros), per stat.

Rules (docs/research/projection_quality.md, "Blend"):
- Our processed projections define the player set and every non-stat column (team, positions,
  status, ADP, playoff_mult ...). External rows for players we do not project are ignored (listed
  in the report): our inactive list / manual decisions win.
- Count stats and makes/attempts are blended linearly: x = sum_s w_s x_s / sum_s w_s over the
  sources that have the stat for that player (weights renormalised; a player missing from a
  source keeps the other sources' weighted mean, ultimately ours). Blending makes and attempts
  separately keeps FG% / FT% consistent: the blended percentage is the attempt-weighted mean.
- Sources without attempts (FantasyPros: FG%, FT%, PTS, 3PM only) get attempts imputed from
  ours with the source's own volume: FGA_s = k * FGA_ours, FTA_s = k * FTA_ours with
  k = (PTS_s - 3PM_s) / (2 * FG%_s * FGA_ours + FT%_s * FTA_ours) (clipped to [0.5, 2]), so that
  2*FGM_s + 3PM_s + FTM_s = PTS_s; makes = percentage x attempts.
- Points are recomputed from blended makes (pts = 2*FGM + 3PM + FTM).
- Games (gp, gp_sd) stay ours (fitted availability model, docs/research/availability_model.md)
  unless `gp_weights` is given; minutes (mpg) are blended for information only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fantasy.data import external
from fantasy.data.provenance import PROCESSED_DIR, REPO_ROOT

# 0.2.0: the blend is the default draft projections (adopted 2026-10-19): `build-projections`
#        writes the unblended model to projections_2026_27.base.csv and the blend configured in
#        data/manual/projection_blend.json to projections_2026_27.csv.
BLEND_VERSION = "blend-0.2.0"
# Version of the default projections file (data/processed/projections_2026_27.csv) as a whole.
PROJECTIONS_VERSION = "projections-2026-27-0.3.0"
BLEND_STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov", "mpg"]
BLEND_OUTPUT = PROCESSED_DIR / "projections_2026_27.blend.csv"
BASE_OUTPUT = PROCESSED_DIR / "projections_2026_27.base.csv"
CONFIG_PATH = REPO_ROOT / "data" / "manual" / "projection_blend.json"
# Prior default (see projection_quality.md): equal weight on ours and ESPN. The ESPN backtest
# (2019, 2021-22, 2024-26) puts the optimum for value-rank Spearman anywhere in 0.4-0.6.
DEFAULT_WEIGHTS = "ours=0.5,espn=0.5"


@dataclass(frozen=True)
class BlendSpec:
    weights: dict[str, float]
    gp_weights: dict[str, float] | None = None

    @classmethod
    def parse(cls, text: str, gp_text: str | None = None) -> BlendSpec:
        return cls(_parse_weights(text), _parse_weights(gp_text) if gp_text else None)


def load_config(path: Path = CONFIG_PATH) -> BlendSpec | None:
    """The default blend (data/manual/projection_blend.json); None if absent or all weight on ours.

    Format: {"weights": {"ours": 0.5, "espn": 0.5}, "gp_weights": null | {...}, ...}; extra keys
    (adopted, rationale) are documentation."""
    if not path.exists():
        return None
    cfg = json.loads(path.read_text())
    w = ",".join(f"{k}={v}" for k, v in cfg["weights"].items())
    gw = cfg.get("gp_weights")
    spec = BlendSpec.parse(w, ",".join(f"{k}={v}" for k, v in gw.items()) if gw else None)
    if set(k for k, v in spec.weights.items() if v > 0) == {"ours"} and not spec.gp_weights:
        return None
    return spec


def _parse_weights(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in text.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise ValueError(f"blend weight {part!r} is not source=weight")
        k, v = part.split("=", 1)
        k = k.strip()
        if k != "ours" and k not in external.SOURCES:
            raise ValueError(f"unknown blend source {k!r}; choose from ours, {external.SOURCES}")
        w = float(v)
        if w < 0:
            raise ValueError(f"negative blend weight for {k}")
        out[k] = w
    if sum(out.values()) <= 0:
        raise ValueError("blend weights must have a positive sum")
    return out


def impute_attempts(ext: pd.DataFrame, ours: pd.DataFrame) -> pd.DataFrame:
    """Fill FGA/FTA/FGM/FTM for an external table that only has FG%, FT%, PTS and 3PM."""
    e = ext.copy()
    need = e["fga"].isna() | e["fta"].isna()
    if not need.any():
        return e
    o = ours.reindex(e.index)
    fga_o, fta_o = o["fga"].astype(float), o["fta"].astype(float)
    fgp, ftp = e["fg_pct"].astype(float), e["ft_pct"].astype(float)
    denom = 2 * fgp * fga_o + ftp * fta_o
    k = ((e["pts"].astype(float) - e["fg3m"].astype(float)) / denom).clip(0.5, 2.0)
    ok = need & denom.gt(0) & k.notna()
    e.loc[ok, "fga"] = k[ok] * fga_o[ok]
    e.loc[ok, "fta"] = k[ok] * fta_o[ok]
    e.loc[ok, "fgm"] = fgp[ok] * e.loc[ok, "fga"]
    e.loc[ok, "ftm"] = ftp[ok] * e.loc[ok, "fta"]
    return e


def blend(
    ours: pd.DataFrame, externals: dict[str, pd.DataFrame], spec: BlendSpec
) -> tuple[pd.DataFrame, dict]:
    """Blend `ours` (processed projections, any index) with external tables keyed by player_id."""
    base = ours.set_index("player_id", drop=False)
    srcs: dict[str, pd.DataFrame] = {"ours": base}
    report: dict = {"weights": spec.weights, "coverage": {}, "ignored_external": {}}
    for name, df in externals.items():
        if spec.weights.get(name, 0) <= 0 and not (spec.gp_weights or {}).get(name):
            continue
        e = df.drop_duplicates("player_id").set_index("player_id")
        report["ignored_external"][name] = sorted(set(e.index) - set(base.index))
        e = e.reindex(base.index)
        e = impute_attempts(e, base)
        srcs[name] = e
        report["coverage"][name] = int(e["pts"].notna().sum())

    out = base.copy()
    for s in BLEND_STATS:
        num = pd.Series(0.0, index=base.index)
        den = pd.Series(0.0, index=base.index)
        for name, df in srcs.items():
            w = spec.weights.get(name, 0.0)
            if w <= 0 or s not in df:
                continue
            x = pd.to_numeric(df[s], errors="coerce")
            has = x.notna()
            num[has] += w * x[has]
            den[has] += w
        blended = num / den.replace(0, np.nan)
        out[s] = blended.fillna(pd.to_numeric(base[s], errors="coerce"))
    if spec.gp_weights:
        num = pd.Series(0.0, index=base.index)
        den = pd.Series(0.0, index=base.index)
        for name, df in srcs.items():
            w = spec.gp_weights.get(name, 0.0)
            x = pd.to_numeric(df["gp"], errors="coerce") if "gp" in df else None
            if w <= 0 or x is None:
                continue
            has = x.notna()
            num[has] += w * x[has]
            den[has] += w
        out["gp"] = (num / den.replace(0, np.nan)).fillna(base["gp"]).clip(0, 82)
    out["fgm"] = np.minimum(out["fgm"], out["fga"])
    out["ftm"] = np.minimum(out["ftm"], out["fta"])
    out["fg3m"] = np.minimum(out["fg3m"], out["fgm"])
    out["pts"] = 2 * out["fgm"] + out["fg3m"] + out["ftm"]
    blended_any = pd.Series(False, index=base.index)
    for name, df in srcs.items():
        if name != "ours" and spec.weights.get(name, 0) > 0:
            blended_any |= df["pts"].notna()
    out.loc[blended_any, "source"] = out.loc[blended_any, "source"].astype(str) + "+blend"
    for c in ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]:
        out[c] = out[c].astype(float).round(3)
    out["mpg"] = out["mpg"].astype(float).round(1)
    out["gp"] = out["gp"].astype(float).round(1)
    report["blended_players"] = int(blended_any.sum())
    return out.reset_index(drop=True)[list(ours.columns)], report


def write_blend(
    spec: BlendSpec,
    base_path: Path | None = None,
    out_path: Path = BLEND_OUTPUT,
    season: int = 2027,
    extra_meta: dict | None = None,
) -> dict:
    """Blend the processed projections file with cached external sources; write CSV + meta."""
    from fantasy.model.projections import OUTPUT_PATH

    base_path = base_path or OUTPUT_PATH
    ours = pd.read_csv(base_path)
    names = set(spec.weights) | set(spec.gp_weights or {})
    externals = {s: external.load(s, season) for s in sorted(names - {"ours"})}
    missing = [s for s, df in externals.items() if df.empty]
    if missing:
        raise FileNotFoundError(
            f"no cached external projections for {missing}; run `fantasy data fetch-external`"
        )
    df, report = blend(ours, externals, spec)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, lineterminator="\n")
    sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
    inputs = [base_path] + [external.csv_path(s, season) for s in externals]
    meta = {
        "blend_version": BLEND_VERSION,
        "output": _rel(out_path),
        "rows": len(df),
        "sha256": sha,
        "weights": spec.weights,
        "gp_weights": spec.gp_weights or "ours (fitted availability model)",
        "inputs": [
            {"path": _rel(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs
        ],
        **{k: v for k, v in report.items() if k != "weights"},
        **(extra_meta or {}),
    }
    out_path.with_name(out_path.stem + ".meta.json").write_text(
        json.dumps(meta, indent=2, default=str) + "\n"
    )
    return meta


def write_default(spec: BlendSpec | None, base_meta: dict, out_path: Path, base_path: Path) -> dict:
    """Write the default projections file: the blend of `base_path` per `spec`, or (spec None)
    a copy of the unblended base. The meta records the blend weights and input shas."""
    from fantasy.model.projections import MODEL_VERSION

    common = {
        "model_version": f"{MODEL_VERSION}+{BLEND_VERSION}" if spec else MODEL_VERSION,
        "projections_version": PROJECTIONS_VERSION,
        "base_model_version": MODEL_VERSION,
        "base_meta": _rel(base_path.with_name(base_path.stem + ".meta.json")),
        "config": _rel(CONFIG_PATH) if CONFIG_PATH.exists() else None,
    }
    if spec is not None:
        return write_blend(spec, base_path=base_path, out_path=out_path, extra_meta=common)
    out_path.write_bytes(base_path.read_bytes())
    meta = base_meta | common | {
        "output": _rel(out_path),
        "blend": "none (unblended baseline)",
        "inputs_base": {"path": _rel(base_path), "sha256": base_meta["sha256"]},
    }
    out_path.with_name(out_path.stem + ".meta.json").write_text(
        json.dumps(meta, indent=2, default=str) + "\n"
    )
    return meta


def _rel(p: Path) -> str:
    p = Path(p)
    return str(p.relative_to(REPO_ROOT)) if p.is_relative_to(REPO_ROOT) else str(p)
