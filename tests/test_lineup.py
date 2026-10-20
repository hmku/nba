"""Lineup optimizer (fantasy.lineup): exact assignment, eligibility, game/no-game, O players, weekly plan, CLI."""

from __future__ import annotations

import itertools
import json
from datetime import date, timedelta

import numpy as np
import pytest
from typer.testing import CliRunner

from fantasy.league.settings import LeagueSettings
from fantasy.lineup.optimize import DaySlots, eligible, lineup_value, score_matrix, solve_day
from fantasy.lineup.plan import LineupParams, build_plan, generic_per_game_values
from fantasy.lineup.players import RosterPlayer, normalize_status, normalize_team, parse_positions
from fantasy.model.lines import load_projections
from fantasy.sim.schedule import NBASchedule

SLOTS = DaySlots(("PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util"), 3, 3)
POS_CHOICES = [("PG",), ("PG", "SG"), ("SG", "SF", "PF"), ("SF", "PF"), ("PF",), ("PF", "C"), ("C",)]
D0 = date(2026, 10, 20)


# ---------------------------------------------------------------- assignment solver
def _brute(positions, values, startable, il_ok, slots):
    cols = slots.columns
    M = score_matrix(positions, values, startable, il_ok, slots, tie_breaks=False)
    best = -np.inf
    for perm in itertools.permutations(range(len(cols)), len(positions)):
        s = sum(M[i, j] for i, j in enumerate(perm))
        best = max(best, s)
    return best


@pytest.mark.parametrize("seed", range(12))
def test_solver_matches_brute_force_on_small_instances(seed):
    rng = np.random.default_rng(seed)
    slots = DaySlots(("G", "F", "C", "Util"), 2, 1)
    n = int(rng.integers(4, 8))
    positions = [POS_CHOICES[k] for k in rng.integers(0, len(POS_CHOICES), n)]
    values = rng.normal(1.0, 1.0, n)
    startable = [bool(x) for x in rng.random(n) > 0.3]
    il_ok = [not s for s in startable]
    best = _brute(positions, values, startable, il_ok, slots)
    if best < -1e8:  # infeasible (more non-IL players than starting + bench slots)
        with pytest.raises(ValueError):
            solve_day(positions, values, startable, il_ok, slots)
        return
    labels = solve_day(positions, values, startable, il_ok, slots)
    got = lineup_value(labels, values, slots)
    assert got == pytest.approx(best, abs=1e-6)
    for pos, lab, st, il in zip(positions, labels, startable, il_ok, strict=True):
        if lab in slots.starting:
            assert st and eligible(pos, lab)
        if lab == "IL":
            assert il


def test_c_only_players_never_in_non_c_slots():
    rng = np.random.default_rng(3)
    positions = [("C",)] * 5 + [("PG",), ("SG",), ("SF",), ("PF",), ("PG", "SG"), ("SF", "PF"), ("PF",), ("SG",)]
    for _ in range(20):
        values = rng.uniform(0.1, 3.0, len(positions))
        labels = solve_day(positions, values, [True] * 13, [False] * 13, SLOTS)
        for pos, lab in zip(positions, labels, strict=True):
            if pos == ("C",):
                assert lab in {"C", "Util", "BN"}
        assert sum(lab in SLOTS.starting for lab, p in zip(labels, positions, strict=True) if p == ("C",)) <= 4


def test_solver_deterministic_and_keeps_current_slots_on_ties():
    positions = [("C",), ("C",), ("PF", "C"), ("PG",)]
    values = np.array([1.0, 1.0, 1.0, 1.0])
    slots = DaySlots(("C", "C", "Util", "PG"), 0, 0)
    cur = ["Util", "C", "C", "PG"]
    a = solve_day(positions, values, [True] * 4, [False] * 4, slots, cur)
    b = solve_day(positions, values, [True] * 4, [False] * 4, slots, cur)
    assert a == b == cur


# ---------------------------------------------------------------- planner
def _schedule(days: list[date], plays: dict[str, list[int]]) -> NBASchedule:
    games = []
    for team, ds in plays.items():
        for k in ds:
            games.append((days[k], team, team))
    return NBASchedule(games=games, source="test", teams=sorted(plays))


