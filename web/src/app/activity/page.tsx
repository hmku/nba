import { Suspense } from "react";
import Link from "next/link";
import type { Metadata } from "next";
import type { SupabaseClient } from "@supabase/supabase-js";
import { anonClient } from "@/lib/supabase";
import { serviceClient } from "@/lib/supabase-admin";
import { isAuthed } from "@/lib/auth";
import type { AgentEvent, AgentRun, Approval, Decision, LeagueSnapshot, Lock, Transaction } from "@/lib/types";
import { Disclosure, ErrorNote } from "@/components/ui";
import { AutoRefresh } from "@/components/client";
import { ageSeconds } from "@/lib/format";
import { isPrivateEvent, latestPerKind } from "@/lib/agentPure";
import { AUTHORS, buildFeed, feedLimit, filterFeed, missingRoots, NOTABLE_EVENT_KINDS } from "@/lib/feed";
import { AgentFeed, chipHref, FeedFilters } from "./Feed";
import { HealthStrip, LocksLine, PastRecords, PendingApprovals, RunTimeline, SystemWarnings } from "./sections";

// approvals and the passphrase cookie make this page per-request
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Activity" };

/** Agents whose daily runs are passive log containers (heartbeat only when they post): never "stuck". */
const PASSIVE_AGENTS = new Set(["muse", "advisor", "user", "claude"]);
// Column lists: only what the cards render (no select("*") on the big tables).
const EVENT_COLS = "event_id,run_id,ts,kind,message,payload";
const RUN_COLS = "run_id,agent,task,status,mode,git_sha,session_ref,summary,started_at,heartbeat_at,ended_at";
const DECISION_COLS = "decision_id,run_id,kind,model_recommendation,choice,agrees_with_model,rationale,created_at";
const TXN_COLS = "txn_id,run_id,kind,status,payload,error,created_at,updated_at";
const APPROVAL_COLS = "approval_id,run_id,action_type,summary,payload,status,note,requested_at,decided_at,expires_at";
const SNAPSHOT_COLS = "snapshot_id,ts,kind,week,observed_at,source_url,summary,last_seen_at,payload";

export default async function ActivityPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (v: string | string[] | undefined) => (typeof v === "string" && v ? v : null);
  const author = (AUTHORS as readonly string[]).includes(one(sp.author) ?? "") ? one(sp.author) : null;
  const kind = one(sp.kind)?.slice(0, 40) ?? null;
  const limit = feedLimit(one(sp.n));
  const showTimeline = one(sp.timeline) === "1";
  const reportRows = limit * 3; // replies share the window with their roots

  // League intel (Yahoo snapshots, group-chat digests/notes) is private: RLS hides it from the anon key, and it is
  // read server-side with the service key only after the passphrase unlock (same cookie as the write panels).
  const intelDb = (await isAuthed()) ? serviceClient() : null;
  const db = intelDb ?? anonClient();
  // every independent query in ONE round trip
  const [runsR, decR, txnR, locksR, apprR, repR, notR, snapR, mrR] = await Promise.all([
    db.from("agent_runs").select(RUN_COLS).order("started_at", { ascending: false }).limit(40),
    db.from("decisions").select(DECISION_COLS).order("created_at", { ascending: false }).limit(100),
    db.from("transactions").select(TXN_COLS).order("created_at", { ascending: false }).limit(100),
    db.from("locks").select("*").order("acquired_at", { ascending: false }),
    db.from("approvals").select(APPROVAL_COLS).order("requested_at", { ascending: false }).limit(100),
    // feed: every report-like row (Muse reports + digests, advisor suggestions, Claude posts, thread replies)
    db
      .from("agent_events")
      .select(EVENT_COLS)
      .not("payload->>report_kind", "is", null)
      .neq("payload->>report_kind", "observed_pick")
      .order("ts", { ascending: false })
      .limit(reportRows),
    // feed: Claude's notable events
    db.from("agent_events").select(EVENT_COLS).in("kind", [...NOTABLE_EVENT_KINDS]).order("ts", { ascending: false }).limit(limit),
    // newest Yahoo snapshots (private; the table may not exist until its migration is applied)
    intelDb
      ? intelDb.from("league_snapshots").select(SNAPSHOT_COLS).order("last_seen_at", { ascending: false, nullsFirst: false }).limit(40)
      : Promise.resolve({ data: [] as LeagueSnapshot[], error: null }),
    db.from("model_runs").select("kind,created_at").order("created_at", { ascending: false }).limit(60),
  ]);
  // defence in depth (and before the RLS migration is applied): no chat content without the unlock
  const visible = (rows: unknown) => ((rows as AgentEvent[]) ?? []).filter((e) => intelDb || !isPrivateEvent(e));

  // threads whose root is older than the window: one extra (rare) round trip
  let feedEvents = [...visible(repR.data), ...visible(notR.data)];
  const missing = missingRoots(feedEvents).slice(0, 100);
  if (missing.length) {
    const { data } = await db.from("agent_events").select(EVENT_COLS).in("event_id", missing);
    feedEvents = [...feedEvents, ...visible(data)];
  }
  const runs = (runsR.data as AgentRun[]) ?? [];
  const decisions = (decR.data as Decision[]) ?? [];
  const snapshots = latestPerKind((snapR.data as LeagueSnapshot[]) ?? []);
  const feed = buildFeed({ events: feedEvents, runs, snapshots, canSeePrivate: !!intelDb });
  const shown = filterFeed(feed, { author, kind });
  const windowFull = (repR.data?.length ?? 0) >= reportRows || (notR.data?.length ?? 0) >= limit;
  const moreHref = (windowFull || shown.length > limit) && limit < 300 ? chipHref({ author, kind, n: limit + 50 }) : null;

  const locks = (locksR.data as Lock[]) ?? [];
  const approvals = (apprR.data as Approval[]) ?? [];
  const pending = approvals.filter((a) => a.status === "pending");
  const now = Date.now();
  const stuck = runs.filter(
    (r) => !PASSIVE_AGENTS.has(r.agent) && r.status === "running" && (ageSeconds(r.heartbeat_at ?? r.started_at, now) ?? 0) > 600,
  );
  const expiredLocks = locks.filter((l) => l.expires_at && Date.parse(l.expires_at) < now);
  const latestByKind = new Map<string, string | null>();
  for (const r of (mrR.data as { kind: string; created_at: string | null }[]) ?? [])
    if (!latestByKind.has(r.kind)) latestByKind.set(r.kind, r.created_at);
  const errors = [runsR.error, decR.error, txnR.error, locksR.error, apprR.error, repR.error, notR.error]
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
        <AgentFeed
          items={shown}
          total={feed.length}
          limit={limit}
          decisions={new Map(decisions.map((d) => [d.decision_id, d]))}
          moreHref={moreHref}
        />
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted">System</h2>
        <HealthStrip latestByKind={latestByKind} now={now} />
        {/* the run timeline (every event of the last 40 runs) is the heaviest part of the page: only fetched and
            rendered on request (?timeline=1), then streamed after the rest */}
        <Suspense fallback={<div className="rounded-xl border border-line bg-panel px-4 py-3 text-sm text-muted">Agent runs (timeline) · loading…</div>}>
          <SystemRecords
            db={db}
            timelineHref={showTimeline ? null : timelineLink(author, kind, one(sp.n) ? limit : null)}
            showPrivate={!!intelDb}
            runs={runs}
            decisions={decisions}
            txns={(txnR.data as Transaction[]) ?? []}
            locks={locks}
            decided={approvals.filter((a) => a.status !== "pending")}
          />
        </Suspense>
      </section>
    </div>
  );
}

