import { Suspense } from "react";
import Link from "next/link";
import type { Metadata } from "next";
import type { SupabaseClient } from "@supabase/supabase-js";
import { anonClient } from "@/lib/supabase";
import { serviceClient } from "@/lib/supabase-admin";
import { isAuthed } from "@/lib/auth";
import type { AgentEvent, AgentRun, Approval, Decision, LeagueSnapshot, Lock, Transaction } from "@/lib/types";
import { Disclosure, ErrorNote } from "@/components/ui";
import { todayET } from "@/lib/sim";
import { latestAdvice, type AdviceRow } from "@/lib/moves";
import { groupInbox, joinDecisions, linkRowsOr, linkThreads, NOT_LINKABLE_IN, withoutLinked } from "@/lib/inbox";
import { AutoRefresh } from "@/components/client";
import { ageSeconds } from "@/lib/format";
import { isPrivateEvent, latestPerKind } from "@/lib/agentPure";
import { buildFeed, decisionIdOf, feedFacets, type FeedItem, filterFeed, isReply, NOTABLE_EVENT_KINDS, threadRootOf } from "@/lib/feed";
import {
  addFacets,
  assemblePage,
  canonTs,
  cmpFeed,
  type Cursor,
  encodeCursor,
  FEED_INDEX_COLS,
  FEED_PAGE_SIZE,
  type FeedIndexRow,
  feedHref,
  feedRowsOr,
  indexToEvent,
  isAuthor,
  parsePageMode,
  quoteIn,
  type Source,
} from "@/lib/feedPage";
import { AgentFeed, FeedFilters, FeedPager, RecentReplies } from "./Feed";
import { HealthStrip, LocksLine, PastRecords, RunTimeline, SystemWarnings } from "./sections";
import { ClosedDecisions, DecisionSection } from "./Decisions";
import { EvidenceLegend } from "./AdviceParts";

// approvals and the passphrase cookie make this page per-request
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Inbox" };

/** Agents whose daily runs are passive log containers (heartbeat only when they post): never "stuck". */
const PASSIVE_AGENTS = new Set(["muse", "advisor", "user", "claude"]);
// Column lists: only what the cards render (no select("*") on the big tables).
const EVENT_COLS = "event_id,run_id,ts,kind,message,payload";
const RUN_COLS = "run_id,agent,task,status,mode,git_sha,session_ref,summary,started_at,heartbeat_at,ended_at";
const DECISION_COLS = "decision_id,run_id,kind,model_recommendation,choice,agrees_with_model,rationale,created_at";
const TXN_COLS = "txn_id,run_id,kind,status,payload,error,created_at,updated_at";
const APPROVAL_COLS = "approval_id,run_id,action_type,summary,payload,status,note,requested_at,decided_at,expires_at";
const SNAPSHOT_COLS = "snapshot_id,ts,kind,week,observed_at,source_url,summary,last_seen_at,payload";
/** Newest feed rows (roots + replies, light columns) read for the filter counts and "who replied where". */
const FACET_ROWS = 1000;
const NEW_REPLIES_SHOWN = 5;
/** Newest replies read in full in round 1 (older ones are fetched by id when a page needs them). */
const RECENT_REPLIES = 100;
/** Newest advice versions read (each item shows its newest version) and candidate thread roots for the cards. */
const ADVICE_ROWS = 300;
const LINK_ROWS = 200;

type Rows = { data: unknown; error: { message: string } | null; count?: number | null };
const none: Promise<Rows> = Promise.resolve({ data: [], error: null, count: 0 });

/**
 * Inbox = the former Moves + Activity pages: decisions (recommendation + approval + discussion, once each), ideas, the
 * agent-updates feed (minus threads shown inside a decision card), done/rejected decisions, and the system records.
 */
