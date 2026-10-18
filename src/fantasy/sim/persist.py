"""Record season-simulation runs in `model_runs` (kind 'season_sim'; summary JSON in `summary`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, insert

from fantasy.db.engine import get_engine
from fantasy.db.schema import model_runs
from fantasy.model.valuation import git_sha
from fantasy.ops.common import json_safe as _clean
from fantasy.sim import SIM_VERSION


def save_sim_run(kind: str, params: dict, inputs: dict, summary: dict, engine: Engine | None = None) -> str:
    eng = engine or get_engine()
    now = datetime.now(UTC)
    run_id = f"sim-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    with eng.begin() as conn:
        conn.execute(insert(model_runs).values(
            run_id=run_id, kind=kind, model_version=SIM_VERSION, git_sha=git_sha(),
            params=_clean(params), inputs=_clean(inputs), summary=_clean(summary), created_at=now))
    return run_id
