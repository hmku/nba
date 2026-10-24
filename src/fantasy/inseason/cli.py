"""`fantasy inseason ...` — position-aware lineups, streaming ranker, trade evaluation (analysis only)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer

from fantasy.cli_common import emit, fail
from fantasy.inseason import INSEASON_VERSION

app = typer.Typer(no_args_is_help=True, help="In-season analysis: lineups, streaming, trades (never touches Yahoo).")

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output")]
LeagueOpt = Annotated[Path | None, typer.Option("--league-file", help="Rosters JSON ({my_team, teams: {id: {name, players}}})")]
DraftOpt = Annotated[str | None, typer.Option("--draft-id", envvar="FANTASY_DRAFT_ID", help="Rosters from this draft")]
TeamOpt = Annotated[int | None, typer.Option("--team", help="Team id (default: my team)")]
ProjOpt = Annotated[Path | None, typer.Option("--projections", help="Projections CSV (default data/processed)")]
NbaOpt = Annotated[Path | None, typer.Option("--nba-schedule", help="NBA games CSV (default data/raw/bbref_schedule)")]


def _league(league_file: Path | None, draft_id: str | None, as_json: bool):
    from fantasy.sim.league import from_draft, from_json

    if league_file:
        return from_json(league_file)
    from fantasy.draft import store

    draft = store.get_draft(draft_id)
    if not draft:
        fail("no rosters: pass --league-file or --draft-id", as_json, code="not_found")
    assert draft is not None
    return from_draft(draft, store.get_picks(draft["draft_id"]))


def _days(start: str, end: str) -> list[date]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [a + timedelta(i) for i in range((b - a).days + 1)]


def _nba(proj, nba_schedule: Path | None):
    from fantasy.sim.schedule import season_schedule

    nba, _ = season_schedule(nba_schedule, sorted({str(t) for t in proj.df["team"].dropna()}))
    return nba


MarketOpt = Annotated[Path | None, typer.Option("--market-projections", help="Conservative projections CSV (`fantasy data "
                                                "alt-projections --blend market --out ..`; default "
                                                "var/projections_alt/market.csv)")]
StressOpt = Annotated[Path | None, typer.Option("--stress-projections", help="Independent stress set (`fantasy data "
                                                "alt-projections --blend espn-only --blend-gp espn=1 --out ..`: ESPN "
                                                "stats AND games; default var/projections_alt/espn-only-gp.csv)")]
NoAltOpt = Annotated[bool, typer.Option("--no-alt", help="Blend projections only (no market/stress sets)")]
SETS_NOTE = ("blend and market share OUR fitted availability model (games played); the stress set (espn-only-gp) "
             "uses ESPN's per-game stats AND ESPN's games (known ~+11 games/player optimistic), so it is the only set "
             "independent of our availability model. When the blend is in-season but the stress file was built from "
             "the PRESEASON base, it is renamed 'stress_preseason' and reported as a sensitivity only: it never votes in "
             "'helps under every set' (it would mix preseason injuries/roles with current ones)")


def _stale_vs_inseason(path: Path, blend_path: str | Path) -> bool:
    """True when the blend is an in-season file (`*.inseason.csv`) but `path` is a preseason build: its name is not
    `*.inseason.csv` and its .meta.json has no in-season `as_of`. Such a set still has preseason injuries/roles."""
    import json

    if not str(blend_path).endswith(".inseason.csv") or path.name.endswith(".inseason.csv"):
        return False
    meta = path.with_name(path.stem + ".meta.json")
    try:
        return not (meta.exists() and json.loads(meta.read_text()).get("as_of"))
    except (OSError, ValueError):
        return True


def _proj_sets(ctx, st, lg, sched, market: Path | None, stress: Path | None, no_alt: bool,
               warnings: list[str]) -> tuple[dict, dict]:
    """{name: SimContext} for blend (= ctx) + market + stress (when the files exist), and their input info."""
    from fantasy.data.provenance import REPO_ROOT
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context

    out, info = {"blend": ctx}, {"blend": {"path": ctx.proj.path, "sha256": ctx.proj.sha256}}
    if no_alt:
        return out, info
    alt = REPO_ROOT / "var" / "projections_alt"
    for name, path, recipe in (("market", market or alt / "market.csv", "--blend market"),
                               ("stress", stress or alt / "espn-only-gp.csv", "--blend espn-only --blend-gp espn=1")):
        if name == "market" and market is None and (alt / "market.inseason.csv").exists() \
                and str(ctx.proj.path).endswith(".inseason.csv"):
            path = alt / "market.inseason.csv"  # in-season blend -> in-season market set (update-projections)
            import hashlib
            import json

            meta = json.loads((alt / "market.inseason.meta.json").read_text()) if (
                alt / "market.inseason.meta.json").exists() else {}
            if meta.get("prior_sha256") != hashlib.sha256((alt / "market.csv").read_bytes()).hexdigest():
                warnings.append("market.inseason.csv was built from another market.csv: re-run "
                                "`fantasy data update-projections`")
        if Path(path).exists():
            pr = load_projections(Path(path))
            if name == "stress" and _stale_vs_inseason(Path(path), ctx.proj.path):
                name = "stress_preseason"  # streamscan.SENSITIVITY_SETS: reported, never votes
                warnings.append(f"stress projections {path} were built from the preseason base while the blend is "
                                "in-season: reported as 'stress_preseason' (sensitivity only), excluded from the "
                                "'helps under every set' rule")
            out[name] = build_context(pr, st, lg.team_ids, schedule_file=sched, nba=ctx.nba)
            info[name] = {"path": str(path), "sha256": pr.sha256}
        else:
            warnings.append(f"{name} projections {path} not found (`fantasy data alt-projections {recipe} --out "
                            f"{path}`): that set is skipped, so no result can be confirmed under it")
    return out, info


def _fa_pool(fa_path: Path | None, day: date, values, taken: set[str], warnings: list[str]) -> list[str]:
    """Realistic free agents (capture; O/INJ skipped), best first by `values`, not rostered."""
    from fantasy.inseason.freeagents import load_free_agents, realistic_refills

    if fa_path is None:
        warnings.append("no free-agent capture: refills use the best unrostered projected player (optimistic)")
        return [p for p in values.index if p not in taken]
    fas, w = load_free_agents(Path(fa_path), day)
    warnings += w
    return [p for p in realistic_refills(fas, values["value"].to_dict()) if p not in taken]


def _fmt3(d: dict) -> str:
    return "  ".join(f"{k} {d[k]['delta'] * (100 if k != 'exp_cat_wins' else 1):+.2f}±"
                     f"{(d[k]['delta_se'] or 0) * (100 if k != 'exp_cat_wins' else 1):.2f}"
                     for k in ("p_playoffs", "p_title", "exp_cat_wins"))


@app.command("lineup")
def lineup_cmd(
    start: Annotated[str, typer.Option("--start", help="First day YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--end", help="Last day YYYY-MM-DD")],
    league_file: LeagueOpt = None, draft_id: DraftOpt = None, team: TeamOpt = None,
    sims: Annotated[int, typer.Option("--sims", help="0 = everyone healthy; >0 = Monte Carlo with p_play = gp/82")] = 0,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    projections: ProjOpt = None, nba_schedule: NbaOpt = None, as_json: JsonOpt = False,
) -> None:
    """Position-aware daily lineups: games available vs started, and games lost to slot rules."""
    from fantasy.inseason.lineup import PlayerSpec, simulate_lineups, team_days
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections, parse_positions
    from fantasy.model.valuation import compute_values

    lg = _league(league_file, draft_id, as_json)
    t = team or lg.my_team
    proj, st = load_projections(projections), load_settings()
    values, _, _ = compute_values(proj, st)
    df = proj.df
    specs = []
    for p in lg.rosters[t]:
        if p not in df.index:
            continue
        is_out = str(df.loc[p, "status"]) in ("out", "out_for_season")
        specs.append(PlayerSpec(p, str(df.loc[p, "name"]), str(df.loc[p, "team"]), parse_positions(df.loc[p, "positions"]),
                                float(values["value"].get(p, 0.0)),
                                0.0 if is_out else (min(1.0, float(df.loc[p, "gp"]) / 82) if sims > 0 else 1.0)))
    days = _days(start, end)
    res = simulate_lineups(specs, st.starting_slots, days, team_days(_nba(proj, nba_schedule).games, days), n_sims=sims, seed=seed)
    out: dict[str, Any] = {"ok": True, "version": INSEASON_VERSION, "team": t, "summary": res.summary(), "players": res.per_player(),
           "days": res.per_day(), "notes": res.notes}
    s = out["summary"]
    lines = [f"team {t} {start}..{end}: {s['player_games']} player-games, {s['starts']} starts, "
             f"{s['lost_to_positions']} lost to positions, {s['lost_to_slot_count']} lost to the 10-slot count"]
    lines += [f"  {p['name']:<24} {p['positions']:<10} games {p['games']:>2}  starts {p['exp_starts']}" for p in out["players"]]
    emit(out, as_json, "\n".join(lines))


@app.command("stream")
def stream_cmd(
    day: Annotated[str, typer.Option("--date", help="Today YYYY-MM-DD (default: today); the scan covers the rest of "
                                     "this matchup week")] = "",
    league_file: LeagueOpt = None, team: TeamOpt = None,
    free_agents: Annotated[Path | None, typer.Option("--free-agents", help="Yahoo free-agent capture (default newest "
                                                     "data/raw/yahoo/free_agents_*.json)")] = None,
    roster: Annotated[Path | None, typer.Option("--roster", help="Yahoo roster capture for tags/positions (default "
                                                "newest data/raw/yahoo/league_rosters_*.json)")] = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON (default "
                                                  "data/manual/league_schedule.json)")] = None,
    drop: Annotated[str, typer.Option("--drop", help="Drop candidates, comma-separated (default: my 3 lowest values)")] = "",
    adds_used: Annotated[int, typer.Option("--adds-used", help="Adds already used this matchup week")] = 0,
    accumulated: Annotated[Path | None, typer.Option("--accumulated", help='JSON {"me": {fgm: ..}, "opp": {..}} '
                                                     "raw totals already banked this week")] = None,
    week_sims: Annotated[int, typer.Option("--week-sims", help="Paired matchup Monte Carlo sims (shortlist)")] = 4000,
    season_sims: Annotated[int, typer.Option("--season-sims", help="Paired season sims per shortlisted pair "
                                             "(0 = skip the season check)")] = 2000,
    season_top: Annotated[int, typer.Option("--season-top", help="Pairs that get a season sim")] = 8,
    top: Annotated[int, typer.Option("--top", help="Extra pairs listed beyond the shortlist")] = 10,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    chain_k: Annotated[int, typer.Option("--chain-k", help="PRIOR: after the stint we hold the (k+1)-th best realistic "
                                         "free agent (0 = best)")] = 5,
    stint_days: Annotated[int | None, typer.Option("--stint-days", help="Streamer kept this many days from his "
                                                   "effective day (default: to the end of the matchup week)")] = None,
    market_projections: MarketOpt = None, stress_projections: StressOpt = None, no_alt: NoAltOpt = False,
    waiver_lag: Annotated[int, typer.Option("--waiver-lag", help="ASSUMPTION: a waiver player plays from his waiver "
                                            "date + this many days (1: claim processes on the date, plays next day)")] = 1,
    window_only: Annotated[str, typer.Option("--window-only", help="Legacy inseason-0.1.0 ranker: START..END window, "
                                             "opponent-agnostic G-scores, no season check")] = "",
    projections: ProjOpt = None, nba_schedule: NbaOpt = None, as_json: JsonOpt = False,
) -> None:
    """Add/drop pairs for THIS matchup week: Δ expected cats and ΔP(win) vs the real opponent (position-aware,
    matchup weights), the drop's season cost (paired season sim ΔP(playoffs)/ΔP(title) ± SE), waiver timing, adds
    budget. Recommends nothing unless a pair beats 2 SE. Analysis only; every add/drop needs the user's approval."""
    from fantasy.data.provenance import sha256_file
    from fantasy.inseason.freeagents import load_free_agents, realistic_refills
    from fantasy.inseason.streamscan import StreamParams, scan_streams
    from fantasy.league.settings import load_settings
    from fantasy.lineup.cli import LEAGUE_FILE, YAHOO_DIR, newest_capture
    from fantasy.lineup.plan import generic_per_game_values
    from fantasy.lineup.players import load_team
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.league import from_json
    from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH, load_nba_schedule, load_schedule_json

    if window_only:
        return _legacy_stream(window_only, league_file, team, free_agents, drop, top, projections, nba_schedule, as_json)
    d0 = date.fromisoformat(day) if day else date.today()
    lf = league_file or LEAGUE_FILE
    lg = from_json(lf)
    raw_league = json.loads(Path(lf).read_text())
    me_id = team or lg.my_team
    st = load_settings().model_copy(update={"num_teams": len(lg.rosters)})
    proj = load_projections(projections)
    sched = schedule or LEAGUE_SCHEDULE_PATH
    ms = load_schedule_json(sched)
    fw = next((w for w in (ms.calendar or []) if w.start <= d0 <= w.end), None)
    if fw is None:
        fail(f"{d0} is not inside any week of {sched}", as_json)
    assert fw is not None
    opp_id = ms.opponent(me_id, fw.week)
    if opp_id is None:
        fail(f"no opponent for team {me_id} in week {fw.week}", as_json)
    nba = load_nba_schedule(nba_schedule)
    if nba is None:
        fail("NBA schedule not cached (run `fantasy sim fetch-schedule`)", as_json)
    cap = roster or newest_capture()
    fa_path: Path | None = free_agents or next(iter(sorted(YAHOO_DIR.glob("free_agents_*.json"), reverse=True)), None)
    if fa_path is None:
        fail("no free-agent capture: pass --free-agents", as_json)

    def entry(t: int) -> dict:
        e = raw_league["teams"][str(t)]
        return e if isinstance(e, dict) else {"players": e}

    me, w1 = load_team(me_id, proj.df, lg.rosters[me_id], cap, entry(me_id))
    opp, w2 = load_team(opp_id, proj.df, lg.rosters[opp_id], cap, entry(opp_id))
    fas, w3 = load_free_agents(Path(fa_path), d0)
    taken = {p for ps in lg.rosters.values() for p in ps}
    fas = [f for f in fas if f.player_id not in taken]
    ctx = build_context(proj, st, lg.team_ids, schedule_file=sched, nba=nba)
    drops = None
    if drop.strip():
        from fantasy.draft.names import NameResolutionError, resolve_player

        try:
            drops = [resolve_player(x, ctx.values) for x in drop.split(",") if x.strip()]
        except NameResolutionError as e:
            fail(str(e), as_json, exit_code=2)
        bad = [p for p in drops if p not in lg.rosters[me_id]]
        if bad:
            fail(f"not on my roster: {bad}", as_json, exit_code=2)
    params = StreamParams(week_sims=week_sims, season_sims=season_sims, season_top=season_top, top=top, seed=seed,
                          chain_k=chain_k, stint_days=stint_days, waiver_lag=waiver_lag)
    season_eval = None
    pre_warn: list[str] = []
    sets_info: dict = {}
    if season_sims > 0:
        from fantasy.inseason.possim import make_sim
        from fantasy.inseason.streamscan import make_stream_season_eval

        ctxs, sets_info = _proj_sets(ctx, st, lg, sched, market_projections, stress_projections, no_alt, pre_warn)
        chain_pool = [p for p in realistic_refills(fas, ctx.values["value"].to_dict()) if p not in taken]
        season_eval = make_stream_season_eval({k: make_sim(c, season_sims, seed) for k, c in ctxs.items()}, lg, me_id,
                                              chain_pool, fw.end, chain_k, stint_days,
                                              names=ctx.values["name"].astype(str).to_dict())

    acc = json.loads(accumulated.read_text()) if accumulated else {}
    days = _days(d0.isoformat(), fw.end.isoformat())
    rep = scan_streams(proj.df, st, nba, me, opp, fas, days, generic_per_game_values(proj, st), ctx.values["value"],
                       params, drops, acc.get("me"), acc.get("opp"), season_eval, adds_used)
    warnings = w1 + w2 + w3 + pre_warn
    if d0 > fw.start and accumulated is None:
        warnings.append(f"mid-week without --accumulated: stats banked {fw.start}..{d0 - timedelta(days=1)} assumed 0")
    out: dict[str, Any] = {
        "ok": True, "version": INSEASON_VERSION, "team": me_id, "week": fw.week,
        "opponent": {"team_id": opp_id, "name": lg.team_name(opp_id)}, **rep,
        "labels": {
            "week_*": "model: lineup-0.2.0 analytic normal matchup model (fitted game noise and projection error; "
                      "availability priors: rest_share, injury-tag priors); week_mc = its paired Monte Carlo (simulated)",
            "season": f"simulated: position-aware season sim ({SIM_LABEL}), paired, {season_sims} sims, seed {seed}; "
                      "the stream CHAIN: streamer for his stint only, then the (chain_k+1)-th best realistic free agent "
                      "(fa0 / fa{k} / replacement level reported); direct = drop -> chain FA with no stream; ± = "
                      "Monte Carlo SE only (excludes projection/model error); other teams never add/drop",
            "chain_k": "PRIOR: other teams claim the best free agents first",
            "projection_sets": SETS_NOTE,
            "season_proxy_d_value": "valuation value(add) - value(drop): season G-score totals (screen only)",
            "effective": "ASSUMPTION: free agent plays from today; waiver claim from its date + --waiver-lag; the claim "
                         "may lose to a team higher in the rolling waiver order"},
        "inputs": {"league_file": str(lf), "roster_capture": str(cap) if cap else None, "free_agents": str(fa_path),
                   "projection_sets": sets_info,
                   "free_agents_sha256": sha256_file(Path(fa_path)), "schedule": str(sched),
                   "projections": proj.path, "projections_sha256": proj.sha256},
        "warnings": warnings + ctx.warnings,
        "note": "Analysis only: never touches Yahoo. Every add/drop needs the user's approval (txn ledger).",
    }
    emit(out, as_json, _stream_text(out))


