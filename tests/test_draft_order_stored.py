"""Python is the single source of the pick order: `draft init|set-order` store `config.order_slots`
(team slot of every overall pick) and the dashboard reads it (web/tests/draftOrderStored.test.mjs)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import update
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.db.engine import get_engine
from fantasy.db.schema import drafts
from fantasy.draft import store
from fantasy.draft.order import order_for_draft

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)


def _slots(d: dict) -> list[int]:
    return [p.slot for p in order_for_draft(d)]


def test_init_stores_order_slots():
    res = runner.invoke(app, ["draft", "init", "--draft-id", "mock-o", "--mock", "--json"])
    assert res.exit_code == 0
    d = store.get_draft("mock-o")
    assert d["config"]["order_slots"] == _slots(d) and len(d["config"]["order_slots"]) == d["num_teams"] * d["num_rounds"]
    assert json.loads(res.stdout)["draft"]["config"]["order_slots"] == _slots(d)


def test_set_order_rewrites_order_slots():
    assert runner.invoke(app, ["draft", "init", "--draft-id", "mock-o", "--mock"]).exit_code == 0
    d = store.get_draft("mock-o")
    n, r = d["num_teams"], d["num_rounds"]
    custom = [list(range(1, n + 1)) if i % 2 == 0 else list(range(n, 0, -1)) for i in range(r)]  # plain snake
    custom[2] = custom[1]  # make round 3 differ from the 3RR formula's neighbours: 3RR-like
    res = runner.invoke(app, ["draft", "set-order", "--rounds", ";".join(",".join(map(str, x)) for x in custom),
                              "--draft-id", "mock-o", "--json"])
    assert res.exit_code == 0, res.stdout
    d = store.get_draft("mock-o")
    assert d["config"]["order_slots"] == [s for rnd in custom for s in rnd] == _slots(d)


def test_init_backfills_rows_created_before_order_slots():
    runner.invoke(app, ["draft", "init", "--draft-id", "mock-o", "--mock"])
    with get_engine().begin() as conn:
        conn.execute(update(drafts).where(drafts.c.draft_id == "mock-o").values(config={}))
    assert "order_slots" not in store.get_draft("mock-o")["config"]
    assert runner.invoke(app, ["draft", "init", "--draft-id", "mock-o", "--mock"]).exit_code == 0
    d = store.get_draft("mock-o")
    assert d["config"]["order_slots"] == _slots(d)
