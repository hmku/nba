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