SIM_LABEL = "season-sim, PositionAwareSimulator"


def _fmt_d(d: dict | None, k: str) -> str:
    if not d:
        return "-"
    x = d[k]
    return f"{x['delta'] * 100:+.1f}±{x['delta_se'] * 100:.1f}"


def _stream_text(out: dict) -> str:
    b = out["baseline"]
    lines = [f"Week {out['week']} {out['window'][0]}..{out['window'][1]} vs {out['opponent']['name']}: "
             f"E[cats] {b['exp_cats']:.2f}, P(win) {b['p_win']:.3f} (analytic). Adds {out['adds']['used']}/"
             f"{out['adds']['max_weekly']} used. Pairs scanned {out['n_pairs_scanned']}.",
             f"{'add':<22} {'drop':<20} {'own':<10} {'eff':<10} {'st/g':>5} {'ΔE[c]':>7} {'ΔPwin':>7} "
             f"{'MC ΔPwin':>12} {'ΔPpo pp':>10} {'ΔPtitle pp':>11}"]
    for r in out["pairs"]:
        mc = r.get("week_mc")
        mcs = f"{mc['d_p_win']:+.3f}±{mc['d_p_win_se']:.3f}" if mc else "-"
        s = r.get("season")
        lines.append(f"{r['add_name'][:22]:<22} {r['drop_name'][:20]:<20} {r['ownership']:<10} {r['effective'][5:]:<10} "
                     f"{r['add_starts']}/{r['add_games']:<3} {r['week_d_exp_cats']:+7.3f} {r['week_d_p_win']:+7.3f} "
                     f"{mcs:>12} {_fmt_d(s, 'p_playoffs'):>10} {_fmt_d(s, 'p_title'):>11} {r.get('season_verdict', '')}")
        if s and "by_set" in s:
            for k, res in s["by_set"].items():
                lines.append(f"    {k:<7} " + "  ".join(
                    f"{lab} {res[lab]['stream']['p_title']['delta'] * 100:+.1f}/{res[lab]['direct']['p_title']['delta'] * 100:+.1f}"
                    f" (stream−direct {_fmt_d(res[lab]['stream_vs_direct'], 'p_title')})" for lab in res)
                    + "   [ΔPtitle pp: chain / direct]")
    if out.get("blocked"):
        lines.append(f"BLOCKED: {out['blocked']}")
    rec = out.get("recommendation")
    lines.append("Recommendation: " + (f"add {rec['add_name']} / drop {rec['drop_name']} ({rec['why']}); needs approval"
                                       if rec else "none (no pair's season effect beats 2 SE)"))
    for w in out.get("week_only", []):
        lines.append(f"  week-only gain: {w['add_name']} / {w['drop_name']}: ΔP(win) {w['week_mc']['d_p_win']:+.3f} ± "
                     f"{w['week_mc']['d_p_win_se']:.3f}; season ΔP(title) lower bound "
                     f"{w['season_title_lower_bound'] * 100:+.1f} pp")
    lines += [f"warning: {w}" for w in out["warnings"]]
    return "\n".join(lines)


