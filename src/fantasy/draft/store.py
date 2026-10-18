"""Persistence for drafts, picks, recommendations, valuation runs (fantasy.db tables)."""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from sqlalchemy import Engine, delete, insert, select, update

from fantasy.db.engine import get_engine
from fantasy.db.schema import (
    draft_picks,
    draft_recommendations,
    drafts,
    model_runs,
    player_values,
    players,
)
from fantasy.draft.order import pick_info
from fantasy.league.settings import LeagueSettings
from fantasy.model.valuation import ValuationRun, git_sha


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(obj: Any) -> Any:
    """Make payloads JSON-safe (NaN -> None, numpy -> python)."""
    return json.loads(json.dumps(obj, default=_default), parse_constant=lambda _: None)


def _default(o: Any) -> Any:
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, float) and math.isnan(o):
        return None
    if isinstance(o, set | tuple):
        return list(o)
    return str(o)


def _nan_none(x: Any) -> Any:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


# ---------------------------------------------------------------- drafts
def create_draft(
    draft_id: str, settings: LeagueSettings, is_mock: bool = False, my_slot: int | None = None,
    config: dict | None = None, engine: Engine | None = None,
) -> dict:
    eng = engine or get_engine()
    row = {
        "draft_id": draft_id,
        "league_key": settings.league_key,
        "status": "pending",
        "num_teams": settings.num_teams,
        "num_rounds": settings.num_rounds,
        "my_slot": my_slot or settings.my_slot,
        "order_type": settings.draft_order_type,
        "config": config or {},
        "is_mock": is_mock,
        "created_at": _now(),
        "updated_at": _now(),
    }
    with eng.begin() as conn:
        existing = conn.execute(select(drafts).where(drafts.c.draft_id == draft_id)).mappings().first()
        if existing:
            return dict(existing)
        conn.execute(insert(drafts).values(**row))
    return row


def get_draft(draft_id: str | None = None, engine: Engine | None = None) -> dict | None:
    eng = engine or get_engine()
    with eng.connect() as conn:
        q = select(drafts)
        if draft_id:
            q = q.where(drafts.c.draft_id == draft_id)
        else:
            q = q.order_by(drafts.c.is_mock.asc(), drafts.c.created_at.desc())
        row = conn.execute(q).mappings().first()
    return dict(row) if row else None


def set_draft_order(draft_id: str, custom_rounds: list[list[int]], engine: Engine | None = None) -> None:
    eng = engine or get_engine()
    with eng.begin() as conn:
        row = conn.execute(select(drafts).where(drafts.c.draft_id == draft_id)).mappings().first()
        if row is None:
            raise KeyError(draft_id)
        config = {**(row["config"] or {}), "custom_rounds": custom_rounds}
        conn.execute(update(drafts).where(drafts.c.draft_id == draft_id).values(
            order_type="custom", config=config, updated_at=_now()))


def set_draft_status(draft_id: str, status: str, engine: Engine | None = None) -> None:
    eng = engine or get_engine()
    with eng.begin() as conn:
        conn.execute(update(drafts).where(drafts.c.draft_id == draft_id).values(status=status, updated_at=_now()))


def get_picks(draft_id: str, engine: Engine | None = None) -> list[dict]:
    eng = engine or get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            select(draft_picks).where(draft_picks.c.draft_id == draft_id).order_by(draft_picks.c.overall_pick)
        ).mappings().all()
    return [dict(r) for r in rows]


class PickConflict(Exception):
    pass


def record_pick(
    draft: dict, overall: int, player_id: str, player_name: str, source: str,
    force: bool = False, engine: Engine | None = None,
) -> dict:
    """Idempotent: re-recording the same player at the same pick is a no-op."""
    eng = engine or get_engine()
    info = pick_info(overall, draft["num_teams"], draft["num_rounds"], draft["order_type"],
                     (draft.get("config") or {}).get("custom_rounds"))
    with eng.begin() as conn:
        existing = conn.execute(
            select(draft_picks).where(draft_picks.c.draft_id == draft["draft_id"], draft_picks.c.overall_pick == overall)
        ).mappings().first()
        if existing:
            if existing["player_id"] == player_id:
                return {**dict(existing), "status": "unchanged"}
            if not force:
                raise PickConflict(f"pick {overall} already recorded as {existing['player_name']} ({existing['player_id']})")
            conn.execute(delete(draft_picks).where(
                draft_picks.c.draft_id == draft["draft_id"], draft_picks.c.overall_pick == overall))
        dup = conn.execute(
            select(draft_picks).where(draft_picks.c.draft_id == draft["draft_id"], draft_picks.c.player_id == player_id)
        ).mappings().first()
        if dup and not force:
            raise PickConflict(f"{player_name} already drafted at pick {dup['overall_pick']}")
        row = {"draft_id": draft["draft_id"], "overall_pick": overall, "round": info.round, "team_slot": info.slot,
               "player_id": player_id, "player_name": player_name, "source": source, "created_at": _now()}
        conn.execute(insert(draft_picks).values(**row))
        if draft.get("status") == "pending":
            conn.execute(update(drafts).where(drafts.c.draft_id == draft["draft_id"]).values(status="live", updated_at=_now()))
    return {**row, "status": "recorded"}