@pytest.fixture(scope="module")
def world(proj_path):
    proj = load_projections(proj_path)
    settings = LeagueSettings()
    days = [D0 + timedelta(days=k) for k in range(6)]
    # team A plays every day, B on 3 days, C on 2, Z never
    nba = _schedule(days, {"AAA": list(range(6)), "BBB": [0, 2, 4], "CCC": [1, 3], "ZZZ": []})
    ids = list(proj.df.index)
    teams = ["AAA", "BBB", "CCC", "ZZZ"]

    def roster(offset: int) -> list[RosterPlayer]:
        out = []
        for k in range(13):
            pid = ids[offset + k * 2]
            row = proj.df.loc[pid]
            out.append(RosterPlayer(pid, str(row["name"]), parse_positions(list(row["positions"])), teams[k % 4],
                                    None, None, float(row["gp"])))
        return out

    me, opp = roster(0), roster(1)
    generic = generic_per_game_values(proj, settings)
    return proj, settings, days, nba, me, opp, generic


def _plan(world, me=None, **kw):
    proj, settings, days, nba, me0, opp, generic = world
    return build_plan(me or me0, opp, proj.df, nba, days, settings, generic,
                      params=LineupParams(n_sims=kw.pop("n_sims", 400), seed=1, **kw))


def test_weekly_plan_has_one_entry_per_day_and_respects_eligibility(world):
    out = _plan(world)
    assert len(out["week_plan"]) == 6
    pos = {p.player_id: p.positions for p in world[4]}
    for day in out["week_plan"]:
        assert sum(e["slot"] in SLOTS.starting for e in day["lineup"]) <= 10
        for e in day["lineup"]:
            if e["slot"] in SLOTS.starting:
                assert eligible(pos[e["player_id"]], e["slot"])
        assert day["started_with_game"] + day["unused_starts"] == day["players_with_game"]


def test_players_without_game_never_displace_players_with_game(world):
    out = _plan(world)
    for day in out["week_plan"]:
        start = [e for e in day["lineup"] if e["slot"] in SLOTS.starting]
        bench_game = [e for e in day["lineup"] if e["slot"] == "BN" and e["has_game"]]
        if bench_game:  # a benched player with a game: every starting slot he fits must hold a player with a game
            pos = {p.player_id: p.positions for p in world[4]}
            for b in bench_game:
                for e in start:
                    if eligible(pos[b["player_id"]], e["slot"]):
                        assert e["has_game"], (day["date"], b, e)


def test_out_players_never_started_and_suggested_for_il(world):
    proj, settings, days, nba, me, opp, generic = world
    star = max(range(13), key=lambda i: generic.get(me[i].player_id, -9))
    hurt = [RosterPlayer(**{**p.__dict__, "status": "O"}) if i == star else p for i, p in enumerate(me)]
    hurt[star] = RosterPlayer(hurt[star].player_id, hurt[star].name, hurt[star].positions, "AAA", "O", "Util", 70.0)
    out = _plan(world, me=hurt)
    pid = me[star].player_id
    for day in out["week_plan"]:
        e = next(x for x in day["lineup"] if x["player_id"] == pid)
        assert e["slot"] in {"BN", "IL"}
        assert not e["has_game"] or e["p_play"] == 0
    assert [m["player_id"] for m in out["il_moves"]] == [pid]
    assert any(c["player_id"] == pid and c["from"] == "Util" for c in out["changes_vs_current"])


def test_questionable_player_flagged_and_discounted(world):
    proj, settings, days, nba, me, opp, generic = world
    i = next(k for k, p in enumerate(me) if p.team == "AAA")
    tagged = list(me)
    tagged[i] = RosterPlayer(me[i].player_id, me[i].name, me[i].positions, "AAA", "Q", None, me[i].gp)
    out = _plan(world, me=tagged)
    assert [t["player_id"] for t in out["decide_at_tipoff"]] == [me[i].player_id]
    assert out["decide_at_tipoff"][0]["p_play"] == pytest.approx(0.5)
    later = next(e for e in out["week_plan"][1]["lineup"] if e["player_id"] == me[i].player_id)
    assert later["p_play"] > 0.9  # the tag prior applies to the next game only


def test_plan_deterministic_and_not_worse_than_naive(world):
    a, b = _plan(world), _plan(world)
    assert a["week_plan"] == b["week_plan"] and a["monte_carlo"] == b["monte_carlo"]
    ex = a["expected"]
    assert ex["optimized"]["expected_cats"] >= ex["naive_season_value"]["expected_cats"] - 1e-6
    assert 0 <= ex["optimized"]["p_win_matchup"] <= 1
    mc = a["monte_carlo"]["plans"]
    assert set(mc) == {"optimized", "naive_season_value", "static_current_lineup"}
    assert mc["optimized"]["expected_cats_se"] > 0


