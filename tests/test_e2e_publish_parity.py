"""E2E: `fantasy publish --rest` against the local DB after a short draft session (dry run counts and a
mocked HTTPS transport for the real path), and Python/dashboard draft-order parity for slot 1."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from e2e_helpers import MY_PICKS_3RR_SLOT1, cli, rounds_arg

PARITY = Path(__file__).parent / "fixtures" / "draft_order_parity.json"
KEY_ENVS = ("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY", "SUPABASE_ANON_KEY",
            "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_URL")
SECRET = "sb_secret_E2E_do_not_leak_42"


@pytest.fixture()
def session(db_url, proj_path, monkeypatch):
    """A small but realistic session: run, lock, draft with picks, a recommendation, decisions, txns."""
    for k in (*KEY_ENVS, "FANTASY_RUN_ID", "FANTASY_DRAFT_ID", "FANTASY_PUBLISH_URL"):
        monkeypatch.delenv(k, raising=False)
    P = ["--projections", str(proj_path), "--draft-id", "mock-pub"]
    assert cli("draft", "init", "--draft-id", "mock-pub", "--mock", "--json")[0] == 0
    _, run = cli("run", "start", "--agent", "e2e", "--mode", "live", "--json")
    rid = run["run_id"]
    assert cli("lock", "acquire", "draft:mock-pub", "--run-id", rid, "--json")[0] == 0
    assert cli("draft", "recommend", "--json", *P)[0] == 0
    _, prep = cli("draft", "prepare-pick", "Player 001", "--overall", 1, "--run-id", rid, "--json", *P)
    assert cli("draft", "confirm-pick", prep["txn_id"], "--evidence", "log", "--json", *P[:2])[0] == 0
    for ov in range(2, 6):
        assert cli("draft", "pick", f"player-{ov + 1:03d}", "--overall", ov, "--json", *P)[0] == 0
    cli("run", "event", "note", "e2e", "--run-id", rid, "--json")
    return db_url


def _local_counts(url: str, tables) -> dict[str, int]:
    eng = sa.create_engine(url)
    try:
        with eng.connect() as c:
            return {t: c.execute(sa.text(f'select count(*) from "{t}"')).scalar_one() for t in tables}
    finally:
        eng.dispose()


def test_publish_rest_dry_run_counts_match_local_db(session):
    code, out = cli("publish", "--rest", "--dry-run", "--json")
    assert code == 0 and out["ok"] is True and out["dry_run"] is True
    rows = out["rows"]
    assert "locks" not in rows  # leases are never published
    assert _local_counts(session, rows) == rows
    for t in ("drafts", "draft_picks", "draft_recommendations", "agent_runs", "decisions", "transactions"):
        assert rows[t] > 0, t
    assert rows["draft_picks"] == 5 and rows["drafts"] == 1


def test_publish_rest_real_path_through_mocked_transport(session, monkeypatch):
    """Intercept at httpx's transport layer (no internal hook needed): every row reaches a POST upsert."""
    posted: dict[str, int] = {}
    requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        requests.append(request)
        table = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200, json=[], request=request)
        body = json.loads(request.read())
        posted[table] = posted.get(table, 0) + len(body)
        return httpx.Response(201, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    monkeypatch.setenv("SUPABASE_URL", "https://e2e.example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", SECRET)
    code, dry = cli("publish", "--rest", "--dry-run", "--json")
    assert code == 0 and requests == []
    code, out = cli("publish", "--rest", "--json")
    assert code == 0 and out["dry_run"] is False and out["errors"] == {} and SECRET not in json.dumps(out)
    assert out["rows"] == dry["rows"]
    assert posted == {t: n for t, n in out["rows"].items() if n}
    assert {r.url.host for r in requests} == {"e2e.example.supabase.co"}
    assert all(r.url.path.startswith("/rest/v1/") for r in requests)
    assert all(r.headers.get("apikey") == SECRET for r in requests if r.method == "POST")


def _parity_case(name_part: str) -> dict:
    cases = json.loads(PARITY.read_text())["cases"]
    return next(c for c in cases if name_part in c["name"])


def _slot_picks(case: dict, slot: int) -> list[int]:
    return [i + 1 for i, s in enumerate(case["slots"]) if s == slot]


def test_board_my_picks_match_dashboard_fixture_for_slot_1(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)
    case = _parity_case("3RR (league 44495")
    expected = _slot_picks(case, 1)
    assert expected == MY_PICKS_3RR_SLOT1
    # formula order (no set-order) for the league's 3RR
    assert cli("draft", "init", "--draft-id", "mock-par", "--mock", "--slot", 1, "--json")[0] == 0
    code, b = cli("draft", "board", "--draft-id", "mock-par", "--json")
    assert code == 0 and b["my_picks"] == expected
    code, o = cli("draft", "order", "--slot", 1, "--type", "snake_3rr", "--json")
    assert code == 0 and o["picks"] == expected
    # custom (commissioner-entered) order: set-order from the fixture's slots -> same my_picks
    custom = _parity_case("custom")
    n = custom["num_teams"]
    rounds = [custom["slots"][i:i + n] for i in range(0, len(custom["slots"]), n)]
    code, so = cli("draft", "set-order", "--rounds", rounds_arg(rounds), "--draft-id", "mock-par", "--json")
    assert code == 0 and so["my_picks"] == _slot_picks(custom, 1)
    code, b = cli("draft", "board", "--draft-id", "mock-par", "--json")
    assert b["my_picks"] == _slot_picks(custom, 1)
