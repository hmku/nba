import json
from datetime import date

import numpy as np
import pytest
from typer.testing import CliRunner

from fantasy.draft.order import draft_order
from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import load_projections
from fantasy.sim.context import build_context
from fantasy.sim.engine import SimParams, bracket_positions
from fantasy.sim.league import LeagueState, complete_rosters, from_draft, from_json
from fantasy.sim.schedule import (
    allstar_break,
    build_calendar,
    fill_tbd_games,
    fit_week1_end,
    load_schedule_json,
    parse_bbref_month,
    round_robin,
    schedule_to_json,
    uniform_schedule,
)

TEAMS = list(range(1, 13))


@pytest.fixture(scope="module")
def ctx(proj_path):
    return build_context(load_projections(proj_path), LeagueSettings(), TEAMS, nba=uniform_schedule(["TST"]))


def _snake_league(ctx, seed=0, noise=1.0):
    empty = LeagueState(rosters={t: [] for t in TEAMS}, my_team=1, source="test")
    rem = [(p.overall, p.slot) for p in draft_order(12, 13, "snake_3rr")]
    return complete_rosters(empty, rem, ctx.values, ctx.market, ctx.settings, seed=seed, noise=noise)


def _params(**kw):
    base = dict(n_sims=300, seed=1)
    base.update(kw)
    return SimParams(**base)


# ---------------------------------------------------------------- schedules
def test_round_robin_each_team_once_per_week_and_all_pairs_meet():
    s = round_robin(TEAMS, 17)
    assert s.validate(TEAMS) == []
    for w in range(1, 18):
        seen = [t for p in s.weeks[w] for t in p]
        assert sorted(seen) == TEAMS
    pairs = {frozenset(p) for w in range(1, 12) for p in s.weeks[w]}
    assert len(pairs) == 66  # every pair exactly once in the first 11 weeks
    assert s.weeks[12] == s.weeks[1]
    assert s.opponent(1, 1) is not None


def test_round_robin_odd_team_count_has_byes():
    s = round_robin([1, 2, 3, 4, 5], 5)
    for w in range(1, 6):
        assert len(s.weeks[w]) == 2


def test_calendar_week1_and_allstar_merge():
    cal = build_calendar(20, start=date(2026, 10, 20))
    assert cal[0].start == date(2026, 10, 20) and cal[0].end == date(2026, 10, 25)
    assert cal[1].start.weekday() == 0 and (cal[1].end - cal[1].start).days == 6
    merged = build_calendar(20, start=date(2026, 10, 20), merge=(date(2027, 2, 19), date(2027, 2, 24)))
    long = [w for w in merged if (w.end - w.start).days == 13]
    assert len(long) == 1 and long[0].start == date(2027, 2, 15)
    assert merged[-1].week == 20
    longw1 = build_calendar(3, start=date(2026, 10, 20), week1_end=date(2026, 11, 1))
    assert longw1[1].start == date(2026, 11, 2)


def test_calendar_fitted_to_playoff_end_date():
    """Yahoo settings: weeks 18-20 end Sun 2027-03-21. With Mon-Sun weeks and the All-Star merge that
    requires a two-week week 1 (Oct 20 - Nov 1) and puts the playoffs on Mar 1-7, 8-14, 15-21."""
    asb = (date(2027, 2, 19), date(2027, 2, 24))
    w1 = fit_week1_end(20, date(2027, 3, 21), start=date(2026, 10, 20), merge=asb)
    assert w1 == date(2026, 11, 1)
    cal = build_calendar(20, start=date(2026, 10, 20), week1_end=w1, merge=asb)
    assert [(w.start, w.end) for w in cal[17:]] == [
        (date(2027, 3, 1), date(2027, 3, 7)), (date(2027, 3, 8), date(2027, 3, 14)),
        (date(2027, 3, 15), date(2027, 3, 21))]
    assert cal[16].start == date(2027, 2, 15) and cal[16].end == date(2027, 2, 28)  # All-Star week 17
    # without the All-Star merge week 1 would have to absorb three weeks
    assert fit_week1_end(20, date(2027, 3, 21), start=date(2026, 10, 20)) == date(2026, 11, 8)
    with pytest.raises(ValueError):
        fit_week1_end(20, date(2027, 3, 20), start=date(2026, 10, 20), merge=asb)  # not a Sunday
    with pytest.raises(ValueError):
        fit_week1_end(20, date(2027, 2, 28), start=date(2026, 10, 20), merge=asb)  # too early