def _legacy_stream(window: str, league_file: Path | None, team: int | None, free_agents: Path | None, drop: str,
                   top: int, projections: Path | None, nba_schedule: Path | None, as_json: bool) -> None:
    from fantasy.inseason.lineup import team_days
    from fantasy.inseason.stream import rank_streamers
    from fantasy.inseason.trade import rostered
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections

    start, _, end = window.partition("..")
    lg = _league(league_file, None, as_json)
    t = team or lg.my_team
    proj, st = load_projections(projections), load_settings()
    cands, flags = None, {}
    if free_agents:
        fa = json.loads(Path(free_agents).read_text()).get("players", [])
        cands = [p["player_id"] for p in fa if p.get("player_id")]
        flags = {p["player_id"]: f"Yahoo {p['status']}" for p in fa if p.get("player_id") and p.get("status")}
    out_ids = {p for p in flags if flags[p] in ("Yahoo O", "Yahoo INJ")}
    days = _days(start, end or start)
    rep = rank_streamers(proj, st, lg.rosters[t], rostered(lg), days, team_days(_nba(proj, nba_schedule).games, days),
                         drop=drop or None, out=out_ids, flags=flags, top=top, candidates=cands)
    out: dict[str, Any] = {"ok": True, "version": INSEASON_VERSION, "team": t, **rep,
                           "caveat": "Legacy window gain only (opponent-agnostic); no season check."}
    lines = [f"{start}..{end}, drop {rep['drop']['name']} (window cost {rep['drop']['window_cost']})"]
    lines += [f"  {c['name']:<24} {c['team']} {c['positions']:<10} games {c['games']} starts {c['starts']} "
              f"gain {c['gain']:+.2f} {c['flag']}" for c in rep["candidates"]]
    emit(out, as_json, "\n".join(lines))


