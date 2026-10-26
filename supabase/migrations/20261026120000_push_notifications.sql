-- Web Push notifications for new pending approvals (docs/runbooks/dashboard.md "Notifications").
-- Mirrors fantasy.db.schema.push_subscriptions / push_sent (tests/fixtures/schema_ddl.sql).
--
-- push_subscriptions: one row per browser/device that pressed "Enable notifications" on the dashboard.
--   endpoint + keys are capability URLs: anyone holding them can push to the device, so they are private.
-- push_sent: one row per approval already notified. POST /api/push/notify inserts it BEFORE sending
--   (on conflict do nothing), so concurrent triggers notify each approval at most once.
--
-- Privacy: RLS on with NO policy for anon/authenticated -> the public (publishable) key can neither read nor
-- write these tables. Only the dashboard's server routes touch them, with the service role (bypasses RLS).
-- Idempotent: safe to re-run.

create table if not exists push_subscriptions (
	endpoint text not null,
	p256dh text not null,
	auth text not null,
	user_agent text,
	created_at timestamp with time zone,
	last_ok_at timestamp with time zone,
	last_error text,
	primary key (endpoint)
);

create table if not exists push_sent (
	approval_id varchar not null,
	sent_at timestamp with time zone,
	recipients integer,
	failures integer,
	primary key (approval_id)
);

alter table push_subscriptions enable row level security;
alter table push_sent enable row level security;
drop policy if exists "public read push_subscriptions" on push_subscriptions;
drop policy if exists "public read push_sent" on push_sent;
-- Defence in depth: no table privileges for the API roles either (the service role keeps its own).
revoke all on push_subscriptions from anon, authenticated;
revoke all on push_sent from anon, authenticated;
