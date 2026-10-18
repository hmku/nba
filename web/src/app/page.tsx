import Link from "next/link";
import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { defaultDraftId, loadDrafts } from "@/lib/data";
import { customRoundsOf, picksForSlot, pickInfo } from "@/lib/draftOrder";
import type { AgentRun, DataSnapshot, Lock, ModelRun } from "@/lib/types";
import { ErrorNote } from "@/components/ui";
import { Ago, AutoRefresh } from "@/components/client";
import { ageSeconds } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Team" };

const FRESH_LIMIT: Record<string, number> = {
  projections: 36 * 3600,
  valuation: 36 * 3600,
};
const SNAPSHOT_LIMIT = 3 * 86400;

function Row({ href, children, tone }: { href?: string; children: React.ReactNode; tone?: "warn" | "accent" }) {
  const cls = `flex items-center gap-3 px-4 py-3.5 ${tone === "warn" ? "text-warn" : ""}`;
  return (
    <li className="border-t border-line first:border-t-0">
      {href ? (
        <Link href={href} className={`${cls} hover:bg-panel-2`}>
          {children}
          <span className="ml-auto text-muted">›</span>
        </Link>
      ) : (
        <div className={cls}>{children}</div>
      )}
    </li>
  );
}

export default async function TeamPage() {
  const db = anonClient();
  const { drafts, error: dErr } = await loadDrafts(db);
  const draftId = defaultDraftId(drafts);
  const draft = drafts.find((d) => d.draft_id === draftId) ?? null;

  const [picksR, runsR, snapsR, agentR, locksR, apprR] = await Promise.all([
    draft
      ? db.from("draft_picks").select("overall_pick").eq("draft_id", draft.draft_id)
      : Promise.resolve({ data: [], error: null }),
    db.from("model_runs").select("run_id,kind,created_at").order("created_at", { ascending: false }).limit(50),
    db.from("data_snapshots").select("snapshot_id,source,fetched_at").order("fetched_at", { ascending: false }).limit(200),
    db.from("agent_runs").select("*").order("started_at", { ascending: false }).limit(5),
    db.from("locks").select("*"),
    db.from("approvals").select("approval_id", { count: "exact", head: true }).eq("status", "pending"),
  ]);
  const errors = [dErr, picksR.error?.message, runsR.error?.message, agentR.error?.message, apprR.error?.message].filter(Boolean) as string[];
  const now = Date.now();

  // active draft
  const picked = ((picksR.data as { overall_pick: number }[]) ?? []).map((p) => p.overall_pick);
  const currentPick = picked.length ? Math.max(...picked) + 1 : 1;
  const total = draft ? draft.num_teams * draft.num_rounds : 0;
  const custom = draft ? customRoundsOf(draft.config, draft.num_teams, draft.num_rounds) : null;
  const myNext = draft
    ? picksForSlot(draft.my_slot, draft.num_teams, draft.num_rounds, draft.order_type, custom).find((p) => p >= currentPick)
    : undefined;
  const cur = draft && currentPick <= total ? pickInfo(currentPick, draft.num_teams, draft.order_type, custom, draft.num_rounds) : null;
  const teamNames = (draft?.config?.team_names ?? {}) as Record<string, string>;

  // approvals
  const pending = apprR.count ?? 0;

  // agents
  const runs = (agentR.data as AgentRun[]) ?? [];
  const last = runs[0];
  const stuck = runs.filter((r) => r.status === "running" && (ageSeconds(r.heartbeat_at ?? r.started_at, now) ?? 0) > 600);
  const locks = (locksR.data as Lock[]) ?? [];
  const expiredLocks = locks.filter((l) => l.expires_at && Date.parse(l.expires_at) < now);

  // freshness
  const latestByKind = new Map<string, ModelRun>();
  for (const r of (runsR.data as ModelRun[]) ?? []) if (!latestByKind.has(r.kind)) latestByKind.set(r.kind, r);
  const latestBySource = new Map<string, DataSnapshot>();
  for (const s of (snapsR.data as DataSnapshot[]) ?? []) if (!latestBySource.has(s.source)) latestBySource.set(s.source, s);
  const staleItems: string[] = [];
  for (const k of ["projections", "valuation"]) {
    const r = latestByKind.get(k);
    if (!r) staleItems.push(`no ${k} run`);
    else if ((ageSeconds(r.created_at, now) ?? 0) > FRESH_LIMIT[k]) staleItems.push(`${k} >${FRESH_LIMIT[k] / 3600}h old`);
  }
  for (const s of latestBySource.values())
    if ((ageSeconds(s.fetched_at, now) ?? Infinity) > SNAPSHOT_LIMIT) staleItems.push(`${s.source} >3d old`);
  const valuation = latestByKind.get("valuation");

  return (
    <div className="space-y-4">
      <AutoRefresh seconds={20} />
      <h1 className="text-xl font-semibold tracking-tight">Team</h1>
      {errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}

      {/* active draft */}
      <Link
        href={draft ? `/draft?id=${encodeURIComponent(draft.draft_id)}` : "/draft"}
        className="block rounded-xl border border-line bg-panel px-4 py-4 hover:border-accent/50"
      >
        <div className="text-xs text-muted">
          {draft ? (
            <>
              {draft.is_mock ? "Mock draft" : "Draft"} · {draft.status}
            </>
          ) : (
            "Draft"
          )}
        </div>
        {!draft ? (
          <div className="mt-1 text-lg">No draft yet</div>
        ) : cur ? (
          <>
            <div className="mt-1 text-lg font-semibold">
              {picked.length === 0 && draft.status === "pending" ? "Not started" : `Pick #${currentPick} on the clock`}
              <span className="font-normal text-muted"> · {teamNames[String(cur.slot)] ?? `Team ${cur.slot}`}</span>
            </div>
            <div className="mt-0.5 text-[15px] text-muted">
              {myNext ? (
                <>
                  Your next pick <span className="font-medium text-accent">#{myNext}</span>
                  {myNext === currentPick ? " — now" : ` in ${myNext - currentPick}`}
                </>
              ) : (
                "No picks left"
              )}
            </div>
          </>
        ) : (
          <div className="mt-1 text-lg font-semibold text-good">Draft complete</div>
        )}
        <div className="mt-2 text-sm text-accent">Open draft room →</div>
      </Link>

      <ul className="rounded-xl border border-line bg-panel text-[15px]">
        <Row href="/activity" tone={pending ? "accent" : undefined}>
          <span className={`grid h-6 min-w-6 place-items-center rounded-full px-1.5 text-xs font-semibold ${pending ? "bg-accent text-accent-fg" : "bg-panel-2 text-muted"}`}>
            {pending}
          </span>
          <span>{pending ? `Approval${pending === 1 ? "" : "s"} waiting for you` : "No approvals pending"}</span>
        </Row>
        <Row href="/activity" tone={stuck.length ? "warn" : undefined}>
          <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${!last ? "bg-line" : last.status === "running" ? "bg-accent" : last.status === "failed" ? "bg-bad" : "bg-good"}`} />
          <span className="min-w-0 truncate">
            {last ? (
              <>
                {stuck.length ? "⚠ " : ""}
                <span className="font-medium">{last.agent}</span> <span className="text-muted">{last.status} · </span>
                <Ago ts={last.heartbeat_at ?? last.started_at} className="text-muted" />
              </>
            ) : (
              <span className="text-muted">No agent runs yet</span>
            )}
          </span>
        </Row>
        <Row href="/history" tone={staleItems.length ? "warn" : undefined}>
          <span className="w-2.5 shrink-0 text-center">{staleItems.length ? "⚠" : ""}</span>
          <span className="min-w-0 truncate">
            {staleItems.length ? (
              <>Stale data: {staleItems.join(", ")}</>
            ) : (
              <span className="text-muted">
                Data fresh · values <Ago ts={valuation?.created_at} />
              </span>
            )}
          </span>
        </Row>
        {expiredLocks.length > 0 && (
          <Row href="/activity" tone="warn">
            <span className="w-2.5 shrink-0 text-center">⚠</span>
            <span className="min-w-0 truncate">
              {expiredLocks.length} expired lock{expiredLocks.length === 1 ? "" : "s"} not released ({expiredLocks.map((l) => l.resource).join(", ")})
            </span>
          </Row>
        )}
      </ul>
    </div>
  );
}