FreeMoveOpt = Annotated[str, typer.Option("--free-move", help="Base move made before any trade (M1): 'auto' (best realistic "
                                         "free agent for one of my 3 lowest-value players, if it beats noise), "
                                         "'none', or 'ADD:DROP' (names or ids)")]
RefillSkipOpt = Annotated[int, typer.Option("--refill-skip", help="PRIOR: a short roster adds the (k+1)-th best realistic "
                                            "free agent (default 5: others claim before a trade clears; 0 = best, "
                                            "reported anyway as the optimistic bound)")]
RefillReplOpt = Annotated[bool, typer.Option("--refill-replacement", help="Refill with replacement-level players "
                                             "instead of the capture's free agents")]
DateOpt = Annotated[str, typer.Option("--date", help="Today YYYY-MM-DD for the free-agent capture (default: today)")]
FaOpt = Annotated[Path | None, typer.Option("--free-agents", help="Yahoo free-agent capture for refills (default newest "
                                            "data/raw/yahoo/free_agents_*.json)")]
TRADE_LABELS = {
    "sim": "simulated: position-aware season sim, paired (common random numbers); ± = Monte Carlo SE only (excludes "
           "projection/model error, which the projection sets bracket); other teams never trade/stream",
    "resim": "seed-averaged over fresh seeds; delta_se = max(paired SE, seed-to-seed sd)/sqrt(n_seeds); "
             "seed_se_factor = sd_across_seeds / paired SE (empirical)",
    "class": "helps = ΔP(title) > 2 SE and neither ΔP(playoffs) nor Δcat-wins < -1 SE; speculative = title gain with "
             "a falling floor; floor_only = ΔP(playoffs) > 2 SE, title unresolved; hurts = ΔP(title) or ΔP(playoffs) "
             "< -2 SE (decision rule, not fitted)",
    "partner_ok": "PRIOR: partner accepts if his simulated ΔP(playoffs) >= -1 pp under every projection set",
    "schedule": "schedule-neutral = incoming players given the outgoing players' NBA schedule (paired re-run); "
                "schedule_bet = more than half of the raw ΔP(title) gain is schedule",
    "refill": "PRIOR/ASSUMPTION: a short roster adds the (k+1)-th best realistic free agent (O/INJ skipped) of the "
              "capture; optimistic_me = the best remaining free agent (upper bound)",
    "base": "trades are compared with the league after my best free move (M1)",
}


