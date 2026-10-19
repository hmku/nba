"""Regressions from the 2026-10-19 draft-path re-review (m1, m4, m5; M1/m3 are web tests in
web/tests/agent.test.mjs).

m1 a `behind` refusal names my own unconfirmed pick and its confirm-pick txn; m4 a name without letters or
digits is never recorded as `unknown:`; m5 `draft watch` survives a failed context rebuild after set-order."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.draft import store
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


def _fill(did: str, picks: range, offset: int = 100) -> None:
    d = store.get_draft(did)
    for k in picks:
        store.record_pick(d, k, f"player-{k + offset:03d}", f"Player {k + offset:03d}", "manual")


# ------------------------------------------------------------------ m1
def test_prepare_pick_behind_names_my_unconfirmed_pick(P):
    D = _init("mock-m1")
    rid = ledger.start_run("t", mode="live")
    assert cli("lock", "acquire", "draft:mock-m1", "--run-id", rid)[0] == 0
    _fill("mock-m1", range(1, 21))
    code, out = cli("draft", "prepare-pick", "Player 050", "--overall", 24, *D, "--run-id", rid, "--json", *P)
    assert code == 4 and out["behind"] == [21, 22, 23] and out["behind_mine"] == [], out  # not mine: no hint
    assert "confirm-pick" not in out["message"]
    _fill("mock-m1", range(21, 24))
    code, out = cli("draft", "prepare-pick", "Player 050", "--overall", 24, *D, "--run-id", rid, "--json", *P)
    assert code == 0 and out["action"] == "CLICK", out
    txn_id = out["txn_id"]
    # clicked on Yahoo but never confirmed; the draft moves on to my #36 (slot 1 under 3RR: 1, 24, 36)
    _fill("mock-m1", range(25, 36))
    code, out = cli("draft", "prepare-pick", "Player 051", "--overall", 36, *D, "--run-id", rid, "--json", *P)
    assert code == 4 and out["action"] == "DO_NOT_CLICK" and out["behind"] == [24], out
    assert out["behind_mine"] == [{"overall": 24, "txn_id": txn_id}]
    assert out["message"].endswith(f"(your #24: confirm-pick {txn_id})"), out["message"]
    # following the hint unblocks the pick
    assert cli("draft", "confirm-pick", txn_id, "--evidence", "draft log #24", "--run-id", rid, *P)[0] == 0
    code, out = cli("draft", "prepare-pick", "Player 051", "--overall", 36, *D, "--run-id", rid, "--json", *P)
    assert code == 0 and out["action"] == "CLICK" and out["behind"] == [], out


# ------------------------------------------------------------------ m4
@pytest.mark.parametrize("name", ["!!!", " - ", "’."])
def test_allow_unknown_rejects_name_without_letters_or_digits(P, name):
    D = _init("mock-m4")
    code, out = cli("draft", "pick", name, "--overall", 1, "--allow-unknown", *D, "--json", *P)
    assert code == 2 and out["code"] == "invalid_name", out
    assert store.get_picks("mock-m4") == []  # nothing like `unknown:` was recorded


# ------------------------------------------------------------------ m5
def test_watch_survives_failed_context_rebuild(proj_path, monkeypatch):
    import fantasy.draft.cli_recommend as cr

    D = _init("mock-m5")
    _fill("mock-m5", range(1, 25))
    real_context = cr._context
    state = {"broken": False, "polls": 0}

    def context(*a, **k):
        if state["broken"]:
            raise ValueError("bad custom order")
        return real_context(*a, **k)

    def between_polls(_s):
        state["polls"] += 1
        if state["polls"] == 1:
            assert store.latest_recommendation("mock-m5")["for_pick"] == 36  # 3RR
            state["broken"] = True
            assert cli("draft", "set-order", "--rounds", SNAKE, *D, "--json")[0] == 0
        elif state["polls"] == 4:
            state["broken"] = False  # e.g. the projections file is back; the rebuild is retried every poll

    monkeypatch.setattr(cr, "_context", context)
    monkeypatch.setattr("fantasy.draft.cli_recommend.time.sleep", between_polls)
    res = runner.invoke(app, ["draft", "watch", *D, "--projections", str(proj_path), "--interval", "0",
                              "--max-iterations", "6"])
    assert res.exit_code == 0, res.output
    assert res.stderr.count("rebuilding the context failed") == 1  # warned once, not every poll
    assert "still watching" in res.stderr
    assert "draft order changed" in res.stdout and "stopping after 6 polls" in res.stdout
    rec = store.latest_recommendation("mock-m5")
    assert rec["for_pick"] == 25, rec  # the new (snake) order once the rebuild succeeded
