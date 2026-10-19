"""Regressions from the 2026-10-19 review of the default draft policy (862cc74).

F1 the displayed `prob_available_next` is computed with my own current pick skipped (the simulated me used to
   take the best-value player, so he showed P = 0 at every later pick, even back-to-back); the decision path
   (scores, ranking) is unchanged. M1 prepare-pick warns when the latest recommendation is for an older board."""

from __future__ import annotations

import json

import numpy as np
import pytest
from typer.testing import CliRunner

import fantasy.draft.room_sim as rs
from fantasy.cli import app
from fantasy.draft import store
from fantasy.ops import ledger

runner = CliRunner()


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


def _fill(did: str, picks: range, offset: int = 100) -> None:
    d = store.get_draft(did)
    for k in picks:
        store.record_pick(d, k, f"player-{k + offset:03d}", f"Player {k + offset:03d}", "manual")


# ------------------------------------------------------------------ F1 (room_sim)
def test_skip_my_current_keeps_draws_and_only_drops_my_pick():
    n = 60
    rng = np.random.default_rng(3)
    value = np.sort(rng.normal(0, 2, n))[::-1].copy()
    market = np.arange(1, n + 1, dtype=float)
    sd_mult = np.ones(n)
    av = np.ones(n, dtype=bool)
    slots = [1, 1, *range(2, 13), *range(12, 0, -1)]  # my back-to-back picks 1, 2; next at 26
    kw = dict(room="mixed", n_sims=200, seed=0)
    base = rs.simulate_availability(market, sd_mult, value, av, slots, 1, [1, 2, 26], 1, **kw)
    disp = rs.simulate_availability(market, sd_mult, value, av, slots, 1, [1, 2, 26], 1, skip_my_current=True, **kw)
    top = int(np.argmax(value))
    assert base[2][top] == 0.0  # the decision table: simulated me took him at pick 1
    assert np.all(disp[2] == 1.0)  # back-to-back: nobody else picks in between
    assert np.array_equal(disp[1], base[1])
    # the same seeds: the default call is unchanged by the flag's existence (deterministic re-run)
    again = rs.simulate_availability(market, sd_mult, value, av, slots, 1, [1, 2, 26], 1, **kw)
    assert all(np.array_equal(base[p], again[p]) for p in base)
    assert disp[26].sum() - base[26].sum() == pytest.approx(1.0)  # exactly one fewer player gone by 26


# ------------------------------------------------------------------ F1 (optimizer payload)
def _strip(rec: dict) -> list[tuple]:
    return [(r["player_id"], r["score"], r["marginal_value"], r["plan"]) for r in rec["recommendations"]]


@pytest.mark.parametrize("filled", [35, 23])  # on the clock at 36 (back-to-back 36/37) and at 24 (next 36)
def test_display_availability_changes_but_decision_does_not(P, monkeypatch, filled):
    assert cli("draft", "init", "--draft-id", "mock-f1", "--mock")[0] == 0
    _fill("mock-f1", range(1, filled + 1))
    code, new = cli("draft", "recommend", "--draft-id", "mock-f1", "--no-save", "--json", *P)
    assert code == 0 and new["for_pick"] == filled + 1 and new["on_the_clock"]["is_me"], new
    orig = rs.simulate_availability
    monkeypatch.setattr(rs, "simulate_availability",
                        lambda *a, **k: orig(*a, **{**k, "skip_my_current": False}))
    code, old = cli("draft", "recommend", "--draft-id", "mock-f1", "--no-save", "--json", *P)
    assert code == 0
    assert _strip(new) == _strip(old)  # identical ranking, scores and plans
    if filled == 35:  # back-to-back: every candidate is still there at 37
        assert all(r["prob_available_next"] == 1.0 for r in new["recommendations"])
        assert min(r["prob_available_next"] for r in old["recommendations"]) == 0.0  # the old artefact


# ------------------------------------------------------------------ M1
def test_prepare_pick_warns_when_recommendation_is_for_an_older_board(P):
    D = ["--draft-id", "mock-m1b"]
    assert cli("draft", "init", *D, "--mock")[0] == 0
    rid = ledger.start_run("t", mode="live")
    assert cli("lock", "acquire", "draft:mock-m1b", "--run-id", rid)[0] == 0
    _fill("mock-m1b", range(1, 23))
    assert cli("draft", "recommend", *D, "--json", *P)[0] == 0  # saved: as_of 22, for_pick 24
    assert store.latest_recommendation("mock-m1b")["as_of_pick"] == 22
    _fill("mock-m1b", range(23, 24))
    code, out = cli("draft", "prepare-pick", "Player 050", "--overall", 24, *D, "--run-id", rid, "--json", *P)
    assert code == 0 and out["action"] == "CLICK", out
    assert any(w.startswith("latest recommendation is for an older board; re-run recommend") for w in out["warnings"])
    assert not any("was for pick" in w for w in out["warnings"])  # for_pick matches: only the new warning
    # after re-running recommend on the current board the warning is gone (checked without a second prepare:
    # pick 24 now has a submitted txn, which prepare-pick rightly refuses)
    from fantasy.draft.cli_pick import _vs_latest_recommendation

    assert cli("draft", "recommend", *D, "--json", *P)[0] == 0
    taken = {p["overall_pick"]: p for p in store.get_picks("mock-m1b")}
    *_, warnings = _vs_latest_recommendation("mock-m1b", "player-150", 24, taken)
    assert not any("older board" in w for w in warnings), warnings