def undo_pick(draft_id: str, overall: int, engine: Engine | None = None) -> int:
    eng = engine or get_engine()
    with eng.begin() as conn:
        res = conn.execute(delete(draft_picks).where(
            draft_picks.c.draft_id == draft_id, draft_picks.c.overall_pick == overall))
    return res.rowcount or 0


def save_recommendation(rec: dict, engine: Engine | None = None) -> str:
    eng = engine or get_engine()
    rec_id = f"rec-{uuid.uuid4().hex[:12]}"
    with eng.begin() as conn:
        conn.execute(insert(draft_recommendations).values(
            rec_id=rec_id, draft_id=rec["draft_id"], as_of_pick=rec["as_of_pick"], for_pick=rec.get("for_pick"),
            model_run_id=rec.get("model_run_id"), payload=_clean(rec), created_at=_now()))
    return rec_id


def latest_recommendation(draft_id: str, engine: Engine | None = None) -> dict | None:
    eng = engine or get_engine()
    with eng.connect() as conn:
        row = conn.execute(
            select(draft_recommendations).where(draft_recommendations.c.draft_id == draft_id)
            .order_by(draft_recommendations.c.created_at.desc()).limit(1)
        ).mappings().first()
    return dict(row["payload"]) if row else None


# ---------------------------------------------------------------- valuation runs
def find_valuation_run(projections_sha: str, params: dict, engine: Engine | None = None) -> str | None:
    eng = engine or get_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            select(model_runs).where(model_runs.c.kind == "valuation").order_by(model_runs.c.created_at.desc()).limit(20)
        ).mappings().all()
    for r in rows:
        if (r["inputs"] or {}).get("projections_sha256") == projections_sha and r["params"] == _clean(params):
            return r["run_id"]
    return None


def save_valuation(run: ValuationRun, proj_df: pd.DataFrame, engine: Engine | None = None) -> str:
    eng = engine or get_engine()
    cats = run.params["categories"]
    with eng.begin() as conn:
        conn.execute(insert(model_runs).values(
            run_id=run.run_id, kind="valuation", model_version=run.model_version, git_sha=git_sha(),
            params=_clean(run.params), inputs=_clean(run.inputs),
            summary=_clean({"n_players": len(run.values),
                            "top10": run.values.head(10)["name"].tolist()}),
            created_at=datetime.fromisoformat(run.created_at)))
        rows = []
        for pid, v in run.values.iterrows():
            rows.append({
                "run_id": run.run_id, "player_id": pid, "rank": int(v["rank"]), "value": float(v["value"]),
                "cat_values": {k: round(float(v[k]), 4) for k in cats}, "adp": _nan_none(float(v["adp"])),
                "positions": list(v["positions"]),
                "extra": _clean({"name": v["name"], "team": v.get("team"), "gp": float(v["gp"]),
                                 "status": v.get("status")}),
            })
        conn.execute(insert(player_values), rows)
        upsert_players(conn, proj_df)
    return run.run_id


def upsert_players(conn, proj_df: pd.DataFrame) -> None:
    ids = list(proj_df.index)
    existing = set(conn.execute(select(players.c.player_id)).scalars().all())
    now = _now()
    for pid in ids:
        r = proj_df.loc[pid]
        vals = {"name": str(r["name"]), "team": _nan_none(r.get("team")) or None, "positions": list(r["positions"]),
                "age": _nan_none(float(r["age"])) if "age" in r and pd.notna(r["age"]) else None,
                "status": _nan_none(r.get("status")) or None, "injury_note": _nan_none(r.get("injury_note")) or None,
                "updated_at": now}
        if pid in existing:
            conn.execute(update(players).where(players.c.player_id == pid).values(**vals))
        else:
            conn.execute(insert(players).values(player_id=pid, **vals))
