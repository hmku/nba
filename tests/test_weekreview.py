"""`fantasy review week` (fantasy.inseason.weekreview): slot filtering, percent cats from makes/attempts, z signs,
missing-capture fallback, ledger 'missed' list, save/load of the week_review row. Synthetic data only."""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from synthetic import make_projections
from typer.testing import CliRunner

from fantasy.inseason import weekreview as wr
from fantasy.model.noise import game_var

START = date(2026, 11, 2)  # Monday
END = START + timedelta(days=6)
STATS = ("fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov")


def _proj() -> pd.DataFrame:
    df = make_projections(n=20, seed=3).set_index("player_id")
    df["status"] = "healthy"
    df["gp"] = 82.0
    return df


def _capture(ts: str, me_slots: dict[str, str], opp_slots: dict[str, str]) -> dict:
    def players(slots: dict[str, str]) -> list[dict]:
        return [{"player_id": p, "slot": s, "name": p, "status": None} for p, s in slots.items()]
    return {"captured_at": ts, "teams": {"11": {"name": "Me", "draft_slot": 1, "players": players(me_slots)},
                                         "6": {"name": "Opp", "draft_slot": 7, "players": players(opp_slots)}}}


def _game(pid: str, d: date, proj: pd.DataFrame, scale: float = 1.0, **over: float) -> dict:
    """A box-score row equal to the player's projection (x scale), with `over` overriding stats."""
    r = proj.loc[pid]
    row = {"player_id": pid, "player_name": pid, "game_id": f"{pid}-{d}", "game_date": d, "team": "TST",
           "played": True, **{s: float(round(r[s] * scale)) for s in STATS}}
    row.update(over)
    row["pts"] = over.get("pts", 2 * row["fgm"] + row["fg3m"] + row["ftm"])
    return row


ME = {"player-000": "PG", "player-001": "SG", "player-002": "BN"}
OPP = {"player-010": "PG", "player-011": "C", "player-012": "IL"}


def _inputs(box_rows: list[dict], snaps: list[wr.Snapshot], proj: pd.DataFrame, **kw) -> wr.ReviewInputs:
    box = pd.DataFrame(box_rows)
    return wr.ReviewInputs(week=3, start=START, end=END, my_yahoo=11, opp_yahoo=6, my_slot=1, opp_slot=7,
                           names={11: "Me", 6: "Opp"}, snapshots=snaps, box=box, proj=proj,
                           proj_source={"kind": "file", "path": "synthetic"}, team_games=lambda t, d: True,
                           start_slots=10, today=END + timedelta(days=1), **kw)


def _snap(ts: str, me: dict[str, str] = ME, opp: dict[str, str] = OPP) -> wr.Snapshot:
    s = wr.parse_snapshot(_capture(ts, me, opp), "synthetic")
    assert s is not None
    return s


def test_slot_filtering_bench_and_il_games_do_not_count():
    proj = _proj()
    snaps = [_snap("2026-11-02T12:00:00Z")]
    rows = [_game(p, START, proj) for p in [*ME, *OPP]]
    cg, per_day, warns = wr.counted_games(pd.DataFrame(rows), snaps, 11, [START])
    assert set(cg[cg["counted"]]["player_id"]) == {"player-000", "player-001"}
    assert list(cg[~cg["counted"]]["player_id"]) == ["player-002"]  # BN
    cg6, _, _ = wr.counted_games(pd.DataFrame(rows), snaps, 6, [START])
    assert set(cg6[cg6["counted"]]["player_id"]) == {"player-010", "player-011"}  # IL excluded
    assert per_day[0]["slots_known"] and not warns


def test_lineup_uses_latest_read_on_or_before_the_day_and_carries_forward():
    proj = _proj()
    snaps = [_snap("2026-11-02T12:00:00Z"),
             _snap("2026-11-02T22:30:00Z", me={"player-000": "PG", "player-001": "BN", "player-002": "SG"}),
             _snap("2026-11-05T12:00:00Z")]  # day 3 read, after the games of day 2
    d2 = START + timedelta(days=1)
    rows = [_game(p, d, proj) for d in (START, d2) for p in ME]
    cg, per_day, _ = wr.counted_games(pd.DataFrame(rows), snaps, 11, [START, d2])
    counted = {(r["player_id"], r["game_date"]) for _, r in cg[cg["counted"]].iterrows()}
    # the 22:30Z read (18:30 ET, same day) replaces the morning one: player-002 starts, player-001 sits
    assert ("player-002", START) in counted and ("player-001", START) not in counted
    assert ("player-002", d2) in counted  # carried forward to day 2
    assert per_day[1]["carried_forward"] is True


