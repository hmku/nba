CREATE TABLE IF NOT EXISTS agent_runs (
	run_id VARCHAR NOT NULL, 
	agent VARCHAR NOT NULL, 
	task TEXT, 
	status VARCHAR NOT NULL, 
	mode VARCHAR, 
	git_sha VARCHAR, 
	session_ref TEXT, 
	summary TEXT, 
	started_at TIMESTAMP WITH TIME ZONE, 
	heartbeat_at TIMESTAMP WITH TIME ZONE, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (run_id)
);

CREATE TABLE IF NOT EXISTS data_snapshots (
	snapshot_id VARCHAR NOT NULL, 
	source VARCHAR NOT NULL, 
	uri TEXT, 
	path TEXT, 
	sha256 VARCHAR, 
	rows INTEGER, 
	meta JSONB, 
	fetched_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (snapshot_id)
);

CREATE TABLE IF NOT EXISTS drafts (
	draft_id VARCHAR NOT NULL, 
	league_key VARCHAR, 
	status VARCHAR NOT NULL, 
	num_teams INTEGER NOT NULL, 
	num_rounds INTEGER NOT NULL, 
	my_slot INTEGER NOT NULL, 
	order_type VARCHAR NOT NULL, 
	config JSONB, 
	is_mock BOOLEAN, 
	created_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (draft_id)
);

CREATE TABLE IF NOT EXISTS league_settings (
	league_key VARCHAR NOT NULL, 
	settings JSONB NOT NULL, 
	source VARCHAR, 
	verified BOOLEAN, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (league_key)
);

CREATE TABLE IF NOT EXISTS locks (
	resource VARCHAR NOT NULL, 
	holder_run_id VARCHAR NOT NULL, 
	purpose TEXT, 
	acquired_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (resource)
);

CREATE TABLE IF NOT EXISTS model_runs (
	run_id VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	model_version VARCHAR NOT NULL, 
	git_sha VARCHAR, 
	params JSONB, 
	inputs JSONB, 
	summary JSONB, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (run_id)
);

CREATE TABLE IF NOT EXISTS players (
	player_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	team VARCHAR, 
	positions JSONB, 
	nba_id VARCHAR, 
	yahoo_player_key VARCHAR, 
	age FLOAT, 
	status VARCHAR, 
	injury_note TEXT, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (player_id)
);

CREATE TABLE IF NOT EXISTS tasks (
	task_id VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	description TEXT, 
	area VARCHAR, 
	priority INTEGER, 
	status VARCHAR NOT NULL, 
	owner VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (task_id), 
	CONSTRAINT uq_tasks_title UNIQUE (title)
);

CREATE TABLE IF NOT EXISTS agent_events (
	event_id VARCHAR NOT NULL, 
	run_id VARCHAR NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE, 
	kind VARCHAR NOT NULL, 
	message TEXT, 
	payload JSONB, 
	PRIMARY KEY (event_id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (run_id)
);

CREATE INDEX IF NOT EXISTS ix_agent_events_run ON agent_events (run_id, ts);

CREATE TABLE IF NOT EXISTS approvals (
	approval_id VARCHAR NOT NULL, 
	run_id VARCHAR, 
	action_type VARCHAR NOT NULL, 
	summary TEXT, 
	payload JSONB, 
	status VARCHAR NOT NULL, 
	decided_by VARCHAR, 
	note TEXT, 
	requested_at TIMESTAMP WITH TIME ZONE, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (approval_id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (run_id)
);

CREATE TABLE IF NOT EXISTS decisions (
	decision_id VARCHAR NOT NULL, 
	run_id VARCHAR, 
	kind VARCHAR NOT NULL, 
	model_recommendation JSONB, 
	choice JSONB, 
	agrees_with_model BOOLEAN, 
	rationale TEXT, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (decision_id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (run_id)
);

CREATE TABLE IF NOT EXISTS draft_picks (
	draft_id VARCHAR NOT NULL, 
	overall_pick INTEGER NOT NULL, 
	round INTEGER NOT NULL, 
	team_slot INTEGER NOT NULL, 
	player_id VARCHAR, 
	player_name VARCHAR, 
	source VARCHAR, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (draft_id, overall_pick), 
	FOREIGN KEY(draft_id) REFERENCES drafts (draft_id)
);

CREATE TABLE IF NOT EXISTS draft_recommendations (
	rec_id VARCHAR NOT NULL, 
	draft_id VARCHAR NOT NULL, 
	as_of_pick INTEGER NOT NULL, 
	for_pick INTEGER, 
	model_run_id VARCHAR, 
	payload JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (rec_id), 
	FOREIGN KEY(draft_id) REFERENCES drafts (draft_id)
);

CREATE INDEX IF NOT EXISTS ix_draft_recs_draft_pick ON draft_recommendations (draft_id, as_of_pick);

CREATE TABLE IF NOT EXISTS player_values (
	run_id VARCHAR NOT NULL, 
	player_id VARCHAR NOT NULL, 
	rank INTEGER, 
	value FLOAT, 
	cat_values JSONB, 
	adp FLOAT, 
	positions JSONB, 
	extra JSONB, 
	PRIMARY KEY (run_id, player_id), 
	FOREIGN KEY(run_id) REFERENCES model_runs (run_id)
);

CREATE TABLE IF NOT EXISTS projections (
	run_id VARCHAR NOT NULL, 
	player_id VARCHAR NOT NULL, 
	season VARCHAR NOT NULL, 
	gp FLOAT, 
	gp_sd FLOAT, 
	mpg FLOAT, 
	fgm FLOAT, 
	fga FLOAT, 
	ftm FLOAT, 
	fta FLOAT, 
	fg3m FLOAT, 
	pts FLOAT, 
	reb FLOAT, 
	ast FLOAT, 
	stl FLOAT, 
	blk FLOAT, 
	tov FLOAT, 
	extra JSONB, 
	PRIMARY KEY (run_id, player_id), 
	FOREIGN KEY(run_id) REFERENCES model_runs (run_id)
);

CREATE TABLE IF NOT EXISTS transactions (
	txn_id VARCHAR NOT NULL, 
	idempotency_key VARCHAR NOT NULL, 
	run_id VARCHAR, 
	kind VARCHAR NOT NULL, 
	payload JSONB, 
	status VARCHAR NOT NULL, 
	approval_id VARCHAR, 
	verification JSONB, 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (txn_id), 
	UNIQUE (idempotency_key), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (run_id)
);

