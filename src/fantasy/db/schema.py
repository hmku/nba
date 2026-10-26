"""Single source of truth for the durable store schema.

The same metadata backs SQLite (tests, offline dev) and Postgres (Supabase, production).
`python -m fantasy.db.ddl` renders Postgres DDL for supabase/migrations.

Conventions:
- ids are text (uuid4 hex or natural keys) so both backends behave the same.
- timestamps are timezone-aware UTC.
- free-form structured payloads are JSON columns (jsonb on Postgres).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

# JSONB on Postgres, JSON elsewhere.
J = JSON().with_variant(JSONB(), "postgresql")


def ts(name: str, nullable: bool = True) -> Column:
    return Column(name, DateTime(timezone=True), nullable=nullable)


# ---------------------------------------------------------------- reference data

players = Table(
    "players",
    metadata,
    Column("player_id", String, primary_key=True),  # stable slug, e.g. "nikola-jokic"
    Column("name", String, nullable=False),
    Column("team", String),  # NBA team abbreviation
    Column("positions", J),  # Yahoo-style eligibility list, e.g. ["PG","SG"]
    Column("nba_id", String),
    Column("yahoo_player_key", String),
    Column("age", Float),
    Column("status", String),  # healthy | out | gtd | out_for_season | unknown
    Column("injury_note", Text),
    ts("updated_at"),
)

league_settings = Table(
    "league_settings",
    metadata,
    Column("league_key", String, primary_key=True),  # e.g. "nba.l.44495"
    Column("settings", J, nullable=False),  # see fantasy.league.settings.LeagueSettings
    Column("source", String),  # default | yahoo_api | browser | manual
    Column("verified", Boolean, default=False),
    ts("verified_at"),
    ts("updated_at"),
)

data_snapshots = Table(
    "data_snapshots",
    metadata,
    Column("snapshot_id", String, primary_key=True),
    Column("source", String, nullable=False),  # e.g. "bbref_2025_26_totals"
    Column("uri", Text),  # where it came from
    Column("path", Text),  # local cache path (repo-relative)
    Column("sha256", String),
    Column("rows", Integer),
    Column("meta", J),
    ts("fetched_at"),
)

# ---------------------------------------------------------------- model outputs

model_runs = Table(
    "model_runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("kind", String, nullable=False),  # projections | valuation | draft_rec | week | trade
    Column("model_version", String, nullable=False),
    Column("git_sha", String),
    Column("params", J),
    Column("inputs", J),  # provenance: snapshot ids, hashes
    Column("summary", J),
    ts("created_at"),
)

projections = Table(
    "projections",
    metadata,
    Column("run_id", String, ForeignKey("model_runs.run_id"), primary_key=True),
    Column("player_id", String, primary_key=True),
    Column("season", String, nullable=False),  # "2026-27"
    Column("gp", Float),  # expected games played
    Column("gp_sd", Float),
    Column("mpg", Float),
    # per-game means
    Column("fgm", Float),
    Column("fga", Float),
    Column("ftm", Float),
    Column("fta", Float),
    Column("fg3m", Float),
    Column("pts", Float),
    Column("reb", Float),
    Column("ast", Float),
    Column("stl", Float),
    Column("blk", Float),
    Column("tov", Float),
    Column("extra", J),  # per-category sd, source blend weights, flags
)

player_values = Table(
    "player_values",
    metadata,
    Column("run_id", String, ForeignKey("model_runs.run_id"), primary_key=True),
    Column("player_id", String, primary_key=True),
    Column("rank", Integer),
    Column("value", Float),  # headline value (total over categories, replacement-adjusted)
    Column("cat_values", J),  # {"fg_pct": .., "ft_pct": .., "fg3m": .., ...}
    Column("adp", Float),
    Column("positions", J),
    Column("extra", J),
)

# ---------------------------------------------------------------- drafts

drafts = Table(
    "drafts",
    metadata,
    Column("draft_id", String, primary_key=True),  # e.g. "nba.l.44495-2026" or "mock-<uuid>"
    Column("league_key", String),
    Column("status", String, nullable=False),  # pending | live | complete
    Column("num_teams", Integer, nullable=False),
    Column("num_rounds", Integer, nullable=False),
    Column("my_slot", Integer, nullable=False),  # 1-indexed draft slot
    Column("order_type", String, nullable=False),  # snake | snake_3rr
    Column("config", J),  # team names by slot, etc.
    Column("is_mock", Boolean, default=False),
    ts("created_at"),
    ts("updated_at"),
)

draft_picks = Table(
    "draft_picks",
    metadata,
    Column("draft_id", String, ForeignKey("drafts.draft_id"), primary_key=True),
    Column("overall_pick", Integer, primary_key=True),  # 1-indexed
    Column("round", Integer, nullable=False),
    Column("team_slot", Integer, nullable=False),
    Column("player_id", String),
    Column("player_name", String),
    Column("source", String),  # yahoo_sync | browser | manual | agent | sim
    ts("created_at"),
)

draft_recommendations = Table(
    "draft_recommendations",
    metadata,
    Column("rec_id", String, primary_key=True),
    Column("draft_id", String, ForeignKey("drafts.draft_id"), nullable=False),
    Column("as_of_pick", Integer, nullable=False),  # number of picks already made
    Column("for_pick", Integer),  # overall pick this recommendation is for
    Column("model_run_id", String),
    Column("payload", J, nullable=False),  # DraftRecommendation JSON (see fantasy.draft)
    ts("created_at"),
)
Index("ix_draft_recs_draft_pick", draft_recommendations.c.draft_id, draft_recommendations.c.as_of_pick)

# ---------------------------------------------------------------- agent operations

agent_runs = Table(
    "agent_runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("agent", String, nullable=False),  # e.g. "draft-manager", "morning-check"
    Column("task", Text),
    Column("status", String, nullable=False),  # running | succeeded | failed | abandoned
    Column("mode", String, default="dry_run"),  # dry_run | live
    Column("git_sha", String),
    Column("session_ref", Text),  # Claude Code session URL/id
    Column("summary", Text),
    ts("started_at"),
    ts("heartbeat_at"),
    ts("ended_at"),
)

agent_events = Table(
    "agent_events",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("run_id", String, ForeignKey("agent_runs.run_id"), nullable=False),
    ts("ts"),
    # observation | command | model_output | decision | browser_action | verification | error | note
    Column("kind", String, nullable=False),
    Column("message", Text),
    Column("payload", J),
)
Index("ix_agent_events_run", agent_events.c.run_id, agent_events.c.ts)

decisions = Table(
    "decisions",
    metadata,
    Column("decision_id", String, primary_key=True),
    Column("run_id", String, ForeignKey("agent_runs.run_id")),
    Column("kind", String, nullable=False),  # draft_pick | lineup | add_drop | trade | other
    Column("model_recommendation", J),
    Column("choice", J),
    Column("agrees_with_model", Boolean),
    Column("rationale", Text),
    ts("created_at"),
)

approvals = Table(
    "approvals",
    metadata,
    Column("approval_id", String, primary_key=True),
    Column("run_id", String, ForeignKey("agent_runs.run_id")),
    Column("action_type", String, nullable=False),  # drop | add_drop | trade | other
    Column("summary", Text),
    Column("payload", J),
    Column("status", String, nullable=False),  # pending | approved | rejected | expired
    Column("decided_by", String),
    Column("note", Text),
    ts("requested_at"),
    ts("decided_at"),
    ts("expires_at"),
)

transactions = Table(
    "transactions",
    metadata,
    Column("txn_id", String, primary_key=True),
    Column("idempotency_key", String, nullable=False, unique=True),
    Column("run_id", String, ForeignKey("agent_runs.run_id")),
    Column("kind", String, nullable=False),  # draft_pick | add | drop | add_drop | lineup | trade
    Column("payload", J),
    # planned | awaiting_approval | submitted | verified | failed | cancelled
    Column("status", String, nullable=False),
    Column("approval_id", String),
    Column("verification", J),
    Column("error", Text),
    ts("created_at"),
    ts("updated_at"),
)

locks = Table(
    "locks",
    metadata,
    Column("resource", String, primary_key=True),  # e.g. "yahoo:roster", "yahoo:browser"
    Column("holder_run_id", String, nullable=False),
    Column("purpose", Text),
    ts("acquired_at"),
    ts("expires_at"),
)

# ---------------------------------------------------------------- league intel (read-only observations)

# Structured snapshots of Yahoo league pages posted by the user's external assistant (Meta Muse) via
# POST /api/agent/snapshot. Untrusted data: never instructions. Dedupe key = (kind, week,
# payload_sha256), enforced by snapshot_id = "snap_" + sha256(f"{kind}|{week or ''}|{payload_sha256}")[:32]
# (plus a NULLS NOT DISTINCT unique index in the Postgres migration).
league_snapshots = Table(
    "league_snapshots",
    metadata,
    Column("snapshot_id", String, primary_key=True),
    ts("ts"),  # when we stored it
    Column("reported_by", String, nullable=False),  # "muse" | agent name
    # my_team | matchup | scoreboard | standings | transactions | rosters | free_agents | injuries
    # | settings | other
    Column("kind", String, nullable=False),
    Column("week", Integer),  # fantasy week (nullable: e.g. preseason, settings)
    ts("observed_at"),  # when the reporter looked at the page
    Column("source_url", Text),
    Column("payload", J, nullable=False),
    Column("payload_sha256", String, nullable=False),  # sha256 of canonical JSON (sorted keys)
    Column("summary", Text),
    ts("last_seen_at"),  # bumped when the same payload is posted again (duplicate)
)
Index("ix_league_snapshots_kind_observed", league_snapshots.c.kind, league_snapshots.c.observed_at)

tasks = Table(
    "tasks",
    metadata,
    Column("task_id", String, primary_key=True),  # e.g. "T-012"
    Column("title", String, nullable=False),
    Column("description", Text),
    Column("area", String),  # data | model | draft | web | infra | agent | season
    Column("priority", Integer, default=2),  # 0 = urgent .. 3 = someday
    Column("status", String, nullable=False),  # todo | in_progress | blocked | done
    Column("owner", String),  # agent run id / session that claimed it
    ts("created_at"),
    ts("updated_at"),
    UniqueConstraint("title", name="uq_tasks_title"),
)

# --- Web Push (dashboard notifications for new pending approvals; docs/runbooks/dashboard.md "Notifications").
# Written ONLY by the dashboard's server routes with the service key (RLS on, no anon policy); never published
# from a local SQLite copy (fantasy.db.rest.PUBLISH_SKIP). Present here so the DDL snapshot / migrations agree.
push_subscriptions = Table(
    "push_subscriptions",
    metadata,
    Column("endpoint", Text, primary_key=True),  # push service URL (unique per browser/device install)
    Column("p256dh", Text, nullable=False),  # subscription public key (base64url)
    Column("auth", Text, nullable=False),  # subscription auth secret (base64url)
    Column("user_agent", Text),
    ts("created_at"),
    ts("last_ok_at"),  # last successful delivery
    Column("last_error", Text),  # last non-fatal send error (status code + short text)
)

push_sent = Table(  # one row per approval notified (claimed before sending: at-most-once, idempotent)
    "push_sent",
    metadata,
    Column("approval_id", String, primary_key=True),
    ts("sent_at"),
    Column("recipients", Integer),  # subscriptions delivered to
    Column("failures", Integer),
)