def _trade_inputs(league_file: Path | None, schedule: Path | None, projections: Path | None, free_agents: Path | None,
                  day: str, market: Path | None, stress: Path | None, no_alt: bool) -> dict:
    from fantasy.league.settings import load_settings
    from fantasy.lineup.cli import LEAGUE_FILE, YAHOO_DIR
    from fantasy.model.lines import load_projections
    from fantasy.sim.context import build_context
    from fantasy.sim.league import from_json
    from fantasy.sim.schedule import LEAGUE_SCHEDULE_PATH

    lf = league_file or LEAGUE_FILE
    lg = from_json(lf)
    st = load_settings().model_copy(update={"num_teams": len(lg.rosters)})
    sched = schedule or LEAGUE_SCHEDULE_PATH
    proj = load_projections(projections)
    ctx = build_context(proj, st, lg.team_ids, schedule_file=sched)
    warnings = list(ctx.warnings)
    ctxs, info = _proj_sets(ctx, st, lg, sched, market, stress, no_alt, warnings)
    d0 = date.fromisoformat(day) if day else date.today()
    fa_path: Path | None = free_agents or next(iter(sorted(YAHOO_DIR.glob("free_agents_*.json"), reverse=True)), None)
    taken = {p for ps in lg.rosters.values() for p in ps}
    pools = {k: _fa_pool(fa_path, d0, c.values, taken, warnings if k == "blend" else []) for k, c in ctxs.items()}
    return {"lf": lf, "lg": lg, "st": st, "sched": sched, "ctx": ctx, "ctxs": ctxs, "sets_info": info, "d0": d0,
            "fa_path": fa_path, "pools": pools, "warnings": warnings, "roster_size": int(st.num_rounds)}


def _free_move(spec: str, inp: dict, n_sims: int, seed: int, as_json: bool) -> tuple[Any, dict]:
    """Resolve --free-move into a tradeeval.Move (or None) and a report."""
    from fantasy.draft.names import NameResolutionError, resolve_player
    from fantasy.inseason.possim import make_sim
    from fantasy.inseason.tradeeval import Move, choose_free_move

    lg, ctx = inp["lg"], inp["ctx"]
    me = lg.my_team
    names = ctx.values["name"]
    if spec.strip().lower() in ("", "none"):
        return None, {"move": None, "how": "none requested"}
    if spec.strip().lower() == "auto":
        pool = inp["pools"]["blend"]
        if not pool:
            return None, {"move": None, "how": "auto: no free agent"}
        val = ctx.values["value"]
        drops = sorted(lg.rosters[me], key=lambda p: float(val.get(p, -99.0)))[:3]
        mv, rows = choose_free_move(make_sim(ctx, n_sims, seed), lg, me, pool[0], drops)
        return mv, {"move": {"add": str(names.get(mv.add, mv.add)), "drop": str(names.get(mv.drop, mv.drop))}
                    if mv else None,
                    "how": f"auto (blend, {n_sims} sims, seed {seed}, whole season): best Δcat-wins > 2 SE with "
                           "P(playoffs)/P(title) not < -2 SE",
                    "candidates": [{"add": str(names.get(r["add"])), "drop": str(names.get(r["drop"])),
                                    "qualifies": r["qualifies"], "season": r["season"]} for r in rows]}
    a, _, d = spec.partition(":")
    add = drop = ""
    try:
        add, drop = resolve_player(a, ctx.values), resolve_player(d, ctx.values)
    except NameResolutionError as e:
        fail(str(e), as_json, exit_code=2)
    if drop not in lg.rosters[me]:
        fail(f"--free-move drop {drop} is not on my roster", as_json, exit_code=2)
    if any(add in ps for ps in lg.rosters.values()):
        fail(f"--free-move add {add} is rostered", as_json, exit_code=2)
    return Move(add, drop), {"move": {"add": str(names.get(add, add)), "drop": str(names.get(drop, drop))},
                             "how": "given"}


