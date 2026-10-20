"""Live matchup score (`fantasy league score`, kind matchup_score) and banked totals in `fantasy lineup plan`."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select
from test_lineup import _cli_world, _plan, world  # noqa: F401  (world: module fixture)
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db.schema import model_runs
from fantasy.league import matchup
from fantasy.league.settings import LeagueSettings
from fantasy.lineup.plan import build_plan
from fantasy.lineup.value import TeamWeek, team_moments
from fantasy.model.lines import STATS
from fantasy.ops.common import engine
from fantasy.ops.testing import use_test_db
from fantasy.sim.schedule import FantasyWeek

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo" / "matchup_week1.html"
WEEK1 = [FantasyWeek(1, date(2026, 10, 20), date(2026, 10, 25)), FantasyWeek(2, date(2026, 10, 26), date(2026, 11, 1))]
runner = CliRunner()


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    use_test_db(tmp_path, monkeypatch)
    import fantasy.league.matchup_cli as mc

    monkeypatch.setattr(mc, "YAHOO_RAW", tmp_path / "no_yahoo_raw")  # hermetic: no repo league_schedule capture


def invoke(args):
    r = runner.invoke(app, args)
    return r.exit_code, (json.loads(r.output) if r.output.strip().startswith("{") else r.output)


def runs(kind):
    with engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(select(model_runs).where(model_runs.c.kind == kind))]


def _capture(me=None, opp=None, captured_at="2026-10-22T14:00:00Z", week=1, me_id=11, opp_id=2, problems=None):
    base_me = {"fgm": 120, "fga": 250, "fg_pct": 0.48, "ftm": 40, "fta": 50, "ft_pct": 0.8, "fg3m": 30, "pts": 310,
               "reb": 130, "ast": 70, "stl": 20, "blk": 10, "tov": 35}
    base_opp = {"fgm": 110, "fga": 240, "fg_pct": 0.458, "ftm": 45, "fta": 60, "ft_pct": 0.75, "fg3m": 25,
                "pts": 290, "reb": 140, "ast": 60, "stl": 20, "blk": 15, "tov": 30}
    return {"source": "test", "captured_at": captured_at, "week": week, "problems": problems or [],
            "teams": [{"yahoo_team_id": me_id, "name": "Me", "totals": {**base_me, **(me or {})}, "cats_won": 5},
                      {"yahoo_team_id": opp_id, "name": "Them", "totals": {**base_opp, **(opp or {})}, "cats_won": 3}]}


# ---------------------------------------------------------------- parser (constructed HTML fixture)
def test_parse_matchup_html_fixture():
    cap = matchup.parse_matchup_html(FIXTURE.read_text(), my_team=11, captured_at="2026-10-22T14:00:00Z")
    assert cap["week"] == 1
    assert [t["yahoo_team_id"] for t in cap["teams"]] == [11, 2]  # the player table (no team links) was skipped
    me, opp = cap["teams"]
    assert me["name"] == "Harrison's Best Team" and opp["name"] == "jesse's Phenomenal Team"
    assert me["totals"] == {"fgm": 120, "fga": 250, "fg_pct": 0.48, "ftm": 40, "fta": 50, "ft_pct": 0.8, "fg3m": 30,
                            "pts": 310, "reb": 130, "ast": 70, "stl": 20, "blk": 10, "tov": 35}
    assert me["cats_won"] == 5 and opp["totals"]["blk"] == 15
    # only the genuinely absent field is flagged
    assert cap["problems"] == ["games_remaining not in the score table"]


def test_parse_matchup_html_reports_problems_when_layout_changes():
    cap = matchup.parse_matchup_html("<html><body><table><tr><th>A</th></tr></table></body></html>")
    assert cap["teams"] == [] and any("no score table" in p for p in cap["problems"])
    assert any("week not found" in p for p in cap["problems"])
    with pytest.raises(matchup.MatchupError, match="exactly 2 teams"):
        matchup.parse_teams(cap)


def test_header_aliases():
    assert matchup.header_field("FGM/A*") == "fgm_a" and matchup.header_field(" 3PTM ") == "fg3m"
    assert matchup.header_field("ST") == "stl" and matchup.header_field("TO") == "tov"
    assert matchup.header_field("Player") is None


# ---------------------------------------------------------------- summary
def test_summary_pct_from_makes_attempts_and_leaders():
    s = matchup.build_summary(_capture(), my_slot=1, slot_map={11: 1, 2: 5}, calendar=WEEK1)
    assert s["schema"] == "matchup_score/1" and s["week"] == 1 and s["date_et"] == "2026-10-22"
    assert s["me"]["slot"] == 1 and s["opp"]["slot"] == 5 and s["me"]["yahoo_team_id"] == 11
    assert set(s["me"]["totals"]) == set(STATS) and "fg_pct" not in s["me"]["totals"]
    assert s["me"]["fg_pct"] == pytest.approx(120 / 250) and s["opp"]["ft_pct"] == pytest.approx(0.75)
    lead = {c["cat"]: c["leader"] for c in s["categories"]}
    assert lead == {"fg_pct": "me", "ft_pct": "me", "fg3m": "me", "pts": "me", "reb": "opp", "ast": "me",
                    "stl": "tie", "blk": "opp", "tov": "opp"}  # TO: fewer wins
    assert s["score"] == {"me": 5, "opp": 3, "ties": 1}
    assert s["warnings"] == []  # Yahoo's displayed cats_won (5 / 3) agrees


def test_summary_uses_makes_attempts_not_the_displayed_pct():
    cap = _capture(me={"fg_pct": 0.9})  # displayed % disagrees: makes/attempts win, with a warning
    s = matchup.build_summary(cap, my_slot=1, slot_map={11: 1, 2: 5}, calendar=WEEK1)
    assert s["me"]["fg_pct"] == pytest.approx(0.48)
    assert any("displayed fg%" in w for w in s["warnings"])
    # % compared at Yahoo's 3 decimals: .4801 vs .4799 is a tie
    s = matchup.build_summary(_capture(me={"fgm": 4801, "fga": 10000, "pts": 9752},
                                       opp={"fgm": 4799, "fga": 10000, "pts": 9693}),
                              my_slot=1, slot_map={11: 1, 2: 5}, calendar=WEEK1)
    assert next(c for c in s["categories"] if c["cat"] == "fg_pct")["leader"] == "tie"


def test_summary_validation_errors():
    with pytest.raises(matchup.MatchupError, match="totals.fga missing"):
        matchup.build_summary(_capture(me={"fga": None}), my_slot=1, slot_map={11: 1, 2: 5}, calendar=WEEK1)
    with pytest.raises(matchup.MatchupError, match="fgm 300 > fga"):
        matchup.build_summary(_capture(me={"fgm": 300}), my_slot=1, slot_map={11: 1, 2: 5}, calendar=WEEK1)
    with pytest.raises(matchup.MatchupError, match="my team"):
        matchup.build_summary(_capture(), my_slot=1, slot_map={2: 5}, calendar=WEEK1)
    # week from the observed date when the capture has none; no attempts yet -> % null and a tie
    cap = _capture(week=None, me={k: 0 for k in STATS}, opp={k: 0 for k in STATS})
    s = matchup.build_summary(cap, my_slot=1, slot_map={11: 1, 2: 5}, calendar=WEEK1)
    assert s["week"] == 1 and s["me"]["fg_pct"] is None and s["score"] == {"me": 0, "opp": 0, "ties": 9}


def test_latest_score_and_banked_checks():
    def row(week, d, obs, opp_slot=5):
        s = {"schema": matchup.SCHEMA, "week": week, "date_et": d, "observed_at": obs,
             "me": {"slot": 1}, "opp": {"slot": opp_slot}}
        return {"run_id": f"{d}-{obs}", "summary": s, "created_at": obs}

    rows = [row(1, "2026-10-21", "2026-10-21T14:00:00Z"), row(1, "2026-10-22", "2026-10-22T14:00:00Z"),
            row(1, "2026-10-23", "2026-10-23T14:00:00Z"), row(2, "2026-10-22", "2026-10-22T15:00:00Z")]
    got = matchup.latest_score(rows, week=1, my_slot=1, on_or_before=date(2026, 10, 22))
    assert got is not None and got["summary"]["date_et"] == "2026-10-22"  # not the later read, not week 2
    assert matchup.latest_score(rows, week=1, my_slot=2, on_or_before=date(2026, 10, 22)) is None
    assert matchup.banked_checks(got["summary"], date(2026, 10, 22)) == []  # 10:00 ET on the plan day
    assert "BEFORE" in matchup.banked_checks(got["summary"], date(2026, 10, 23))[0].upper()
    late = {"observed_at": "2026-10-22T23:30:00Z"}  # 19:30 ET
    assert "counted as banked AND as remaining" in matchup.banked_checks(late, date(2026, 10, 22))[0]


# ---------------------------------------------------------------- CLI
def _league_with_yahoo_ids(tmp_path):
    lf = tmp_path / "league.json"
    league = json.loads(lf.read_text())
    league["teams"]["1"]["yahoo_team_id"] = 11
    league["teams"]["2"]["yahoo_team_id"] = 2
    lf.write_text(json.dumps(league))
    return lf


def test_cli_score_html_and_capture_save_idempotent(tmp_path, proj_path):
    _cli_world(tmp_path, proj_path)
    lf = _league_with_yahoo_ids(tmp_path)
    common = ["--state", str(lf), "--schedule", str(tmp_path / "sched.json"), "--json"]
    code, out = invoke(["league", "score", "--html", str(FIXTURE), "--captured-at", "2026-10-22T14:00:00Z",
                        "--save", *common])
    assert code == 0, out
    s = out["summary"]
    assert s["me"]["slot"] == 1 and s["opp"]["slot"] == 2 and s["score"] == {"me": 5, "opp": 3, "ties": 1}
    assert out["saved"]["inserted"] is True and out["run_id"].startswith("ms-w1-2026-10-22-")
    [row] = runs("matchup_score")
    assert row["model_version"] == "matchup-score-0.1.0" and row["inputs"]["capture_sha256"]
    # the same score from the JS capture (as the JSON string the JS tool returns), read later the same day: no-op
    capf = tmp_path / "cap.json"
    capf.write_text(json.dumps(json.dumps(_capture(captured_at="2026-10-22T15:30:00Z"))))
    code, out2 = invoke(["league", "score", "--capture", str(capf), "--save", *common])
    assert code == 0 and out2["run_id"] == out["run_id"] and out2["saved"]["inserted"] is False
    assert len(runs("matchup_score")) == 1
    # changed totals -> a new row
    capf.write_text(json.dumps(_capture(me={"reb": 131})))
    code, out3 = invoke(["league", "score", "--capture", str(capf), "--save", *common])
    assert code == 0 and out3["saved"]["inserted"] is True and len(runs("matchup_score")) == 2
    # unusable capture -> exit 2 invalid_capture
    capf.write_text(json.dumps(_capture(me={"fga": None}, problems=["team 11: fga not parsed"])))
    code, bad = invoke(["league", "score", "--capture", str(capf), *common])
    assert code == 2 and bad["code"] == "invalid_capture" and "fga not parsed" in bad["message"]


def _save_score(tmp_path, cap):
    capf = tmp_path / "score.json"
    capf.write_text(json.dumps(cap))
    code, out = invoke(["league", "score", "--capture", str(capf), "--state", str(tmp_path / "league.json"),
                        "--schedule", str(tmp_path / "sched.json"), "--save", "--json"])
    assert code == 0, out
    return out


def test_plan_uses_newest_saved_score_and_banked_lead_zeroes_the_weight(tmp_path, proj_path):
    base, _ = _cli_world(tmp_path, proj_path, captured_at="2026-10-22T13:05:00Z")
    _league_with_yahoo_ids(tmp_path)
    args = base + ["--date", "2026-10-22"]
    code, plain = invoke(args + ["--no-score"])
    assert code == 0 and plain["accumulated_source"]["kind"] == "none"
    assert any("MID-WEEK" in w for w in plain["warnings"])
    # a huge banked rebounding lead: rebounds are settled -> weight ~0, P(win reb) ~1
    saved = _save_score(tmp_path, _capture(me={"reb": 2000}, captured_at="2026-10-22T13:30:00Z"))
    code, out = invoke(args + ["--save"])
    assert code == 0, out
    src = out["accumulated_source"]
    assert src["kind"] == "matchup_score" and src["run_id"] == saved["run_id"]
    assert src["observed_at"] == "2026-10-22T13:30:00Z" and src["score"]["me"] == 6
    assert not any("MID-WEEK" in w for w in out["warnings"])
    assert out["accumulated"]["me"]["reb"] == 2000 and out["accumulated"]["opp"]["fga"] == 240
    w0, w1 = plain["category_weights"]["reb"]["weight"], out["category_weights"]["reb"]["weight"]
    assert abs(w1) < 1e-3 * abs(w0)
    cwp = out["expected"]["cat_win_prob"]
    assert set(cwp) == {"fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"}
    assert cwp["reb"] > 0.999 and out["expected"]["cat_win_prob_method"].startswith("analytic")  # --sims 0
    # the saved lineup_plan row carries it for the web page
    [row] = runs("lineup_plan")
    assert row["summary"]["expected"]["cat_win_prob"]["reb"] > 0.999
    assert row["summary"]["accumulated_source"]["run_id"] == saved["run_id"]
    # a read from a later day is never used for an earlier plan
    code, early = invoke(base + ["--date", "2026-10-21", "--allow-stale"])
    assert code == 0 and early["accumulated_source"]["kind"] == "none"
    assert "no saved matchup_score" in early["accumulated_source"]["note"]


def test_plan_score_file_and_mismatch(tmp_path, proj_path):
    base, _ = _cli_world(tmp_path, proj_path, captured_at="2026-10-22T13:05:00Z")
    _league_with_yahoo_ids(tmp_path)
    capf = tmp_path / "s.json"
    capf.write_text(json.dumps(_capture(captured_at="2026-10-22T13:30:00Z")))
    code, out = invoke(base + ["--date", "2026-10-22", "--score", str(capf), "--sims", "300"])
    assert code == 0, out
    assert out["accumulated_source"]["kind"] == "score_file" and out["inputs"]["matchup_score"]["sha256"]
    assert out["expected"]["cat_win_prob_method"].startswith("monte_carlo")
    assert all(0 <= v <= 1 for v in out["expected"]["cat_win_prob"].values())
    # wrong opponent (Yahoo team 7 is not this week's opponent) -> refused
    capf.write_text(json.dumps(_capture(opp_id=7)))
    league = json.loads((tmp_path / "league.json").read_text())
    league["teams"]["3"] = {"name": "Other", "players": [], "yahoo_team_id": 7}
    (tmp_path / "league.json").write_text(json.dumps(league))
    code, bad = invoke(base + ["--date", "2026-10-22", "--score", str(capf)])
    assert code == 2 and bad["code"] == "score_mismatch" and "opponent" in bad["message"]


# ---------------------------------------------------------------- model: banked stats in the weekly margin model
def test_tied_category_gets_the_largest_weight(world):  # noqa: F811
    proj, settings, days, nba, me, opp, generic = world
    base = _plan(world, n_sims=0)
    mu0 = base["category_weights"]["reb"]["margin"]
    sd = base["category_weights"]["reb"]["sd"]
    res = {}
    for k in (-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0):  # banked margin shifts the final margin to ~ -k sd
        shift = mu0 + k * sd
        acc_me, acc_opp = ({"reb": -shift}, {}) if shift < 0 else ({}, {"reb": shift})
        res[k] = _plan_acc(world, acc_me, acc_opp)["category_weights"]["reb"]
    assert abs(res[0.0]["margin"]) < 0.25 * res[0.0]["sd"]
    assert all(res[0.0]["weight"] >= r["weight"] for r in res.values())
    assert res[2.0]["weight"] < 0.2 * res[0.0]["weight"]  # two sds behind: nearly out of play


def _plan_acc(world, acc_me, acc_opp):  # noqa: F811
    from fantasy.lineup.plan import LineupParams

    proj, settings, days, nba, me, opp, generic = world
    return build_plan(me, opp, proj.df, nba, days, settings, generic, acc_me, acc_opp, LineupParams(n_sims=0))


def test_banked_pct_enters_as_makes_and_attempts():
    settings = LeagueSettings()
    cats = settings.cats
    acc = np.zeros(len(STATS))
    acc[STATS.index("fgm")], acc[STATS.index("fga")] = 45.0, 100.0
    acc[STATS.index("ftm")], acc[STATS.index("fta")] = 9.0, 10.0
    S = np.zeros((1, len(STATS)))
    S[0, STATS.index("fgm")], S[0, STATS.index("fga")] = 10.0, 20.0  # a 50% shooter, 20 FGA a game
    one = np.ones(1)
    tw = TeamWeek(["p"], S, np.ones((1, 2)), acc, 0.1 * one, 0.02 * one, 0.02 * one)
    fg = [c.key for c in cats].index("fg_pct")
    done = team_moments(tw, np.zeros((1, 2), dtype=bool), cats)  # no games left: exactly banked makes/attempts
    assert done.mean[fg] == pytest.approx(0.45) and done.var[fg] == 0
    two = team_moments(tw, np.ones((1, 2), dtype=bool), cats)  # 2 games: (45 + 20) / (100 + 40), not avg of %s
    assert two.mean[fg] == pytest.approx(65 / 140)
    s = matchup.build_summary(_capture(), my_slot=1, slot_map={11: 1, 2: 5}, calendar=WEEK1)
    acc_json = matchup.accumulated(s)
    assert acc_json["me"]["fgm"] == 120 and acc_json["me"]["fga"] == 250 and "fg_pct" not in acc_json["me"]
