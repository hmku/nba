import Link from "next/link";
import { anonClient } from "@/lib/supabase";
import { defaultDraftId, loadDrafts } from "@/lib/data";
import { picksForSlot, pickInfo } from "@/lib/draftOrder";
import type { AgentRun, Approval, DataSnapshot, DraftRecommendation, Lock, ModelRun, Task } from "@/lib/types";
import { Badge, Card, Empty, ErrorNote, Stat, statusTone } from "@/components/ui";
import { Ago, AutoRefresh } from "@/components/client";
import { ageSeconds } from "@/lib/format";

export const dynamic = "force-dynamic";

const FRESH_LIMIT: Record<string, number> = {
  projections: 36 * 3600,
  valuation: 36 * 3600,
  draft_rec: 3600,
};

export default async function Overview() {
  const db = anonClient();
  const { drafts, error: dErr } = await loadDrafts(db);
  const draftId = defaultDraftId(drafts);
  const draft = drafts.find((d) => d.draft_id === draftId) ?? null;

  const [picksR, recR, runsR, snapsR, agentR, locksR, apprR, tasksR] = await Promise.all([
    draft
      ? db.from("draft_picks").select("overall_pick", { count: "exact" }).eq("draft_id", draft.draft_id)
      : Promise.resolve({ data: [], count: 0, error: null }),
    draft
      ? db.from("draft_recommendations").select("rec_id,as_of_pick,for_pick,created_at,payload").eq("draft_id", draft.draft_id).order("as_of_pick", { ascending: false }).order("created_at", { ascending: false }).limit(1)
      : Promise.resolve({ data: [], error: null }),
    db.from("model_runs").select("run_id,kind,model_version,created_at,git_sha").order("created_at", { ascending: false }).limit(200),
    db.from("data_snapshots").select("snapshot_id,source,rows,fetched_at").order("fetched_at", { ascending: false }).limit(200),
    db.from("agent_runs").select("*").order("started_at", { ascending: false }).limit(8),
    db.from("locks").select("*"),
    db.from("approvals").select("*").eq("status", "pending").order("requested_at", { ascending: false }).limit(10),
    db.from("tasks").select("*").neq("status", "done").order("priority", { ascending: true }).order("updated_at", { ascending: false }).limit(40),
  ]);
  const errors = [dErr, picksR.error?.message, runsR.error?.message, snapsR.error?.message, agentR.error?.message].filter(Boolean) as string[];

  // freshness: latest run per kind
  const latestByKind = new Map<string, ModelRun>();
  for (const r of (runsR.data as ModelRun[]) ?? []) if (!latestByKind.has(r.kind)) latestByKind.set(r.kind, r);
  const latestBySource = new Map<string, DataSnapshot>();
  for (const s of (snapsR.data as DataSnapshot[]) ?? []) if (!latestBySource.has(s.source)) latestBySource.set(s.source, s);

  const picked = new Set(((picksR.data as { overall_pick: number }[]) ?? []).map((p) => p.overall_pick));
  const picksMade = picksR.count ?? picked.size;
  const currentPick = picked.size ? Math.max(...picked) + 1 : 1;
  const myNext = draft
    ? picksForSlot(draft.my_slot, draft.num_teams, draft.num_rounds, draft.order_type).find((p) => p >= currentPick)
    : undefined;
  const rec = ((recR.data as DraftRecommendation[]) ?? [])[0];
  const runs = (agentR.data as AgentRun[]) ?? [];
  const locks = (locksR.data as Lock[]) ?? [];
  const approvals = (apprR.data as Approval[]) ?? [];
  const tasks = (tasksR.data as Task[]) ?? [];

  // warnings
  const warnings: string[] = [];
  const now = Date.now();
  for (const k of ["projections", "valuation"]) {
    const r = latestByKind.get(k);
    if (!r) warnings.push(`No ${k} model run yet.`);
    else if ((ageSeconds(r.created_at, now) ?? 0) > FRESH_LIMIT[k]) warnings.push(`Latest ${k} run is older than ${FRESH_LIMIT[k] / 3600}h.`);
  }
  if (draft && draft.status === "live" && rec && rec.as_of_pick < picksMade)
    warnings.push(`Draft recommendation is stale (as of pick ${rec.as_of_pick}, ${picksMade} picks made).`);
  if (draft && !rec) warnings.push("No draft recommendation for the active draft yet.");
  for (const r of runs)
    if (r.status === "running" && (ageSeconds(r.heartbeat_at ?? r.started_at, now) ?? 0) > 600)
      warnings.push(`Agent run ${r.run_id} (${r.agent}) is running but heartbeat is >10m old.`);
  for (const l of locks)
    if (l.expires_at && Date.parse(l.expires_at) < now) warnings.push(`Lock ${l.resource} expired but not released (holder ${l.holder_run_id}).`);
  if (!drafts.some((d) => !d.is_mock)) warnings.push("No real (non-mock) draft row yet.");

  return (
    <div className="space-y-3">
      <AutoRefresh seconds={20} />
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Overview</h1>
        <span className="text-xs text-muted">auto-refresh 20s</span>
      </div>
      {errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}

      <div className="grid gap-3 lg:grid-cols-3">
        <Card
          title="Active draft"
          className="lg:col-span-2"
          right={draft ? <Link href={`/draft?id=${encodeURIComponent(draft.draft_id)}`} className="text-accent">open draft room →</Link> : null}
        >
          {!draft ? (
            <Empty>No drafts yet.</Empty>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{draft.draft_id}</span>
                <Badge tone={statusTone(draft.status)}>{draft.status}</Badge>
                {draft.is_mock && <Badge tone="warn">mock</Badge>}
                <Badge>{draft.order_type}</Badge>
                <span className="text-xs text-muted">
                  {draft.num_teams} teams · slot {draft.my_slot}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Picks made" value={`${picksMade}/${draft.num_teams * draft.num_rounds}`} />
                <Stat
                  label="On the clock"
                  value={currentPick <= draft.num_teams * draft.num_rounds ? `#${currentPick}` : "done"}
                  sub={currentPick <= draft.num_teams * draft.num_rounds ? `slot ${pickInfo(currentPick, draft.num_teams, draft.order_type).slot}` : undefined}
                />
                <Stat label="My next pick" value={myNext ? `#${myNext}` : "—"} sub={myNext ? `in ${myNext - currentPick} picks` : undefined} />
                <Stat
                  label="Latest rec"
                  value={rec ? `as of ${rec.as_of_pick}` : "none"}
                  sub={rec ? <Ago ts={rec.created_at} /> : undefined}
                />
              </div>
              {rec?.payload?.recommendations?.[0] && (
                <div className="text-sm">
                  <span className="text-muted">Top rec: </span>
                  <span className="font-medium">{rec.payload.recommendations[0].name}</span>
                  {rec.payload.recommendations[0].reason && <span className="text-muted"> — {rec.payload.recommendations[0].reason}</span>}
                </div>
              )}
            </div>
          )}
        </Card>

        <Card title="Warnings" right={<Badge tone={warnings.length ? "warn" : "good"}>{warnings.length}</Badge>}>
          {warnings.length === 0 ? (
            <Empty>All clear.</Empty>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {warnings.map((w, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-warn">⚠</span>
                  <span>{w}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <Card title="Model runs (latest by kind)" bodyClassName="p-0">
          {latestByKind.size === 0 ? (
            <Empty>No model runs.</Empty>
          ) : (
            <table className="dense w-full text-sm">
              <tbody>
                {[...latestByKind.values()].map((r) => (
                  <tr key={r.kind}>
                    <td className="font-medium">{r.kind}</td>
                    <td className="truncate text-xs text-muted">{r.model_version}</td>
                    <td className="text-right">
                      <Ago ts={r.created_at} staleAfter={FRESH_LIMIT[r.kind] ?? 86400} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Data snapshots (latest by source)" bodyClassName="p-0">
          {latestBySource.size === 0 ? (
            <Empty>No snapshots.</Empty>
          ) : (
            <table className="dense w-full text-sm">
              <tbody>
                {[...latestBySource.values()].slice(0, 12).map((s) => (
                  <tr key={s.source}>
                    <td className="max-w-[12rem] truncate font-medium" title={s.source}>
                      {s.source}
                    </td>
                    <td className="num text-right text-xs text-muted">{s.rows ?? "—"} rows</td>
                    <td className="text-right">
                      <Ago ts={s.fetched_at} staleAfter={3 * 86400} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Agents" right={<Link href="/agents" className="text-accent">all →</Link>} bodyClassName="p-0">
          {runs.length === 0 ? (
            <Empty>No agent runs.</Empty>
          ) : (
            <ul>
              {runs.map((r) => (
                <li key={r.run_id} className="flex items-center gap-2 border-t border-line px-3 py-1.5 text-sm first:border-t-0">
                  <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                  <span className="truncate font-medium">{r.agent}</span>
                  <span className="truncate text-xs text-muted">{r.task}</span>
                  <Ago ts={r.heartbeat_at ?? r.started_at} className="ml-auto shrink-0 text-xs text-muted" />
                </li>
              ))}
            </ul>
          )}
          {locks.length > 0 && (
            <div className="border-t border-line px-3 py-2">
              <div className="mb-1 text-[10.5px] uppercase tracking-wider text-muted">Held locks</div>
              {locks.map((l) => (
                <div key={l.resource} className="flex items-center gap-2 text-xs">
                  <code className="text-accent">{l.resource}</code>
                  <span className="truncate text-muted">{l.holder_run_id}</span>
                  <span className="ml-auto text-muted">
                    expires <Ago ts={l.expires_at} />
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card title="Pending approvals" right={<Link href="/approvals" className="text-accent">review →</Link>} bodyClassName="p-0">
          {approvals.length === 0 ? (
            <Empty>Nothing pending.</Empty>
          ) : (
            <ul>
              {approvals.map((a) => (
                <li key={a.approval_id} className="flex items-center gap-2 border-t border-line px-3 py-2 text-sm first:border-t-0">
                  <Badge tone="accent">{a.action_type}</Badge>
                  <span className="truncate">{a.summary}</span>
                  <Ago ts={a.requested_at} className="ml-auto shrink-0 text-xs text-muted" />
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Open tasks" right={<span>{tasks.length}</span>} bodyClassName="p-0">
          {tasks.length === 0 ? (
            <Empty>No open tasks.</Empty>
          ) : (
            <ul className="scroll-thin max-h-80 overflow-auto">
              {tasks.map((t) => (
                <li key={t.task_id} className="flex items-center gap-2 border-t border-line px-3 py-1.5 text-sm first:border-t-0">
                  <Badge tone={t.priority === 0 ? "bad" : t.priority === 1 ? "warn" : "neutral"}>P{t.priority ?? "?"}</Badge>
                  <span className="truncate">{t.title}</span>
                  <span className="ml-auto flex shrink-0 items-center gap-1">
                    {t.area && <span className="text-xs text-muted">{t.area}</span>}
                    <Badge tone={statusTone(t.status)}>{t.status}</Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