def _resim_lines(rs: dict) -> list[str]:
    out = []
    for k, r in rs["by_set"].items():
        sch = r.get("schedule") or {}
        line = (f"    {k:<7} me {_fmt3(r['me'])} [{r['class']}]  partner ΔPpo "
                f"{(r['partner']['p_playoffs']['delta'] or 0) * 100:+.1f}pp")
        if sch:
            line += f"  neutral ΔPtitle {(sch.get('title_neutral') or 0) * 100:+.1f}pp"
        if "optimistic_me" in r:
            line += f"  optimistic ΔPtitle {(r['optimistic_me']['p_title']['delta'] or 0) * 100:+.1f}pp"
        out.append(line)
    out.append(f"    => {rs['class']}; partner_ok {rs['partner_ok']}; win_win {rs['win_win']}; "
               f"schedule_bet {rs['schedule_bet']}")
    return out


@app.command("trade")
def trade_cmd(
    partner: Annotated[int, typer.Option("--partner", help="Other team's id")],
    give: Annotated[str, typer.Option("--give", help="My players, comma-separated (name or id)")],
    get: Annotated[str, typer.Option("--get", help="Their players, comma-separated")],
    league_file: LeagueOpt = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON (default "
                                                  "data/manual/league_schedule.json)")] = None,
    sims: Annotated[int, typer.Option("--sims", help="Paired season sims per seed and projection set")] = 2000,
    seeds: Annotated[int, typer.Option("--seeds", help="Fresh seeds averaged (M4)")] = 5,
    seed: Annotated[int, typer.Option("--seed", help="Seeds used: seed+101 .. seed+100+seeds")] = 0,
    refill_skip: RefillSkipOpt = 5, refill_replacement: RefillReplOpt = False,
    free_move: FreeMoveOpt = "auto", free_agents: FaOpt = None, day: DateOpt = "",
    market_projections: MarketOpt = None, stress_projections: StressOpt = None, no_alt: NoAltOpt = False,
    no_neutral: Annotated[bool, typer.Option("--no-neutral", help="Skip the schedule-neutral re-run")] = False,
    projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Paired season simulation of one trade vs the base WITH my best free move, realistic free-agent refill,
    seed-averaged, under blend / market / stress projections: both teams' ΔP(playoffs)/ΔP(title)/category wins,
    schedule-neutral delta, partner acceptability, decision class. Analysis only."""
    from fantasy.draft.names import NameResolutionError, resolve_player
    from fantasy.inseason.trade import Trade
    from fantasy.inseason.tradeeval import (
        EvalParams,
        SetSpec,
        apply_move,
        evaluate_trade,
        pre_trade_league,
    )

    inp = _trade_inputs(league_file, schedule, projections, free_agents, day, market_projections, stress_projections,
                        no_alt)
    lg, ctx = inp["lg"], inp["ctx"]
    g: list[str] = []
    r: list[str] = []
    try:
        g = [resolve_player(x, ctx.values) for x in give.split(",") if x.strip()]
        r = [resolve_player(x, ctx.values) for x in get.split(",") if x.strip()]
    except NameResolutionError as e:
        fail(str(e), as_json, exit_code=2)
    bad = [p for p in g if p not in lg.rosters[lg.my_team]] + [p for p in r if p not in lg.rosters.get(partner, [])]
    if bad:
        fail(f"not on the giving roster: {bad}", as_json, exit_code=2)
    tr = Trade(partner, g, r)
    move, move_info = _free_move(free_move, inp, sims, seed, as_json)
    base_league = apply_move(lg, lg.my_team, move)
    pre, pre_note = pre_trade_league(lg, lg.my_team, tr, move, ctx.values)
    sets = {k: SetSpec(c, inp["pools"][k]) for k, c in inp["ctxs"].items()}
    params = EvalParams(n_sims=sims, seeds=tuple(seed + 101 + i for i in range(max(1, seeds))),
                        refill_skip=refill_skip, replacement=refill_replacement, neutral=not no_neutral)
    res = evaluate_trade(tr, pre, base_league, lg.my_team, sets, inp["roster_size"], params)
    out: dict[str, Any] = {
        "ok": True, "version": INSEASON_VERSION, "trade": tr.label(ctx.values["name"]), "partner": partner,
        "partner_name": lg.team_name(partner), "base": move_info, "pre_trade": pre_note, **res,
        "labels": {**TRADE_LABELS, "projection_sets": SETS_NOTE},
        "inputs": {"league_file": str(inp["lf"]), "free_agents": str(inp["fa_path"]) if inp["fa_path"] else None,
                   "projection_sets": inp["sets_info"]},
        "warnings": inp["warnings"],
        "note": "Analysis only. Trades always need the user's approval; never propose on Yahoo from here."}
    lines = [f"{out['trade']}  (base: {move_info['move'] or 'no free move'}; {pre_note}; refill {res['refill']}; "
             f"{len(params.seeds)} seeds x {sims} sims)", *_resim_lines(res)]
    lines += [f"warning: {w}" for w in inp["warnings"]]
    emit(out, as_json, "\n".join(lines))


@app.command("trade-scan")
def trade_scan_cmd(
    league_file: LeagueOpt = None,
    schedule: Annotated[Path | None, typer.Option("--schedule", help="League schedule JSON (default "
                                                  "data/manual/league_schedule.json)")] = None,
    free_agents: FaOpt = None, day: DateOpt = "",
    market_projections: MarketOpt = None, stress_projections: StressOpt = None, no_alt: NoAltOpt = False,
    free_move: FreeMoveOpt = "auto",
    partner: Annotated[str, typer.Option("--partner", help="Only these team ids, comma-separated")] = "",
    kinds: Annotated[str, typer.Option("--kinds", help="1-1,2-1 (I give 2),1-2 (I get 2)")] = "1-1,2-1,1-2",
    top_per_team: Annotated[int, typer.Option("--top-per-team", help="Per partner, by the regular-season screen")] = 3,
    top_po_per_team: Annotated[int, typer.Option("--top-po-per-team", help="Per partner, extra by the playoff-weeks "
                                                 "screen (0 = off)")] = 1,
    max_trades: Annotated[int, typer.Option("--max-trades", help="Shortlist size cap (simulated trades)")] = 44,
    partner_tol: Annotated[float, typer.Option("--partner-tol", help="PRIOR: partner proxy may fall this much "
                                               "(cats/week) and still pass the screen")] = 0.05,
    naive_tol: Annotated[float, typer.Option("--naive-tol", help="PRIOR: partner may lose this much summed valuation "
                                             "value (by rankings) and still pass the screen")] = 0.5,
    sims: Annotated[int, typer.Option("--sims", help="Paired season sims per trade, seed and projection set")] = 2000,
    refill_skip: RefillSkipOpt = 5, refill_replacement: RefillReplOpt = False,
    resim_top: Annotated[int, typer.Option("--resim-top", help="Rows re-simulated with fresh seeds (stage 2)")] = 5,
    resim_seeds: Annotated[int, typer.Option("--resim-seeds", help="Fresh seeds per stage-2 trade (>= 5)")] = 5,
    seed: Annotated[int, typer.Option("--seed", help="Screen/stage-1 seed; stage 2 uses seed+101..")] = 0,
    top: Annotated[int, typer.Option("--top", help="Rows printed (text mode)")] = 10,
    projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Scan 1-for-1 and 2-for-1 trades with every team from the base WITH my best free move: cheap all-play screen,
    stage-1 paired season sims of the shortlist under blend / market / stress projections, stage-2 seed-averaged
    re-simulation of the top rows (schedule-neutral delta, optimistic refill bound, partner ΔP(playoffs), decision
    class). Analysis only."""
    from fantasy.data.provenance import sha256_file
    from fantasy.inseason.possim import make_sim
    from fantasy.inseason.tradeeval import EvalParams, SetSpec, apply_move, refill_pool
    from fantasy.inseason.tradescan import (
        ScanParams,
        Screen,
        games_per_week,
        resimulate_top,
        screen_trades,
        simulate_shortlist,
        start_shares,
        trade_rows,
    )

    inp = _trade_inputs(league_file, schedule, projections, free_agents, day, market_projections, stress_projections,
                        no_alt)
    lg, st, ctx = inp["lg"], inp["st"], inp["ctx"]
    me_id = lg.my_team
    rs = inp["roster_size"]
    warnings = inp["warnings"]
    move, move_info = _free_move(free_move, inp, sims, seed, as_json)
    base_league = apply_move(lg, me_id, move)
    sets = {k: SetSpec(c, inp["pools"][k]) for k, c in inp["ctxs"].items()}
    set_names = list(sets)
    params = ScanParams(top_per_team=top_per_team, max_trades=max_trades, partner_tol=partner_tol, naive_tol=naive_tol,
                        kinds=tuple(k.strip() for k in kinds.split(",") if k.strip()), sims=sims, seed=seed,
                        partners=tuple(int(x) for x in partner.split(",") if x.strip()) or None,
                        top_po_per_team=top_po_per_team)
    regular = [w.week for w in ctx.calendar if w.week < min(st.playoff_weeks)]
    reg_days = [d for w in ctx.calendar if w.week in regular for d in w.days()]
    base = make_sim(ctx, sims, seed).run(base_league)
    gpw = games_per_week(ctx.nba, ctx.calendar, regular)
    shares = start_shares(base_league, ctx.proj.df, ctx.values, st, ctx.nba, reg_days, params.start_share_sims, seed)
    screen = Screen(base, base_league, ctx.proj.df, ctx.values, st, gpw, shares)
    po_weeks = list(st.playoff_weeks)
    po_screen = Screen(base, base_league, ctx.proj.df, ctx.values, st, games_per_week(ctx.nba, ctx.calendar, po_weeks),
                       shares, weeks=po_weeks) if top_po_per_team > 0 else None
    taken = {p for ps in base_league.rosters.values() for p in ps}
    blend_pool = [p for p in sets["blend"].pool if p not in taken]
    screen_pool = refill_pool(blend_pool, refill_skip, refill_replacement)
    rows, n_enum = screen_trades(screen, base_league, me_id, ctx.values, rs, screen_pool, params, po_screen)
    simulate_shortlist(rows, base_league, me_id, sets, rs, sims, seed, refill_skip, refill_replacement, params.sig)
    table = trade_rows(rows, ctx.values, ctx.proj.df, ctx.nba, ctx.calendar, st.playoff_weeks, gpw, base_league,
                       set_names)
    eparams = EvalParams(n_sims=sims, seeds=tuple(seed + 101 + i for i in range(max(1, resim_seeds))),
                         refill_skip=refill_skip, replacement=refill_replacement)
    by_label = {r["trade"].label(ctx.values["name"]): r for r in rows}
    resimulate_top(table, by_label, base_league, me_id, sets, rs, eparams, resim_top)
    names = ctx.values["name"]
    refill_fa = None if refill_replacement or len(blend_pool) <= refill_skip else blend_pool[refill_skip]
    out: dict[str, Any] = {
        "ok": True, "version": INSEASON_VERSION, "team": me_id, "projection_sets": set_names, "base": move_info,
        "n_enumerated": n_enum, "n_simulated": len(rows), "n_resimulated": min(resim_top, len(table)),
        "refill": {"skip": refill_skip, "replacement": refill_replacement,
                   "blend_refill_free_agent": {"player_id": refill_fa, "name": str(names.get(refill_fa))}
                   if refill_fa else None,
                   "optimistic_free_agent": {"player_id": blend_pool[0], "name": str(names.get(blend_pool[0]))}
                   if blend_pool else None},
        "start_share_by_rank": [round(float(x), 3) for x in shares[:13]],
        "trades": table,
        "helps_all": [r["trade"] for r in table if r["helps_all"]],
        "decision": [{"trade": r["trade"], "class": r["resim"]["class"], "partner_ok": r["resim"]["partner_ok"],
                      "win_win": r["resim"]["win_win"], "schedule_bet": r["resim"]["schedule_bet"]}
                     for r in table if "resim" in r],
        "rule": "stage 1 helps_all = season verdict 'helps' (ΔP(playoffs) or ΔP(title) > 2 paired SE, neither < -2 SE) "
                "under every projection set (a screen, one seed); the decision is the stage-2 class (labels.class)",
        "labels": {**TRADE_LABELS, "projection_sets": SETS_NOTE,
                   "screen": "deterministic proxy (all-play expected cats/week, anchored on the base sim); start shares "
                             "measured by the lineup Monte Carlo with gp/82 iid availability (prior)",
                   "partner_tol": "prior (screen only)"},
        "params": {**params.to_dict(), "refill_skip": refill_skip, "refill_replacement": refill_replacement,
                   "resim_top": resim_top, "resim_seeds": list(eparams.seeds)},
        "inputs": {"league_file": str(inp["lf"]), "schedule": str(inp["sched"]), "projection_sets": inp["sets_info"],
                   "free_agents": str(inp["fa_path"]) if inp["fa_path"] else None,
                   "free_agents_sha256": sha256_file(Path(inp["fa_path"])) if inp["fa_path"] else None},
        "warnings": warnings,
        "note": "Analysis only. Trades always need the user's approval; never propose on Yahoo from here.",
    }
    refill_txt = "replacement level" if refill_replacement else f"realistic FA #{refill_skip + 1}"
    lines = [f"{n_enum} trades enumerated, {len(rows)} simulated ({', '.join(set_names)}; {sims} paired sims each). "
             f"Base: {move_info['move'] or 'no free move'}. Refill: {refill_txt}"]
    for r in table[:top]:
        s = "  ".join(f"{k}: me ΔPpo {_fmt_d(r['sim'][k]['me'], 'p_playoffs')} ΔPtitle "
                      f"{_fmt_d(r['sim'][k]['me'], 'p_title')} | partner ΔPpo {_fmt_d(r['sim'][k]['partner'], 'p_playoffs')}"
                      for k in set_names if k in r["sim"])
        lines.append(f"{'*' if r['helps_all'] else ' '} {r['trade']}\n      {s}")
        if "resim" in r:
            lines += _resim_lines(r["resim"])
    lines += [f"warning: {w}" for w in warnings]
    emit(out, as_json, "\n".join(lines))


