"""`fantasy draft watch` must survive transient DB errors on draft night (tech-debt F13)."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.draft import store

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)
    monkeypatch.setattr("fantasy.draft.cli.time.sleep", lambda _s: None)


def _watch(proj_path, n: int):
    return runner.invoke(app, ["draft", "watch", "--draft-id", "mock-w", "--projections", str(proj_path),
                               "--interval", "0", "--max-iterations", str(n)])


def _init():
    assert runner.invoke(app, ["draft", "init", "--draft-id", "mock-w", "--mock"]).exit_code == 0


def test_save_failure_is_logged_and_retried_without_recomputing(proj_path, monkeypatch):
    _init()
    real_save = store.save_recommendation
    calls = {"save": 0}

    def flaky_save(rec, engine=None):
        calls["save"] += 1
        if calls["save"] == 1:
            raise OperationalError("INSERT", {}, Exception("server closed the connection unexpectedly"))
        return real_save(rec, engine)

    monkeypatch.setattr(store, "save_recommendation", flaky_save)
    res = _watch(proj_path, 3)
    assert res.exit_code == 0, res.output
    assert "DB error saving the recommendation" in res.stderr and "still watching" in res.stderr
    assert "DB recovered after 1 failed poll(s)" in res.stderr
    assert res.stdout.count("picks → for #1") == 1  # computed once, saved on the retry
    assert calls["save"] == 2
    rec = store.latest_recommendation("mock-w")
    assert rec is not None and rec["for_pick"] == 1


def test_read_failures_back_off_and_keep_polling(proj_path, monkeypatch):
    _init()
    real_get = store.get_picks
    calls = {"n": 0}
    waits: list[float] = []

    def flaky_get(draft_id, engine=None):
        calls["n"] += 1
        if 1 < calls["n"] <= 4:  # first poll ok, then three failures
            raise OperationalError("SELECT", {}, Exception("timeout"))
        return real_get(draft_id, engine)

    monkeypatch.setattr(store, "get_picks", flaky_get)
    monkeypatch.setattr("fantasy.draft.cli.time.sleep", waits.append)
    res = runner.invoke(app, ["draft", "watch", "--draft-id", "mock-w", "--projections", str(proj_path),
                              "--interval", "2", "--max-iterations", "6"])
    assert res.exit_code == 0, res.output
    assert res.stderr.count("DB error reading picks") == 3
    assert waits[:5] == [2, 2, 4, 8, 2]  # backoff doubles from the interval, resets after recovery
    assert "stopping after 6 polls" in res.stdout


def test_backoff_is_capped(proj_path, monkeypatch):
    _init()
    waits: list[float] = []

    def always_fail(draft_id, engine=None):
        raise OperationalError("SELECT", {}, Exception("down"))

    monkeypatch.setattr(store, "get_picks", always_fail)
    monkeypatch.setattr("fantasy.draft.cli.time.sleep", waits.append)
    res = runner.invoke(app, ["draft", "watch", "--draft-id", "mock-w", "--projections", str(proj_path),
                              "--interval", "3", "--max-iterations", "8"])
    assert res.exit_code == 0, res.output
    assert max(waits) == 30.0 and waits[0] == 3


def test_optimizer_error_does_not_kill_the_loop(proj_path, monkeypatch):
    _init()
    from fantasy.draft.optimizer import DraftOptimizer

    def boom(self, *a, **k):
        raise ValueError("bad state")

    monkeypatch.setattr(DraftOptimizer, "recommend", boom)
    res = _watch(proj_path, 3)
    assert res.exit_code == 0, res.output
    assert res.stderr.count("recommendation failed") == 1  # skipped until the board changes