def test_context_calendar_follows_settings(ctx):
    assert ctx.calendar[-1].end == date(2027, 3, 21) and ctx.calendar[-1].week == 20
    assert ctx.calendar[17].start == date(2027, 3, 1)
    s = LeagueSettings(playoff_end_date="2027-03-28")
    c2 = build_context(ctx.proj, s, TEAMS, nba=ctx.nba)
    assert c2.calendar[-1].end == date(2027, 3, 28) and c2.calendar[0].end == date(2026, 11, 8)


def test_uniform_schedule_is_3_5_games_per_week():
    s = uniform_schedule(["AAA", "BBB", "CCC"], date(2026, 10, 20), date(2026, 12, 14))
    _, M = s.plays_matrix([date.fromordinal(date(2026, 10, 20).toordinal() + k) for k in range(56)])
    assert np.allclose(M[:-1].sum(axis=1) / 8, 3.5)


def test_parse_bbref_month_and_tbd_fill():
    row = ('<tr ><th scope="row" class="left " data-stat="date_game" csk="202610200DET" ><a>Tue</a></th>'
           '<td data-stat="visitor_team_name" csk="BOS.202610200DET" ><a>Boston</a></td>'
           '<td data-stat="home_team_name" csk="DET.202610200DET" ><a>Detroit</a></td></tr>')
    assert parse_bbref_month("<table>" + row + row.replace("BOS", "NYK").replace("DET", "CHO") + "</table>") == [
        ("2026-10-20", "BOS", "DET"), ("2026-10-20", "NYK", "CHO")]
    s = uniform_schedule(["AAA", "BBB"], date(2026, 10, 20), date(2026, 11, 30))
    s.games = [g for g in s.games if not (date(2026, 11, 1) <= g[0] <= date(2026, 11, 30))] + \
        [(date(2026, 12, 20), "AAA", "BBB")]
    filled, added = fill_tbd_games(s, target=20)
    assert added > 0
    from collections import Counter
    c = Counter(t for _, v, h in filled.games for t in ({v, h}))
    assert c["AAA"] >= 20 and c["BBB"] >= 20


def test_allstar_break_detection():
    s = uniform_schedule(["AAA", "BBB"], date(2026, 10, 20), date(2027, 4, 11))
    s.games = [g for g in s.games if not (date(2027, 2, 13) <= g[0] <= date(2027, 2, 18))]
    assert allstar_break(s) == (date(2027, 2, 13), date(2027, 2, 18))


def test_schedule_json_roundtrip(tmp_path):
    cal = build_calendar(20)
    s = round_robin(TEAMS, 17)
    p = tmp_path / "s.json"
    p.write_text(schedule_to_json(s, cal))
    back = load_schedule_json(p)
    assert back.weeks[5] == s.weeks[5] and len(back.calendar) == 20 and back.calendar[17].week == 18


# ---------------------------------------------------------------- league state
def test_complete_rosters_fills_without_duplicates(ctx):
    lg = _snake_league(ctx)
    assert all(len(v) == 13 for v in lg.rosters.values())
    allp = [p for v in lg.rosters.values() for p in v]
    assert len(allp) == len(set(allp)) == 156
    assert sum(len(v) for v in lg.projected.values()) == 156 and "expected_picks" in lg.source


