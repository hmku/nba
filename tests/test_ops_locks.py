import threading
from datetime import timedelta

import pytest
from sqlalchemy import update

from fantasy.db.schema import locks as locks_t
from fantasy.ops import locks
from fantasy.ops.common import LockHeldError, engine, now


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("FANTASY_DB_URL", f"sqlite:///{tmp_path / 'ops.db'}")


def _expire(resource):
    with engine().begin() as conn:
        conn.execute(
            update(locks_t)
            .where(locks_t.c.resource == resource)
            .values(expires_at=now() - timedelta(seconds=1))
        )


def test_exclusive_and_reentrant():
    a = locks.acquire(locks.YAHOO_ROSTER, "run-a", 60, "lineup")
    assert a["holder_run_id"] == "run-a" and not a["taken_over"]
    with pytest.raises(LockHeldError) as ei:
        locks.acquire(locks.YAHOO_ROSTER, "run-b", 60)
    assert ei.value.exit_code == 3
    assert ei.value.details["holder"]["holder_run_id"] == "run-a"
    again = locks.acquire(locks.YAHOO_ROSTER, "run-a", 120)  # re-entrant renew
    assert again["holder_run_id"] == "run-a"
    assert locks.holds(locks.YAHOO_ROSTER, "run-a")
    assert not locks.holds(locks.YAHOO_ROSTER, "run-b")


def test_expiry_takeover():
    locks.acquire(locks.YAHOO_BROWSER, "run-a", 60)
    _expire(locks.YAHOO_BROWSER)
    assert not locks.holds(locks.YAHOO_BROWSER, "run-a")
    b = locks.acquire(locks.YAHOO_BROWSER, "run-b", 60)
    assert b["holder_run_id"] == "run-b" and b["taken_over"]
    # the old holder can no longer renew or release it
    with pytest.raises(LockHeldError):
        locks.renew(locks.YAHOO_BROWSER, "run-a")
    with pytest.raises(LockHeldError):
        locks.release(locks.YAHOO_BROWSER, "run-a")


def test_release_holder_only():
    r = locks.draft_resource("d1")
    locks.acquire(r, "run-a")
    with pytest.raises(LockHeldError):
        locks.release(r, "run-b")
    assert locks.release(r, "run-a") is True
    assert locks.get_lock(r) is None
    assert locks.release(r, "run-a") is False  # not locked
    locks.acquire(r, "run-b")  # free again


def test_context_manager_releases():
    with locks.lock(locks.YAHOO_ROSTER, "run-a", 30) as info:
        assert info["holder_run_id"] == "run-a"
        with pytest.raises(LockHeldError):
            locks.acquire(locks.YAHOO_ROSTER, "run-b")
    assert locks.get_lock(locks.YAHOO_ROSTER) is None
    with pytest.raises(RuntimeError), locks.lock(locks.YAHOO_ROSTER, "run-a"):
        raise RuntimeError("boom")
    assert locks.get_lock(locks.YAHOO_ROSTER) is None


def test_list_locks_hides_expired():
    locks.acquire("x", "run-a")
    locks.acquire("y", "run-a")
    _expire("y")
    assert [lk["resource"] for lk in locks.list_locks()] == ["x"]
    assert len(locks.list_locks(include_expired=True)) == 2


@pytest.mark.parametrize("expired_first", [False, True])
def test_concurrent_acquire_single_winner(expired_first):
    if expired_first:
        locks.acquire(locks.YAHOO_ROSTER, "run-old", 60)
        _expire(locks.YAHOO_ROSTER)
    n = 12
    barrier = threading.Barrier(n)
    winners, losers, errors = [], [], []

    def worker(i):
        barrier.wait()
        try:
            locks.acquire(locks.YAHOO_ROSTER, f"run-{i}", 60)
            winners.append(i)
        except LockHeldError:
            losers.append(i)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(winners) == 1, (winners, losers)
    assert locks.get_lock(locks.YAHOO_ROSTER)["holder_run_id"] == f"run-{winners[0]}"
