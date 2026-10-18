"""E2E: the live-draft skill's protocol (.claude/skills/live-draft/SKILL.md), driven only through the public
CLI, for a 12-team 13-round third-round-reversal mock where I pick from slot 1.

run start (live) -> draft init --mock -> lock acquire draft:<id> -> set-order (3RR) -> per pick:
opponents recorded with `draft pick --source browser`; at my picks `recommend --json` -> `prepare-pick`
-> `confirm-pick`. Afterwards: 13-man roster, feasible starting lineup, complete board, nothing stuck in
`submitted`, decisions logged with agrees_with_model, `run end` releases the locks.

The fast default stops after my 4th pick (#37); the full 156-pick draft is `@pytest.mark.slow`.
"""

from __future__ import annotations

import json
import math

import pytest
from e2e_helpers import (
    MY_PICKS_3RR_SLOT1,
    N_ROUNDS,
    N_TEAMS,
    cli,
    lineup_feasible,
    rounds_3rr,
    rounds_arg,
    slots_by_overall,
)
from synthetic import make_projections

DRAFT = "mock-e2e"
DEVIATE_AT = 36  # take recommendations[1] here to exercise agrees_with_model=false


@pytest.fixture()
def env(db_url, proj_path, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)
    return {"P": ["--projections", str(proj_path)]}


def _adp_order() -> list[tuple[str, str]]:
    df = make_projections()  # same data as the proj_path fixture
    df = df.assign(_adp=df["adp"].map(lambda x: math.inf if x != x else x)).sort_values(["_adp", "player_id"])
    return list(zip(df["player_id"], df["name"], strict=True))


def _positions() -> dict[str, set[str]]:
    df = make_projections()
    return {pid: set(pos.split("|")) for pid, pos in zip(df["player_id"], df["positions"], strict=True)}


def _setup_live_run(monkeypatch, P):
    # export FANTASY_RUN_ID=$(fantasy run start --agent draft-manager --task "..." --mode live)
    code, rid = cli("run", "start", "--agent", "draft-manager", "--task", f"rehearsal {DRAFT}", "--mode", "live")
    assert code == 0 and rid.strip()
    rid = rid.strip()
    monkeypatch.setenv("FANTASY_RUN_ID", rid)
    code, out = cli("draft", "init", "--draft-id", DRAFT, "--mock", "--slot", 1, "--json")
    assert code == 0 and out["ok"] is True and out["draft_id"] == DRAFT
    monkeypatch.setenv("FANTASY_DRAFT_ID", DRAFT)
    code, out = cli("lock", "acquire", f"draft:{DRAFT}", "--ttl", 7200, "--purpose", "mock rehearsal", "--json")
    assert code == 0 and out["ok"] is True
    code, out = cli("draft", "set-order", "--rounds", rounds_arg(rounds_3rr()), "--json")
    assert code == 0 and out["draft_id"] == DRAFT and out["my_picks"] == MY_PICKS_3RR_SLOT1
    code, out = cli("run", "event", "observation", "setup complete",
                    "--payload", json.dumps({"my_picks": MY_PICKS_3RR_SLOT1}), "--json")
    assert code == 0
    return rid


def _drive_draft(P, last_pick: int) -> dict:
    """Run the skill's loop up to `last_pick`. Returns what happened at my picks."""
    slots = slots_by_overall(rounds_3rr())
    adp = _adp_order()
    taken: set[str] = set()
    mine: dict[int, dict] = {}
    for ov in range(1, last_pick + 1):
        slot = slots[ov - 1]
        if slot != 1:
            # opponent: best ADP available with a slot-dependent skip, recorded as read from the draft log
            avail = [(pid, name) for pid, name in adp if pid not in taken]
            pid, name = avail[min(slot % 3, len(avail) - 1)]
            code, out = cli("draft", "pick", name, "--overall", ov, "--source", "browser", "--json", *P)
            assert code == 0 and out["pick"]["player_id"] == pid and out["draft_id"] == DRAFT, out
            taken.add(pid)
            continue
        # my pick: board must have no gaps, then recommend -> prepare -> (click) -> confirm
        code, board = cli("draft", "board", "--json")
        assert code == 0 and board["gaps"] == [] and board["on_the_clock"]["is_me"] is True
        assert board["on_the_clock"]["overall"] == ov
        code, rec = cli("draft", "recommend", "--json", *P)
        assert code == 0 and rec["ok"] is True and rec["draft_id"] == DRAFT and rec["for_pick"] == ov, rec
        recs = rec["recommendations"]
        assert len(recs) >= 5 and not ({r["player_id"] for r in recs} & taken)
        for k in ("player_id", "name", "score", "prob_available_next"):
            assert k in recs[0]
        deviate = ov == DEVIATE_AT
        choice = recs[1] if deviate else recs[0]
        rationale = "rec[1]: rehearsal deviation test" if deviate else "top recommendation"
        code, prep = cli("draft", "prepare-pick", choice["name"], "--overall", ov, "--rationale", rationale,
                         "--json", *P)
        assert code == 0 and prep["action"] == "CLICK" and prep["run_mode"] == "live", prep
        assert prep["player_id"] == choice["player_id"] and prep["gaps"] == [] and prep["attempt"] == 1
        assert prep["agrees_with_model"] is (not deviate)
        assert prep["rank_in_recommendations"] == (2 if deviate else 1)
        # ... agent clicks Draft in Yahoo, re-reads the draft log ...
        code, conf = cli("draft", "confirm-pick", prep["txn_id"],
                         "--evidence", f"draft log: #{ov} {choice['name']} -> my team", "--json", *P)
        assert code == 0 and conf["pick"]["status"] == "recorded" and conf["txn"]["status"] == "verified", conf
        cli("run", "heartbeat", "--json")
        taken.add(choice["player_id"])
        mine[ov] = {"player_id": choice["player_id"], "txn_id": prep["txn_id"], "deviate": deviate}
    return mine