def test_from_draft_partial_and_reserved(ctx):
    draft = {"draft_id": "mock-x", "num_teams": 12, "num_rounds": 13, "order_type": "snake_3rr", "my_slot": 1,
             "config": {}}
    picks = [{"overall_pick": i + 1, "team_slot": pk.slot, "player_id": ctx.market[i]}
             for i, pk in enumerate(draft_order(12, 13, "snake_3rr")[:5])]
    lg = from_draft(draft, picks)
    assert lg.rosters[1] == [ctx.market[0]] and lg.rosters[5] == [ctx.market[4]] and lg.rosters[12] == []
    from fantasy.sim.league import remaining_order
    rem = remaining_order(draft, picks)
    assert len(rem) == 156 - 5
    target = ctx.market[5]  # the next market player: others would take him first
    full = complete_rosters(lg, rem, ctx.values, ctx.market, ctx.settings, seed=0, noise=0.0,
                            my_plan=[target], reserved={target})
    assert target in full.rosters[1]


def test_league_json(tmp_path):
    p = tmp_path / "lg.json"
    p.write_text(json.dumps({"my_team": 3, "teams": {"3": {"name": "Me", "players": ["a"]}, "4": ["b", "c"]}}))
    lg = from_json(p)
    assert lg.my_team == 3 and lg.rosters == {3: ["a"], 4: ["b", "c"]} and lg.team_name(3) == "Me"
    assert lg.with_change(3, ["d"], ["a"]).rosters[3] == ["d"]
    with pytest.raises(ValueError):
        lg.with_change(3, ["b"], [])


# ---------------------------------------------------------------- engine
def test_deterministic_given_seed(ctx):
    lg = _snake_league(ctx)
    a = ctx.simulator(_params(n_sims=100)).run(lg)
    b = ctx.simulator(_params(n_sims=100)).run(lg)
    c = ctx.simulator(_params(n_sims=100, seed=2)).run(lg)
    assert np.array_equal(a.totals, b.totals) and np.array_equal(a.champion, b.champion)
    assert not np.array_equal(a.totals, c.totals)


def test_stronger_team_more_likely_to_make_playoffs(ctx):
    # team 1 gets the 13 best players by value, everybody else snake-drafts the rest
    best = list(ctx.values.index[:13])
    rest = [p for p in ctx.market if p not in best]
    rosters = {1: best}
    for i, t in enumerate(TEAMS[1:]):
        rosters[t] = rest[i::11][:13]
    lg = LeagueState(rosters=rosters, my_team=1, source="test")
    res = ctx.simulator(_params()).run(lg)
    p = res.playoff.mean(axis=0)
    assert p[0] > 0.9 and p[0] == p.max()
    assert (res.champion == 0).mean() > 0.3
    assert res.cat_wins[:, 0].mean() > res.cat_wins[:, 1:].mean() + 10
    # all standings ranks are a permutation each sim
    assert (np.sort(res.rank, axis=1) == np.arange(1, 13)).all()


def test_turnovers_lower_is_better(ctx):
    sim = ctx.simulator(_params(n_sims=10))
    C = len(sim.cats)
    ti = [c.key for c in sim.cats].index("tov")
    a, b = np.zeros(C, dtype=np.float32), np.zeros(C, dtype=np.float32)
    a[ti], b[ti] = 10, 20
    w, lo = sim.compare(a, b)
    assert w[ti] and not lo[ti]
    pi = [c.key for c in sim.cats].index("pts")
    a[pi], b[pi] = 10, 20
    w, lo = sim.compare(a, b)
    assert lo[pi] and not w[pi]


def test_fg_pct_is_makes_over_attempts(ctx):
    """Team FG% must be sum(makes)/sum(attempts), not the mean of player percentages."""
    df = ctx.proj.df
    pct = df["fgm"] / df["fga"]
    hi_vol = df["fga"].idxmax()
    lo_pct = pct.drop(hi_vol).idxmin()
    others = [p for p in df.index if p not in (hi_vol, lo_pct)][:2]
    lg = LeagueState(rosters={1: [hi_vol, lo_pct], 2: others}, my_team=1, source="t")
    prm = _params(n_sims=2000, vol_sd=0, stat_sd=0, fg_pct_sd=0, ft_pct_sd=0, rest_share=0, streaming=False)
    sim = ctx.simulator(prm)
    res = sim.run(lg)
    ci = res.cats.index("fg_pct")
    got = float(res.totals[:, :, 0, ci][res.games[:, :, 0] > 0].mean())
    a = df.loc[[hi_vol, lo_pct]]
    w = a["gp"].to_numpy()  # expected games played weight each player's volume
    makes_over_att = float((a["fgm"] * w).sum() / (a["fga"] * w).sum())
    mean_of_pcts = float(pct.loc[[hi_vol, lo_pct]].mean())
    assert abs(got - makes_over_att) < abs(got - mean_of_pcts)
    assert abs(got - makes_over_att) < 0.012


