"""E2E failure paths of the live-draft protocol, via the public CLI only (exit codes/JSON keys from
.claude/skills/live-draft/SKILL.md section 2 and .claude/skills/agent-run/SKILL.md), plus determinism of
`draft recommend` for a given board state."""

from __future__ import annotations

import pytest
from e2e_helpers import MY_PICKS_3RR_SLOT1, cli, strip_volatile

D = "mock-f"


@pytest.fixture()
def P(db_url, proj_path, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.setenv("FANTASY_DRAFT_ID", D)
    code, out = cli("draft", "init", "--draft-id", D, "--mock", "--slot", 1, "--json")
    assert code == 0 and out["draft_id"] == D
    return ["--projections", str(proj_path)]


def run(mode: str, lock: bool = True) -> str:
    code, out = cli("run", "start", "--agent", "e2e", "--mode", mode, "--json")
    assert code == 0 and out["run"]["mode"] == mode
    rid = out["run_id"]
    if lock:
        assert cli("lock", "acquire", f"draft:{D}", "--run-id", rid, "--json")[0] == 0
    return rid


def prepare(P, player, overall, rid, *extra):
    return cli("draft", "prepare-pick", player, "--overall", overall, "--run-id", rid, *extra, "--json", *P)


def board():
    code, out = cli("draft", "board", "--json")
    assert code == 0 and out["draft_id"] == D
    return out


def test_dry_run_refuses_and_logs_decision(P):
    rid = run("dry_run")
    code, out = prepare(P, "Player 003", 1, rid)
    assert code == 4 and out["action"] == "DO_NOT_CLICK" and out["run_mode"] == "dry_run"
    assert out["draft_id"] == D and out["ok"] is False
    assert board()["picks_made"] == 0
    code, show = cli("run", "show", rid, "--json")
    decs = [d for d in show["decisions"] if d["kind"] == "draft_pick"]
    assert len(decs) == 1 and decs[0]["choice"]["mode"] == "dry_run"
    code, rc = cli("txn", "reconcile", "--json")
    assert rc["txns"] == []  # nothing was marked submitted


def test_missing_lock_refuses_then_lock_allows(P):
    rid = run("live", lock=False)
    code, out = prepare(P, "Player 003", 1, rid)
    assert code == 4 and out["action"] == "DO_NOT_CLICK" and "lock" in out["message"]
    assert cli("lock", "acquire", f"draft:{D}", "--run-id", rid, "--json")[0] == 0
    code, out = prepare(P, "Player 003", 1, rid)
    assert code == 0 and out["action"] == "CLICK" and out["attempt"] == 1


def test_lock_held_by_other_run_blocks(P):
    other = run("live")
    code, out = cli("lock", "acquire", f"draft:{D}", "--run-id", run("live", lock=False), "--json")
    assert code == 3 and out["ok"] is False
    me = run("live", lock=False)
    code, out = prepare(P, "Player 003", 1, me)
    assert code == 4 and out["action"] == "DO_NOT_CLICK"
    assert prepare(P, "Player 003", 1, other)[0] == 0


def test_missing_run_id_is_usage_error(P):
    code, out = cli("draft", "prepare-pick", "Player 003", "--overall", 1, "--json", *P)
    assert code == 2 and out["ok"] is False


def test_duplicate_prepare_refused_then_confirm(P):
    rid = run("live")
    code, first = prepare(P, "Player 003", 1, rid)
    assert code == 0
    for player in ("Player 003", "Player 004"):  # same or another player: never click twice
        code, again = prepare(P, player, 1, rid)
        assert code in (5, 7) and again["action"] == "DO_NOT_CLICK"
    code, rc = cli("txn", "reconcile", "--json")
    assert [t["txn_id"] for t in rc["txns"]] == [first["txn_id"]]
    code, conf = cli("draft", "confirm-pick", first["txn_id"], "--evidence", "draft log #1", "--json", *P)
    assert code == 0 and conf["pick"]["player_id"] == "player-003"
    code, conf = cli("draft", "confirm-pick", first["txn_id"], "--evidence", "again", "--json", *P)
    assert code in (0, 7)  # idempotent or invalid-state, never a second board row
    assert board()["picks_made"] == 1
    code, out = prepare(P, "Player 005", 1, rid)  # pick already on the board
    assert code == 4 and "already recorded" in out["message"]
    assert cli("txn", "reconcile", "--json")[1]["txns"] == []


def test_confirm_requires_evidence_and_known_txn(P):
    rid = run("live")
    code, prep = prepare(P, "Player 003", 1, rid)
    code, out = cli("draft", "confirm-pick", prep["txn_id"], "--json", *P)
    assert code == 2 and out["ok"] is False
    code, out = cli("draft", "confirm-pick", "txn-does-not-exist", "--evidence", "x", "--json", *P)
    assert code == 6 and out["ok"] is False


def test_failed_pick_then_retry_plans_attempt_2(P):
    rid = run("live")
    code, out = prepare(P, "Player 003", 1, rid)
    assert code == 0 and out["attempt"] == 1
    code, f = cli("draft", "confirm-pick", out["txn_id"], "--failed", "click did not register", "--json")
    assert code == 0 and f["txn"]["status"] == "failed"
    assert board()["picks_made"] == 0
    code, out2 = prepare(P, "Player 003", 1, rid)
    assert code == 0 and out2["action"] == "CLICK" and out2["attempt"] == 2
    assert out2["txn_note"] == "retry (attempt 2)" and out2["txn_id"] != out["txn_id"]
    code, conf = cli("draft", "confirm-pick", out2["txn_id"], "--evidence", "draft log #1", "--json", *P)
    assert code == 0 and board()["my_roster"][0]["player_id"] == "player-003"


def test_pick_that_is_not_mine_refused(P):
    rid = run("live")
    code, out = prepare(P, "Player 003", 2, rid)
    assert code == 4 and out["action"] == "DO_NOT_CLICK"
    assert out["my_picks"] == MY_PICKS_3RR_SLOT1


def test_already_drafted_player_refused(P):
    rid = run("live")
    assert cli("draft", "pick", "Player 003", "--overall", 1, "--source", "browser", "--json", *P)[0] == 0
    for ov in range(2, 24):
        assert cli("draft", "pick", f"player-{ov + 10:03d}", "--overall", ov, "--json", *P)[0] == 0
    code, out = prepare(P, "Player 003", 24, rid)
    assert code == 4 and "already recorded as drafted" in out["message"]
    # and the board refuses the same player twice
    code, out = cli("draft", "pick", "Player 003", "--overall", 24, "--json", *P)
    assert code == 2 and out["ok"] is False


def test_name_ambiguity_returns_candidates(P):
    code, out = cli("draft", "pick", "Player 01", "--overall", 1, "--source", "browser", "--json", *P)
    assert code == 2 and out["ok"] is False and len(out["candidates"]) >= 2
    pid = out["candidates"][0][0]
    rid = run("live")
    code, prep = prepare(P, "Player 01", 1, rid)
    assert code == 2 and prep["candidates"]
    code, out = cli("draft", "pick", pid, "--overall", 1, "--source", "browser", "--json", *P)
    assert code == 0 and out["pick"]["player_id"] == pid
    # a player not in projections: --force records unknown:<name>
    code, out = cli("draft", "pick", "Zz Nobody Qq", "--overall", 2, "--force", "--json", *P)
    assert code == 0 and out["pick"]["player_id"].startswith("unknown:")


def test_sync_gap_warning_everywhere(P):
    rid = run("live")
    assert cli("draft", "pick", "Player 010", "--overall", 5, "--source", "browser", "--json", *P)[0] == 0
    b = board()
    assert b["gaps"] == [1, 2, 3, 4] and b["picks_made"] == 1
    code, rec = cli("draft", "recommend", "--no-save", "--json", *P)
    assert code == 0 and any("not recorded" in w for w in rec["warnings"])
    for ov, pid in [(1, "player-000"), (2, "player-001"), (3, "player-002"), (4, "player-003")]:
        assert cli("draft", "pick", pid, "--overall", ov, "--json", *P)[0] == 0
    for ov in range(6, 24):
        assert cli("draft", "pick", f"player-{ov + 20:03d}", "--overall", ov, "--json", *P)[0] == 0
    assert cli("draft", "undo", 12, "--json")[1]["removed"] == 1
    code, out = prepare(P, "Player 100", 24, rid)
    assert code == 0 and out["gaps"] == [12] and any("not recorded" in w for w in out["warnings"])
    code, rec = cli("draft", "recommend", "--no-save", "--json", *P)
    assert any("not recorded" in w for w in rec["warnings"])


def test_undo_and_rerecord(P):
    assert cli("draft", "pick", "Player 007", "--overall", 1, "--json", *P)[0] == 0
    code, out = cli("draft", "pick", "Player 007", "--overall", 1, "--json", *P)
    assert code == 0 and out["pick"]["status"] == "unchanged"  # idempotent
    code, out = cli("draft", "pick", "Player 008", "--overall", 1, "--json", *P)
    assert code == 2 and out["ok"] is False  # conflict without --force
    code, out = cli("draft", "undo", 1, "--json")
    assert code == 0 and out["removed"] == 1 and out["draft_id"] == D
    code, out = cli("draft", "undo", 1, "--json")
    assert out["removed"] == 0 and out["ok"] is False
    code, out = cli("draft", "pick", "Player 008", "--overall", 1, "--json", *P)
    assert code == 0 and out["pick"]["status"] == "recorded"
    assert board()["my_roster"][0]["player_id"] == "player-008"
    code, out = cli("draft", "pick", "Player 009", "--overall", 1, "--force", "--json", *P)
    assert code == 0 and board()["my_roster"][0]["player_id"] == "player-009"


def test_out_of_order_recording(P):
    order = [(3, "player-002"), (1, "player-000"), (6, "player-005"), (2, "player-001")]
    for ov, pid in order:
        code, out = cli("draft", "pick", pid, "--overall", ov, "--source", "browser", "--json", *P)
        assert code == 0 and out["pick"]["overall_pick"] == ov
    b = board()
    assert b["gaps"] == [4, 5] and b["picks_made"] == 4
    assert [p["overall_pick"] for p in b["recent_picks"]] == [1, 2, 3, 6]
    # recording without --overall fills the next open pick (the first gap)
    code, out = cli("draft", "pick", "player-003", "--json", *P)
    assert code == 0 and out["pick"]["overall_pick"] == 4
    assert cli("draft", "pick", "player-004", "--overall", 5, "--json", *P)[0] == 0
    b = board()
    assert b["gaps"] == [] and b["on_the_clock"]["overall"] == 7
    code, rec = cli("draft", "recommend", "--no-save", "--json", *P)
    assert rec["for_pick"] == 24 and not any("not recorded" in w for w in rec["warnings"])


def test_pick_overall_zero_rejected(P):
    code, out = cli("draft", "pick", "Player 003", "--overall", 0, "--json", *P)
    assert code == 2 and out["ok"] is False
    assert board()["picks_made"] == 0


@pytest.mark.parametrize("ov", [157, -1])
def test_pick_overall_out_of_range_is_json_usage_error(P, ov):
    code, out = cli("draft", "pick", "Player 003", "--overall", ov, "--json", *P)
    assert code == 2 and out["ok"] is False


# ------------------------------------------------------------------ determinism


def _rec(P):
    code, rec = cli("draft", "recommend", "--no-save", "--json", *P)
    assert code == 0 and rec["ok"] is True
    return strip_volatile(rec)


def test_recommend_is_deterministic_for_a_state(P):
    a, b = _rec(P), _rec(P)
    assert a == b and a["for_pick"] == 1
    for ov in range(1, 24):
        assert cli("draft", "pick", f"player-{(ov * 7) % 60:03d}", "--overall", ov, "--json", *P)[0] == 0
    c, d = _rec(P), _rec(P)
    assert c == d and c["for_pick"] == 24
    assert c["recommendations"] != a["recommendations"]