def test_missing_capture_falls_back_to_all_games_with_warning():
    proj = _proj()
    snaps = [_snap("2026-11-04T12:00:00Z")]  # first read after day 1
    rows = [_game(p, START, proj) for p in ME]
    cg, per_day, warns = wr.counted_games(pd.DataFrame(rows), snaps, 11, [START])
    assert cg["counted"].all() and len(cg) == 3  # BN player counted too: slots unknown
    assert per_day[0]["slots_known"] is False
    assert any("slots unknown" in w for w in warns)


def test_percent_categories_from_makes_and_attempts():
    proj = _proj()
    pid = "player-000"
    p = proj.loc[pid, "fgm"] / proj.loc[pid, "fga"]
    # game 1: 2/4, game 2: 10/20 -> 12/24 = .500 (the mean of game %s would also be .5; use unequal %s:)
    rows = [_game(pid, START, proj, fgm=1, fga=4), _game(pid, START + timedelta(1), proj, fgm=10, fga=20)]
    snaps = [_snap("2026-11-02T12:00:00Z", me={pid: "PG"})]
    cg, _, _ = wr.counted_games(pd.DataFrame(rows), snaps, 11, [START, START + timedelta(1)])
    br, _, _ = wr.team_breakdown(cg, proj, {"totals": None})
    fg = br["categories"]["fg_pct"]
    assert fg["actual"] == pytest.approx(11 / 24, abs=1e-4)  # not mean(1/4, 10/20) = .375
    assert fg["expected_pct_on_actual_attempts"] == pytest.approx(p, abs=1e-4)
    assert fg["makes_above_expected"] == pytest.approx(11 - 24 * p, abs=0.05)
    assert fg["sd_makes"] == pytest.approx(np.sqrt(24 * p * (1 - p)), abs=0.01)
    assert (fg["z"] < 0) == (11 < 24 * p)


def test_z_sign_and_helps_for_counts_and_turnovers():
    proj = _proj()
    pid = "player-000"
    m_pts, m_tov = proj.loc[pid, "pts"], proj.loc[pid, "tov"]
    row = _game(pid, START, proj, pts=float(round(m_pts + 15)), tov=float(round(m_tov + 4)))
    snaps = [_snap("2026-11-02T12:00:00Z", me={pid: "PG"})]
    cg, _, _ = wr.counted_games(pd.DataFrame([row]), snaps, 11, [START])
    br, players, _ = wr.team_breakdown(cg, proj, {"totals": None})
    pts, tov = br["categories"]["pts"], br["categories"]["tov"]
    assert pts["z"] > 0 and pts["helps"] is True
    assert pts["sd"] == pytest.approx(float(np.sqrt(game_var("pts", m_pts))), abs=0.01)
    assert tov["z"] > 0 and tov["helps"] is False  # more turnovers than projected hurts
    assert players[0]["contrib_z"]["pts"] > 0 and players[0]["contrib_z"]["tov"] < 0


def test_score_categories_ratio_rounding_and_turnovers():
    me = dict.fromkeys(STATS, 10.0) | {"fgm": 4740, "fga": 10000, "tov": 5}
    opp = dict.fromkeys(STATS, 10.0) | {"fgm": 4744, "fga": 10000, "tov": 9}
    rows, score = wr.score_categories(me, opp)
    by = {r["cat"]: r["winner"] for r in rows}
    assert by["fg_pct"] == "tie"  # .474 vs .474 at Yahoo's 3 decimals
    assert by["tov"] == "me"
    assert score == {"me": 1, "opp": 0, "ties": 8}


