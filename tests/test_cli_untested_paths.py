"""CLI paths that had no tests (tech-debt audit 2026-10-18): `draft watch`, `settings show|verify`,
and the top-level CLI's handling of a sub-app that fails to import."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fantasy.cli import app
from fantasy.draft import store
from fantasy.league import settings as settings_mod

runner = CliRunner()


@pytest.fixture(autouse=True)
def _env(db_url, monkeypatch):
    monkeypatch.delenv("FANTASY_RUN_ID", raising=False)
    monkeypatch.delenv("FANTASY_DRAFT_ID", raising=False)


class _Stop(Exception):
    pass


def test_draft_watch_publishes_one_recommendation_per_board_change(proj_path, monkeypatch):
    assert runner.invoke(app, ["draft", "init", "--draft-id", "mock-w", "--mock"]).exit_code == 0
    sleeps = []

    def fake_sleep(_s):  # first poll publishes; the second poll (same board) must not; then stop
        sleeps.append(_s)
        if len(sleeps) == 2:
            raise _Stop

    monkeypatch.setattr("fantasy.draft.cli_recommend.time.sleep", fake_sleep)
    res = runner.invoke(app, ["draft", "watch", "--draft-id", "mock-w", "--projections", str(proj_path),
                              "--interval", "0"])
    assert isinstance(res.exception, _Stop)
    assert "watching mock-w" in res.stdout and res.stdout.count("picks → for #1") == 1
    rec = store.latest_recommendation("mock-w")
    assert rec is not None and rec["for_pick"] == 1


def test_settings_show_json_matches_loaded_settings():
    res = runner.invoke(app, ["settings", "show", "--json"])
    assert res.exit_code == 0
    out = json.loads(res.stdout)
    s = settings_mod.load_settings()
    assert out["num_teams"] == s.num_teams and out["num_rounds"] == s.num_rounds
    assert out["starting_slots"] == s.starting_slots
    assert len(out["my_picks"]) == s.num_rounds and out["my_picks"][0] == s.my_slot


def test_settings_verify_writes_only_the_given_file(tmp_path, monkeypatch):
    target = tmp_path / "league_settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", target)
    res = runner.invoke(app, ["settings", "verify", "num_teams", "categories", "--source", "manual", "--json"])
    assert res.exit_code == 0
    out = json.loads(res.stdout)
    assert out["ok"] is True and out["path"] == str(target)
    assert "num_teams" not in out["unverified"] and "categories" not in out["unverified"]
    saved = json.loads(target.read_text())
    assert saved["verified"]["num_teams"] is True and saved["source"] == "manual"
    bad = runner.invoke(app, ["settings", "verify", "no_such_field"])
    assert bad.exit_code == 2


def test_broken_subapp_is_reported_not_silently_dropped(capsys):
    """Re-execute fantasy/cli.py with one extra, unimportable sub-app: the rest still registers and the
    import error is printed to stderr (previously it vanished and `fantasy ghost` was "No such command")."""
    import fantasy.cli as top

    src = Path(top.__file__)
    code = src.read_text().replace("_SUBAPPS: list[tuple[str, str, str]] = [",
                                   "_SUBAPPS: list[tuple[str, str, str]] = [\n"
                                   "    ('fantasy.no_such_module', 'app', 'ghost'),", 1)
    ns: dict = {"__name__": "fantasy_cli_copy"}
    exec(compile(code, str(src), "exec"), ns)
    err = capsys.readouterr().err
    assert "`fantasy ghost` unavailable" in err and "no_such_module" in err
    assert {"draft", "settings", "sim"} <= {g.name for g in ns["app"].registered_groups}
