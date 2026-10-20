"""Small pure helpers in league/ops/cli_common that had no direct test."""

import json

import pytest
import typer

from fantasy.cli_common import CODE_FOR_EXIT, check_choice, failure, parse_punt
from fantasy.league.settings import CATEGORIES
from fantasy.ops.common import EXIT_USAGE


def test_category_typed_accessors():
    assert CATEGORIES["pts"].stat_col == "pts"
    assert (CATEGORIES["fg_pct"].num_col, CATEGORIES["fg_pct"].den_col) == ("fgm", "fga")
    with pytest.raises(ValueError, match="no 'stat'"):
        _ = CATEGORIES["ft_pct"].stat_col
    with pytest.raises(ValueError, match="no 'den'"):
        _ = CATEGORIES["tov"].den_col


def test_failure_payload_contract():
    f = failure("bad thing", "usage", valid=("a", "b"))
    assert f == {"ok": False, "error": "bad thing", "code": "usage", "message": "bad thing", "valid": ["a", "b"]}
    assert failure("x", "not_found", error="not_found")["error"] == "not_found"  # ops-style `error` == code
    assert failure("nan", "error", v=float("nan"))["v"] is None
    assert CODE_FOR_EXIT[EXIT_USAGE] == "usage"


def test_check_choice_and_parse_punt_exit_2_with_json(capsys):
    check_choice(None, ["a"], "mode", True)  # not given: fine
    check_choice("a", ["a"], "mode", True)
    with pytest.raises(typer.Exit) as e:
        check_choice("z", ["a", "b"], "mode", True)
    assert e.value.exit_code == EXIT_USAGE
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "usage" and out["valid"] == ["a", "b"] and "invalid mode 'z'" in out["message"]

    assert parse_punt(" ft_pct, tov ", list(CATEGORIES), True) == ["ft_pct", "tov"]
    assert parse_punt(None, list(CATEGORIES), True) == []
    with pytest.raises(typer.Exit):
        parse_punt("ft_pct,bogus", list(CATEGORIES), False)
    assert "invalid punt category bogus" in capsys.readouterr().err


def test_lineup_slots_as_of_sources(tmp_path):
    from datetime import date

    from fantasy.lineup.cli import slots_as_of

    entry = {"players": ["a"], "lineup": {"a": "PG"}}
    league = {"captured_at": "2026-10-20T03:25:00Z", "source": "x", "synced_at": "2026-10-21T12:00:00Z"}
    assert slots_as_of(league, entry, None) == (date(2026, 10, 19), "league file captured_at")
    league = {"source": "sync (captured 2026-10-20 09:10 ET)", "synced_at": "2026-10-21T12:00:00Z"}
    assert slots_as_of(league, entry, None) == (date(2026, 10, 20), "league file source (captured ...)")
    assert slots_as_of({"synced_at": "2026-10-21T12:00:00Z"}, entry, None) == (date(2026, 10, 21),
                                                                               "league file synced_at")
    # no lineup in the league entry -> the capture file decides
    cap = tmp_path / "cap.json"
    cap.write_text(json.dumps({"captured_at": "2026-10-22T15:00:00Z", "teams": {}}))
    assert slots_as_of(league, {"players": ["a"]}, cap) == (date(2026, 10, 22), "cap.json captured_at")
    cap.write_text(json.dumps({"source": "read 2026-10-23 08:00 ET", "teams": {}}))
    assert slots_as_of(None, None, cap) == (date(2026, 10, 23), "cap.json source")
    assert slots_as_of(None, None, tmp_path / "missing.json") == (None, "none")


def test_lineup_current_slots_overlay_warnings(tmp_path):
    from fantasy.lineup.cli import _apply_current_slots
    from fantasy.lineup.players import RosterPlayer

    me = [RosterPlayer("a", "A", ("PG",), "BOS", None, slot="BN"), RosterPlayer("b", "B", ("C",), "BOS", None, slot="C")]
    cs = tmp_path / "cs.json"
    cs.write_text(json.dumps({"slots": {"a": "PG", "zz": "SG"}}))
    out, warnings = _apply_current_slots(me, cs)
    assert [p.slot for p in out] == ["PG", "C"] and me[0].slot == "BN"  # input untouched
    assert warnings == ["--current-slots: ids not on my roster ignored: ['zz']",
                        "--current-slots: no slot given for ['b']; using the capture/league slot"]


def test_publish_text_renderers_and_url_normalisation():
    from fantasy.ops.status import _ping_text, _psycopg_url, _publish_rest_text, _publish_sql_text

    res = {"target": "https://x.supabase.co", "auth": "SUPABASE_KEY", "policy": {"players": "merge", "tasks": "insert"},
           "rows": {"players": 3, "tasks": 2}, "batches": {"players": 1, "tasks": 1}, "inserted": {"tasks": 1},
           "skipped": {}, "errors": {}}
    assert _publish_rest_text(res, "sqlite:///db", dry_run=False).splitlines() == [
        "sqlite:///db -> https://x.supabase.co (auth: SUPABASE_KEY)",
        "players: upserted 3 rows in 1 batch(es)",
        "tasks: insert-if-missing sent 2 rows in 1 batch(es), 1 new",
    ]
    dry = _publish_rest_text({**res, "skipped": {"tasks": ["T-1"]}, "errors": {"players": "boom"}}, "s", True)
    assert dry.startswith("DRY RUN s -> ") and "would upsert 3" in dry and "would insert-if-missing 2" in dry
    assert dry.splitlines()[-2:] == ["SKIPPED (unique-key clash with different target row): {'tasks': ['T-1']}",
                                     "ERRORS: {'players': 'boom'}"]
    assert _publish_sql_text(res) == "players: upserted 3 rows\ntasks: insert-if-missing 2 rows, 1 new"
    ping = {"target": "t", "reachable": False, "status": None, "auth_ok": False, "auth": None, "message": "hint"}
    assert _ping_text(ping) == "t: UNREACHABLE auth_ok=False (auth: None)\nhint"
    assert _ping_text({**ping, "reachable": True, "status": 200, "auth_ok": True, "message": None}) == \
        "t: HTTP 200 auth_ok=True (auth: None)"
    assert _psycopg_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert _psycopg_url("postgresql://u@h/db") == "postgresql+psycopg://u@h/db"
    assert _psycopg_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"


def test_status_text_labels_mock_draft():
    from fantasy.ops.status import _render_text

    base = {"as_of": __import__("datetime").datetime(2026, 10, 20), "db": "sqlite:///x", "locks": [],
            "runs": {"active": [], "stale": []}, "approvals_pending": [], "txns_needing_reconciliation": [],
            "tasks_open": {}, "model_freshness": []}
    assert "draft: mock-check pending (mock)" in _render_text(
        {**base, "active_draft": {"draft_id": "mock-check", "status": "pending", "is_mock": True}})
    assert _render_text({**base, "active_draft": {"draft_id": "nba.l.1", "status": "live", "is_mock": False}}).endswith(
        "draft: nba.l.1 live")
    assert _render_text({**base, "active_draft": None}).endswith("draft: none active")
