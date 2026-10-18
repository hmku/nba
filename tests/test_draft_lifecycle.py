"""Draft lifecycle (tech-debt F5): pending -> live on the first pick, complete when the board is full,
back to live on an undo; idempotent; visible in `draft board`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.draft import store
from fantasy.league.settings import load_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)


FIXTURE = Path(__file__).parent / "fixtures" / "draft_status_parity.json"


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text())["cases"])
def test_status_rule(case):
    """Same cases are checked against web/src/lib/draftStatus.ts in web/tests/draftStatus.test.mjs."""
    assert store.status_for(case["current"], case["n_picks"], case["n_total"]) == case["expected"]


def _tiny_draft() -> dict:
    """2 teams x 2 rounds (num_rounds is derived from the roster in settings, so set it on the row)."""
    from sqlalchemy import update

    from fantasy.db.engine import get_engine
    from fantasy.db.schema import drafts

    s = load_settings().model_copy(update={"num_teams": 2, "my_slot": 1, "draft_order_type": "snake"})
    store.create_draft("mock-life", s, is_mock=True)
    with get_engine().begin() as conn:
        conn.execute(update(drafts).where(drafts.c.draft_id == "mock-life").values(num_rounds=2))
    d = store.get_draft("mock-life")
    assert d is not None and d["num_teams"] * d["num_rounds"] == 4
    return d


def _status() -> str:
    d = store.get_draft("mock-life")
    assert d is not None
    return d["status"]


def test_full_board_marks_complete_and_undo_reopens(proj_path):
    d = _tiny_draft()
    assert d["status"] == "pending"
    ids = ["p1", "p2", "p3", "p4"]
    for k, pid in enumerate(ids[:3], 1):
        store.record_pick(d, k, pid, pid.upper(), "manual")
        assert _status() == "live"
    updated = store.get_draft("mock-life")["updated_at"]
    store.record_pick(d, 3, "p3", "P3", "manual")  # idempotent re-record: no status write
    assert store.get_draft("mock-life")["updated_at"] == updated
    store.record_pick(d, 4, "p4", "P4", "manual")
    assert _status() == "complete"
    assert store.sync_draft_status("mock-life") == "complete"  # idempotent
    board = json.loads(runner.invoke(app, ["draft", "board", "--draft-id", "mock-life", "--json"]).stdout)
    assert board["status"] == "complete" and board["on_the_clock"] is None and board["picks_made"] == 4
    assert store.undo_pick("mock-life", 4) == 1
    assert _status() == "live"
    assert store.undo_pick("mock-life", 4) == 0 and _status() == "live"
    assert store.sync_draft_status("nope") is None


def test_board_heals_a_full_draft_left_live(proj_path):
    """Drafts filled before the fix stayed 'live'; `draft board` repairs the row."""
    from sqlalchemy import update

    from fantasy.db.engine import get_engine
    from fantasy.db.schema import drafts

    d = _tiny_draft()
    for k, pid in enumerate(["p1", "p2", "p3", "p4"], 1):
        store.record_pick(d, k, pid, pid.upper(), "manual")
    with get_engine().begin() as conn:
        conn.execute(update(drafts).where(drafts.c.draft_id == "mock-life").values(status="live"))
    res = runner.invoke(app, ["draft", "board", "--draft-id", "mock-life", "--json"])
    assert res.exit_code == 0 and json.loads(res.stdout)["status"] == "complete"
    assert _status() == "complete"


def test_cli_pick_fills_board_then_refuses(proj_path):
    assert runner.invoke(app, ["draft", "init", "--draft-id", "mock-cli", "--mock"]).exit_code == 0
    d = store.get_draft("mock-cli")
    total = d["num_teams"] * d["num_rounds"]
    import pandas as pd

    pids = list(pd.read_csv(proj_path)["player_id"][:total])
    for k, pid in enumerate(pids, 1):
        store.record_pick(d, k, pid, pid, "manual")
    assert store.get_draft("mock-cli")["status"] == "complete"
    res = runner.invoke(app, ["draft", "pick", "anyone", "--draft-id", "mock-cli", "--projections", str(proj_path),
                              "--force", "--json"])
    assert res.exit_code == 1 and json.loads(res.stdout)["ok"] is False
