"""Shared helpers for the weekly audit: repo/git access, schedule + week-start commits, estimators, findings.

Pure functions (estimators, findings comparison) are imported by tests/test_audit_scripts.py; keep them free of
repo/network access.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
WEEK_START_CUTOFF = time(12, 0)  # the Monday daily routine commits ~07:30 ET; noon ET = "state at week start"
CANDIDATE_T = 3.0  # |t| >= 3 -> "candidate fix"
STATS = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fgm", "fga", "ftm", "fta"]
CATS = ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
RAW = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]


# ----------------------------------------------------------------------------------------------- git / files
def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def git_exists(sha: str, path: str) -> bool:
    r = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{sha}:{path}"], capture_output=True)
    return r.returncode == 0


def git_text(sha: str, path: str) -> str:
    return git("show", f"{sha}:{path}")


def git_csv(sha: str, path: str, **kw: Any) -> pd.DataFrame:
    return pd.read_csv(StringIO(git_text(sha, path)), **kw)


def last_commit_before(when: datetime, paths: list[str] | None = None) -> str | None:
    """Newest commit on HEAD's history with committer date < `when` (optionally touching `paths`)."""
    args = ["log", "-1", "--format=%h", f"--before={when.isoformat()}", "HEAD"]
    if paths:
        args += ["--", *paths]
    sha = git(*args).strip()
    return sha or None


def commit_date(sha: str) -> str:
    return git("log", "-1", "--format=%cd", "--date=iso", sha).strip()


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))
    return "-".join(s.split())


# ----------------------------------------------------------------------------------------------- context
@dataclass
class Week:
    week: int
    start: date
    end: date
    matchups: list[tuple[int, int]]
    sha: str | None = None  # repo state at week start (noon ET on the start day)
    proj_path: str | None = None  # projection file the daily routine used at that state

    @property
    def cutoff(self) -> datetime:
        return datetime.combine(self.start, WEEK_START_CUTOFF, tzinfo=ET)


@dataclass
class AuditContext:
    start: date
    through: date
    out: Path
    sims: int
    season_tag: str  # e.g. 2026_27
    season_dash: str  # e.g. 2026-27
    box_year: int  # ESPN season-end year, e.g. 2027
    schedule: dict
    weeks: list[Week]  # completed weeks (end <= through), with week-start commits
    preseason_sha: str
    asof_sha: str  # repo state the morning after `through` (current in-season version)
    slot2yahoo: dict[int, int] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def proj_path(self) -> str:
        return f"data/processed/projections_{self.season_tag}.csv"

    @property
    def ins_path(self) -> str:
        return f"data/processed/projections_{self.season_tag}.inseason.csv"

    @property
    def rosters_path(self) -> str:
        return f"data/manual/league_rosters_{self.season_dash}.json"

    def boxscores(self, year: int | None = None) -> pd.DataFrame:
        return pd.read_csv(REPO / f"data/raw/espn_boxscores/player_games_{year or self.box_year}.csv.gz")

    def teamgames(self, year: int | None = None) -> pd.DataFrame:
        return pd.read_csv(REPO / f"data/raw/espn_boxscores/team_games_{year or self.box_year}.csv.gz")


def last_sunday(today: date | None = None) -> date:
    """Most recent Sunday strictly before `today` (Mon -> yesterday; Sun -> a week ago: that week isn't over)."""
    today = today or datetime.now(ET).date()
    return today - timedelta(days=today.weekday() + 1)


