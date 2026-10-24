"""`fantasy inseason opportunity`: gather inputs, detect, value, evaluate (opportunity-0.1.0).

Inputs (all read-only): in-season projections (+ the preseason file for preseason mpg), this season's ESPN box scores
(last-3 mpg, starts, games played during a teammate's absence), the ESPN injury report, Yahoo tags in the league
file, the news digest (optional), the Yahoo free-agent capture (FA vs waiver status). Universe = projected players on
no league roster (presumed available; the capture gives their exact status when listed). Evaluation of the top
candidates: this week's paired matchup Monte Carlo (the stream tool's week model, `streamscan.scan_streams`, on the
WINDOW override lines) and a paired season simulation (season-sim, `sim compare` semantics) on the SEASON override
lines, add = candidate / drop = my lowest-value healthy player. Pure logic lives in `fantasy.inseason.opportunity`.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fantasy.inseason.opportunity import (
    OPPORTUNITY_VERSION,
    Absence,
    OpportunityParams,
    detect,
    override_frames,
)

INJURY_FLAGS = {"indefinite", "season_ending", "surgery", "timeline", "injury_exit"}
NEWS_ONLY_FLAGS = {"indefinite", "season_ending", "surgery", "injury_exit"}  # enough on their own (timeline is not)
YAHOO_OUT = {"O", "INJ", "OFS", "IR"}


# ------------------------------------------------------------------ inputs
def recent_box(pg: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, dict[str, list[bool]]]:
    """Per player (box scores before `as_of`): last3_mpg (last 3 games played), starts_last3 (of his team's last 3
    games), games_played; and played flags over his CURRENT team's games so far (chronological)."""
    d = pg[pd.to_datetime(pg["game_date"]).dt.date < as_of].copy()
    if "regular_season" in d:
        d = d[d["regular_season"].astype(bool)]
    d = d[d["player_id"].notna()]
    d["min"] = pd.to_numeric(d["min"], errors="coerce").fillna(0.0)
    d["played"] = d["played"].astype(bool) & (d["min"] > 0)
    d["starter"] = d["starter"].fillna(False).astype(bool) & d["played"]
    tg = d[["team", "game_id", "game_date"]].drop_duplicates().sort_values(["game_date", "game_id"])
    team_games = {t: list(g["game_id"]) for t, g in tg.groupby("team")}
    rows, flags = [], {}
    for pid, g in d.sort_values(["game_date", "game_id"]).groupby("player_id"):
        team = str(g["team"].iloc[-1])
        pl = g[g["played"]]
        games = team_games.get(team, [])
        on = set(g.loc[g["team"] == team, "game_id"][g["played"]])
        flags[str(pid)] = [gid in on for gid in games]
        last3_ids = set(games[-3:])
        rows.append({"player_id": str(pid), "box_team": team, "games_played": int(len(pl)),
                     "last3_mpg": round(float(pl["min"].tail(3).mean()), 1) if len(pl) else np.nan,
                     "starts_last3": int(g[g["game_id"].isin(last3_ids)]["starter"].sum())})
    return pd.DataFrame(rows).set_index("player_id") if rows else pd.DataFrame(), flags


def team_games_between(nba, team: str, start: date, end: date) -> int:
    """Games of `team` on days start <= d < end (NBA schedule)."""
    return sum(1 for d, v, h in nba.games if start <= d < end and team in (v, h))