def test_accumulated_stats_move_the_weights(world):
    base = _plan(world, n_sims=0)
    proj, settings, days, nba, me, opp, generic = world
    acc = {"blk": 200.0}  # blocks already won by a mile -> weight ~ 0
    out = build_plan(me, opp, proj.df, nba, days, settings, generic, acc_me=acc, params=LineupParams(n_sims=0))
    assert abs(out["category_weights"]["blk"]["weight"]) < 1e-3 * abs(base["category_weights"]["blk"]["weight"])
    assert out["category_weights"]["tov"]["weight"] < 0


def test_normalisers():
    assert normalize_status("out") == "O" and normalize_status(None) is None and normalize_status("q") == "Q"
    assert normalize_team("CHA") == "CHO" and normalize_team("PHX") == "PHO"
    assert parse_positions("PG,SG,G,Util") == ("PG", "SG") and parse_positions("PF|C") == ("PF", "C")


# ---------------------------------------------------------------- CLI
def test_cli_plan_json_smoke(tmp_path, proj_path):
    from fantasy.cli import app

    proj = load_projections(proj_path)
    ids = list(proj.df.index)
    teams_codes = ["BOS", "DET", "NYK"]
    games = tmp_path / "games.csv"
    rows = ["date,visitor,home"] + [f"{(D0 + timedelta(days=k)).isoformat()},BOS,DET" for k in range(0, 6, 2)] + [
        f"{(D0 + timedelta(days=k)).isoformat()},NYK,BOS" for k in range(1, 6, 2)]
    games.write_text("\n".join(rows) + "\n")
    league = {"my_team": 1, "teams": {"1": {"name": "Me", "players": ids[0:26:2]},
                                      "2": {"name": "Them", "players": ids[1:26:2]}}}
    lf = tmp_path / "league.json"
    lf.write_text(json.dumps(league))
    sched = tmp_path / "sched.json"
    sched.write_text(json.dumps({"weeks": [{"week": 1, "start": "2026-10-20", "end": "2026-10-25",
                                            "matchups": [[1, 2]]}]}))
    cap = {"captured_at": "2026-10-21T13:05:00Z", "teams": {}}
    for yid, (slot, pids) in enumerate([(1, ids[0:26:2]), (2, ids[1:26:2])], start=1):
        cap["teams"][str(yid)] = {"draft_slot": slot, "players": [
            {"player_id": p, "name": p, "team": teams_codes[k % 3], "positions": "|".join(proj.df.loc[p, "positions"]),
             "status": "Q" if k == 0 else None, "slot": SLOTS.columns[k]} for k, p in enumerate(pids)]}
    capf = tmp_path / "cap.json"
    capf.write_text(json.dumps(cap))
    acc = tmp_path / "acc.json"
    acc.write_text(json.dumps({"me": {"pts": 10}, "opp": {"pts": 12}}))
    r = CliRunner().invoke(app, ["lineup", "plan", "--date", "2026-10-21", "--week", "--json", "--projections",
                                 str(proj_path), "--league-file", str(lf), "--schedule", str(sched), "--nba-schedule",
                                 str(games), "--roster", str(capf), "--accumulated", str(acc), "--sims", "200"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["week"] == 1 and out["opponent"]["team_id"] == 2 and len(out["week_plan"]) == 5
    assert out["model_version"] == "lineup-0.2.0"
    assert {"lineup", "bench", "il_moves", "changes_vs_current", "decide_at_tipoff", "category_weights",
            "expected", "inputs", "params"} <= set(out)
    assert out["inputs"]["accumulated"]["sha256"]
    r2 = CliRunner().invoke(app, ["lineup", "plan", "--date", "2027-01-01", "--json", "--projections", str(proj_path),
                                  "--league-file", str(lf), "--schedule", str(sched), "--nba-schedule", str(games),
                                  "--roster", str(capf), "--sims", "0"])
    assert r2.exit_code == 1 and "error" in json.loads(r2.output)


@pytest.mark.parametrize("seed", range(30))
def test_starter_count_matches_inseason_lineup_solver(seed):
    """Cross-check with fantasy.inseason.lineup.LineupSolver (priority-greedy max matching): with positive values the
    exact max-weight assignment is a maximum matching (transversal matroid), so both start the same number."""
    from fantasy.inseason.lineup import LineupSolver

    rng = np.random.default_rng(100 + seed)
    while True:  # rejection-sample rosters that fit 10 starting + 3 bench slots at all
        positions = [POS_CHOICES[k] for k in rng.integers(0, len(POS_CHOICES), 13)]
        has_game = rng.random(13) < rng.uniform(0.3, 1.0)
        values = np.where(has_game, rng.uniform(0.01, 2.0, 13), 0.0)
        try:
            labels = solve_day(positions, values, [True] * 13, [False] * 13, SLOTS)
            break
        except ValueError:
            continue
    mine = sum(1 for lab, g in zip(labels, has_game, strict=True) if lab in SLOTS.starting and g)
    mask = sum(1 << i for i in range(13) if has_game[i])
    theirs = bin(LineupSolver(positions, list(SLOTS.starting)).starters(mask)).count("1")
    assert mine == theirs


def test_load_team_null_draft_slot_and_league_lineup_status_maps(tmp_path, proj_path):
    from fantasy.lineup.players import load_team

    proj = load_projections(proj_path)
    ids = list(proj.df.index)[:3]
    cap = {"teams": {"11": {"draft_slot": None, "players": [
        {"player_id": ids[0], "name": "A", "team": "CHA", "positions": "PG,SG", "status": None, "slot": "PG"},
        {"player_id": ids[1], "name": "B", "team": "NYK", "positions": "C", "status": "Q", "slot": "C"},
        {"player_id": ids[2], "name": "C", "team": "NYK", "positions": "SF", "status": None, "slot": "BN"}]},
        "3": {"draft_slot": None, "players": []}}}
    f = tmp_path / "cap.json"
    f.write_text(json.dumps(cap))
    # no draft slot and no yahoo id -> warning, attributes from projections (no crash on null draft_slot)
    players, warns = load_team(1, proj.df, ids, f)
    assert warns and players[0].team == normalize_team(proj.df.loc[ids[0], "team"])
    # yahoo_team_id from the league state resolves the capture; lineup/status maps take precedence
    team = {"players": ids, "yahoo_team_id": 11, "lineup": {ids[0]: "Util", ids[1]: "BN", ids[2]: "SF"},
            "status": {ids[2]: "O"}}
    players, warns = load_team(1, proj.df, ids, f, team)
    assert [p.slot for p in players] == ["Util", "BN", "SF"]
    assert [p.status for p in players] == [None, None, "O"]
    assert players[0].team == "CHO" and players[1].positions == ("C",)


# ---------------------------------------------------------------- review fixes (lineup-0.2.0)
def _cli_world(tmp_path, proj_path, captured_at="2026-10-21T13:05:00Z", n_me=13, statuses=None, slots=None):
    proj = load_projections(proj_path)
    ids = list(proj.df.index)
    games = tmp_path / "games.csv"
    rows = ["date,visitor,home"] + [f"{(D0 + timedelta(days=k)).isoformat()},BOS,DET" for k in range(6)]
    games.write_text("\n".join(rows) + "\n")
    mine, theirs = ids[0:2 * n_me:2], ids[1:26:2]
    lf = tmp_path / "league.json"
    lf.write_text(json.dumps({"my_team": 1, "teams": {"1": {"name": "Me", "players": mine},
                                                      "2": {"name": "Them", "players": theirs}}}))
    sched = tmp_path / "sched.json"
    sched.write_text(json.dumps({"weeks": [{"week": 1, "start": "2026-10-20", "end": "2026-10-25",
                                            "matchups": [[1, 2]]}]}))
    cols = slots or SLOTS.columns
    cap = {"captured_at": captured_at, "teams": {}}
    for yid, (slot, pids) in enumerate([(1, mine), (2, theirs)], start=1):
        cap["teams"][str(yid)] = {"draft_slot": slot, "players": [
            {"player_id": p, "name": p, "team": "BOS" if k % 2 else "DET", "yahoo_player_id": 1000 + k,
             "positions": "|".join(proj.df.loc[p, "positions"]),
             "status": (statuses or {}).get(k) if slot == 1 else None, "slot": cols[k] if k < len(cols) else "BN"}
            for k, p in enumerate(pids)]}
    capf = tmp_path / "cap.json"
    capf.write_text(json.dumps(cap))
    base = ["lineup", "plan", "--json", "--projections", str(proj_path), "--league-file", str(lf), "--schedule",
            str(sched), "--nba-schedule", str(games), "--capture", str(capf), "--sims", "0"]
    return base, mine


def _run(args):
    from fantasy.cli import app

    r = CliRunner().invoke(app, args)
    return r.exit_code, json.loads(r.output)


def test_cli_refuses_stale_capture_unless_allowed_or_current_slots(tmp_path, proj_path):
    # 03:25 UTC on 10-20 is 23:25 ET on 10-19: stale for a 10-20 plan
    base, mine = _cli_world(tmp_path, proj_path, captured_at="2026-10-20T03:25:00Z")
    code, out = _run(base + ["--date", "2026-10-20"])
    assert code == 2 and out["code"] == "stale_capture" and out["slots_as_of"] == "2026-10-19"
    # shared failure contract (fantasy.cli_common): `error` keeps the text, `message` == `error`
    assert out["ok"] is False and out["message"] == out["error"] and "stale slots" in out["message"]
    code, out = _run(base + ["--date", "2026-10-20", "--allow-stale"])
    assert code == 0 and any("STALE" in w for w in out["warnings"])
    cs = tmp_path / "cs.json"
    cs.write_text(json.dumps({p: ("BN" if k >= 10 else SLOTS.columns[k]) for k, p in enumerate(mine)}))
    code, out = _run(base + ["--date", "2026-10-20", "--current-slots", str(cs)])
    assert code == 0 and not any("STALE" in w for w in out["warnings"])
    assert set(out["target_slots"]) == set(mine)
    assert out["target_slots"][mine[0]]["yahoo_player_id"] == 1000


def test_parse_timestamp_et():
    from fantasy.lineup.cli import parse_timestamp_et

    assert parse_timestamp_et("2026-10-20T03:25:00Z")[0] == date(2026, 10, 19)
    assert parse_timestamp_et("pages read ..., 2026-10-20 ~03:25 UTC")[0] == date(2026, 10, 19)
    assert parse_timestamp_et("read 2026-10-20 09:10 ET")[0] == date(2026, 10, 20)
    assert parse_timestamp_et("2026-10-21T13:05:00+00:00")[0] == date(2026, 10, 21)
    assert parse_timestamp_et(None)[0] is None


def test_cli_midweek_without_accumulated_warns(tmp_path, proj_path):
    base, _ = _cli_world(tmp_path, proj_path)
    code, out = _run(base + ["--date", "2026-10-21"])
    assert code == 0 and any("MID-WEEK" in w for w in out["warnings"])
    assert "rest_share" in out["expected"]["availability_assumption"] or "PRIOR" in out["expected"]["availability_assumption"]


def test_cli_infeasible_roster_returns_json(tmp_path, proj_path):
    base, _ = _cli_world(tmp_path, proj_path, n_me=14)
    code, out = _run(base + ["--date", "2026-10-21"])
    assert code == 2 and out["code"] == "infeasible_roster" and "drop" in out["action"]


def test_cli_locked_player_stays_in_current_slot(tmp_path, proj_path):
    base, mine = _cli_world(tmp_path, proj_path)
    code, free = _run(base + ["--date", "2026-10-21"])
    moved = [c["player_id"] for c in free["changes_vs_current"]]
    assert moved, "fixture should produce at least one move"
    code, out = _run(base + ["--date", "2026-10-21", "--locked", ",".join(moved)])
    assert code == 0
    assert not {c["player_id"] for c in out["changes_vs_current"]} & set(moved)


def test_monotone_shift_keeps_order_of_negative_values():
    """Two players with a game and negative matchup value compete for one slot: the less negative one starts."""
    from fantasy.lineup.plan import daily_plan

    a = RosterPlayer("a", "A", ("C",), "AAA", None, "BN", 70.0)
    b = RosterPlayer("b", "B", ("C",), "AAA", None, "C", 70.0)  # current starter: keep-bonus must not decide
    slots = DaySlots(("C",), 1, 0)
    P = np.ones((2, 1))
    X, labels = daily_plan([a, b], np.array([[-0.01], [-0.02]]), P, slots)
    assert labels[0] == ["C", "BN"]
    c = RosterPlayer("c", "C", ("C",), "AAA", None, "BN", 70.0)
    X, labels = daily_plan([b, c], np.array([[-0.5], [0.0]]), np.array([[1.0], [0.0]]), slots)
    assert labels[0] == ["C", "BN"]  # a player with a game (even negative) beats one without


def test_out_player_on_bench_moved_to_free_il():
    positions = [("PG",), ("C",)]
    slots = DaySlots(("PG",), 1, 1)
    labels = solve_day(positions, np.array([1.0, 0.0]), [True, False], [False, True], slots, current=["PG", "BN"])
    assert labels == ["PG", "IL"]


def test_tipoff_notes_depend_on_slot(world):
    proj, settings, days, nba, me, opp, generic = world
    aaa = [k for k, p in enumerate(me) if p.team == "AAA"]
    tagged = list(me)
    for k in aaa[:2]:
        tagged[k] = RosterPlayer(me[k].player_id, me[k].name, me[k].positions, "AAA", "Q", None, me[k].gp)
    out = build_plan(tagged, opp, proj.df, nba, days, settings, generic, params=LineupParams(n_sims=0))
    for t in out["decide_at_tipoff"]:
        if t["slot"] in SLOTS.starting:
            assert t["note"].startswith("starting: if he is ruled out")
        else:
            assert t["note"].startswith("benched:")


def _fd_world(world):
    from fantasy.lineup.plan import TeamSide, p_matrix, team_week
    from fantasy.lineup.value import cat_state

    proj, settings, days, nba, me, opp, generic = world
    from fantasy.lineup.plan import games_matrix

    def side(players, acc=None):
        G, _ = games_matrix(players, days, nba)
        P = p_matrix(players, G)
        return TeamSide(players, G, P, team_week(players, proj.df, P, acc))

    return settings.cats, side, cat_state


def test_weights_match_finite_differences(world):
    from fantasy.model.lines import STATS

    cats, side, cat_state = _fd_world(world)
    me, opp = side(world[4]), side(world[5])
    X_me, X_opp = me.P > 0, opp.P > 0
    st = cat_state(me.tw, X_me, opp.tw, X_opp, cats)
    w = st.weights()
    keys = [c.key for c in cats]
    for j, c in enumerate(cats):
        if c.kind != "count":
            continue
        h = 1e-3 * max(st.sigma[j], 1.0)
        me.tw.acc[STATS.index(c.stat)] += h
        up = cat_state(me.tw, X_me, opp.tw, X_opp, cats).expected_cats
        me.tw.acc[STATS.index(c.stat)] -= 2 * h
        dn = cat_state(me.tw, X_me, opp.tw, X_opp, cats).expected_cats
        me.tw.acc[STATS.index(c.stat)] += h
        assert (up - dn) / (2 * h) == pytest.approx(w[j], rel=1e-3, abs=1e-9), keys[j]
    # FG%: one extra make (no extra attempt) -> d E ~ w_fg * (1 / A_me) (variance changes are second order)
    j = keys.index("fg_pct")
    A = st.me.atts["fg_pct"]
    h = 0.05
    me.tw.acc[STATS.index("fgm")] += h
    up = cat_state(me.tw, X_me, opp.tw, X_opp, cats).expected_cats
    me.tw.acc[STATS.index("fgm")] -= 2 * h
    dn = cat_state(me.tw, X_me, opp.tw, X_opp, cats).expected_cats
    me.tw.acc[STATS.index("fgm")] += h
    assert (up - dn) / (2 * h) == pytest.approx(w[j] / A, rel=0.05)


def test_turnover_weight_negative_and_more_turnovers_hurt(world):
    from fantasy.model.lines import STATS

    cats, side, cat_state = _fd_world(world)
    me, opp = side(world[4]), side(world[5])
    X_me, X_opp = me.P > 0, opp.P > 0
    st = cat_state(me.tw, X_me, opp.tw, X_opp, cats)
    j = [c.key for c in cats].index("tov")
    assert st.weights()[j] < 0
    before = st.p_win[j]
    me.tw.acc[STATS.index("tov")] += 5
    assert cat_state(me.tw, X_me, opp.tw, X_opp, cats).p_win[j] < before


def test_rest_share_param_changes_healthy_availability(world):
    a = _plan(world, n_sims=0)
    b = _plan(world, n_sims=0, rest_share=1.0)
    pa = {p["player_id"]: p["p_play_today"] for p in a["players"] if p["has_game_today"]}
    pb = {p["player_id"]: p["p_play_today"] for p in b["players"] if p["has_game_today"]}
    assert all(pb[k] <= pa[k] for k in pa) and any(pb[k] < pa[k] for k in pa)
    assert b["params"]["rest_share"] == 1.0 and b["params"]["rest_share_label"] == "prior"
