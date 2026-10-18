"""Draft CLI hardening (2026-10-18): draft id guards, prepare-pick retry/re-own/board checks,
confirm-pick after `txn mark verified`, input validation, simulate custom order, ok/draft_id keys."""

import json

import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.ops import ledger, transactions

runner = CliRunner()
SNAKE = ";".join(",".join(map(str, range(1, 13) if r % 2 == 0 else range(12, 0, -1))) for r in range(13))


@pytest.fixture(autouse=True)
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)


def cli(*args):
    res = runner.invoke(app, [str(a) for a in args])
    out = res.stdout
    if "--json" in args:
        out = json.loads(out)
    return res.exit_code, out


@pytest.fixture()
def P(proj_path):
    return ["--projections", str(proj_path)]


def _live_run(draft_id):
    rid = ledger.start_run("t", mode="live")
    assert cli("lock", "acquire", f"draft:{draft_id}", "--run-id", rid)[0] == 0
    return rid


def test_init_draft_id_guards():
    code, out = cli("draft", "init", "--draft-id", "mock-x", "--json")
    assert code == 2 and "--mock" in out["error"]
    code, out = cli("draft", "init", "--draft-id", "my-league", "--json")
    assert code == 2 and "nba.l." in out["error"]
    assert cli("draft", "init", "--draft-id", "my-league", "--force", "--json")[0] == 0
    code, out = cli("draft", "init", "--draft-id", "nba.l.44495-2026", "--json")
    assert code == 0 and out["draft_id"] == "nba.l.44495-2026" and out["ok"] is True
    code, out = cli("draft", "init", "--draft-id", "mock-1", "--mock", "--json")
    assert code == 0 and out["draft"]["is_mock"]
    # the default draft is the real one, and board says which one it resolved
    code, out = cli("draft", "board", "--json")
    assert code == 0 and out["ok"] is True and out["draft_id"] in ("nba.l.44495-2026", "my-league")
    code, out = cli("draft", "board", "--draft-id", "mock-1", "--json")
    assert out["draft_id"] == "mock-1"


def test_ok_and_draft_id_in_outputs(P):
    cli("draft", "init", "--draft-id", "mock-o", "--mock")
    D = ["--draft-id", "mock-o"]
    code, out = cli("draft", "order", "--json")
    assert code == 0 and out["ok"] is True and out["picks"][:3] == [1, 24, 36]
    code, out = cli("draft", "queue", "--top", 5, *D, "--json", *P)
    assert code == 0 and out["ok"] is True and out["draft_id"] == "mock-o" and len(out["queue"]) == 5
    code, out = cli("draft", "pick", "Player 005", "--overall", 1, *D, "--json", *P)
    assert code == 0 and out["draft_id"] == "mock-o"
    code, out = cli("draft", "undo", 1, *D, "--json")
    assert code == 0 and out["draft_id"] == "mock-o"
    code, out = cli("draft", "set-order", "--rounds", SNAKE, *D, "--json")
    assert code == 0 and out["draft_id"] == "mock-o" and out["my_picks"][:3] == [1, 24, 25]


def test_input_validation(P):
    cli("draft", "init", "--draft-id", "mock-v", "--mock")
    D = ["--draft-id", "mock-v"]
    code, out = cli("draft", "recommend", *D, "--strategy", "bogus", "--json", *P)
    assert code == 2 and "punt_ft" in out["valid"] and "bogus" in out["error"]
    code, out = cli("draft", "recommend", *D, "--objective", "bogus", "--json", *P)
    assert code == 2 and out["valid"] == ["cat_wins", "matchup"]
    code, out = cli("draft", "recommend", *D, "--punt", "bogus", "--json", *P)
    assert code == 2 and "ft_pct" in out["valid"]
    code, out = cli("draft", "simulate", "--strategy", "bogus", "--json", *P)
    assert code == 2
    code, out = cli("values", "build", "--punt", "ft_pct,bogus", "--no-save", "--json", *P)
    assert code == 2 and "bogus" in out["error"] and "ft_pct" in out["valid"]
    code, out = cli("values", "build", "--punt", "ft_pct", "--no-save", "--top", 3, "--json", *P)
    assert code == 0 and out["ok"] is True and out["params"]["punt"] == ["ft_pct"]
    # set-order: wrong round count / bad slots / junk -> exit 2
    assert cli("draft", "set-order", "--rounds", "1,2,3;3,2,1", *D, "--json")[0] == 2
    bad = SNAKE.replace("12,", "11,", 1)
    assert cli("draft", "set-order", "--rounds", bad, *D, "--json")[0] == 2
    assert cli("draft", "set-order", "--rounds", "a,b", *D, "--json")[0] == 2


def test_recommend_valuation_params(P):
    cli("draft", "init", "--draft-id", "mock-k", "--mock")
    code, out = cli("draft", "recommend", "--draft-id", "mock-k", "--kappa", 0, "--punt", "ft_pct",
                    "--strategy", "balanced", "--no-save", "--json", *P)
    assert code == 0, out
    assert out["ok"] is True and out["valuation_params"] == {"kappa": 0.0, "punt": ["ft_pct"]}
    assert out["draft_id"] == "mock-k" and out["recommendations"]


def test_simulate_uses_custom_order(P):
    cli("draft", "init", "--draft-id", "mock-s", "--mock")
    cli("draft", "set-order", "--rounds", SNAKE, "--draft-id", "mock-s")
    code, out = cli("draft", "simulate", "--sims", 1, "--draft-id", "mock-s", "--strategy", "balanced", "--json", *P)
    assert code == 0, out
    assert out["ok"] is True and out["draft_id"] == "mock-s" and out["custom_order"] is True
    assert out["order_type"] == "custom" and len(out["sims"][0]["roster"]) == 13
    code, out = cli("draft", "simulate", "--sims", 1, "--strategy", "balanced", "--json", *P)
    assert code == 0 and out["draft_id"] is None and out["custom_order"] is False
    assert cli("draft", "simulate", "--draft-id", "nope", "--json", *P)[0] == 1