def test_build_review_summary_and_volume():
    proj = _proj()
    snaps = [_snap("2026-11-01T12:00:00Z")]
    rows = [_game(p, START + timedelta(d), proj) for d in range(7) for p in ("player-000", "player-001")]
    rows += [_game(p, START + timedelta(d), proj, scale=1.6) for d in range(7) for p in ("player-010", "player-011")]
    rev = wr.build_review(_inputs(rows, snaps, proj))
    assert rev["schema"] == wr.SCHEMA and rev["final"]["complete"] and rev["final"]["basis"] == "box_scores"
    me, op = rev["teams"]["me"], rev["teams"]["opp"]
    assert me["games_counted"] == 14 and me["games_benched"] == 0  # player-002 had no games
    # 3 non-IL players x 7 days x P(plays)=1 (gp 82) = 21 expected; 2 played
    assert me["games_expected_week_start"] == pytest.approx(21.0)
    assert me["categories"]["pts"]["over_under"] == pytest.approx(0, abs=7)
    assert me["categories"]["pts"]["volume_effect"] < 0
    assert op["categories"]["pts"]["z"] > 2
    s = rev["summary"]
    assert s["headline"].startswith(("Won", "Lost", "Tied")) and "vs Opp" in s["headline"]
    assert 1 <= len(s["bullets"]) <= 4 and any("Opponent" in b for b in s["bullets"])


def test_ledger_missed_moves():
    tables = {
        "transactions": [
            {"txn_id": "t1", "kind": "add", "status": "failed", "payload": {"add": {"player_id": "x"}},
             "error": "Yahoo error #845", "created_at": "2026-11-03T15:00:00+00:00"},
            {"txn_id": "t2", "kind": "lineup", "status": "verified", "payload": {"date": "2026-11-03", "changes": []},
             "created_at": "2026-11-03T15:00:00+00:00"},
            {"txn_id": "t0", "kind": "add", "status": "failed", "payload": {}, "created_at": "2026-10-20T00:00:00Z"}],
        "decisions": [{"kind": "lineup", "choice": {"a": 1}, "agrees_with_model": True, "rationale": "r",
                       "created_at": "2026-11-04T12:00:00Z"}],
        "approvals": [{"approval_id": "a1", "action_type": "add_drop", "status": "expired", "summary": "add y",
                       "requested_at": "2026-11-04T12:00:00Z"}],
        "agent_events": [{"event_id": "e1", "kind": "error", "message": "transaction t1 failed",
                          "ts": "2026-11-03T15:01:00Z"}],
    }
    led = wr.ledger_window(START, END, fetch=lambda t: tables[t])
    assert led["available"] and led["counts"] == {"transactions": 2, "decisions": 1, "missed": 2}
    assert [m["type"] for m in led["missed"]] == ["txn_failed", "approval_expired"]  # error event deduped


def test_pwin_trajectory_prefers_monte_carlo():
    rows = [{"run_id": "lp-b", "created_at": "2026-11-03T11:00:00Z",
             "summary": {"week": 3, "my_team": 1, "date": "2026-11-03",
                         "expected": {"optimized": {"p_win_matchup": 0.7}}}},
            {"run_id": "lp-a", "created_at": "2026-11-02T11:00:00Z",
             "summary": {"week": 3, "my_team": 1, "date": "2026-11-02", "expected": {
                 "optimized": {"p_win_matchup": 0.9},
                 "monte_carlo": {"plans": {"optimized": {"p_win_matchup": 0.8, "p_win_matchup_se": 0.003}}}}}},
            {"run_id": "lp-x", "created_at": "2026-11-02T11:00:00Z", "summary": {"week": 2, "expected": {}}}]
    t = wr.pwin_trajectory(rows, 3, 1)
    assert [(x["run_id"], x["p_win"], x["method"]) for x in t] == [("lp-a", 0.8, "monte_carlo"),
                                                                    ("lp-b", 0.7, "analytic")]


