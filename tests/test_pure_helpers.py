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