def test_prepare_pick_board_checks_and_gaps(P):
    cli("draft", "init", "--draft-id", "mock-b", "--mock")
    D = ["--draft-id", "mock-b"]
    rid = _live_run("mock-b")
    base = ["draft", "prepare-pick", "Player 050", *D, "--run-id", rid, "--json", *P]
    code, out = cli(*base, "--overall", 2)  # slot 2's pick
    assert code == 4 and out["action"] == "DO_NOT_CLICK" and out["my_picks"][:2] == [1, 24]
    cli("draft", "pick", "Player 000", "--overall", 1, *D, *P)
    code, out = cli(*base, "--overall", 1)  # already on the board
    assert code == 4 and out["action"] == "DO_NOT_CLICK" and "already recorded" in out["message"]
    code, out = cli("draft", "prepare-pick", "Player 000", "--overall", 24, *D, "--run-id", rid, "--json", *P)
    assert code == 4 and "already recorded as drafted" in out["message"]
    cli("draft", "pick", "Player 010", "--overall", 20, *D, *P)  # picks 2..19 missing
    code, out = cli(*base, "--overall", 24)
    assert code == 0 and out["action"] == "CLICK", out
    assert out["gaps"] == list(range(2, 20)) and any("not recorded" in w for w in out["warnings"])


def test_prepare_pick_retry_reown_and_mode(P):
    cli("draft", "init", "--draft-id", "mock-r", "--mock")
    D = ["--draft-id", "mock-r"]
    dry = ledger.start_run("t")
    base = ["draft", "prepare-pick", "Player 003", "--overall", 1, *D, "--json", *P]
    code, out = cli(*base, "--run-id", dry)
    assert code == 4 and out["run_mode"] == "dry_run"
    planned = transactions.draft_pick_txns("mock-r", 1)
    assert [t["status"] for t in planned] == ["planned"] and planned[0]["run_id"] == dry
    decs = ledger.list_decisions(dry)
    assert decs[-1]["choice"]["mode"] == "dry_run"

    live = _live_run("mock-r")
    code, out = cli(*base, "--run-id", live)  # re-owns the dry run's planned txn
    assert code == 0 and out["action"] == "CLICK" and out["txn_note"].startswith("re-owned")
    assert out["txn_id"] == planned[0]["txn_id"] and out["run_mode"] == "live"
    assert transactions.get_txn(out["txn_id"])["run_id"] == live
    assert ledger.list_decisions(live)[-1]["choice"]["mode"] == "live"
    # submitted -> never click again (exit 7)
    code, again = cli(*base, "--run-id", live)
    assert code == 7 and again["action"] == "DO_NOT_CLICK"
    # failed -> automatic retry with attempt 2 (same player)
    assert cli("draft", "confirm-pick", out["txn_id"], "--failed", "click did not register", "--json")[0] == 0
    code, out2 = cli(*base, "--run-id", live)
    assert code == 0 and out2["action"] == "CLICK" and out2["attempt"] == 2, out2
    assert out2["txn_id"] != out["txn_id"]
    # failed again, then a different player: attempt 3
    cli("draft", "confirm-pick", out2["txn_id"], "--failed", "taken", "--json")
    code, out3 = cli("draft", "prepare-pick", "Player 004", "--overall", 1, *D, "--run-id", live, "--json", *P)
    assert code == 0 and out3["attempt"] == 3


def test_prepare_pick_cancels_planned_txn_for_other_player(P):
    cli("draft", "init", "--draft-id", "mock-c", "--mock")
    D = ["--draft-id", "mock-c"]
    dry = ledger.start_run("t")
    cli("draft", "prepare-pick", "Player 003", "--overall", 1, *D, "--run-id", dry, "--json", *P)
    live = _live_run("mock-c")
    code, out = cli("draft", "prepare-pick", "Player 007", "--overall", 1, *D, "--run-id", live, "--json", *P)
    assert code == 0 and out["action"] == "CLICK"
    st = {t["payload"]["player_id"]: t["status"] for t in transactions.draft_pick_txns("mock-c", 1)}
    assert st == {"player-003": "cancelled", "player-007": "submitted"}


def test_confirm_pick_after_txn_mark_verified(P):
    cli("draft", "init", "--draft-id", "mock-t", "--mock")
    D = ["--draft-id", "mock-t"]
    live = _live_run("mock-t")
    code, out = cli("draft", "prepare-pick", "Player 002", "--overall", 1, *D, "--run-id", live, "--json", *P)
    assert code == 0
    tid = out["txn_id"]
    assert cli("txn", "mark", tid, "verified", "--evidence", '{"seen": true}', "--json")[0] == 0
    board = cli("draft", "board", *D, "--json")[1]
    assert board["picks_made"] == 0  # txn mark alone does not touch the board
    code, res = cli("draft", "confirm-pick", tid, "--json", *P)  # no evidence needed: already verified
    assert code == 0 and res["already_verified"] is True and res["pick"]["status"] == "recorded"
    code, res = cli("draft", "confirm-pick", tid, "--evidence", "again", "--json", *P)
    assert code == 0 and res["pick"]["status"] == "unchanged"
    board = cli("draft", "board", *D, "--json")[1]
    assert board["my_roster"][0]["player_id"] == "player-002"