def build_context(start: date | None, through: date, out: Path | None, sims: int) -> AuditContext:
    sched = json.loads((REPO / "data/manual/league_schedule.json").read_text())
    all_weeks = [Week(int(w["week"]), date.fromisoformat(w["start"]), date.fromisoformat(w["end"]),
                      [(int(a), int(b)) for a, b in w.get("matchups", [])]) for w in sched["weeks"]]
    season_start = start or all_weeks[0].start
    y = season_start.year if season_start.month >= 7 else season_start.year - 1
    tag, dash = f"{y}_{(y + 1) % 100:02d}", f"{y}-{(y + 1) % 100:02d}"
    weeks = [w for w in all_weeks if w.start >= season_start and w.end <= through]
    warnings: list[str] = []
    proj = f"data/processed/projections_{tag}.csv"
    ins = f"data/processed/projections_{tag}.inseason.csv"
    for w in weeks:
        w.sha = last_commit_before(w.cutoff, [proj, ins, f"data/manual/league_rosters_{dash}.json"])
        if w.sha is None:
            warnings.append(f"week {w.week}: no commit before {w.cutoff}")
            continue
        w.proj_path = ins if git_exists(w.sha, ins) else proj
    pre_cut = datetime.combine(season_start, time(0, 0), tzinfo=ET)
    pre = last_commit_before(pre_cut, [proj])
    if pre is None:
        raise SystemExit(f"no commit touching {proj} before {pre_cut}")
    asof = last_commit_before(datetime.combine(through + timedelta(days=1), WEEK_START_CUTOFF, tzinfo=ET)) \
        or git("rev-parse", "--short", "HEAD").strip()
    names = {int(k): v for k, v in sched.get("team_names", {}).items()}
    ctx = AuditContext(start=season_start, through=through, out=out or REPO / f"data/processed/audit/{through}",
                       sims=sims, season_tag=tag, season_dash=dash, box_year=y + 1, schedule=sched, weeks=weeks,
                       preseason_sha=pre, asof_sha=asof, names=names, warnings=warnings)
    ros = json.loads(git_text(asof, ctx.rosters_path))
    ctx.slot2yahoo = {int(k): int(v["yahoo_team_id"]) for k, v in ros["teams"].items() if v.get("yahoo_team_id")}
    ctx.names.update({int(k): v["name"] for k, v in ros["teams"].items() if v.get("name")})
    return ctx


# ----------------------------------------------------------------------------------------------- estimators
def cluster_mean(groups: Any, x: Any, w: Any = None) -> tuple[float, float]:
    """Weighted mean of x with an SE clustered by `groups` (CR1 small-sample factor)."""
    x = np.asarray(x, float)
    w = np.ones(len(x)) if w is None else np.asarray(w, float)
    mu = float(np.sum(w * x) / w.sum())
    g = pd.Series(w * (x - mu)).groupby(np.asarray(groups)).sum()
    k = len(g)
    se = float(np.sqrt(np.sum(g.to_numpy() ** 2)) / w.sum() * np.sqrt(k / max(k - 1, 1)))
    return mu, se


@dataclass
class Fit:
    params: pd.Series
    bse: pd.Series

    @property
    def tvalues(self) -> pd.Series:
        return self.params / self.bse


def wls(y: Any, X: pd.DataFrame, w: Any) -> Fit:
    """WLS with HC1 sandwich SEs; X must include a constant column if wanted."""
    Xv, yv, wv = np.asarray(X, float), np.asarray(y, float), np.asarray(w, float)
    XtW = Xv.T * wv
    A = np.linalg.pinv(XtW @ Xv)
    b = A @ XtW @ yv
    e = yv - Xv @ b
    meat = (Xv.T * (wv * e) ** 2) @ Xv
    n, k = Xv.shape
    V = A @ meat @ A * n / max(n - k, 1)
    return Fit(pd.Series(b, index=X.columns), pd.Series(np.sqrt(np.diag(V)), index=X.columns))


def add_const(X: Any) -> pd.DataFrame:
    X = pd.DataFrame(X).copy()
    X.insert(0, "const", 1.0)
    return X


def brier(p: Any, y: Any) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def fit_slope(p: Any, y: Any) -> float:
    """Logistic recalibration slope b in logit(q) = b * logit(p) (no intercept). >1 = underconfident."""
    from scipy.optimize import minimize_scalar

    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    L = np.log(p / (1 - p))

    def nll(b: float) -> float:
        q = np.clip(1 / (1 + np.exp(-b * L)), 1e-9, 1 - 1e-9)
        return float(-np.sum(y * np.log(q) + (1 - y) * np.log(1 - q)))

    return float(minimize_scalar(nll, bounds=(0.01, 5), method="bounded").x)


