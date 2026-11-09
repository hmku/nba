"""Weekly model audit (Mondays): projection bias, league standings, simulator calibration, week-over-week changes.

    python scripts/audit/weekly_audit.py --through YYYY-MM-DD [--start 2026-10-20] [--out DIR] [--sims N] [--skip-sim]

Read-only on the repo and Yahoo: reads git history (no checkout), box scores and matchup captures; runs
`fantasy review week --json --no-ledger` (no --save) and the season simulator in-process (nothing saved).
Outputs in --out (default data/processed/audit/<through>/): report.md, findings.json, meta.json, CSVs, reviews/, preds.json.
Method and first run: docs/research/bias_audit_2026-11-09.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CANDIDATE_T,
    ET,
    REPO,
    build_context,
    commit_date,
    compare_findings,
    last_sunday,
)

HERE = Path(__file__).resolve().parent
MOVES = HERE / "moves_review.py"
AUDIT_ROOT = REPO / "data/processed/audit"
NOT_MODEL = {"team", "league"}  # areas reported but never "candidate fix" for the model


def previous_findings(out: Path, through: date) -> tuple[Path | None, list[dict]]:
    """Newest findings.json from an earlier --through (sibling dirs named YYYY-MM-DD)."""
    cands = []
    for root in {out.parent, AUDIT_ROOT}:
        if not root.exists():
            continue
        for d in root.iterdir():
            try:
                dd = date.fromisoformat(d.name)
            except ValueError:
                continue
            if dd < through and (d / "findings.json").exists():
                cands.append((dd, d / "findings.json"))
    if not cands:
        return None, []
    p = max(cands)[1]
    data = json.loads(p.read_text())
    return p, data if isinstance(data, list) else data.get("findings", [])


def run_moves(ctx, md: list[str], findings: list[dict]) -> None:  # noqa: ANN001
    """Hook for scripts/audit/moves_review.py (rival moves; added by another agent). Contract:
    `python moves_review.py --start D --end D --out DIR`; optional DIR/report.md (linked) and DIR/findings.json
    (a list in the same schema, merged here)."""
    if not MOVES.exists():
        md += ["## Rival moves", "_moves_review.py not present yet (placeholder hook)._"]
        return
    last = ctx.weeks[-1] if ctx.weeks else None
    start = last.start if last else ctx.start
    mdir = ctx.out / "moves"
    r = subprocess.run([sys.executable, str(MOVES), "--start", str(start), "--end", str(ctx.through), "--out", str(mdir)],
                       capture_output=True, text=True, cwd=REPO)
    if r.returncode != 0:
        md += ["## Rival moves", f"_moves_review.py failed (exit {r.returncode}): `{r.stderr.strip()[-300:]}`_"]
        return
    if (mdir / "findings.json").exists():
        extra = json.loads((mdir / "findings.json").read_text())
        findings += extra if isinstance(extra, list) else extra.get("findings", [])
    md += ["## Rival moves", f"See [moves/report.md](moves/report.md) ({start}..{ctx.through})."
           if (mdir / "report.md").exists()
           else f"Outputs in `moves/` (moves.csv, summary.json, signals_pool.csv; {start}..{ctx.through}); interpret per the weekly-audit skill §2."]


def headline(heads: dict, findings: list[dict], cmp: dict | None) -> list[str]:
    pb, lc = heads.get("bias", {}), heads.get("league", {})
    out = []
    by = {f["id"]: f for f in findings}

    def g(i: str, scale: float = 1, k: int = 2) -> str:
        f = by.get(i)
        if not f or f["estimate"] is None:
            return "n/a"
        se = f" ± {scale * f['se']:.{k}f}" if f.get("se") is not None else ""
        return f"{scale * f['estimate']:+.{k}f}{se} (t {f['t']:+.1f})" if f.get("t") is not None else f"{scale * f['estimate']:+.{k}f}"

    if pb:
        out.append(f"- **Projections (preseason, per game):** PTS {g('bias.preseason.pts')}, MIN {g('bias.preseason.min')}, "
                   f"FG% {g('bias.preseason.fg%', 100)} pp; week-start in-season PTS {g('bias.weekstart.pts')}. "
                   f"Rostered players PTS {g('bias.rostered.pts')}. Sample: {pb.get('sample', '')}.")
    if "brier" in lc:
        b, bc, n = lc["brier"]
        out.append(f"- **Simulator:** matchup Brier {b:.3f} vs {bc:.3f} if calibrated (n={n}); category slope "
                   f"{g('calib.cat.slope')} vs 1; streaming games gap {lc.get('games_gap', float('nan')):+.1f}/team-week "
                   f"(z {g('simz.stream.games.mean')}).")
    if "me" in lc:
        out.append(f"- **Me:** {lc['me']}; {lc.get('me_exp', '')}.")
    cands = [f for f in findings if f.get("candidate_fix")]
    line = f"- **Candidate fixes (|t| ≥ {CANDIDATE_T:.0f} or clear bug):** {len(cands)}"
    if cmp is not None:
        line += f"; new vs last week {len(cmp['new'])}, persisting {len(cmp['persisting'])}, resolved {len(cmp['resolved'])}"
    out.append(line + ".")
    return out


def candidates_md(findings: list[dict], per_area: int = 4) -> list[str]:
    cands = sorted((f for f in findings if f.get("candidate_fix")), key=lambda f: -abs(f["t"] or 99))
    areas: dict[str, list[dict]] = {}
    for f in cands:
        areas.setdefault(f["area"], []).append(f)
    lines = ["## Candidate fixes (top per area; full list in findings.json)"]
    for a, fs in areas.items():
        shown = ", ".join(f"`{f['id']}` {f['estimate']:+.3g}" + (f" ± {f['se']:.2g} (t {f['t']:+.1f})" if f["se"] else "")
                          for f in fs[:per_area])
        more = f" (+{len(fs) - per_area} more)" if len(fs) > per_area else ""
        lines.append(f"- **{a}**: {shown}{more}. → {fs[0]['suggestion']}")
    return lines


def _t(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1f}"


def compare_md(prev_path: Path | None, cmp: dict | None) -> list[str]:
    if cmp is None:
        return ["## Week over week", "_No previous findings.json found._"]
    rows = ["## Week over week (vs " + str(prev_path.relative_to(REPO) if prev_path and prev_path.is_relative_to(REPO)
                                         else prev_path) + ")",
            f"- New candidate fixes: {', '.join(f'`{i}`' for i in cmp['new']) or 'none'}",
            f"- Persisting ({len(cmp['persisting'])}; top 10 by |t|): "
            + (", ".join(f"`{p['id']}` t {_t(p['t_prev'])}→{_t(p['t_now'])}"
                         for p in sorted(cmp["persisting"], key=lambda p: -abs(p["t_now"] or 0))[:10]) or "none"),
            f"- Resolved (now < {CANDIDATE_T:.0f} SE): {', '.join(f'`{i}`' for i in cmp['resolved']) or 'none'}"]
    if cmp["dropped"]:
        rows.append(f"- No longer computed: {', '.join(cmp['dropped'])}")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--through", type=date.fromisoformat, default=None, help="last day included (default: last Sunday)")
    ap.add_argument("--start", type=date.fromisoformat, default=None, help="season start (default: week 1 start)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--sims", type=int, default=10000, help="sims per week-start run (7 runs; ~1.5 s per 1k)")
    ap.add_argument("--skip-sim", action="store_true", help="skip simulator re-runs (reuse out/preds.json if present)")
    a = ap.parse_args(argv)
    t0 = time.time()
    through = a.through or last_sunday()
    ctx = build_context(a.start, through, a.out, a.sims)
    ctx.out.mkdir(parents=True, exist_ok=True)
    print(f"audit {ctx.start}..{through} -> {ctx.out}; preseason {ctx.preseason_sha}, as-of {ctx.asof_sha}, weeks "
          + ", ".join(f"W{w.week}@{w.sha}" for w in ctx.weeks), file=sys.stderr)

    import league_calibration
    import projection_bias

    sections: list[str] = []
    findings: list[dict] = []
    heads: dict[str, dict] = {}
    for key, fn in (("bias", lambda: projection_bias.run(ctx)), ("league", lambda: league_calibration.run(ctx, a.skip_sim))):
        try:
            md, F, h = fn()
            sections += md
            findings += F
            heads[key] = h
        except Exception as e:  # noqa: BLE001 - keep the other step's output; report the failure
            tb = traceback.format_exc(limit=3)
            sections += [f"## {key}: FAILED", f"```\n{type(e).__name__}: {e}\n{tb[-800:]}\n```"]
            ctx.warnings.append(f"{key} step failed: {e}")
            print(tb, file=sys.stderr)
    run_moves(ctx, sections, findings)
    for f in findings:
        if f["area"] in NOT_MODEL:
            f["candidate_fix"] = False

    prev_path, prev = previous_findings(ctx.out, through)
    cmp = compare_findings(findings, prev) if prev else None
    secs = time.time() - t0
    meta = {"through": str(through), "start": str(ctx.start), "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
            "preseason_sha": ctx.preseason_sha, "preseason_date": commit_date(ctx.preseason_sha), "asof_sha": ctx.asof_sha,
            "weeks": [{"week": w.week, "start": str(w.start), "end": str(w.end), "sha": w.sha, "proj": w.proj_path}
                      for w in ctx.weeks], "sims": ctx.sims, "skip_sim": a.skip_sim, "runtime_s": round(secs),
            "previous": str(prev_path) if prev_path else None, "comparison": cmp, "warnings": ctx.warnings}
    (ctx.out / "findings.json").write_text(json.dumps(findings, indent=1, default=str))
    (ctx.out / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    with open(ctx.out / "findings.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "area", "stat", "estimate", "se", "t", "candidate_fix", "suggestion"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(findings)

    report = [f"# Weekly audit through {through} (weeks {ctx.weeks[0].week if ctx.weeks else '-'}–"
              f"{ctx.weeks[-1].week if ctx.weeks else '-'}, games {ctx.start}..{through})", "",
              *headline(heads, findings, cmp), "",
              *candidates_md(findings), "", *compare_md(prev_path, cmp), "", *sections, "",
              "## Provenance",
              f"Preseason `{ctx.preseason_sha}` ({meta['preseason_date']}); as-of `{ctx.asof_sha}`; week starts "
              + ", ".join(f"W{w.week} `{w.sha}`" for w in ctx.weeks) + f"; {ctx.sims:,} sims"
              + (" (skipped)" if a.skip_sim else "") + f"; runtime {secs:.0f} s. Estimates are **fitted** on this "
              "season's sample (SEs as stated); small samples early in the season. "
              + (f"Warnings: {'; '.join(ctx.warnings)}" if ctx.warnings else "")]
    (ctx.out / "report.md").write_text("\n".join(report) + "\n")
    print(f"wrote {ctx.out / 'report.md'} ({secs:.0f} s, {sum(f.get('candidate_fix', False) for f in findings)} "
          f"candidate fixes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
