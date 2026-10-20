from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from synthetic import write_projections  # noqa: E402


@pytest.fixture(scope="session")
def proj_path(tmp_path_factory) -> Path:
    return write_projections(tmp_path_factory.mktemp("proj") / "projections.csv")


@pytest.fixture()
def db_url(tmp_path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("FANTASY_DB_URL", url)
    return url


@pytest.fixture(autouse=True)
def _no_repo_league_schedule(monkeypatch, tmp_path) -> None:
    """`fantasy sim` defaults to data/manual/league_schedule.json when it exists; tests stay independent of repo data."""
    import fantasy.sim.cli as sim_cli

    monkeypatch.setattr(sim_cli, "LEAGUE_SCHEDULE_PATH", tmp_path / "no_league_schedule.json")
