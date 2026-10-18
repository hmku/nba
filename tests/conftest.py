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