def find_absences(df: pd.DataFrame, pre_mpg: pd.Series, as_of: date, nba, injuries: pd.DataFrame | None,
                  league_raw: dict | None, news: dict | None, params: OpportunityParams,
                  injuries_date: str | None = None) -> tuple[list[Absence], list[str]]:
    """Out teammates projected >= star_mpg: ESPN Out (or out for season), Yahoo O/INJ/OFS/IR tag, or a high/medium
    injury news item (indefinite / season-ending / surgery / exited a game; a 'timeline' item only adds context)."""
    from fantasy.lineup.players import normalize_status

    warnings: list[str] = []
    level = pd.concat([pd.to_numeric(df["mpg"], errors="coerce"), pre_mpg.reindex(df.index)], axis=1).max(axis=1)
    stars = set(level[level >= params.star_mpg].index)
    src: dict[str, list[dict]] = {}
    ret: dict[str, str] = {}
    season_out: set[str] = set()
    if injuries is not None and not injuries.empty:
        from fantasy.data.injuries import model_status

        for r in injuries.itertuples():
            st = model_status(str(r.status), str(r.fantasy_status))
            if r.player_id in stars and st in ("out", "out_for_season"):
                src.setdefault(r.player_id, []).append({
                    "source": "ESPN injury report", "status": st, "date": str(r.updated),
                    "detail": f"{r.injury}: {str(r.comment)[:120]}".strip(": "),
                    "fetched": injuries_date})
                if st == "out_for_season":
                    season_out.add(r.player_id)
                if str(r.return_date or "").strip():
                    ret[r.player_id] = str(r.return_date)[:10]
    if league_raw:
        synced = str(league_raw.get("synced_at") or "")[:10]
        for t in (league_raw.get("teams") or {}).values():
            if not isinstance(t, dict):
                continue
            for pid, tag in (t.get("status") or {}).items():
                nt = normalize_status(tag)
                if pid in stars and nt in YAHOO_OUT:
                    src.setdefault(pid, []).append({"source": "Yahoo tag (league file)", "status": nt,
                                                    "date": synced, "detail": ""})
                    if nt == "OFS":
                        season_out.add(pid)
    if news:
        for pl in news.get("players", []):
            pid = pl["player_id"]
            if pid not in stars:
                continue
            for it in pl.get("items", []):
                fl = set(it.get("flags") or [])
                if it.get("severity") in ("high", "medium") and fl & INJURY_FLAGS:
                    if pid not in src and not fl & NEWS_ONLY_FLAGS:
                        continue
                    src.setdefault(pid, []).append({"source": f"news ({it.get('source') or 'ESPN'})",
                                                    "status": ",".join(sorted(fl & INJURY_FLAGS)),
                                                    "date": str(it.get("published") or "")[:10],
                                                    "detail": str(it.get("headline") or "")[:140]})
                    if "season_ending" in fl:
                        season_out.add(pid)
    out = []
    for pid, ss in src.items():
        if pid not in df.index:
            continue
        r = df.loc[pid]
        team = str(r["team"])
        rd = ret.get(pid)
        eg = None
        if rd:
            try:
                rdd = date.fromisoformat(rd)
                eg = team_games_between(nba, team, as_of, rdd) if rdd > as_of and nba is not None else None
            except ValueError:
                eg = None
        missed = int(pd.to_numeric(r.get("missed_streak", 0), errors="coerce") or 0)
        news_only = all(s["source"].startswith("news") for s in ss)
        out.append(Absence(pid, str(r["name"]), team, float(level[pid]), missed, pid in season_out, rd, eg, ss,
                           params.p_out_news if news_only else 1.0))
    return out, warnings


# ------------------------------------------------------------------ evaluation
def _verdict(by_set: dict[str, dict], week: dict | None, sig: float) -> tuple[str, str]:
    """helps: ΔP(playoffs) or ΔP(title) > sig·SE and neither < -sig·SE under every projection set (the stream tool's
    season rule, `streamscan.season_verdict`); hurts/tradeoff as there; speculative: not significant, no set
    negative beyond noise, and this week's ΔP(win) > sig·SE or the blend's ΔP(playoffs) > 1 SE (PRIOR); else noise."""
    from fantasy.inseason.streamscan import season_verdict

    vs = {k: season_verdict(v, sig) for k, v in by_set.items()}
    if vs and all(v == "helps" for v in vs.values()):
        return "helps", "season ΔP(playoffs) or ΔP(title) beyond 2 SE under every projection set, neither down"
    for bad in ("hurts", "tradeoff"):
        if bad in vs.values():
            return bad, f"{bad} under {[k for k, v in vs.items() if v == bad]}"
    wk = bool(week and week.get("d_p_win", 0) > 0 and week["d_p_win"] >= sig * week["d_p_win_se"])
    b = by_set.get("blend", {}).get("p_playoffs")
    pos = bool(b and b["delta"] > 0 and b["delta"] >= b["delta_se"])
    if wk or pos:
        why = "this week's ΔP(win) beyond 2 SE" if wk else "season ΔP(playoffs) > 1 SE but < 2 SE"
        return "speculative", why + "; season effect not resolved"
    return "noise", "season and week effects within noise"