def test_lineup_cap_limits_player_games(ctx):
    # 16 players on one NBA team (synthetic: everyone is "TST") -> all play the same days
    ids = list(ctx.values.index[:16])
    lg = LeagueState(rosters={1: ids, 2: list(ctx.values.index[16:32])}, my_team=1, source="t")
    capped = ctx.simulator(_params(n_sims=200, streaming=False)).run(lg)
    loose = ctx.simulator(_params(n_sims=200, streaming=False, active=16)).run(lg)
    days = np.array([len(w.days()) for w in capped.weeks])
    _, M = ctx.nba.plays_matrix([d for w in capped.weeks for d in w.days()])
    game_days = np.add.reduceat(M[0].astype(int), np.cumsum(np.r_[0, days[:-1]]))
    assert (capped.games[:, :, 0] <= 10 * game_days[None, :] + 1e-6).all()
    assert capped.games[:, :, 0].mean() < loose.games[:, :, 0].mean() - 1
    pts = capped.cats.index("pts")
    assert capped.totals[:, :, 0, pts].mean() < loose.totals[:, :, 0, pts].mean()


def test_streaming_adds_games(ctx):
    lg = _snake_league(ctx)
    on = ctx.simulator(_params(n_sims=100)).run(lg)
    off = ctx.simulator(_params(n_sims=100, streaming=False)).run(lg)
    assert on.games.mean() > off.games.mean()
    assert on.stream_games.max() <= 6 + 1e-6


def test_injury_spells_miss_whole_weeks(ctx):
    sim = ctx.simulator(_params(n_sims=400))
    pid = ctx.values.index[0]
    arr = sim._player_arrays([pid])
    played = arr["health"][:, 0]
    wk = np.stack([played[:, a:b].sum(-1) for a, b in sim.week_slices], axis=1)
    p_zero_week = (wk == 0).mean()
    assert p_zero_week > 0.03  # spells, not iid misses (iid at ~15% would almost never zero a 3-4 game week)
    gp = float(ctx.proj.df.loc[pid, "gp"])
    team_games = sim.plays[0].sum()
    assert abs(played.sum(1).mean() / team_games - gp / 82) < 0.08


def test_bracket_and_playoffs(ctx):
    assert bracket_positions(8) == [1, 8, 4, 5, 2, 7, 3, 6]
    sim = ctx.simulator(_params(n_sims=4))
    W, T, C = len(sim.calendar), 12, len(sim.cats)
    totals = np.zeros((4, W, T, C), dtype=np.float32)
    tov = [c.key for c in sim.cats].index("tov")
    strength = np.arange(T, dtype=np.float32)  # team index 11 strongest
    totals[:] = strength[None, None, :, None]
    totals[..., tov] = -strength[None, None, :]  # fewer TO for stronger teams
    week_pos = {w.week: i for i, w in enumerate(sim.calendar)}
    seeds = np.tile(np.array([0, 1, 2, 3, 4, 11]), (4, 1))  # seed 6 is the strongest team
    champ, runner = sim._playoffs(totals, seeds, week_pos)
    # wk18: 4v5 -> team 4, 3v6 -> team 11 (1, 2 byes); wk19: team 4 beats seed 1, team 11 beats seed 2
    assert (champ == 11).all() and (runner == 4).all()
    # seed 1 strongest overall but awful in week 18: the bye protects it
    t2 = totals.copy()
    t2[:, :, 0, :] = 100
    t2[:, :, 0, tov] = -100
    t2[:, week_pos[18], 0, :] = -100
    t2[:, week_pos[18], 0, tov] = 1000
    champ2, _ = sim._playoffs(t2, seeds, week_pos)
    assert (champ2 == 0).all()
    # ties in categories go to the higher seed
    flat = np.zeros_like(totals)
    champ3, runner3 = sim._playoffs(flat, seeds, week_pos)
    assert (champ3 == 0).all() and (runner3 == 1).all()


