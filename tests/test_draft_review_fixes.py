"""Regressions from the 2026-10-19 independent draft-path review (A1-A4, B1, B5).

A1 `draft watch` reloads the draft order; A2 `set-order` refuses to silently re-own recorded picks;
A3 `prepare-pick` refuses while the board is behind the clock; A4 unknown names never overwrite;
B1 the resolver applies ids.ALIASES and unknown ids match the web's; B5 cosmetics."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.data.ids import canonical_id
from fantasy.draft import store
from fantasy.draft.names import resolve_player
from fantasy.ops import ledger

runner = CliRunner()
SNAKE = ";".join(",".join(map(str, range(1, 13) if r % 2 == 0 else range(12, 0, -1))) for r in range(13))


@pytest.fixture(autouse=True)
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)


@pytest.fixture()
def P(proj_path):
    return ["--projections", str(proj_path)]


def cli(*args):
    res = runner.invoke(app, [str(a) for a in args])
    out = res.stdout
    if "--json" in args:
        out = json.loads(out)
    return res.exit_code, out


def _init(did: str) -> list[str]:
    assert cli("draft", "init", "--draft-id", did, "--mock")[0] == 0
    return ["--draft-id", did]


def _fill(did: str, upto: int, offset: int = 100) -> None:
    d = store.get_draft(did)
    for k in range(1, upto + 1):
        store.record_pick(d, k, f"player-{k + offset:03d}", f"Player {k + offset:03d}", "manual")


# ------------------------------------------------------------------ A1
def test_watch_reloads_order_after_set_order(proj_path, monkeypatch):
    D = _init("mock-w1")
    _fill("mock-w1", 24)  # picks 1..24 have the same slots under 3RR and plain snake
    polls = {"n": 0}

    def between_polls(_s):
        polls["n"] += 1
        if polls["n"] == 1:
            assert store.latest_recommendation("mock-w1")["for_pick"] == 36  # 3RR: slot 1 picks 1, 24, 36
            code, out = cli("draft", "set-order", "--rounds", SNAKE, *D, "--json")
            assert code == 0 and out["my_picks"][:4] == [1, 24, 25, 48], out
        elif polls["n"] == 2:
            assert store.latest_recommendation("mock-w1")["for_pick"] == 25  # order change alone recomputes
            store.record_pick(store.get_draft("mock-w1"), 25, "player-001", "Player 001", "manual")

    monkeypatch.setattr("fantasy.draft.cli_recommend.time.sleep", between_polls)
    res = runner.invoke(app, ["draft", "watch", *D, "--projections", str(proj_path), "--interval", "0",
                              "--max-iterations", "3"])
    assert res.exit_code == 0, res.output
    rec = store.latest_recommendation("mock-w1")
    assert rec["for_pick"] == 48 and rec["as_of_pick"] == 25, rec  # the NEW order's next pick, not 36
    assert "draft order changed" in res.stdout


# ------------------------------------------------------------------ A2
def test_set_order_refuses_to_reassign_recorded_picks(P):
    D = _init("mock-o2")
    _fill("mock-o2", 26)  # round 3 begun: 25, 26 are slots 12, 11 under 3RR, 1, 2 under snake
    before = {p["overall_pick"]: p["team_slot"] for p in store.get_picks("mock-o2")}
    code, out = cli("draft", "set-order", "--rounds", SNAKE, *D, "--json")
    assert code == 2 and out["code"] == "order_conflicts_with_recorded_picks", out
    assert out["conflicts"] == [{"overall": 25, "old_slot": 12, "new_slot": 1, "player_id": "player-125",
                                 "player_name": "Player 125"},
                                {"overall": 26, "old_slot": 11, "new_slot": 2, "player_id": "player-126",
                                 "player_name": "Player 126"}]
    d = store.get_draft("mock-o2")
    assert d["order_type"] == "snake_3rr" and "custom_rounds" not in d["config"]  # nothing written
    assert {p["overall_pick"]: p["team_slot"] for p in store.get_picks("mock-o2")} == before
    # --force: the commissioner really re-assigned made picks -> rewrite team_slot and report it
    code, out = cli("draft", "set-order", "--rounds", SNAKE, "--force", *D, "--json")
    assert code == 0 and [c["overall"] for c in out["reassigned_picks"]] == [25, 26], out
    after = {p["overall_pick"]: p["team_slot"] for p in store.get_picks("mock-o2")}
    assert after[25] == 1 and after[26] == 2 and after[24] == before[24]
    board = cli("draft", "board", *D, "--json")[1]
    assert [p["overall_pick"] for p in board["my_roster"]] == [1, 24, 25]


def test_set_order_before_picks_and_no_conflict_unchanged(P):
    D = _init("mock-o3")
    code, out = cli("draft", "set-order", "--rounds", SNAKE, *D, "--json")
    assert code == 0 and out["my_picks"][:3] == [1, 24, 25] and out["reassigned_picks"] == []
    _fill("mock-o3", 10)  # round 1 is identical under 3RR / snake -> no conflict
    THREE_RR = ";".join(",".join(map(str, range(1, 13) if r in (0,) or (r >= 3 and r % 2 == 1)
                                      else range(12, 0, -1))) for r in range(13))
    code, out = cli("draft", "set-order", "--rounds", THREE_RR, *D, "--json")
    assert code == 0 and out["matches_3rr_formula"] is True and out["reassigned_picks"] == []


# ------------------------------------------------------------------ A3
def _live_run(did):
    rid = ledger.start_run("t", mode="live")
    assert cli("lock", "acquire", f"draft:{did}", "--run-id", rid)[0] == 0
    return rid


def test_prepare_pick_refuses_when_board_behind_the_clock(P):
    D = _init("mock-b3")
    rid = _live_run("mock-b3")
    _fill("mock-b3", 20)  # contiguous: gaps == [] but picks 21..23 (before my #24) are not recorded
    board = cli("draft", "board", *D, "--json")[1]
    assert board["gaps"] == [] and board["behind"] == [21, 22, 23]
    code, rec = cli("draft", "recommend", *D, "--no-save", "--search", "none", "--json", *P)
    assert code == 0 and rec["behind"] == [21, 22, 23] and any("behind" in w for w in rec["warnings"])
    code, out = cli("draft", "prepare-pick", "Player 050", "--overall", 24, *D, "--run-id", rid, "--json", *P)
    assert code == 4 and out["action"] == "DO_NOT_CLICK", out
    assert out["behind"] == [21, 22, 23] and out["message"].startswith("board not synced: picks")
    assert "not recorded" in out["message"] and out["gaps"] == []
    _fill("mock-b3", 23)
    code, out = cli("draft", "prepare-pick", "Player 050", "--overall", 24, *D, "--run-id", rid, "--json", *P)
    assert code == 0 and out["action"] == "CLICK" and out["behind"] == [] and out["gaps"] == []
    board = cli("draft", "board", *D, "--json")[1]
    assert board["behind"] == [] and board["gaps"] == []


# ------------------------------------------------------------------ A4
def test_unknown_name_never_overwrites(P):
    D = _init("mock-u4")
    assert cli("draft", "pick", "Player 000", "--overall", 1, *D, "--json", *P)[0] == 0
    for flag in ("--force", "--allow-unknown"):  # wrong --overall that is already taken -> conflict
        code, out = cli("draft", "pick", "Zz Nobody Qq", "--overall", 1, flag, *D, "--json", *P)
        assert code == 2 and out["code"] == "conflict", (flag, out)
    assert store.get_picks("mock-u4")[0]["player_id"] == "player-000"
    code, out = cli("draft", "pick", "Zz Nobody Qq", "--overall", 2, "--allow-unknown", *D, "--json", *P)
    assert code == 0 and out["pick"]["player_id"] == "unknown:zz-nobody-qq" and out["overwrote"] is None
    # the same unknown player again at another pick is a duplicate, also with --force
    code, out = cli("draft", "pick", "Zz Nobody Qq", "--overall", 3, "--force", *D, "--json", *P)
    assert code == 2 and out["code"] == "conflict" and "already drafted" in out["error"]
    # --force on a known player no longer overwrites; --overwrite does, and reports it
    code, out = cli("draft", "pick", "Player 009", "--overall", 1, "--force", *D, "--json", *P)
    assert code == 2 and out["code"] == "conflict" and "--overwrite" in out["error"]
    code, out = cli("draft", "pick", "Zz Nobody Qq", "--overall", 1, "--force", "--overwrite", *D, "--json", *P)
    assert code == 2 and "already drafted at pick 2" in out["error"]  # overwrite never skips the duplicate check
    code, out = cli("draft", "pick", "Player 009", "--overall", 1, "--overwrite", *D, "--json", *P)
    assert code == 0 and out["overwrote"]["player_id"] == "player-000" and out["pick"]["player_id"] == "player-009"
    code, out = cli("draft", "pick", "Yy Other", "--overall", 1, "--allow-unknown", "--overwrite", *D, "--json", *P)
    assert code == 0 and out["overwrote"]["player_id"] == "player-009"


def test_unknown_without_flag_still_unresolved(P):
    D = _init("mock-u5")
    code, out = cli("draft", "pick", "Zz Nobody Qq", "--overall", 1, *D, "--json", *P)
    assert code == 2 and out["code"] == "unresolved_player"


# ------------------------------------------------------------------ B1
def test_resolver_applies_aliases():
    df = pd.DataFrame({"name": ["Cameron Johnson", "AJ Johnson", "Moritz Wagner", "Franz Wagner"]},
                      index=["cameron-johnson", "aj-johnson", "moritz-wagner", "franz-wagner"])
    assert resolve_player("Cam Johnson", df) == "cameron-johnson"
    assert resolve_player("Moe Wagner", df) == "moritz-wagner"


def test_unknown_id_is_canonical():
    from fantasy.draft.names import unknown_player_id

    assert unknown_player_id("  D'Angelo  Nobody Jr. ") == "unknown:dangelo-nobody-jr"
    assert unknown_player_id("Moe Wagner") == "unknown:" + canonical_id("Moe Wagner") == "unknown:moritz-wagner"


# ------------------------------------------------------------------ B5
def test_undo_missing_pick_is_not_found_and_single_gaps_warning(P):
    D = _init("mock-c5")
    code, out = cli("draft", "undo", 7, *D, "--json")
    assert code == 6 and out["code"] == "not_found" and out["removed"] == 0 and out["ok"] is False
    assert cli("draft", "pick", "Player 010", "--overall", 5, *D, "--json", *P)[0] == 0
    code, rec = cli("draft", "recommend", *D, "--no-save", "--search", "none", "--json", *P)
    assert sum("not recorded" in w for w in rec["warnings"]) == 1, rec["warnings"]