def test_save_and_load_row_idempotent(tmp_path, monkeypatch):
    from fantasy.lineup import record
    from fantasy.ops.testing import use_test_db

    use_test_db(tmp_path, monkeypatch)
    proj = _proj()
    rows = [_game(p, START, proj) for p in [*ME, *OPP]]
    rev = wr.build_review(_inputs(rows, [_snap("2026-11-01T12:00:00Z")], proj))
    row = wr.review_row(rev, inputs={"box_scores": {"file": "x"}}, params={"command": "review week"})
    assert row["run_id"].startswith("wr-w3-") and row["model_version"] == wr.MODEL_VERSION
    st = record.store()
    assert wr.save(row, st)["inserted"] is True
    assert wr.save(row, st)["inserted"] is False
    got = wr.latest_review(st.rows(wr.KIND), week=3)
    assert got is not None and got["summary"]["summary"] == rev["summary"]
    assert wr.latest_review(st.rows(wr.KIND), week=2) is None


def test_cli_week_review_end_to_end(tmp_path, monkeypatch):
    from fantasy.cli import app
    from fantasy.inseason import review_cli
    from fantasy.sim import schedule as sched_mod

    proj = _proj()
    proj_path = tmp_path / "proj.csv"
    proj.reset_index().to_csv(proj_path, index=False)
    caps = tmp_path / "yahoo"
    caps.mkdir()
    (caps / "league_rosters_2026-11-01.json").write_text(json.dumps(_capture("2026-11-01T12:00:00Z", ME, OPP)))
    tot = dict.fromkeys(STATS, 0)
    (caps / "matchup_2026-11-04T1200Z.json").write_text(json.dumps({
        "captured_at": "2026-11-04T12:00:00Z", "week": 3,
        "teams": [{"yahoo_team_id": 11, "totals": tot, "games_played": 0, "games_remaining": 20},
                  {"yahoo_team_id": 6, "totals": tot, "games_played": 0, "games_remaining": 20}]}))
    rows = [_game(p, START + timedelta(d), proj) for d in range(7) for p in [*ME, *OPP]]
    box = tmp_path / "box.csv"
    pd.DataFrame(rows).to_csv(box, index=False)
    sched = tmp_path / "sched.json"
    sched.write_text(json.dumps({"weeks": [{"week": 3, "start": str(START), "end": str(END),
                                            "matchups": [[1, 7]]}]}))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"my_team": 1, "teams": {"1": {"yahoo_team_id": 11, "name": "Me"},
                                                         "7": {"yahoo_team_id": 6, "name": "Opp"}}}))
    monkeypatch.setattr(review_cli, "STATE", state)
    monkeypatch.setattr(sched_mod, "load_nba_schedule", lambda *a, **k: None)
    res = CliRunner().invoke(app, ["review", "week", "--week", "3", "--projections", str(proj_path),
                                   "--box-scores", str(box), "--captures-dir", str(caps), "--schedule", str(sched),
                                   "--no-ledger", "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    r = out["review"]
    assert r["opponent"]["yahoo_team_id"] == 6 and r["final"]["basis"] == "yahoo+box_scores"
    assert len(r["trajectory"]) == 1 and r["final"]["complete"]
    # banked through 11-03 (morning read): Yahoo's zeros + our counted games from 11-04 on
    assert r["teams"]["me"]["games_counted"] == 14
    assert r["projection_source"]["kind"] == "file"


def test_backlog_flags_weeks_without_final_score_or_complete_review() -> None:
    from types import SimpleNamespace as NS

    cal = [NS(week=1, start=date(2026, 10, 20), end=date(2026, 10, 25)),
           NS(week=2, start=date(2026, 10, 26), end=date(2026, 11, 1)),
           NS(week=3, start=date(2026, 11, 2), end=date(2026, 11, 8))]

    def score(week: int, rem: int) -> dict:
        return {"summary": {"week": week, "me": {"games_remaining": rem}, "opp": {"games_remaining": 0}}}

    def review(week: int, complete: bool) -> dict:
        return {"summary": {"schema": wr.SCHEMA, "week": week, "final": {"complete": complete}}}

    scores = [score(1, 0), score(2, 2)]
    reviews = [review(1, True), review(2, True), review(3, False)]
    todo = wr.backlog(cal, date(2026, 11, 9), scores, reviews)
    assert [(t["week"], t["needs_final_score"], t["needs_review"]) for t in todo] == [(2, True, False), (3, True, True)]
    assert wr.backlog(cal, date(2026, 11, 8), scores, reviews)[-1]["week"] == 2  # week 3 not over yet