def test_playoff_reseeding(ctx):
    """Seeds 1..6 = teams 0..5. Week 18: 4 beats 5, 6 beats 3. Week 19 strengths: seed 1 beats seed 6
    but loses to seed 4. Re-seeded (league setting): 1 v 6 and 2 v 4 -> seed 1 survives; fixed
    bracket: 1 v 4 -> seed 1 is out."""
    W, T = len(ctx.calendar), 12
    week_pos = {w.week: i for i, w in enumerate(ctx.calendar)}
    seeds = np.tile(np.arange(6), (2, 1))
    sim = ctx.simulator(_params(n_sims=2))
    tov = [c.key for c in sim.cats].index("tov")
    totals = np.zeros((2, W, T, len(sim.cats)), dtype=np.float32)

    def set_week(week, strength):  # strength by team index; higher = better in every category
        st = np.asarray(strength, dtype=np.float32)
        full = np.zeros(T, dtype=np.float32)
        full[: len(st)] = st
        totals[:, week_pos[week]] = full[None, :, None]
        totals[:, week_pos[week], :, tov] = -full[None, :]

    set_week(18, [9, 9, 1, 5, 2, 8])  # 4 (idx 3) beats 5; 6 (idx 5) beats 3
    set_week(19, [5, 1, 0, 9, 0, 2])  # seed 1 > seed 6, seed 1 < seed 4, seed 4 > seed 2
    set_week(20, [5, 1, 0, 3, 0, 2])
    assert sim.reseed is True  # LeagueSettings default (verified: reseeding yes)
    champ, runner = sim._playoffs(totals, seeds, week_pos)
    assert (champ == 0).all() and (runner == 3).all()  # final: seed 1 v seed 4 in week 20
    fixed = ctx.simulator(_params(n_sims=2, reseed=False))
    champ_f, _ = fixed._playoffs(totals, seeds, week_pos)
    assert (champ_f == 3).all()  # seed 1 eliminated by seed 4 in week 19


def test_report_outputs(ctx):
    from fantasy.sim.report import matchup_report, paired_delta, season_summary

    lg = _snake_league(ctx)
    sim = ctx.simulator(_params(n_sims=200))
    res = sim.run(lg)
    s = season_summary(res, lg, 1)
    assert len(s["league_table"]) == 12
    assert abs(sum(r["p_playoffs"] for r in s["league_table"]) - 6) < 1e-3
    assert abs(sum(r["p_title"] for r in s["league_table"]) - 1) < 1e-3
    assert len(s["my_team"]["weekly_matchups"]) == 17
    assert abs(sum(s["my_team"]["final_standing_dist"].values()) - 1) < 1e-3
    assert set(s["team_profiles"]["2"]["rank"]) == set(res.cats)
    m = matchup_report(res, lg, 1, 3)
    assert m["opponent"] == res.matchups.opponent(1, 3) and 0 <= m["p_win"] <= 1
    # roster change with common random numbers: dropping the best player hurts
    best = max(lg.rosters[1], key=lambda p: ctx.values.loc[p, "value"])
    fa = [p for p in ctx.values.index if all(p not in v for v in lg.rosters.values())][-1]
    alt = sim.run(lg.with_change(1, [fa], [best]))
    d = paired_delta(res, alt, 1)
    assert d["exp_cat_wins"]["delta"] < 0
    same = paired_delta(res, sim.run(lg), 1)
    assert same["p_playoffs"]["delta"] == 0 and same["exp_cat_wins"]["delta_se"] == 0


