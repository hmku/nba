-- Privacy: league-intel content from the group chat / Yahoo league pages is NOT public.
--
-- The dashboard is public and the anon/publishable key ships to every browser, so anything anon can
-- SELECT is readable by anyone on the internet. Chat digests carry verbatim excerpts of other league
-- members' messages, and league snapshots carry rosters/standings as Muse saw them. From now on:
--   * league_snapshots: no anon/authenticated read at all (the service role bypasses RLS: the Muse
--     API routes, the dashboard *after the passphrase unlock*, and `fantasy intel --rest`).
--   * agent_events: public read stays for agent logs, EXCEPT rows whose payload.report_kind is
--     group-chat content ('chat_digest', 'chat_note').
-- Realtime postgres_changes honours RLS, so the hidden rows are not broadcast to anon either.
-- Writes were already service-role only. Idempotent: safe to re-run.

drop policy if exists "public read league_snapshots" on league_snapshots;

drop policy if exists "public read agent_events" on agent_events;
drop policy if exists "public read agent_events (no chat content)" on agent_events;
create policy "public read agent_events (no chat content)" on agent_events
	for select to anon, authenticated
	using (coalesce(payload->>'report_kind', '') not in ('chat_digest', 'chat_note'));