def evaluate(cands: list[dict], season_csv: Path, window_df: pd.DataFrame, base_proj, settings, lg, me_id: int,
             drop: str, sched: Path, nba, fas: dict, d0: date, season_sims: int, week_sims: int, seed: int,
             market_csv: Path | None, raw_league: dict, roster_cap: Path | None, sig: float = 2.0) -> tuple[dict, list[str]]:
    """{player_id: {week, season: {set: delta}, verdict, why}} for `cands` (add) vs `drop`."""
    from fantasy.inseason.freeagents import FreeAgent
    from fantasy.inseason.streamscan import StreamParams, scan_streams
    from fantasy.lineup.plan import generic_per_game_values
    from fantasy.lineup.players import load_team, parse_positions
    from fantasy.model.lines import ProjectionSet, load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.engine import SimParams
    from fantasy.sim.report import paired_delta
    from fantasy.sim.schedule import load_schedule_json

    warnings: list[str] = []
    res: dict[str, dict] = {c["player_id"]: {} for c in cands}
    # ---- week: the stream tool's matchup model on the window lines
    ms = load_schedule_json(sched)
    fw = next((w for w in (ms.calendar or []) if w.start <= d0 <= w.end), None)
    opp_id = ms.opponent(me_id, fw.week) if fw else None
    if fw is not None and opp_id is not None and week_sims > 0:
        wproj = ProjectionSet(df=window_df, path="opportunity-window", sha256="")
        from fantasy.model.valuation import compute_values

        vals, _, _ = compute_values(wproj, settings)

        def entry(t: int) -> dict:
            e = raw_league["teams"][str(t)]
            return e if isinstance(e, dict) else {"players": e}

        me, w1 = load_team(me_id, window_df, lg.rosters[me_id], roster_cap, entry(me_id))
        opp, w2 = load_team(opp_id, window_df, lg.rosters[opp_id], roster_cap, entry(opp_id))
        warnings += w1 + w2
        flist = []
        for c in cands:
            pid = c["player_id"]
            fa = fas.get(pid)
            if fa is None:
                r = window_df.loc[pid]
                fa = FreeAgent(pid, str(r["name"]), str(r["team"]), tuple(parse_positions(r["positions"])), None, None,
                               "none")
            flist.append(fa)
        days = [d0 + timedelta(i) for i in range((fw.end - d0).days + 1)]
        params = StreamParams(week_sims=week_sims, season_sims=0, season_top=2 * len(flist) + 2, top=0, seed=seed)
        rep = scan_streams(window_df, settings, nba, me, opp, flist, days, generic_per_game_values(wproj, settings),
                           vals["value"], params, [drop])
        for r in rep["pairs"]:
            if r["drop"] == drop and r["add"] in res:
                res[r["add"]]["week"] = {"week": fw.week, "window": rep["window"], "opponent": opp_id,
                                         "effective": r["effective"], "add_games": r["add_games"],
                                         "add_starts": r["add_starts"], "d_exp_cats_analytic": r["week_d_exp_cats"],
                                         "d_p_win_analytic": r["week_d_p_win"], "mc": r.get("week_mc")}
    else:
        warnings.append("week evaluation skipped (no league-schedule week/opponent for this date, or --week-sims 0)")
    # ---- season: paired season sims on the season override (blend; market when available)
    sets = {"blend": load_projections(season_csv)}
    if market_csv is not None and Path(market_csv).exists():
        mk = pd.read_csv(market_csv).set_index("player_id")
        base_df = base_proj.df
        sdf = sets["blend"].df
        for pid in (c["player_id"] for c in cands):
            if pid in mk.index and pid in base_df.index and float(base_df.at[pid, "mpg"]) > 0:
                f = float(sdf.at[pid, "mpg"]) / float(base_df.at[pid, "mpg"])
                for col in ("fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"):
                    mk.at[pid, col] = float(mk.at[pid, col]) * f
                if "mpg" in mk.columns:
                    mk.at[pid, "mpg"] = float(mk.at[pid, "mpg"]) * f
        mpath = season_csv.with_name(season_csv.stem + ".market.csv")
        mk.reset_index().to_csv(mpath, index=False)
        sets["market"] = load_projections(mpath)
    else:
        warnings.append(f"market projections {market_csv} not found: season verdict uses the blend set only "
                        "(the stream tool requires every set; treat 'helps' as blend-only)")
    if season_sims > 0:
        sp = SimParams(n_sims=season_sims, seed=seed, streaming=True, il_replacement=True, il_min_spell_days=7.0,
                       roster_cap=True)
        for name, pr in sets.items():
            ctx = build_context(pr, settings, lg.team_ids, schedule_file=sched, nba=nba)
            sim = ctx.simulator(sp)
            base = sim.run(lg)
            for c in cands:
                alt = sim.run(lg.with_change(me_id, [c["player_id"]], [drop]))
                res[c["player_id"]].setdefault("season", {})[name] = paired_delta(base, alt, me_id)
    for r in res.values():
        v, why = _verdict(r.get("season", {}), (r.get("week") or {}).get("mc"), sig)
        r["verdict"], r["verdict_why"] = v, why
    return res, warnings