@app.command("opportunity")
def opportunity_cmd(
    day: Annotated[str, typer.Option("--date", help="Today YYYY-MM-DD (default: today)")] = "",
    free_agents: Annotated[Path | None, typer.Option("--free-agents", help="Yahoo free-agent capture (default newest "
                                                     "data/raw/yahoo/free_agents_*.json)")] = None,
    top: Annotated[int, typer.Option("--top", help="Candidates listed")] = 15,
    eval_top: Annotated[int, typer.Option("--eval-top", help="Top available candidates given the paired week + "
                                          "season evaluation (0 = none)")] = 5,
    season_sims: Annotated[int, typer.Option("--season-sims", help="Paired season sims per candidate")] = 2000,
    week_sims: Annotated[int, typer.Option("--week-sims", help="Paired matchup Monte Carlo sims")] = 4000,
    seed: Annotated[int, typer.Option("--seed")] = 0,
    include_rostered: Annotated[bool, typer.Option("--include-rostered", help="Also list other teams' players "
                                                   "(flagged available: false)")] = False,
    news_file: Annotated[Path | None, typer.Option("--news-file", help="ESPN news file (default newest "
                                                   "data/raw/espn/news/news_*.json)")] = None,
    market_projections: Annotated[Path | None, typer.Option("--market-projections", help="Market set for the "
                                                            "season check (default var/projections_alt/"
                                                            "market.inseason.csv)")] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Season override CSV (default var/opportunity/"
                                             "opportunity_<date>.csv; the window file is <out>.window.csv)")] = None,
    league_file: LeagueOpt = None, projections: ProjOpt = None, as_json: JsonOpt = False,
) -> None:
    """Minutes-opportunity detector (opportunity-0.1.0): free agents whose minutes are about to jump (an observed
    model-A jump, or minutes vacated by an Out >= 28-mpg teammate), their opportunity line over the expected window,
    window value vs my weakest player, and for the top ones the paired week + season evaluation. Writes override
    projections (only candidate rows changed) for `sim compare` / `inseason stream --projections`. Analysis only."""
    from fantasy.inseason.opportunity_run import run, text_report

    d0 = date.fromisoformat(day) if day else date.today()
    try:
        res = run(d0, free_agents=free_agents, top=top, eval_top=eval_top, projections=projections,
                  league_file=league_file, out=out, news_file=news_file, include_rostered=include_rostered,
                  season_sims=season_sims, week_sims=week_sims, seed=seed, market=market_projections)
    except (FileNotFoundError, ValueError) as e:
        fail(str(e), as_json)
    emit(res, as_json, text_report(res))