export default async function InboxPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (v: string | string[] | undefined) => (typeof v === "string" && v ? v : null);
  const author = isAuthor(one(sp.author)) ? one(sp.author) : null;
  const kind = one(sp.kind)?.slice(0, 40) ?? null;
  const cursorParams = { before: one(sp.before), at: one(sp.at), after: one(sp.after) };
  const mode = parsePageMode(cursorParams);
  const c: Cursor | null = mode.dir === "first" ? null : mode.c;
  const showTimeline = one(sp.timeline) === "1";

  // League intel (Yahoo snapshots, group-chat digests/notes) is private: RLS hides it from the anon key, and it is
  // read server-side with the service key only after the passphrase unlock (same cookie as the write panels).
  const intelDb = (await isAuthed()) ? serviceClient() : null;
  const db = intelDb ?? anonClient();
  const canSeePrivate = !!intelDb;

  // Feed sources for this filter (run summaries are Claude's; the snapshot card is Muse's, newest page only).
  const wantEvents = kind !== "run summary" && kind !== "yahoo snapshots";
  const wantRuns = (!author || author === "claude") && (!kind || kind === "run summary");
  const wantSnaps = canSeePrivate && (!author || author === "muse") && (!kind || kind === "yahoo snapshots");
  // author filters match in JS (started OR replied in), so they read a wider light window; otherwise ~2 pages
  const evLimit = author ? 1000 : FEED_PAGE_SIZE * 2 + 10;
  const runLimit = FEED_PAGE_SIZE + 5;
  // without an author filter the ~2-page window is read with full columns, so the usual page needs no 2nd round
  const fullWindow = !author;
  const rootsOr = feedRowsOr({ canSeePrivate });
  const eventIndex = (dir: "older" | "newer"): PromiseLike<Rows> => {
    if (!wantEvents || (dir === "newer" && mode.dir !== "after")) return none;
    let q = db.from("agent_events").select(fullWindow ? EVENT_COLS : FEED_INDEX_COLS).or(rootsOr);
    if (kind)
      q = NOTABLE_EVENT_KINDS.includes(kind) ? q.eq("kind", kind).is("payload->>report_kind", null) : q.eq("payload->>report_kind", kind);
    if (c) q = dir === "older" ? q.lte("ts", c.ts) : q.gte("ts", c.ts);
    const asc = dir === "newer";
    return q.order("ts", { ascending: asc }).order("event_id", { ascending: asc }).limit(evLimit);
  };
  // finished, summarised runs of working agents (feed time = ended_at)
  const feedRuns = (cols: string, opts?: { count: "exact"; head: true }) =>
    db
      .from("agent_runs")
      .select(cols, opts)
      .not("summary", "is", null)
      .neq("status", "running")
      .not("agent", "in", `(${[...PASSIVE_AGENTS].join(",")})`)
      .not("ended_at", "is", null);
  const runIndex = (dir: "older" | "newer"): PromiseLike<Rows> => {
    if (!wantRuns || (dir === "newer" && mode.dir !== "after")) return none;
    let q = feedRuns(RUN_COLS);
    if (c) q = dir === "older" ? q.lte("ended_at", c.ts) : q.gte("ended_at", c.ts);
    const asc = dir === "newer";
    return q.order("ended_at", { ascending: asc }).order("run_id", { ascending: asc }).limit(runLimit);
  };

  // round 1: every independent query (system records, approvals, the feed's light index + facets) in ONE round trip
  const [runsR, decR, txnR, locksR, apprR, snapR, mrR, facetR, runCountR, evOldR, evNewR, runOldR, runNewR, recentRepliesR, adviceR, linkR] = await Promise.all([
    db.from("agent_runs").select(RUN_COLS).order("started_at", { ascending: false }).limit(40),
    db.from("decisions").select(DECISION_COLS).order("created_at", { ascending: false }).limit(100),
    db.from("transactions").select(TXN_COLS).order("created_at", { ascending: false }).limit(100),
    db.from("locks").select("*").order("acquired_at", { ascending: false }),
    db.from("approvals").select(APPROVAL_COLS).order("requested_at", { ascending: false }).limit(100),
    // newest Yahoo snapshots (private; the table may not exist until its migration is applied)
    wantSnaps && (mode.dir === "first" || mode.dir === "after")
      ? intelDb!.from("league_snapshots").select(SNAPSHOT_COLS).order("last_seen_at", { ascending: false, nullsFirst: false }).limit(40)
      : none,
    db.from("model_runs").select("kind,created_at").order("created_at", { ascending: false }).limit(60),
    // facets + "who replied where": the newest feed rows incl. replies, ids/kinds only
    db
      .from("agent_events")
      .select(FEED_INDEX_COLS)
      .or(feedRowsOr({ canSeePrivate, withReplies: true }))
      .order("ts", { ascending: false })
      .limit(FACET_ROWS),
    feedRuns("run_id", { count: "exact", head: true }),
    eventIndex("older"),
    eventIndex("newer"),
    runIndex("older"),
    runIndex("newer"),
    // the newest replies in full (threads on the usual page; anything older is fetched by id in round 2)
    db.from("agent_events").select(EVENT_COLS).eq("payload->>report_kind", "reply").order("ts", { ascending: false }).limit(RECENT_REPLIES),
    // decisions: recommendations (newest versions) and candidate thread roots that name an advice/approval id
    db.from("model_runs").select("run_id,kind,created_at,summary").eq("kind", "advice").order("created_at", { ascending: false }).limit(ADVICE_ROWS),
    db
      .from("agent_events")
      .select(EVENT_COLS)
      .or(linkRowsOr())
      .not("payload->>report_kind", "in", NOT_LINKABLE_IN)
      .order("ts", { ascending: false })
      .limit(LINK_ROWS),
  ]);
  // defence in depth (and before the RLS migration is applied): no chat content without the unlock
  const visible = (rows: unknown) => ((rows as AgentEvent[]) ?? []).filter((e) => canSeePrivate || !isPrivateEvent(e));
  const indexEvents = (rows: unknown) => visible(((rows as FeedIndexRow[]) ?? []).map(indexToEvent));

  // decision cards: advice ⋈ approvals (both directions), threads linked by explicit id or mention (lib/inbox.ts)
  const approvals = (apprR.data as Approval[]) ?? [];
  const cards = joinDecisions(latestAdvice((adviceR.data as AdviceRow[]) ?? []), approvals);
  const groups = groupInbox(cards, todayET());
  const linkEvents = visible(linkR.data);
  const { byCard, linked } = linkThreads(cards, linkEvents);

  // threads shown inside a decision card are left out of the feed (and its counts)
  const facetFeed = withoutLinked(buildFeed({ events: indexEvents(facetR.data), canSeePrivate, order: "posted" }), linked);
  const repliedIn = new Set(author ? facetFeed.filter((it) => it.replies.some((r) => r.author === author)).map((it) => it.key) : []);
  const matches = (it: FeedItem) =>
    !linked.has(it.key) && (!kind || it.kind === kind) && (!author || it.author === author || repliedIn.has(it.key));
  const asEvents = (rows: unknown) => (fullWindow ? visible(rows) : indexEvents(rows));
  const evSource = (rows: unknown): Source<FeedItem> => {
    const raw = (rows as { event_id: string; ts: string | null }[]) ?? [];
    const last = raw.length >= evLimit ? raw.at(-1) : null;
    return {
      items: buildFeed({ events: asEvents(raw), canSeePrivate, order: "posted" }).filter(matches),
      truncatedAt: last ? { ts: last.ts ?? "", key: last.event_id } : null,
    };
  };
  const runSource = (rows: unknown): Source<FeedItem> => {
    const raw = (rows as AgentRun[]) ?? [];
    const last = raw.length >= runLimit ? raw.at(-1) : null;
    return {
      items: buildFeed({ events: [], runs: raw }).filter(matches),
      truncatedAt: last ? { ts: last.ended_at ?? "", key: `run:${last.run_id}` } : null,
    };
  };
  const page = assemblePage({
    mode,
    older: [evSource(evOldR.data), runSource(runOldR.data)],
    newer: mode.dir === "after" ? [evSource(evNewR.data), runSource(runNewR.data)] : [],
  });

  // newest page: replies newer than its oldest update on threads further down (a reply never moves its thread)
  const onPage = new Set(page.items.map((it) => it.key));
  const oldest = canonTs(page.items.at(-1)?.ts) ?? "";
  const newReplies =
    page.isFirst && page.older
      ? filterFeed(facetFeed, { author, kind })
          .filter((it) => !onPage.has(it.key))
          .flatMap((root) => root.replies.map((r) => ({ root, id: r.event.event_id, ts: canonTs(r.event.ts) ?? "" })))
          .filter((r) => r.ts > oldest)
          .sort((a, b) => b.ts.localeCompare(a.ts))
      : [];

  // Full rows already read: the window (no author filter) and the newest replies. Round 2 runs only for what is
  // missing: roots read as light rows, older replies of the page's threads, older decision cards.
  const fullById = new Map<string, AgentEvent>();
  const putFull = (rows: unknown) => {
    for (const e of visible(rows)) fullById.set(e.event_id, e);
  };
  if (fullWindow) [evOldR.data, evNewR.data].forEach(putFull);
  putFull(recentRepliesR.data);
  const rootIds = page.items.filter((it) => it.event).map((it) => it.key);
  const facetByKey = new Map(facetFeed.map((it) => [it.key, it]));
  const facetRows = (facetR.data as FeedIndexRow[]) ?? [];
  // the facet index lists every reply newer than its oldest row; a deeper page's threads are read by root instead
  const facetCovers =
    facetRows.length < FACET_ROWS || (canonTs(page.items.at(-1)?.ts) ?? "") >= (canonTs(facetRows.at(-1)?.ts) ?? "");
  const replyIds = rootIds.flatMap((k) => (facetByKey.get(k)?.replies ?? []).map((r) => r.event.event_id));
  // replies on the decision cards' threads (the facet index knows which; full rows come from round 1 or by id)
  const linkedById = new Map(linkEvents.map((e) => [e.event_id, e]));
  const cardReplyIds = indexEvents(facetR.data)
    .filter((e) => isReply(e) && linked.has(threadRootOf(e) ?? ""))
    .map((e) => e.event_id);
  const missingIds = [...rootIds, ...replyIds, ...cardReplyIds, ...newReplies.slice(0, NEW_REPLIES_SHOWN).map((r) => r.id)].filter(
    (id) => !fullById.has(id),
  );
  const decisions = new Map(((decR.data as Decision[]) ?? []).map((d) => [d.decision_id, d]));
  const needDecisions = page.items
    .map((it) => (it.event ? decisionIdOf(it.event) : null))
    .filter((d): d is string => !!d && !decisions.has(d));
  const [fullR, repliesR, moreDecR] = await Promise.all([
    missingIds.length ? db.from("agent_events").select(EVENT_COLS).in("event_id", [...new Set(missingIds)]) : none,
    rootIds.length && !facetCovers
      ? db
          .from("agent_events")
          .select(EVENT_COLS)
          .eq("payload->>report_kind", "reply")
          .or(`payload->>thread_root.in.(${quoteIn(rootIds)}),payload->>reply_to.in.(${quoteIn(rootIds)})`)
          .order("ts", { ascending: true })
          .limit(1000)
      : none,
    needDecisions.length ? db.from("decisions").select(DECISION_COLS).in("decision_id", needDecisions) : none,
  ]);
  for (const d of (moreDecR.data as Decision[]) ?? []) decisions.set(d.decision_id, d);
  putFull(fullR.data);
  putFull(repliesR.data);
  const full = [...fullById.values()];
  const cardFeed = new Map(
    buildFeed({ events: [...linkedById.values(), ...full.filter(isReply)], canSeePrivate, order: "posted" }).map((it) => [it.key, it]),
  );
  const threadsOf = (key: string) => (byCard.get(key) ?? []).map((id) => cardFeed.get(id)).filter((it): it is FeedItem => !!it);
  const snapshots = page.isFirst ? latestPerKind((snapR.data as LeagueSnapshot[]) ?? []) : [];
  const shown = buildFeed({
    events: full.filter((e) => onPage.has(e.event_id) || isReply(e)),
    runs: page.items.filter((it) => it.run).map((it) => it.run!),
    snapshots,
    canSeePrivate,
    order: "posted",
  })
    .filter((it) => onPage.has(it.key) || it.type === "snapshots")
    .sort(cmpFeed);
  const replyStrip = newReplies.slice(0, NEW_REPLIES_SHOWN).flatMap((r) => {
    const event = fullById.get(r.id);
    const href = feedHref({ author, kind, at: encodeCursor({ ts: r.root.ts, key: r.root.key }) }, r.root.key);
    return event ? [{ event, root: r.root, href }] : [];
  });

  const runCount = runCountR.count ?? 0;
  const facets = addFacets(feedFacets(facetFeed), [
    { author: "claude", kind: "run summary", n: runCount },
    { author: "muse", kind: "yahoo snapshots", n: snapshots.length ? 1 : 0 },
  ]);
  const href = (p: { before?: string | null; after?: string | null }) => feedHref({ author, kind, ...p });
  const pager = {
    newerHref: page.newer ? href({ after: page.newer }) : null,
    olderHref: page.older ? href({ before: page.older }) : null,
    newestHref: page.isFirst ? null : href({}),
  };

  const runs = (runsR.data as AgentRun[]) ?? [];
  const locks = (locksR.data as Lock[]) ?? [];
  const now = Date.now();
  const stuck = runs.filter(
    (r) => !PASSIVE_AGENTS.has(r.agent) && r.status === "running" && (ageSeconds(r.heartbeat_at ?? r.started_at, now) ?? 0) > 600,
  );
  const expiredLocks = locks.filter((l) => l.expires_at && Date.parse(l.expires_at) < now);
  const latestByKind = new Map<string, string | null>();
  for (const r of (mrR.data as { kind: string; created_at: string | null }[]) ?? [])
    if (!latestByKind.has(r.kind)) latestByKind.set(r.kind, r.created_at);
  const errors = [runsR, decR, txnR, locksR, apprR, facetR, evOldR, evNewR, runOldR, runNewR, fullR, repliesR, adviceR, linkR]
    .map((r) => r.error?.message)
    .filter((m): m is string => !!m);

  const decisionProps = { threadsOf, decisions, now };
  const waiting = groups.needsYes.length;

  return (
    <div className="space-y-4">
      <AutoRefresh seconds={15} />
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Inbox</h1>
        {page.isFirst && cards.length > 0 && <EvidenceLegend />}
      </div>
      {errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}

      <SystemWarnings stuck={stuck} expiredLocks={expiredLocks} />

      {/* decisions on the newest feed page only (older feed pages are for reading history) */}
      {page.isFirst ? (
        <>
          <DecisionSection
            title="Needs your yes"
            sub="Approve = your yes; the next daily run executes it after re-checking Yahoo."
            cards={groups.needsYes}
            variant="yes"
            empty="Nothing needs your yes right now."
            {...decisionProps}
          />
          <DecisionSection title="Approved · queued" cards={groups.queued} variant="queued" {...decisionProps} />
          <DecisionSection title="Ideas" cards={groups.ideas} variant="idea" {...decisionProps} />
        </>
      ) : (
        waiting > 0 && (
          <Link href="/inbox" className="block rounded-xl border border-accent/50 bg-accent/5 px-4 py-2.5 text-[13px] text-accent">
            {waiting} decision{waiting === 1 ? "" : "s"} need{waiting === 1 ? "s" : ""} your yes → newest page
          </Link>
        )
      )}

      <section id="feed" className="scroll-mt-16 space-y-2">
        <h2 className="text-sm font-medium text-muted">
          Agent updates
          {!intelDb && <span className="font-normal"> · 🔒 unlock to see Muse&apos;s private snapshots and chat digests</span>}
        </h2>
        <FeedFilters facets={facets} approx={((facetR.data as unknown[]) ?? []).length >= FACET_ROWS} author={author} kind={kind} />
        {!page.isFirst && <FeedPager {...pager} top />}
        {replyStrip.length > 0 && <RecentReplies rows={replyStrip} more={newReplies.length - replyStrip.length} />}
        <AgentFeed items={shown} anyUpdates={facetFeed.length + runCount > 0} decisions={decisions} />
        <FeedPager {...pager} />
      </section>

      {page.isFirst && <ClosedDecisions cards={groups.closed} {...decisionProps} />}

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted">System</h2>
        <HealthStrip latestByKind={latestByKind} now={now} />
        {/* the run timeline (every event of the last 40 runs) is the heaviest part of the page: only fetched and
            rendered on request (?timeline=1), then streamed after the rest */}
        <Suspense fallback={<div className="rounded-xl border border-line bg-panel px-4 py-3 text-sm text-muted">Agent runs (timeline) · loading…</div>}>
          <SystemRecords
            db={db}
            timelineHref={showTimeline ? null : timelineLink(feedHref({ author, kind, ...cursorParams }))}
            showPrivate={canSeePrivate}
            runs={runs}
            decisions={(decR.data as Decision[]) ?? []}
            txns={(txnR.data as Transaction[]) ?? []}
            locks={locks}
            decided={approvals.filter((a) => a.status !== "pending")}
          />
        </Suspense>
      </section>
    </div>
  );
}

function timelineLink(href: string): string {
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
