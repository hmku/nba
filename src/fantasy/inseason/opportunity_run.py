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
    calibrated_gain,
    detect,
    override_frames,
)
from fantasy.sim import SIM_VERSION

INJURY_FLAGS = {"indefinite", "season_ending", "surgery", "timeline", "injury_exit"}
NEWS_ONLY_FLAGS = {"indefinite", "season_ending", "surgery", "injury_exit"}  # enough on their own (timeline is not)
YAHOO_OUT = {"O", "INJ", "OFS", "IR"}


# ------------------------------------------------------------------ inputs
def recent_box(pg: pd.DataFrame, as_of: date) -> tuple[pd.DataFrame, dict[str, list[bool]]]:
    """Per player (box scores before `as_of`): last3_mpg (last 3 games played), starts_last3 (of his team's last 3
    games), games_played, prev_mpg (games played before those 3); and played flags over his CURRENT team's games so far (chronological)."""
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
        rows.append({"player_id": str(pid), "name": str(g["player_name"].iloc[-1]) if "player_name" in g else str(pid),
                     "box_team": team, "games_played": int(len(pl)),
                     "last3_mpg": round(float(pl["min"].tail(3).mean()), 1) if len(pl) else np.nan,
                     "prev_mpg": round(float(pl["min"].iloc[:-3].mean()), 1) if len(pl) > 3 else np.nan,
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
def _verdict(season: dict | None, week: dict | None, sig: float) -> tuple[str, str]:
    """Verdict of one candidate. `season` = a stream-chain result (`streamscan.make_stream_season_eval`: the candidate
    for his window only, then the realistic F_k free agent), judged by the stream tool's rule
    (`streamscan.stream_verdict`: helps only if the chain's ΔP(playoffs) or ΔP(title) > sig·SE and neither < -sig·SE
    under every projection set, and the stream is not significantly worse than the direct move). 'noise' becomes
    'speculative' when this week's ΔP(win) > sig·SE or the display set's chain ΔP(playoffs) > 1 SE (PRIOR).
    No season result -> 'unevaluated' (never present a season verdict without the chain)."""
    from fantasy.inseason.streamscan import stream_verdict

    wk = bool(week and week.get("d_p_win", 0) > 0 and week["d_p_win"] >= sig * week["d_p_win_se"])
    if not season:
        return "unevaluated", ("no season chain evaluated; " + ("this week's ΔP(win) beyond 2 SE" if wk
                                                               else "week effect within noise"))
    v = stream_verdict(season, sig)
    if v == "helps":
        from fantasy.inseason.streamscan import decisive_sets, sig_sign

        prim = season["primary_chain"]
        adds = all(any(sig_sign(season["by_set"][k][prim]["stream_vs_direct"][m], sig) > 0
                       for m in ("p_playoffs", "p_title")) for k in decisive_sets(season["by_set"]))
        if not adds:
            return "direct_helps", ("the chain helps, but the window stint adds nothing beyond the direct move (drop -> "
                                    "the chain free agent): the gain is the drop, not this opportunity")
        return v, ("stream chain ΔP(playoffs) or ΔP(title) beyond 2 SE under every projection set, neither down, and "
                   "the stint beats the direct move beyond 2 SE")
    if v in ("hurts", "tradeoff", "direct_better"):
        return v, f"stream chain: {v}"
    # the opportunity's own contribution = the stint vs the direct move (display set), not the drop -> chain FA part
    first = season.get("display_set") or next(iter(season.get("by_set") or {}), None)
    svd = (season.get("by_set") or {}).get(first, {}).get(season.get("primary_chain"), {}).get("stream_vs_direct", {})
    b = svd.get("p_playoffs")
    pos = bool(b and b["delta"] > 0 and b["delta"] >= b["delta_se"])
    if wk or pos:
        why = "this week's ΔP(win) beyond 2 SE" if wk else "stint vs direct ΔP(playoffs) > 1 SE but < 2 SE"
        return "speculative", why + "; season effect not resolved"
    return "noise", "season chain and week effects within noise"


def evaluate(cands: list[dict], week_fn, season_fn, sig: float = 2.0) -> dict[str, dict]:
    """{player_id: {week, season, verdict, verdict_why}}. week_fn(c) -> dict | None (with "mc"), season_fn(c) ->
    stream-chain result | None; either may be None (skipped). Pure: the builders below do the simulation."""
    out = {}
    for c in cands:
        wk = week_fn(c) if week_fn else None
        se = season_fn(c) if season_fn else None
        v, why = _verdict(se, (wk or {}).get("mc"), sig)
        out[c["player_id"]] = {"week": wk, "season": se, "verdict": v, "verdict_why": why}
    return out


def _fa_record(pid: str, df: pd.DataFrame, fas: dict):
    from fantasy.inseason.freeagents import FreeAgent
    from fantasy.lineup.players import parse_positions

    if pid in fas:
        return fas[pid]
    r = df.loc[pid]
    return FreeAgent(pid, str(r["name"]), str(r["team"]), tuple(parse_positions(r["positions"])), None, None, "none")


def build_week_fn(cands: list[dict], raw_df: pd.DataFrame, settings, lg, me_id: int, drop: str, sched: Path, nba,
                  fas: dict, d0: date, week_sims: int, seed: int, raw_league: dict, roster_cap: Path | None,
                  warnings: list[str], waiver_lag: int = 1):
    """This week's paired matchup Monte Carlo (the stream tool's model) with each candidate on a WEEK line: the
    opportunity minutes for his first `window_games` games this week only, model B after (opportunity.week_mpg)."""
    from fantasy.inseason.opportunity import scaled_lines, week_mpg
    from fantasy.inseason.streamscan import StreamParams, scan_streams
    from fantasy.lineup.plan import generic_per_game_values
    from fantasy.lineup.players import load_team
    from fantasy.model.valuation import compute_values
    from fantasy.sim.schedule import load_schedule_json

    ms = load_schedule_json(sched)
    fw = next((w for w in (ms.calendar or []) if w.start <= d0 <= w.end), None)
    opp_id = ms.opponent(me_id, fw.week) if fw else None
    if fw is None or opp_id is None or week_sims <= 0:
        warnings.append("week evaluation skipped (no league-schedule week/opponent for this date, or --week-sims 0)")
        return None
    if d0 > fw.start:
        warnings.append(f"MID-WEEK: week ΔP(win) ignores stats banked {fw.start}..{d0 - timedelta(days=1)} (assumed 0-0); "
                        "p_win_before is not the week's odds - quote week effects from a Monday run only")
    targets, week_info = {}, {}
    for c in cands:
        pid = c["player_id"]
        eff = _fa_record(pid, raw_df, fas).available_from(d0, waiver_lag)
        g = sum(1 for d, v, h in nba.games if eff <= d <= fw.end and c["team"] in (v, h))
        targets[pid] = week_mpg(float(raw_df.at[pid, "mpg"]), float(c["opp_mpg"]), float(c["window_games"]), g)
        week_info[pid] = {"week_games": g, "week_mpg": round(targets[pid], 2)}
    wproj = _as_projset(scaled_lines(raw_df, targets), "opportunity-week")
    wdf = wproj.df
    vals, _, _ = compute_values(wproj, settings)

    def entry(t: int) -> dict:
        e = raw_league["teams"][str(t)]
        return e if isinstance(e, dict) else {"players": e}

    me, w1 = load_team(me_id, wdf, lg.rosters[me_id], roster_cap, entry(me_id))
    opp, w2 = load_team(opp_id, wdf, lg.rosters[opp_id], roster_cap, entry(opp_id))
    warnings += w1 + w2
    flist = [_fa_record(c["player_id"], wdf, fas) for c in cands]
    days = [d0 + timedelta(i) for i in range((fw.end - d0).days + 1)]
    params = StreamParams(week_sims=week_sims, season_sims=0, season_top=2 * len(flist) + 2, top=0, seed=seed,
                          waiver_lag=waiver_lag)
    rep = scan_streams(wdf, settings, nba, me, opp, flist, days, generic_per_game_values(wproj, settings),
                       vals["value"], params, [drop])
    rows = {r["add"]: r for r in rep["pairs"] if r["drop"] == drop}

    def fn(c: dict) -> dict | None:
        r = rows.get(c["player_id"])
        if r is None:
            return None
        return {"week": fw.week, "window": rep["window"], "opponent": opp_id, "effective": r["effective"],
                **week_info[c["player_id"]], "add_games": r["add_games"], "add_starts": r["add_starts"],
                "d_exp_cats_analytic": r["week_d_exp_cats"], "d_p_win_analytic": r["week_d_p_win"],
                "mc": r.get("week_mc")}

    return fn


def build_season_fn(cands: list[dict], window_raw: pd.DataFrame, base_raw: pd.DataFrame, settings, lg, me_id: int,
                    drop: str, sched: Path, nba, fas: dict, d0: date, season_sims: int, seed: int,
                    market_csv: Path | None, taken: set[str], warnings: list[str], chain_k: int = 5,
                    waiver_lag: int = 1):
    """Season effect of the opportunity as a STREAM CHAIN (the stream tool's `make_stream_season_eval`, position-aware):
    drop -> candidate on his WINDOW line from his effective day until the day after his ceil(window)-th game
    (opportunity.stint_end_day), then the (chain_k+1)-th best realistic free agent for the rest of the season; under
    the blend and (when present) market projection sets."""
    from fantasy.inseason.freeagents import realistic_refills
    from fantasy.inseason.opportunity import stint_end_day
    from fantasy.inseason.possim import make_sim
    from fantasy.inseason.streamscan import make_stream_season_eval
    from fantasy.sim.context import build_context

    if season_sims <= 0:
        return None
    sets = {"blend": _as_projset(window_raw, "opportunity-window")}
    if market_csv is not None and Path(market_csv).exists():
        mk = pd.read_csv(market_csv).drop_duplicates("player_id").set_index("player_id")
        for c in cands:
            pid = c["player_id"]
            if pid in mk.index and float(base_raw.at[pid, "mpg"]) > 0:
                f = float(window_raw.at[pid, "mpg"]) / float(base_raw.at[pid, "mpg"])
                for col in ("fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"):
                    mk.at[pid, col] = float(mk.at[pid, col]) * f
                if "mpg" in mk.columns:
                    mk.at[pid, "mpg"] = float(mk.at[pid, "mpg"]) * f
        sets["market"] = _as_projset(mk, "opportunity-window-market")
    else:
        warnings.append(f"market projections {market_csv} not found: the chain verdict uses the blend set only")
    ctxs = {k: build_context(v, settings, lg.team_ids, schedule_file=sched, nba=nba) for k, v in sets.items()}
    sims = {k: make_sim(c, season_sims, seed) for k, c in ctxs.items()}
    bases = {k: s.run(lg) for k, s in sims.items()}
    fa_list = list(fas.values())
    pool = [p for p in realistic_refills(fa_list, ctxs["blend"].values["value"].to_dict()) if p not in taken]
    names = ctxs["blend"].values["name"].astype(str).to_dict()

    def fn(c: dict) -> dict:
        pid = c["player_id"]
        eff = _fa_record(pid, window_raw, fas).available_from(d0, waiver_lag)
        end = stint_end_day(c["team"], eff, float(c["window_games"]), nba.games)
        ev = make_stream_season_eval(sims, lg, me_id, [p for p in pool if p != pid], end - timedelta(days=1),
                                     chain_k, (end - eff).days, names=names, bases=bases)
        return ev(pid, drop, eff)

    return fn


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
        rising = unprojected_risers(box, set(df.index), set(owner))
        if rising:
            warnings.append(f"{len(rising)} unprojected player(s) on no roster with rising minutes (look manually; no "
                            "projection row, so the detector cannot value them): "
                            + ", ".join(f"{r['name']} ({r['team']}, last-3 {r['last3_mpg']} mpg)" for r in rising[:8]))
    else:
        box, played_flags, rising = pd.DataFrame(), {}, []
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
    news, nf = None, None
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
        v0, v1 = float(pgv0.get(pid, 0.0)), float(pgv1.get(pid, 0.0))
        c["window_value_gain_vs_drop_raw"] = round(W * (pc * v1 - p_drop * pd_drop), 3)
        c["window_value_gain_vs_drop"] = round(calibrated_gain(W, pc, v0, v1, p_drop, pd_drop, p.value_calib), 3)
        c["window_value_uplift"] = round(W * pc * p.value_calib * (v1 - v0), 3)
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
    mk = market or REPO_ROOT / "var" / "projections_alt" / "market.inseason.csv"
    if ev_c and (season_sims > 0 or week_sims > 0):
        wfn = build_week_fn(ev_c, raw_df, st, lg, me_id, drop, LEAGUE_SCHEDULE_PATH, nba, fas, d0, week_sims, seed,
                            raw_league, newest_capture(), warnings)
        sfn = build_season_fn(ev_c, window_raw, raw_df, st, lg, me_id, drop, LEAGUE_SCHEDULE_PATH, nba, fas, d0,
                              season_sims, seed, mk, set(owner), warnings)
        ev = evaluate(ev_c, wfn, sfn)
        for c in ev_c:
            c["evaluation"] = ev[c["player_id"]]
    hashes = {"projections": proj.sha256, "injuries": _sha(inj_path), "news": _sha(Path(nf) if nf else None),
              "box_scores": _sha(box_path), "free_agents": _sha(Path(fa_path) if fa_path else None),
              "league_file": _sha(Path(lf)), "market": _sha(Path(mk))}
    meta_out = {"version": OPPORTUNITY_VERSION, "as_of": d0.isoformat(), "source_projections": str(proj_path),
                "source_sha256": proj.sha256, "changed_rows": changed, "input_sha256": hashes,
                "sim_version": SIM_VERSION,
                "season_file": str(out), "window_file": str(wpath),
                "semantics": {"season_file": "rest-of-season blend: mpg = (W*opp + (R-W)*mpg)/R, W = window games x "
                                             "gp/82, R = ros_games; counting stats scaled by the mpg ratio; "
                                             "mpg_next5 = opportunity mpg (for `fantasy sim compare --projections`)",
                              "window_file": "per-game line during the window: counting stats scaled to the "
                                             "opportunity mpg; mpg = mpg_next5 = opportunity mpg (for week tools)"},
                "params": p.to_dict()}
    out.with_name(out.stem + ".meta.json").write_text(json.dumps(meta_out, indent=1, default=str))
    return {
        "ok": True, "version": OPPORTUNITY_VERSION, "sim_version": SIM_VERSION, "as_of": d0.isoformat(), "team": me_id,
        "drop": {"player_id": drop, "name": str(raw_df.at[drop, "name"]), "per_game_value": round(pd_drop, 3),
                 "rule": "my lowest season-value player not tagged Out/IL"},
        "absences": [a.to_dict() | {"window": _win(a, p)} for a in absences],
        "n_candidates": len(cands), "candidates": listed, "unprojected_risers": rising,
        "override": {"season_csv": str(out), "window_csv": str(wpath), "changed_rows": changed},
        "labels": {
            "vacated_mpg": "FITTED share rule (2024-25 + 2025-26 box scores, placebo-corrected): share 0.77 (SE 0.034) "
                           "of the star's mpg, by depth bucket x primary-position match, x "
                           f"{p.vacated_calib} calibration for free-agent-like players (FITTED, 2024-25 backtest); "
                           "docs/research/opportunity_detector.md",
            "window_games": "FITTED absence survival given games missed; with an ESPN return estimate the SHORTER of "
                            f"the two (PRIOR rule, both reported); capped at {p.horizon} games (PRIOR)",
            "mpg_next5": "FITTED minutes model A (minutes-0.2.0)",
            "thresholds": f"PRIOR: jump >= {p.jump_min} mpg over preseason, vacated >= {p.vacated_min} mpg, "
                          f"absence observed after {p.observed_games} team games, cap {p.mpg_cap} mpg",
            "jump": "a jump candidate's minutes are model A's (minutes-0.2.0) with no extra adjustment: the detector "
                    "only surfaces 'model A says his minutes rose' (backtest: no incremental minutes beyond model A)",
            "per_minute_rates": "kept from the in-season projections (FITTED: ratio during absences ~1.00-1.04)",
            "after_window": "minutes-model level (model B mpg); no retention credit (conservative)",
            "per_game_value": "G-score per game above replacement (fantasy.inseason.stream.per_game_values)",
            "window_value_gain_vs_drop": "window games x (p_play x (base per-game value + value_calib x opportunity "
                                         f"uplift) - the drop's); value_calib {p.value_calib} FITTED "
                                         "(2024-25 backtest, docs/research/opportunity_detector.md); *_raw = uncalibrated",
            "evaluation": "week: stream tool's matchup model (lineup-0.2.0), paired MC, candidate on a week line (the "
                          "opportunity minutes for his first window games this week only); season: the stream tool's "
                          "position-aware STREAM CHAIN (make_stream_season_eval): drop -> candidate on his window line "
                          "for the window (to the day after his ceil(window)-th game) -> the 6th-best realistic free "
                          f"agent ({SIM_VERSION}); blend and market sets; ± = Monte Carlo SE only",
            "verdict": "streamscan.stream_verdict on the chain (helps = ΔP(playoffs) or ΔP(title) > 2 SE under every "
                       "set, neither < -2 SE, stream not worse than direct); speculative = noise but week ΔP(win) > "
                       "2 SE or the stint-vs-direct ΔP(playoffs) > 1 SE (PRIOR); direct_helps = the chain helps only through the drop; unevaluated = no chain run"},
        "inputs": {"projections": str(proj_path), "projections_as_of": meta.get("as_of"),
                   "projections_version": meta.get("model_version"), "free_agents": str(fa_path) if fa_path else None,
                   "free_agents_sha256": sha256_file(Path(fa_path)) if fa_path else None,
                   "injuries_fetched": inj_date, "league_file": str(lf),
                   "news": (news or {}).get("news_file"), "box_scores": str(box_path), "sha256": hashes},
        "warnings": warnings,
        "note": "Analysis only: never touches Yahoo. Every add/drop needs the user's approval.",
    }


def _win(a: Absence, p: OpportunityParams) -> dict:
    from fantasy.inseason.opportunity import absence_window, expected_absence_games

    g, s = absence_window(a, p)
    return {"games": round(g, 2), "source": s, "fitted_games": round(expected_absence_games(a.missed, p), 2),
            "espn_games": a.espn_games, "espn_return_date": a.return_date}


def _sha(path: Path | None) -> str | None:
    import hashlib

    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path is not None and Path(path).exists() else None
    except OSError:
        return None


def unprojected_risers(box: pd.DataFrame, projected: set[str], rostered: set[str], min_last3: float = 15.0,
                       min_rise: float = 6.0) -> list[dict]:
    """Box-score players with no projection row and on no roster whose minutes are rising sharply: last-3 mpg >=
    min_last3 and (no earlier games, or last-3 minus earlier mpg >= min_rise). Thresholds are PRIORS; a reading list
    for the agent, not a model."""
    out = []
    if box is None or box.empty:
        return out
    for pid, r in box.iterrows():
        if pid in projected or pid in rostered or pd.isna(r["last3_mpg"]):
            continue
        prev = r.get("prev_mpg")
        if r["last3_mpg"] >= min_last3 and (pd.isna(prev) or r["last3_mpg"] - prev >= min_rise):
            out.append({"player_id": pid, "name": str(r.get("name", pid)), "team": str(r["box_team"]),
                        "last3_mpg": float(r["last3_mpg"]), "prev_mpg": None if pd.isna(prev) else float(prev),
                        "games_played": int(r["games_played"])})
    return sorted(out, key=lambda x: -x["last3_mpg"])


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
