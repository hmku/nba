"""One failure contract for every command group (tech-debt F6): with --json a failure prints
{"ok": false, "code": <machine code>, "message": <text>, "error": ...} on stdout, and exit codes are
unchanged. `error` keeps its historical meaning per group (ops: == code; draft/sim/values/settings:
== message) so existing callers keep working."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from fantasy.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(db_url, monkeypatch, proj_path, tmp_path):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)
    from fantasy.league import settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", tmp_path / "league_settings.json")
    P = ["--projections", str(proj_path)]
    assert runner.invoke(app, ["draft", "init", "--draft-id", "mock-e", "--mock"]).exit_code == 0
    assert runner.invoke(app, ["draft", "pick", "player-000", "--overall", "1", "--draft-id", "mock-e", *P]).exit_code == 0
    return P


def _p(proj_path):
    return ["--projections", str(proj_path)]


# (args, exit code, code, error_is_code)
CASES = [
    (["draft", "board", "--draft-id", "nope"], 1, "not_found", False),
    (["draft", "init", "--draft-id", "mock-x"], 2, "usage", False),
    (["draft", "pick", "zzz-nobody", "--overall", "2", "--draft-id", "mock-e", "{P}"], 2, "unresolved_player", False),
    (["draft", "pick", "player-001", "--overall", "1", "--draft-id", "mock-e", "{P}"], 2, "conflict", False),
    (["draft", "pick", "player-001", "--overall", "0", "--draft-id", "mock-e", "{P}"], 2, "usage", False),
    (["draft", "recommend", "--objective", "bogus", "--draft-id", "mock-e", "{P}"], 2, "usage", False),
    (["draft", "recommend", "--punt", "bogus", "--draft-id", "mock-e", "{P}"], 2, "usage", False),
    (["draft", "set-order", "--rounds", "1,2", "--draft-id", "mock-e"], 2, "usage", False),
    (["draft", "prepare-pick", "player-001", "--overall", "1", "--draft-id", "mock-e", "{P}"], 2, "usage", False),
    (["values", "build", "--punt", "bogus", "--no-save", "{P}"], 2, "usage", False),
    (["settings", "verify", "no_such_field"], 2, "usage", False),
    (["sim", "season", "--draft-id", "nope", "--sims", "2", "{P}"], 1, "not_found", False),
    (["run", "heartbeat"], 2, "usage", True),
    (["approval", "show", "apr-nope"], 6, "not_found", True),
    (["txn", "show", "txn-nope"], 6, "not_found", True),
]


@pytest.mark.parametrize(("args", "exit_code", "code", "error_is_code"), CASES,
                         ids=[" ".join(c[0][:2]) + f"-{i}" for i, c in enumerate(CASES)])
def test_failure_shape(args, exit_code, code, error_is_code, proj_path):
    argv = [a for x in args for a in (_p(proj_path) if x == "{P}" else [x])] + ["--json"]
    res = runner.invoke(app, argv)
    assert res.exit_code != 0, res.stdout
    if exit_code is not None:
        assert res.exit_code == exit_code, res.stdout
    out = json.loads(res.stdout)
    assert out["ok"] is False
    assert isinstance(out["code"], str) and out["code"]
    if code is not None:
        assert out["code"] == code
    assert isinstance(out["message"], str) and out["message"]
    assert out["error"] == (out["code"] if error_is_code else out["message"])


def test_prepare_pick_refusal_is_ops_style(proj_path):
    from fantasy.ops import ledger

    rid = ledger.start_run("t", mode="live")
    res = runner.invoke(app, ["draft", "prepare-pick", "player-001", "--overall", "2", "--draft-id", "mock-e",
                              "--run-id", rid, *_p(proj_path), "--json"])
    assert res.exit_code == 4
    out = json.loads(res.stdout)
    assert out["error"] == out["code"] == "not_allowed" and out["action"] == "DO_NOT_CLICK" and out["message"]


def test_text_mode_failure_goes_to_stderr(proj_path):
    res = runner.invoke(app, ["draft", "board", "--draft-id", "nope"])
    assert res.exit_code == 1 and res.stdout == "" and res.stderr.startswith("error: draft not found")