# ------------------------------------------------------------------ orchestration
def run(d0: date, free_agents: Path | None = None, top: int = 15, eval_top: int = 5, projections: Path | None = None,
        league_file: Path | None = None, out: Path | None = None, news_file: Path | None = None,
        include_rostered: bool = False, season_sims: int = 2000, week_sims: int = 4000, seed: int = 0,
        market: Path | None = None, params: OpportunityParams | None = None) -> dict[str, Any]:
    from fantasy.data.injuries import parse_espn
    from fantasy.data.provenance import RAW_DIR, REPO_ROOT, read_provenance, sha256_file
    from fantasy.inseason.freeagents import load_free_agents
    from fantasy.inseason.stream import per_game_values
    from fantasy.league.settings import load_settings
    from fantasy.lineup.cli import LEAGUE_FILE, YAHOO_DIR, newest_capture
    from fantasy.lineup.players import IL_TAGS, normalize_status
    from fantasy.model.lines import (
        DEFAULT_PROJECTIONS,
        default_projections_path,
        load_projections,
    )
    from fantasy.model.valuation import compute_values
    from fantasy.sim.league import from_json
    from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH, load_nba_schedule

    p = params or OpportunityParams()
    warnings: list[str] = []
    proj_path = Path(projections) if projections else default_projections_path()
    proj = load_projections(proj_path)
    raw_csv = pd.read_csv(proj_path)
    raw_df = raw_csv.drop_duplicates("player_id").set_index("player_id")
    df = raw_df.copy()
    meta_p = proj_path.with_name(proj_path.stem + ".meta.json")
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    if "mpg_next5" not in df.columns:
        warnings.append("projections have no mpg_next5 (not an in-season file): model A = mpg")
        df["mpg_next5"] = df["mpg"]
    pre = pd.read_csv(DEFAULT_PROJECTIONS).drop_duplicates("player_id").set_index("player_id")
    df["mpg_pre"] = pd.to_numeric(pre["mpg"], errors="coerce").reindex(df.index).fillna(df["mpg"])
    lf = league_file or LEAGUE_FILE
    lg = from_json(lf)
    raw_league = json.loads(Path(lf).read_text())
    me_id = lg.my_team
    st = load_settings().model_copy(update={"num_teams": len(lg.rosters)})
    nba = load_nba_schedule()
    owner = {pid: t for t, ps in lg.rosters.items() for pid in ps}
    # box scores of this season
    season_end = int(str(meta.get("as_of") or d0.isoformat())[:4]) + (1 if d0.month >= 8 else 0)
    box_path = RAW_DIR / "espn_boxscores" / f"player_games_{season_end}.csv.gz"
    if box_path.exists():
        box, played_flags = recent_box(pd.read_csv(box_path), d0)
    else:
        box, played_flags = pd.DataFrame(), {}
        warnings.append(f"no box scores {box_path}: last-3 mpg / observed-absence checks unavailable")
    # injuries
    inj_path = RAW_DIR / "espn" / "injuries.json"
    inj, inj_date = None, None
    if inj_path.exists():
        inj = parse_espn(inj_path)
        inj_date = str((read_provenance(inj_path) or {}).get("fetched_at") or "")[:10] or None
        if inj_date and (d0 - date.fromisoformat(inj_date)).days > 1:
            warnings.append(f"ESPN injury report fetched {inj_date}: stale, run `fantasy data fetch-injuries`")
    else:
        warnings.append("no ESPN injury report (`fantasy data fetch-injuries`)")
    news = None
    try:
        from fantasy.data.news import digest, newest_news_file

        nf = news_file or newest_news_file()
        if nf is not None:
            news = digest(nf, since=(d0 - timedelta(days=3)).isoformat(), new_only=False, min_severity="medium",
                          league_file=Path(lf))
        else:
            warnings.append("no news file (`fantasy data fetch-news`): news-digest injury source skipped")
    except Exception as e:  # the news source is optional
        warnings.append(f"news digest unavailable: {e}")
    # who is out (the player himself)
    yahoo_tag = {pid: normalize_status(tag) for t in (raw_league.get("teams") or {}).values() if isinstance(t, dict)
                 for pid, tag in (t.get("status") or {}).items()}
    espn_out = set()
    if inj is not None and not inj.empty:
        from fantasy.data.injuries import model_status

        espn_out = {r.player_id for r in inj.itertuples()
                    if model_status(str(r.status), str(r.fantasy_status)) in ("out", "out_for_season")}
    df["out"] = (df["status"].astype(str).isin(["out", "out_for_season"]) | df.index.isin(espn_out)
                 | df.index.map(lambda x: yahoo_tag.get(x) in YAHOO_OUT))
    absences, w = find_absences(df, df["mpg_pre"], d0, nba, inj, raw_league, news, p, inj_date)
    warnings += w
    # free agents
    fa_path = free_agents or next(iter(sorted(YAHOO_DIR.glob("free_agents_*.json"), reverse=True)), None)
    fas: dict = {}
    if fa_path is not None:
        fl, w = load_free_agents(Path(fa_path), d0)
        warnings += w
        fas = {f.player_id: f for f in fl}
        cap_age = (d0 - _capture_date(fa_path, d0)).days
        if cap_age >= 1:
            warnings.append(f"free-agent capture {Path(fa_path).name} is {cap_age} day(s) old (top {len(fl)} only): "
                            "status of listed players may have changed; unlisted players are presumed available")
    else:
        warnings.append("no free-agent capture: availability = not on any league roster")
    universe = [pid for pid in df.index if pid not in owner] + (
        [pid for pid in df.index if pid in owner and owner[pid] != me_id] if include_rostered else [])
    cands = detect(df, absences, p, played_flags, universe)
    # values: per-game G-score above replacement, base vs opportunity line
    window_raw, season_raw = override_frames(raw_df, cands)
    win_proj = _as_projset(window_raw, "opportunity-window")
    pgv0 = per_game_values(proj, st)
    pgv1 = per_game_values(win_proj, st)
    values, _, _ = compute_values(proj, st)
    mine = [x for x in lg.rosters[me_id] if x in proj.df.index]
    healthy = [x for x in mine if yahoo_tag.get(x) not in IL_TAGS and x not in espn_out] or mine
    drop = min(healthy, key=lambda x: float(values["value"].get(x, 99.0)))
    pd_drop = float(pgv0.get(drop, 0.0))
    p_drop = min(1.0, float(proj.df.at[drop, "gp"]) / 82.0)
    for c in cands:
        pid = c["player_id"]
        pc = min(1.0, float(proj.df.at[pid, "gp"]) / 82.0)
        W = float(c["window_games"])
        c["name"] = str(raw_df.at[pid, "name"])
        c["p_play"] = round(pc, 3)
        c["per_game_value_base"] = round(float(pgv0.get(pid, np.nan)), 3)
        c["per_game_value_opp"] = round(float(pgv1.get(pid, np.nan)), 3)
        c["window_value_gain_vs_drop"] = round(W * (pc * float(pgv1.get(pid, 0.0)) - p_drop * pd_drop), 3)
        c["window_value_uplift"] = round(W * pc * (float(pgv1.get(pid, 0.0)) - float(pgv0.get(pid, 0.0))), 3)
        if pid in box.index:
            b = box.loc[pid]
            c["last3_mpg"] = None if pd.isna(b["last3_mpg"]) else float(b["last3_mpg"])
            c["starts_last3"], c["games_played"] = int(b["starts_last3"]), int(b["games_played"])
        else:
            c["last3_mpg"], c["starts_last3"], c["games_played"] = None, 0, 0
        fa = fas.get(pid)
        c["available"] = pid not in owner
        c["ownership"] = (fa.ownership if fa else ("presumed FA (not in the capture's top list; not on any roster)"
                                                    if pid not in owner else f"rostered by team {owner[pid]}"))
        c["why"] = _why(c)
    cands.sort(key=lambda c: (-c["available"], -c["window_value_gain_vs_drop"]))
    listed = cands[:top]
    # override files: only listed candidates' rows changed
    out = out or REPO_ROOT / "var" / "opportunity" / f"opportunity_{d0.isoformat()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    window_raw, season_raw = override_frames(raw_df, [c for c in listed if c["available"]])
    cols = list(raw_csv.columns)
    season_raw.reset_index()[cols].to_csv(out, index=False)
    wpath = out.with_name(out.stem + ".window.csv")
    window_raw.reset_index()[cols].to_csv(wpath, index=False)
    changed = [c["player_id"] for c in listed if c["available"]]
    # evaluation of the top available candidates
    ev_c = [c for c in listed if c["available"]][:eval_top]
    if ev_c and (season_sims > 0 or week_sims > 0):
        mk = market or REPO_ROOT / "var" / "projections_alt" / "market.inseason.csv"
        ev, w = evaluate(ev_c, out, _as_projset(window_raw, "w").df, proj, st, lg, me_id, drop,
                         LEAGUE_SCHEDULE_PATH, nba, fas, d0, season_sims, week_sims, seed, mk, raw_league,
                         newest_capture(), )
        warnings += w
        for c in ev_c:
            c["evaluation"] = ev[c["player_id"]]
    meta_out = {"version": OPPORTUNITY_VERSION, "as_of": d0.isoformat(), "source_projections": str(proj_path),
                "source_sha256": proj.sha256, "changed_rows": changed,
                "season_file": str(out), "window_file": str(wpath),
                "semantics": {"season_file": "rest-of-season blend: mpg = (W*opp + (R-W)*mpg)/R, W = window games x "
                                             "gp/82, R = ros_games; counting stats scaled by the mpg ratio; "
                                             "mpg_next5 = opportunity mpg (for `fantasy sim compare --projections`)",
                              "window_file": "per-game line during the window: counting stats scaled to the "
                                             "opportunity mpg; mpg = mpg_next5 = opportunity mpg (for week tools)"},
                "params": p.to_dict()}
    out.with_name(out.stem + ".meta.json").write_text(json.dumps(meta_out, indent=1, default=str))
    return {
        "ok": True, "version": OPPORTUNITY_VERSION, "as_of": d0.isoformat(), "team": me_id,
        "drop": {"player_id": drop, "name": str(raw_df.at[drop, "name"]), "per_game_value": round(pd_drop, 3),
                 "rule": "my lowest season-value player not tagged Out/IL"},
        "absences": [a.to_dict() | {"window": _win(a, p)} for a in absences],
        "n_candidates": len(cands), "candidates": listed,
        "override": {"season_csv": str(out), "window_csv": str(wpath), "changed_rows": changed},
        "labels": {
            "vacated_mpg": "FITTED share rule (2024-25 + 2025-26 box scores, placebo-corrected): share 0.77 (SE 0.034) "
                           "of the star's mpg, by depth bucket x position match, x 0.54 calibration for free-agent-like players (SE 0.057; 2024-25 backtest; OOS 2025-26 calibrated slope 1.07 +- 0.16); docs/research/opportunity_detector.md",
            "window_games": "FITTED absence survival given games missed (or ESPN's return estimate when given), capped "
                            f"at {p.horizon} games (PRIOR)",
            "mpg_next5": "FITTED minutes model A (minutes-0.2.0)",
            "thresholds": f"PRIOR: jump >= {p.jump_min} mpg over preseason, vacated >= {p.vacated_min} mpg, "
                          f"observed after {p.observed_games} games played in the absence, cap {p.mpg_cap} mpg",
            "per_minute_rates": "kept from the in-season projections (FITTED: ratio during absences ~1.00-1.04)",
            "after_window": "minutes-model level (model B mpg); no retention credit (conservative)",
            "per_game_value": "G-score per game above replacement (fantasy.inseason.stream.per_game_values)",
            "window_value_gain_vs_drop": "window games x (p_play x per-game value of the opportunity line - the drop's)",
            "evaluation": "week: stream tool's matchup model (lineup-0.2.0), paired MC on the window lines; season: "
                          "paired season sim (season-sim) on the season override, add candidate / drop `drop`; "
                          "± = Monte Carlo SE only",
            "verdict": "helps = season ΔP(playoffs) or ΔP(title) > 2 SE under every set, neither < -2 SE (stream "
                       "rule); speculative = not significant but week ΔP(win) > 2 SE or blend ΔP(playoffs) > 1 SE "
                       "(PRIOR); else noise / hurts / tradeoff"},
        "inputs": {"projections": str(proj_path), "projections_as_of": meta.get("as_of"),
                   "projections_version": meta.get("model_version"), "free_agents": str(fa_path) if fa_path else None,
                   "free_agents_sha256": sha256_file(Path(fa_path)) if fa_path else None,
                   "injuries_fetched": inj_date, "league_file": str(lf),
                   "news": (news or {}).get("news_file"), "box_scores": str(box_path)},
        "warnings": warnings,
        "note": "Analysis only: never touches Yahoo. Every add/drop needs the user's approval.",
    }