# ---------------------------------------------------------------- CLI + persistence
def test_cli_season_compare_and_draft_check(proj_path, db_url, tmp_path):
    from sqlalchemy import select

    from fantasy.cli import app
    from fantasy.db.engine import get_engine
    from fantasy.db.schema import model_runs

    r = CliRunner()
    nba = tmp_path / "missing.csv"  # -> uniform fallback, no network/data dependency
    common = ["--projections", str(proj_path), "--nba-schedule", str(nba), "--json"]
    out = r.invoke(app, ["draft", "init", "--draft-id", "mock-s1", "--mock", "--json"])
    assert out.exit_code == 0, out.output
    out = r.invoke(app, ["sim", "season", "--draft-id", "mock-s1", "--complete-rosters", "--sims", "60", *common])
    assert out.exit_code == 0, out.output
    js = json.loads(out.output)
    assert js["ok"] and js["my_team"]["team"] == 1 and js["run_id"]
    assert any("uniform 3.5" in w for w in js["warnings"]) and any("EXPECTED" in w for w in js["warnings"])
    with get_engine().connect() as c:
        row = c.execute(select(model_runs).where(model_runs.c.run_id == js["run_id"])).mappings().one()
    assert row["kind"] == "season_sim" and row["inputs"]["projections_sha256"] and row["summary"]["my_team"]
    roster = js["rosters"]["1"]["players"]
    out = r.invoke(app, ["sim", "compare", "--draft-id", "mock-s1", "--complete-rosters", "--sims", "60",
                         "--drop", roster[0], "--add", "player-250", *common])
    assert out.exit_code == 0, out.output
    assert "delta" in json.loads(out.output)["p_playoffs"]
    out = r.invoke(app, ["sim", "matchup", "--week", "2", "--draft-id", "mock-s1", "--complete-rosters",
                         "--sims", "60", *common])
    assert out.exit_code == 0, out.output
    assert len(json.loads(out.output)["categories"]) == 9
    out = r.invoke(app, ["sim", "draft-check", "--draft-id", "mock-s1", "--candidates", "player-000,player-001",
                         "--sims", "40", *common])
    assert out.exit_code == 0, out.output
    dc = json.loads(out.output)
    assert [c["player_id"] for c in dc["candidates"]] == ["player-000", "player-001"]
    assert dc["candidates"][0]["p_playoffs_diff_vs_first"] == 0
    assert dc["candidates"][1]["my_completed_roster"][0] == "player-001"
    out = r.invoke(app, ["sim", "season", "--draft-id", "nope", *common])
    assert out.exit_code == 1 and not json.loads(out.output)["ok"]


# ---------------------------------------------------------------- post-draft: names, partial schedules, H2H matrix
def test_schedule_json_team_names_and_partial_weeks(tmp_path, proj_path):
    from fantasy.sim.schedule import fill_missing_weeks

    names = {f"Team {t}": t for t in TEAMS}
    p = tmp_path / "s.json"
    wk1 = [["team 1", "Team 7"], [2, 8], ["3", 9], [4, 10], [5, 11], [6, 12]]
    p.write_text(json.dumps({"source": "muse scoreboard wk1", "weeks": [{"week": 1, "matchups": wk1}]}))
    s = load_schedule_json(p, names)
    assert s.weeks[1][0] == (1, 7) and s.weeks[1][2] == (3, 9) and s.calendar is None
    filled = fill_missing_weeks(s, TEAMS, list(range(1, 18)))
    assert filled.assumed_weeks == list(range(2, 18)) and filled.weeks[1] == s.weeks[1]
    assert filled.opponent(1, 1) == 7 and "round_robin" in filled.source
    p.write_text(json.dumps({"weeks": [{"week": 1, "matchups": [["Nobody", "Team 2"]]}]}))
    with pytest.raises(ValueError, match="unknown team"):
        load_schedule_json(p, names)
    # through build_context: partial week dates are ignored with a warning; names resolve via team_names
    p.write_text(json.dumps({"weeks": [{"week": 1, "start": "2026-10-20", "end": "2026-10-25", "matchups": wk1}]}))
    c = build_context(load_projections(proj_path), LeagueSettings(), TEAMS, schedule_file=p,
                      nba=uniform_schedule(["TST"]), team_names={t: f"Team {t}" for t in TEAMS})
    assert c.matchups.opponent(1, 1) == 7 and c.matchups.assumed_weeks == list(range(2, 18))
    assert any("ignored" in w for w in c.warnings) and any("ASSUMED round robin" in w for w in c.warnings)
    info = c.matchup_info()
    assert info["assumed_weeks"][0] == 2 and not info["all_assumed"]