def cluster_boot(fn: Any, groups: np.ndarray, B: int, rng: np.random.Generator) -> tuple[float, list[float]]:
    ug = np.unique(groups)
    idx_by = {g: np.where(groups == g)[0] for g in ug}
    vals = [fn(np.concatenate([idx_by[g] for g in rng.choice(ug, len(ug), replace=True)])) for _ in range(B)]
    return float(np.std(vals, ddof=1)), [float(v) for v in np.percentile(vals, [5, 95])]


def calibration(p: np.ndarray, y: np.ndarray, groups: np.ndarray, rng: np.random.Generator, B: int = 2000) -> dict:
    """Brier / favourite hit-rate / logistic slope with cluster-bootstrap SEs + reliability bins.
    p = P(side a wins) with ties as 0.5, y in {0, 0.5, 1}."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    fav = np.where(p >= 0.5, p, 1 - p)
    yf = np.where(p >= 0.5, y, 1 - y)
    out: dict[str, Any] = {"n": int(len(p)), "clusters": int(len(np.unique(groups))), "brier": brier(p, y),
                           "brier_if_calibrated": float(np.mean(p * (1 - p))), "brier_coin": brier(np.full(len(p), .5), y),
                           "fav_pred": float(fav.mean()), "fav_actual": float(yf.mean()), "slope": fit_slope(p, y)}
    out["brier_gap"] = out["brier"] - out["brier_if_calibrated"]
    out["fav_gap"] = out["fav_actual"] - out["fav_pred"]
    out["fav_gap_se"], _ = cluster_boot(lambda i: float((yf[i] - fav[i]).mean()), groups, B, rng)
    out["brier_gap_se"], _ = cluster_boot(lambda i: float(np.mean((p[i] - y[i]) ** 2 - p[i] * (1 - p[i]))),
                                          groups, B, rng)
    out["slope_se"], out["slope_90ci"] = cluster_boot(lambda i: fit_slope(p[i], y[i]), groups, max(B // 4, 200), rng)
    out["bins"] = []
    edges = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        k = (fav >= lo) & (fav < hi)
        if k.sum():
            out["bins"].append({"bin": f"{lo:.1f}-{min(hi, 1):.1f}", "n": int(k.sum()), "pred": float(fav[k].mean()),
                                "actual": float(yf[k].mean())})
    return out


# ----------------------------------------------------------------------------------------------- findings
def finding(fid: str, area: str, stat: str, estimate: float | None, se: float | None, suggestion: str = "",
            t: float | None = None, null: float = 0.0, **extra: Any) -> dict:
    """One machine-readable finding. t = (estimate - null)/se unless given."""
    if t is None and estimate is not None and se:
        t = (estimate - null) / se
    f = {"id": fid, "area": area, "stat": stat, "estimate": _num(estimate), "se": _num(se), "t": _num(t, 2),
         "candidate_fix": bool(t is not None and np.isfinite(t) and abs(t) >= CANDIDATE_T), "suggestion": suggestion}
    f.update(extra)
    return f


def _num(x: Any, k: int = 5) -> float | None:
    if x is None:
        return None
    x = float(x)
    return round(x, k) if np.isfinite(x) else None


def compare_findings(cur: list[dict], prev: list[dict]) -> dict:
    """Week-over-week: new candidate fixes, persisting ones, resolved ones (by finding id)."""
    p = {f["id"]: f for f in prev}
    c = {f["id"]: f for f in cur}
    cand_c = {i for i, f in c.items() if f.get("candidate_fix")}
    cand_p = {i for i, f in p.items() if f.get("candidate_fix")}
    persisting = sorted(cand_c & cand_p)
    return {
        "new": sorted(cand_c - cand_p),
        "persisting": [{"id": i, "t_prev": p[i]["t"], "t_now": c[i]["t"], "est_prev": p[i]["estimate"],
                        "est_now": c[i]["estimate"]} for i in persisting],
        "resolved": sorted(i for i in cand_p - cand_c if i in c),
        "dropped": sorted(cand_p - set(c)),
    }


def fmt_pm(est: float | None, se: float | None, k: int = 2) -> str:
    if est is None:
        return "n/a"
    return f"{est:+.{k}f}" + (f" ± {se:.{k}f}" if se is not None else "")


def md_table(rows: list[dict], cols: list[str]) -> str:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out)
