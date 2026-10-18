import threading

import pytest

from fantasy.ops import seed_tasks, tasks
from fantasy.ops.common import ConflictError, DuplicateError, InvalidStateError


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("FANTASY_DB_URL", f"sqlite:///{tmp_path / 'ops.db'}")


def test_ids_and_duplicates():
    a = tasks.add("first", area="infra", priority=1)
    b = tasks.add("second")
    assert (a["task_id"], b["task_id"]) == ("T-001", "T-002")
    with pytest.raises(DuplicateError):
        tasks.add("first")


def test_claim_conflicts():
    t = tasks.add("work")["task_id"]
    c = tasks.claim(t, "agent-a")
    assert c["status"] == "in_progress" and c["owner"] == "agent-a"
    assert tasks.claim(t, "agent-a")["owner"] == "agent-a"  # idempotent for the owner
    with pytest.raises(ConflictError) as ei:
        tasks.claim(t, "agent-b")
    assert ei.value.exit_code == 3
    with pytest.raises(ConflictError):
        tasks.done(t, "agent-b")
    # blocked tasks can be picked up by someone else
    tasks.update_task(t, status="blocked")
    assert tasks.claim(t, "agent-b")["owner"] == "agent-b"
    tasks.done(t, "agent-b")
    with pytest.raises(InvalidStateError):
        tasks.claim(t, "agent-c")


def test_concurrent_claim_single_winner():
    t = tasks.add("race")["task_id"]
    n = 10
    barrier = threading.Barrier(n)
    wins = []

    def worker(i):
        barrier.wait()
        try:
            tasks.claim(t, f"agent-{i}")
            wins.append(i)
        except ConflictError:
            pass

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert len(wins) == 1
    assert tasks.get_task(t)["owner"] == f"agent-{wins[0]}"


def test_list_filters_and_seed():
    r1 = seed_tasks.seed()
    assert len(r1["created"]) == len(seed_tasks.SEED) and not r1["existing"]
    r2 = seed_tasks.seed()
    assert not r2["created"] and len(r2["existing"]) == len(seed_tasks.SEED)
    rows = tasks.list_tasks(open_only=True)
    assert [r["priority"] for r in rows] == sorted(r["priority"] for r in rows)
    assert rows[0]["priority"] == 0
    assert all(r["area"] == "model" for r in tasks.list_tasks(area="model"))
    tasks.done("T-001")
    assert "T-001" not in {r["task_id"] for r in tasks.list_tasks(open_only=True)}
    assert [r["task_id"] for r in tasks.list_tasks("done")] == ["T-001"]