@pytest.mark.parametrize("last_pick", [37, pytest.param(N_TEAMS * N_ROUNDS, marks=pytest.mark.slow)],
                         ids=["first-4-of-my-picks", "full-156"])
def test_live_draft_rehearsal(env, monkeypatch, last_pick):
    P = env["P"]
    rid = _setup_live_run(monkeypatch, P)
    mine = _drive_draft(P, last_pick)
    full = last_pick == N_TEAMS * N_ROUNDS
    my_done = [p for p in MY_PICKS_3RR_SLOT1 if p <= last_pick]
    assert sorted(mine) == my_done

    code, board = cli("draft", "board", "--json")
    assert code == 0 and board["draft_id"] == DRAFT
    assert board["picks_made"] == last_pick and board["gaps"] == []
    assert [p["overall_pick"] for p in board["my_roster"]] == my_done
    assert [p["player_id"] for p in board["my_roster"]] == [mine[p]["player_id"] for p in my_done]
    assert board["my_upcoming"] == [p for p in MY_PICKS_3RR_SLOT1 if p > last_pick]
    if full:
        assert len(board["my_roster"]) == 13 and board["on_the_clock"] is None
        if board["status"] in ("complete", "completed"):  # see test_draft_status_complete_after_last_pick
            assert board["status"] == "complete"
        pos = _positions()
        code, settings = cli("settings", "show", "--json")
        assert code == 0
        assert lineup_feasible(settings["starting_slots"], [pos[p["player_id"]] for p in board["my_roster"]])
        # my picks are done: recommend says so instead of recommending
        code, rec = cli("draft", "recommend", "--no-save", "--json", *P)
        assert code == 0 and rec["for_pick"] is None and rec["recommendations"] == []

    # nothing stuck in submitted
    code, rc = cli("txn", "reconcile", "--json")
    assert code == 0 and rc["txns"] == []
    code, st = cli("status", "--json")
    assert code == 0 and st["status"]["txns_needing_reconciliation"] == []
    code, txns = cli("txn", "list", "--kind", "draft_pick", "--json")
    assert sorted(t["status"] for t in txns["txns"]) == ["verified"] * len(my_done)

    # decisions: one per pick of mine, agrees_with_model logged, the deviation flagged with its rationale
    code, show = cli("run", "show", rid, "--json")
    assert code == 0 and show["run"]["mode"] == "live"
    decs = [d for d in show["decisions"] if d["kind"] == "draft_pick"]
    assert len(decs) == len(my_done)
    assert all(d["agrees_with_model"] is not None for d in decs)
    disagree = [d for d in decs if d["agrees_with_model"] is False]
    assert len(disagree) == 1 and disagree[0]["choice"]["overall"] == DEVIATE_AT
    assert "deviation" in disagree[0]["rationale"]
    assert {d["choice"]["mode"] for d in decs} == {"live"}

    # run end releases every lock it holds
    code, end = cli("run", "end", "--summary", "rehearsal done", "--json")
    assert code == 0 and end["run"]["status"] == "succeeded"
    assert f"draft:{DRAFT}" in end["released_locks"]
    code, lk = cli("lock", "show", "--json")
    assert code == 0 and lk["locks"] == []


@pytest.mark.slow
def test_draft_status_complete_after_last_pick(env):
    P = env["P"]
    assert cli("draft", "init", "--draft-id", "mock-done", "--mock", "--json")[0] == 0
    adp = _adp_order()
    for ov in range(1, N_TEAMS * N_ROUNDS + 1):
        code, out = cli("draft", "pick", adp[ov - 1][0], "--overall", ov, "--draft-id", "mock-done", "--json", *P)
        assert code == 0, out
    code, board = cli("draft", "board", "--draft-id", "mock-done", "--json")
    assert board["picks_made"] == 156 and board["on_the_clock"] is None
    assert board["status"] == "complete"
