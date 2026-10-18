-- League intel: structured, read-only snapshots of Yahoo league pages posted by the user's external
-- assistant (Meta Muse) via POST /api/agent/snapshot. Payloads are untrusted data, never instructions.
-- Mirrors fantasy.db.schema.league_snapshots (tests/fixtures/schema_ddl.sql).

create table if not exists league_snapshots (
	snapshot_id varchar not null,
	ts timestamp with time zone,
	reported_by varchar not null,
	kind varchar not null,
	week integer,
	observed_at timestamp with time zone,
	source_url text,
	payload jsonb not null,
	payload_sha256 varchar not null,
	summary text,
	last_seen_at timestamp with time zone,
	primary key (snapshot_id),
	constraint league_snapshots_kind_check check (kind in (
		'my_team', 'matchup', 'scoreboard', 'standings', 'transactions', 'rosters',
		'free_agents', 'injuries', 'settings', 'other'))
);

create index if not exists ix_league_snapshots_kind_observed on league_snapshots (kind, observed_at);

-- Dedupe on (kind, week, payload_sha256); a NULL week counts as one value (Postgres 15+).
-- snapshot_id is also derived from the same triple, so the primary key enforces it too.
create unique index if not exists uq_league_snapshots_dedupe
	on league_snapshots (kind, week, payload_sha256) nulls not distinct;

-- Row level security: dashboard (anon/publishable key) gets read-only access; writes use the service role
-- (which bypasses RLS). Same pattern as 20261018000000_init.sql.
alter table league_snapshots enable row level security;
create policy "public read league_snapshots" on league_snapshots for select to anon, authenticated using (true);