def test_no_schedule_is_all_assumed(ctx):
    info = ctx.matchup_info()
    assert info["all_assumed"] and info["assumed_weeks"] == list(range(1, 18)) and "ASSUMPTION" in info["note"]


def test_weekly_h2h_matrix(ctx):
    from fantasy.sim.report import season_summary

    lg = _snake_league(ctx)
    res = ctx.simulator(_params(n_sims=100)).run(lg)
    s = season_summary(res, lg, 1)
    mx = s["my_team"]["weekly_h2h_matrix"]
    assert [w["week"] for w in mx] == list(range(1, 18)) and set(mx[0]["vs"]) == {str(t) for t in TEAMS[1:]}
    # the scheduled opponent's cell equals that week's weekly_matchups entry
    w3 = mx[2]
    wm = next(w for w in s["my_team"]["weekly_matchups"] if w["week"] == 3)
    assert w3["scheduled_opponent"] == wm["opponent"] and w3["vs"][str(wm["opponent"])]["p_win"] == wm["p_win"]
    assert wm["opponent_assumed"] and s["matchup_schedule"]["assumed_weeks"] == list(range(1, 18))
    assert set(w3["vs"]["2"]["cat_win_probs"]) == set(res.cats)


def test_team_names_and_league_json_from_draft():
    from fantasy.sim.league import league_json_from_draft, parse_team_names

    assert parse_team_names(["A", "B"], 12) == {1: "A", 2: "B"}
    assert parse_team_names({"names": {"2": "B"}}, 12) == {2: "B"}
    with pytest.raises(ValueError):
        parse_team_names(["A", "A"], 12)
    with pytest.raises(ValueError):
        parse_team_names({"13": "X"}, 12)
    draft = {"draft_id": "d1", "num_teams": 2, "num_rounds": 2, "order_type": "snake", "my_slot": 1, "config": {}}
    picks = [{"overall_pick": 1, "team_slot": 1, "player_id": "a"}, {"overall_pick": 2, "team_slot": 2, "player_id": "b"},
             {"overall_pick": 3, "team_slot": 2, "player_id": "c"}]
    js = league_json_from_draft(draft, picks, {1: "Me", 2: "You"}, "test")
    assert js["teams"]["2"] == {"name": "You", "players": ["b", "c"]} and js["draft_id"] == "d1"
    assert js["picks_recorded"] == 3 and js["picks_total"] == 4 and js["source"] == "test"


def test_cli_export_league_and_scenario(proj_path, db_url, tmp_path):
    from fantasy.cli import app

    r = CliRunner()
    from fantasy.draft import store

    assert r.invoke(app, ["draft", "init", "--draft-id", "mock-x1", "--mock", "--json"]).exit_code == 0
    d = store.get_draft("mock-x1")
    for i in range(12):
        store.record_pick(d, i + 1, f"player-{i:03d}", f"P{i}", "manual")
    names = tmp_path / "names.json"
    names.write_text(json.dumps({"source": "t", "names": [f"T{i}" for i in range(1, 13)]}))
    lf = tmp_path / "league.json"
    out = r.invoke(app, ["sim", "export-league", "--draft-id", "mock-x1", "--names-file", str(names), "--out", str(lf),
                         "--json"])
    assert out.exit_code == 0, out.output
    js = json.loads(out.output)
    assert js["picks_recorded"] == 12 and js["teams"]["3"]["name"] == "T3"
    out = r.invoke(app, ["sim", "season", "--league-file", str(lf), "--sims", "40", "--projections", str(proj_path),
                         "--nba-schedule", str(tmp_path / "none.csv"), "--scenario", "post-draft: actual rosters",
                         "--json"])
    assert out.exit_code == 0, out.output
    s = json.loads(out.output)
    assert s["scenario"] == "post-draft: actual rosters" and s["draft_id"] == "mock-x1"
    assert s["matchup_schedule"]["all_assumed"] and s["my_team"]["name"] == "T1"