function timelineLink(author: string | null, kind: string | null, n: number | null): string {
  const href = chipHref({ author, kind, n });
  return `${href}${href.includes("?") ? "&" : "?"}timeline=1#runs`;
}

async function SystemRecords({
  db,
  timelineHref,
  showPrivate,
  runs,
  decisions,
  txns,
  locks,
  decided,
}: {
  db: SupabaseClient;
  /** Link that opens the timeline, or null to render it now. */
  timelineHref: string | null;
  showPrivate: boolean;
  runs: AgentRun[];
  decisions: Decision[];
  txns: Transaction[];
  locks: Lock[];
  decided: Approval[];
}) {
  const ids = runs.map((r) => r.run_id);
  const runIds = new Set(ids);
  const rest = (
    <>
      <LocksLine locks={locks} />
      <PastRecords
        decided={decided}
        orphanDecisions={decisions.filter((d) => !d.run_id || !runIds.has(d.run_id))}
        orphanTxns={txns.filter((t) => !t.run_id || !runIds.has(t.run_id))}
      />
    </>
  );
  if (timelineHref)
    return (
      <>
        <Link
          href={timelineHref}
          scroll={false}
          className="flex items-center gap-2 rounded-xl border border-line bg-panel px-4 py-3 hover:border-accent/50"
        >
          <span className="text-xs text-muted">▶</span>
          <span className="min-w-0 flex-1 text-[15px] font-medium">Agent runs (timeline)</span>
          <span className="shrink-0 text-xs text-muted">{runs.length} runs · every event, decision and transaction</span>
        </Link>
        {rest}
      </>
    );
  const evR = ids.length
    ? await db.from("agent_events").select(EVENT_COLS).in("run_id", ids).order("ts", { ascending: true }).limit(1000)
    : { data: [], error: null };
  const group = <T extends { run_id: string | null }>(rows: T[]) => {
    const m = new Map<string, T[]>();
    for (const r of rows) if (r.run_id) m.set(r.run_id, [...(m.get(r.run_id) ?? []), r]);
    return m;
  };
  const events = group(((evR.data as AgentEvent[]) ?? []).filter((e) => showPrivate || !isPrivateEvent(e)));
  return (
    <>
      {evR.error && <ErrorNote error={evR.error.message} />}
      <div id="runs" className="scroll-mt-16">
        <Disclosure summary="Agent runs (timeline)" right={`${runs.length}`} open>
          <RunTimeline runs={runs} events={events} decByRun={group(decisions)} txnByRun={group(txns)} />
        </Disclosure>
      </div>
      {rest}
    </>
  );
}