def _win(a: Absence, p: OpportunityParams) -> dict:
    from fantasy.inseason.opportunity import absence_window

    g, s = absence_window(a, p)
    return {"games": round(g, 2), "source": s}


def _capture_date(path: Path, fallback: date) -> date:
    from fantasy.inseason.freeagents import capture_date

    try:
        return capture_date(json.loads(Path(path).read_text()), fallback)
    except (OSError, ValueError):
        return fallback


def _as_projset(raw: pd.DataFrame, label: str):
    """A raw (CSV-like) frame -> ProjectionSet via the standard loader (positions parsed, numeric columns)."""
    import tempfile

    from fantasy.model.lines import load_projections

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        raw.reset_index().to_csv(f.name, index=False)
        ps = load_projections(f.name)
    Path(f.name).unlink(missing_ok=True)
    return replace(ps, path=label)


def _why(c: dict) -> str:
    dr = sorted(c.get("drivers", []), key=lambda d: -d["extra_mpg"])
    fresh = [d for d in dr if not d["observed"]]
    if c["signal"].startswith("vacated") and fresh:
        d = fresh[0]
        src = d["sources"][0] if d["sources"] else {}
        return (f"{d['name']} ({d['mpg']:.0f} mpg) out ~{d['window_games']:.0f} g [{src.get('source', '?')} "
                f"{src.get('date', '')}]: +{c['vacated_unobserved_mpg']:.1f} mpg expected -> {c['opp_mpg']:.1f} mpg "
                f"(preseason {c['mpg_pre']:.1f}, now {c['mpg_next5']:.1f})")
    ctx = f"; {dr[0]['name']} out" if dr else ""
    return (f"minutes up: model-A next-5 {c['mpg_next5']:.1f} vs preseason {c['mpg_pre']:.1f} mpg "
            f"(last 3: {c.get('last3_mpg') or '-'}; starts {c.get('starts_last3', 0)}/3{ctx})")


def text_report(out: dict) -> str:
    lines = [f"opportunity {out['as_of']} ({out['version']}): {out['n_candidates']} candidates; drop = "
             f"{out['drop']['name']}"]
    for c in out["candidates"]:
        ev = c.get("evaluation") or {}
        s = (ev.get("season") or {}).get("blend", {})
        po = s.get("p_playoffs")
        tail = f"  ΔP(po) {po['delta'] * 100:+.1f}±{po['delta_se'] * 100:.1f}pp [{ev.get('verdict')}]" if po else ""
        lines.append(f"  {c['name']:<22} {c['team']:<4} {c['ownership'][:12]:<12} {c['signal']:<18} opp "
                     f"{c['opp_mpg']:>4.1f} mpg x {c['window_games']:>4.1f} g  gain {c['window_value_gain_vs_drop']:+.2f}"
                     f"{tail}\n      {c['why']}")
    lines += [f"  warning: {w}" for w in out["warnings"]]
    return "\n".join(lines)
