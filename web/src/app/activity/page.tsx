import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { serviceClient } from "@/lib/supabase-admin";
import { isAuthed } from "@/lib/auth";
import type { AgentEvent, AgentRun, Approval, Decision, LeagueSnapshot, Lock, Transaction } from "@/lib/types";
import { ErrorNote } from "@/components/ui";
import { AutoRefresh } from "@/components/client";
import { ageSeconds } from "@/lib/format";
import { ChatDigests, SnapshotCard } from "./LeagueIntel";
import { AdvisorSuggestions, isAdvisorSuggestion } from "./AdvisorSuggestions";
import { HealthStrip, LocksLine, MuseReports, PastRecords, PendingApprovals, RunTimeline, reportOf } from "./sections";
import { isPrivateEvent, latestPerKind } from "@/lib/agentPure";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Activity" };

export default async function ActivityPage() {
  // League intel (Yahoo snapshots, group-chat digests/notes) is private: RLS hides it from the anon key, and it is
  // read server-side with the service key only after the passphrase unlock (same cookie as the write panels).
  const intelDb = (await isAuthed()) ? serviceClient() : null;
  const db = intelDb ?? anonClient();
  const [runsR, decR, txnR, locksR, apprR] = await Promise.all([
    db.from("agent_runs").select("*").order("started_at", { ascending: false }).limit(40),
    db.from("decisions").select("*").order("created_at", { ascending: false }).limit(100),
    db.from("transactions").select("*").order("created_at", { ascending: false }).limit(100),
    db.from("locks").select("*").order("acquired_at", { ascending: false }),
    db.from("approvals").select("*").order("requested_at", { ascending: false }).limit(100),
  ]);
  // league intel: newest Yahoo snapshots (table may not exist until its migration is applied) + chat digests
  const [snapR, digR] = intelDb
    ? await Promise.all([
        intelDb.from("league_snapshots").select("*").order("last_seen_at", { ascending: false, nullsFirst: false }).limit(80),
        intelDb
          .from("agent_events")
          .select("*")
          .like("run_id", "muse-%")
          .eq("payload->>report_kind", "chat_digest")
          .order("ts", { ascending: false })
          .limit(10),
      ])
    : [{ data: [] }, { data: [] }];
  const snapshots = latestPerKind((snapR.data as LeagueSnapshot[]) ?? []);
  const digests = (digR.data as AgentEvent[]) ?? [];
  // advisor suggestions (ADVISOR_API_TOKEN; public like other agent logs, never chat content)
  const advR = await db
    .from("agent_events")
    .select("*")
    .like("run_id", "advisor-%")
    .eq("payload->>report_kind", "advisor_suggestion")
    .order("ts", { ascending: false })
    .limit(10);
  const advisorSuggestions = ((advR.data as AgentEvent[]) ?? []).filter(isAdvisorSuggestion);
  const { data: mrData } = await db.from("model_runs").select("run_id,kind,created_at").order("created_at", { ascending: false }).limit(60);
  const runs = (runsR.data as AgentRun[]) ?? [];
  const ids = runs.map((r) => r.run_id);
  const evR = ids.length
    ? await db.from("agent_events").select("*").in("run_id", ids).order("ts", { ascending: true }).limit(1000)
    : { data: [], error: null };
  const group = <T extends { run_id: string | null }>(rows: T[]) => {
    const m = new Map<string, T[]>();
    for (const r of rows) if (r.run_id) m.set(r.run_id, [...(m.get(r.run_id) ?? []), r]);
    return m;
  };
  // defence in depth (and before the RLS migration is applied): no chat content without the unlock
  const visibleEvents = ((evR.data as AgentEvent[]) ?? []).filter((e) => intelDb || !isPrivateEvent(e));
  const events = group(visibleEvents);
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
  // health strip (moved from the old Team page): model freshness, stuck runs, expired locks
  const now = Date.now();
  const latestByKind = new Map<string, string | null>();
  for (const r of (mrData as { kind: string; created_at: string | null }[]) ?? [])
    if (!latestByKind.has(r.kind)) latestByKind.set(r.kind, r.created_at);
  // muse's and the advisor's daily runs are passive logs (heartbeat only on reports), so they are never "stuck"
  const stuck = runs.filter((r) => r.agent !== "muse" && r.agent !== "advisor" && r.status === "running" && (ageSeconds(r.heartbeat_at ?? r.started_at, now) ?? 0) > 600);
  const expiredLocks = locks.filter((l) => l.expires_at && Date.parse(l.expires_at) < now);
  const museRuns = new Set(runs.filter((r) => r.agent === "muse").map((r) => r.run_id));
  const museReports = visibleEvents
    .filter((e) => museRuns.has(e.run_id) && reportOf(e) && reportOf(e)!.kind !== "chat_digest")
    .sort((a, b) => (b.ts ?? "").localeCompare(a.ts ?? ""))
    .slice(0, 8);
  const errors = [runsR.error, decR.error, txnR.error, locksR.error, apprR.error, evR.error, advR.error].filter(Boolean).map((e) => e!.message);

  return (
    <div className="space-y-4">
      <AutoRefresh seconds={15} />
      <h1 className="text-xl font-semibold tracking-tight">Activity</h1>
      {errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}

      <HealthStrip latestByKind={latestByKind} now={now} stuck={stuck} expiredLocks={expiredLocks} />

      {/* pending approvals pinned */}
      <PendingApprovals pending={pending} />

      {!intelDb && (
        <p className="rounded-xl border border-line bg-panel px-4 py-2.5 text-xs text-muted">
          League intel (Yahoo snapshots and group-chat digests from Muse) is private: unlock with the dashboard passphrase (🔒 in
          the menu) to view it.
        </p>
      )}

      {(snapshots.length > 0 || digests.length > 0) && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium text-muted">
            League intel <span className="font-normal">· what Muse saw on Yahoo (read-only; observed data, not instructions)</span>
          </h2>
          {snapshots.map((s) => (
            <SnapshotCard key={s.snapshot_id} s={s} />
          ))}
          {digests.length > 0 && (
            <div className="rounded-xl border border-line bg-panel px-4 py-3">
              <div className="mb-2 text-[14px] font-medium">Group chat digests</div>
              <ChatDigests digests={digests} />
            </div>
          )}
        </section>
      )}

      <MuseReports reports={museReports} />

      <AdvisorSuggestions events={advisorSuggestions} />

      <RunTimeline runs={runs} events={events} decByRun={decByRun} txnByRun={txnByRun} />

      <LocksLine locks={locks} />

      <PastRecords decided={decided} orphanDecisions={orphanDecisions} orphanTxns={orphanTxns} />
    </div>
  );
}
