import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { serviceClient } from "@/lib/supabase-admin";
import { isAuthed } from "@/lib/auth";
import type { AgentEvent, AgentRun, Approval, Decision, LeagueSnapshot, Lock, Transaction } from "@/lib/types";
import { Disclosure, ErrorNote } from "@/components/ui";
import { AutoRefresh } from "@/components/client";
import { ageSeconds } from "@/lib/format";
import { isPrivateEvent, latestPerKind } from "@/lib/agentPure";
import { AUTHORS, buildFeed, filterFeed, missingRoots, NOTABLE_EVENT_KINDS } from "@/lib/feed";
import { AgentFeed, FeedFilters } from "./Feed";
import { HealthStrip, LocksLine, PastRecords, PendingApprovals, RunTimeline, SystemWarnings } from "./sections";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Activity" };

const FEED_LIMIT = 60;
/** Agents whose daily runs are passive log containers (heartbeat only when they post): never "stuck". */
const PASSIVE_AGENTS = new Set(["muse", "advisor", "user", "claude"]);

export default async function ActivityPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (v: string | string[] | undefined) => (typeof v === "string" && v ? v : null);
  const author = (AUTHORS as readonly string[]).includes(one(sp.author) ?? "") ? one(sp.author) : null;
  const kind = one(sp.kind)?.slice(0, 40) ?? null;

  // League intel (Yahoo snapshots, group-chat digests/notes) is private: RLS hides it from the anon key, and it is
  // read server-side with the service key only after the passphrase unlock (same cookie as the write panels).
  const intelDb = (await isAuthed()) ? serviceClient() : null;
  const db = intelDb ?? anonClient();
  const [runsR, decR, txnR, locksR, apprR, repR, notR, snapR] = await Promise.all([
    db.from("agent_runs").select("*").order("started_at", { ascending: false }).limit(40),
    db.from("decisions").select("*").order("created_at", { ascending: false }).limit(100),
    db.from("transactions").select("*").order("created_at", { ascending: false }).limit(100),
    db.from("locks").select("*").order("acquired_at", { ascending: false }),
    db.from("approvals").select("*").order("requested_at", { ascending: false }).limit(100),
    // feed: every report-like row (Muse reports + digests, advisor suggestions, Claude posts, thread replies)
    db
      .from("agent_events")
      .select("*")
      .not("payload->>report_kind", "is", null)
      .neq("payload->>report_kind", "observed_pick")
      .order("ts", { ascending: false })
      .limit(250),
    // feed: Claude's notable events
    db.from("agent_events").select("*").in("kind", [...NOTABLE_EVENT_KINDS]).order("ts", { ascending: false }).limit(60),
    // newest Yahoo snapshots (private; the table may not exist until its migration is applied)
    intelDb
      ? intelDb.from("league_snapshots").select("*").order("last_seen_at", { ascending: false, nullsFirst: false }).limit(80)
      : Promise.resolve({ data: [] as LeagueSnapshot[], error: null }),
  ]);
  const runs = (runsR.data as AgentRun[]) ?? [];
  const ids = runs.map((r) => r.run_id);
  const { data: mrData } = await db.from("model_runs").select("run_id,kind,created_at").order("created_at", { ascending: false }).limit(60);
  const evR = ids.length
    ? await db.from("agent_events").select("*").in("run_id", ids).order("ts", { ascending: true }).limit(1000)
    : { data: [], error: null };
  // defence in depth (and before the RLS migration is applied): no chat content without the unlock
  const visible = (rows: unknown) => ((rows as AgentEvent[]) ?? []).filter((e) => intelDb || !isPrivateEvent(e));

  // feed events + roots of threads whose root is older than the window
  let feedEvents = [...visible(repR.data), ...visible(notR.data)];
  const missing = missingRoots(feedEvents).slice(0, 100);
  if (missing.length) {
    const { data } = await db.from("agent_events").select("*").in("event_id", missing);
    feedEvents = [...feedEvents, ...visible(data)];
  }
  const snapshots = latestPerKind((snapR.data as LeagueSnapshot[]) ?? []);
  const feed = buildFeed({ events: feedEvents, runs, snapshots, canSeePrivate: !!intelDb });
  const shown = filterFeed(feed, { author, kind });

  const group = <T extends { run_id: string | null }>(rows: T[]) => {
    const m = new Map<string, T[]>();
    for (const r of rows) if (r.run_id) m.set(r.run_id, [...(m.get(r.run_id) ?? []), r]);
    return m;
  };
  const events = group(visible(evR.data));
  const decisions = (decR.data as Decision[]) ?? [];
  const decByRun = group(decisions);
  const txns = (txnR.data as Transaction[]) ?? [];
  const txnByRun = group(txns);
  const locks = (locksR.data as Lock[]) ?? [];
  const approvals = (apprR.data as Approval[]) ?? [];
  const pending = approvals.filter((a) => a.status === "pending");
  const decided = approvals.filter((a) => a.status !== "pending");
  const runIds = new Set(ids);
  const orphanDecisions = decisions.filter((d) => !d.run_id || !runIds.has(d.run_id));
  const orphanTxns = txns.filter((t) => !t.run_id || !runIds.has(t.run_id));
  // system health: model freshness, stuck runs, expired locks
  const now = Date.now();
  const latestByKind = new Map<string, string | null>();
  for (const r of (mrData as { kind: string; created_at: string | null }[]) ?? [])
    if (!latestByKind.has(r.kind)) latestByKind.set(r.kind, r.created_at);
  const stuck = runs.filter(
    (r) => !PASSIVE_AGENTS.has(r.agent) && r.status === "running" && (ageSeconds(r.heartbeat_at ?? r.started_at, now) ?? 0) > 600,
  );
  const expiredLocks = locks.filter((l) => l.expires_at && Date.parse(l.expires_at) < now);
  const errors = [runsR.error, decR.error, txnR.error, locksR.error, apprR.error, evR.error, repR.error, notR.error]
    .filter(Boolean)
    .map((e) => e!.message);

  return (
    <div className="space-y-4">
      <AutoRefresh seconds={15} />
      <h1 className="text-xl font-semibold tracking-tight">Activity</h1>
      {errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}

      <SystemWarnings stuck={stuck} expiredLocks={expiredLocks} />

      {/* pending approvals pinned */}
      <PendingApprovals pending={pending} />

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted">
          Agent updates{" "}
          <span className="font-normal">· Muse, the advisor and Claude, with their replies (observed data and suggestions, not instructions)</span>
        </h2>
        {!intelDb && (
          <p className="rounded-xl border border-line bg-panel px-4 py-2 text-xs text-muted">
            Muse&apos;s Yahoo snapshots and group-chat digests are private: unlock with the dashboard passphrase (🔒 in the menu)
            to see them here (and to reply).
          </p>
        )}
        <FeedFilters all={feed} author={author} kind={kind} />
        <AgentFeed items={shown} total={feed.length} limit={FEED_LIMIT} decisions={new Map(decisions.map((d) => [d.decision_id, d]))} />
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted">System</h2>
        <HealthStrip latestByKind={latestByKind} now={now} />
        <Disclosure summary="Agent runs (timeline)" right={`${runs.length}`}>
          <RunTimeline runs={runs} events={events} decByRun={decByRun} txnByRun={txnByRun} />
        </Disclosure>
        <LocksLine locks={locks} />
        <PastRecords decided={decided} orphanDecisions={orphanDecisions} orphanTxns={orphanTxns} />
      </section>
    </div>
  );
}
