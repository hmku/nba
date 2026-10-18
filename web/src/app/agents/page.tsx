import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import type { AgentEvent, AgentRun, Decision, Lock, Transaction } from "@/lib/types";
import { Badge, Card, Empty, ErrorNote, JsonView, statusTone } from "@/components/ui";
import { Ago, AutoRefresh } from "@/components/client";
import { KindIcon, KIND_NAMES } from "@/components/KindIcon";
import { shortTime } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Agents" };

function short(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (typeof o.name === "string") return o.name;
    if (typeof o.player_id === "string") return o.player_id;
    return JSON.stringify(v).slice(0, 80);
  }
  return String(v);
}

export default async function AgentsPage() {
  const db = anonClient();
  const [runsR, decR, txnR, locksR] = await Promise.all([
    db.from("agent_runs").select("*").order("started_at", { ascending: false }).limit(40),
    db.from("decisions").select("*").order("created_at", { ascending: false }).limit(40),
    db.from("transactions").select("*").order("created_at", { ascending: false }).limit(40),
    db.from("locks").select("*").order("acquired_at", { ascending: false }),
  ]);
  const runs = (runsR.data as AgentRun[]) ?? [];
  const ids = runs.map((r) => r.run_id);
  const evR = ids.length
    ? await db.from("agent_events").select("*").in("run_id", ids).order("ts", { ascending: true }).limit(1000)
    : { data: [], error: null };
  const events = (evR.data as AgentEvent[]) ?? [];
  const byRun = new Map<string, AgentEvent[]>();
  for (const e of events) byRun.set(e.run_id, [...(byRun.get(e.run_id) ?? []), e]);
  const decisions = (decR.data as Decision[]) ?? [];
  const txns = (txnR.data as Transaction[]) ?? [];
  const locks = (locksR.data as Lock[]) ?? [];
  const errors = [runsR.error, decR.error, txnR.error, locksR.error, evR.error].filter(Boolean).map((e) => e!.message);

  return (
    <div className="space-y-3">
      <AutoRefresh seconds={15} />
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Agents</h1>
        <span className="flex flex-wrap gap-2 text-[11px] text-muted">
          {KIND_NAMES.map((k) => (
            <span key={k} className="flex items-center">
              <KindIcon kind={k} />
              {k}
            </span>
          ))}
        </span>
      </div>
      {errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <Card title="Run timeline" bodyClassName="p-0" right={<span>{runs.length} runs</span>}>
          {runs.length === 0 ? (
            <Empty>No agent runs.</Empty>
          ) : (
            <ul>
              {runs.map((r, i) => {
                const evs = byRun.get(r.run_id) ?? [];
                const errs = evs.filter((e) => e.kind === "error").length;
                return (
                  <li key={r.run_id} className="border-t border-line first:border-t-0">
                    <details open={i === 0 && r.status === "running"}>
                      <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm hover:bg-panel-2">
                        <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                        <span className="font-medium">{r.agent}</span>
                        <Badge tone={r.mode === "live" ? "bad" : "neutral"}>{r.mode ?? "?"}</Badge>
                        <span className="min-w-0 flex-1 truncate text-xs text-muted">{r.task}</span>
                        {errs > 0 && <Badge tone="bad">{errs} err</Badge>}
                        <span className="num text-xs text-muted">{evs.length} ev</span>
                        <span className="text-xs text-muted">{shortTime(r.started_at)}</span>
                      </summary>
                      <div className="border-t border-line bg-panel-2/50 px-3 py-2">
                        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                          <span>
                            run <code>{r.run_id}</code>
                          </span>
                          <span>
                            heartbeat <Ago ts={r.heartbeat_at} staleAfter={r.status === "running" ? 120 : undefined} />
                          </span>
                          {r.ended_at && <span>ended {shortTime(r.ended_at)}</span>}
                          {r.git_sha && (
                            <span>
                              sha <code>{r.git_sha.slice(0, 8)}</code>
                            </span>
                          )}
                          {r.session_ref && (
                            <a href={r.session_ref.startsWith("http") ? r.session_ref : undefined} className="text-accent" target="_blank" rel="noreferrer">
                              session
                            </a>
                          )}
                        </div>
                        {r.summary && <p className="mb-2 text-sm">{r.summary}</p>}
                        {evs.length === 0 ? (
                          <div className="text-xs text-muted">No events.</div>
                        ) : (
                          <ol className="relative space-y-1.5 border-l border-line pl-3">
                            {evs.map((e) => (
                              <li key={e.event_id} className={`text-xs ${e.kind === "error" ? "text-bad" : ""}`}>
                                <div className="flex items-start gap-1.5">
                                  <KindIcon kind={e.kind} />
                                  <span className="num shrink-0 text-muted">{shortTime(e.ts)}</span>
                                  <span className={`min-w-0 flex-1 ${e.kind === "decision" ? "font-medium" : ""}`}>{e.message}</span>
                                </div>
                                {e.payload != null && (
                                  <div className="pl-6">
                                    <JsonView value={e.payload} label="payload" />
                                  </div>
                                )}
                              </li>
                            ))}
                          </ol>
                        )}
                      </div>
                    </details>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <div className="space-y-3">
          <Card title="Locks" bodyClassName="p-0">
            {locks.length === 0 ? (
              <Empty>No locks held.</Empty>
            ) : (
              <table className="dense w-full text-sm">
                <thead>
                  <tr className="text-left">
                    <th>Resource</th>
                    <th>Holder</th>
                    <th className="text-right">Expires</th>
                  </tr>
                </thead>
                <tbody>
                  {locks.map((l) => (
                    <tr key={l.resource}>
                      <td>
                        <code className="text-accent">{l.resource}</code>
                        {l.purpose && <div className="text-[11px] text-muted">{l.purpose}</div>}
                      </td>
                      <td className="max-w-[8rem] truncate text-xs">{l.holder_run_id}</td>
                      <td className="text-right text-xs">
                        {l.expires_at && Date.parse(l.expires_at) < Date.now() ? <Badge tone="warn">expired</Badge> : shortTime(l.expires_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card title="Decisions" bodyClassName="p-0" right={<span>{decisions.filter((d) => d.agrees_with_model === false).length} disagreements</span>}>
            {decisions.length === 0 ? (
              <Empty>No decisions.</Empty>
            ) : (
              <ul>
                {decisions.map((d) => (
                  <li
                    key={d.decision_id}
                    className={`border-t border-line px-3 py-2 text-sm first:border-t-0 ${d.agrees_with_model === false ? "bg-warn/10" : ""}`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge>{d.kind}</Badge>
                      {d.agrees_with_model === false ? (
                        <Badge tone="warn">agent ≠ model</Badge>
                      ) : d.agrees_with_model ? (
                        <Badge tone="good">agrees</Badge>
                      ) : null}
                      <span className="ml-auto text-xs text-muted">{shortTime(d.created_at)}</span>
                    </div>
                    <div className="mt-1 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <div className="text-muted">model</div>
                        <div className="truncate">{short(d.model_recommendation)}</div>
                      </div>
                      <div>
                        <div className="text-muted">choice</div>
                        <div className={`truncate ${d.agrees_with_model === false ? "font-semibold text-warn" : ""}`}>{short(d.choice)}</div>
                      </div>
                    </div>
                    {d.rationale && <p className="mt-1 text-xs text-muted">{d.rationale}</p>}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Transactions" bodyClassName="p-0">
            {txns.length === 0 ? (
              <Empty>No transactions.</Empty>
            ) : (
              <ul>
                {txns.map((t) => (
                  <li key={t.txn_id} className="border-t border-line px-3 py-2 text-sm first:border-t-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={statusTone(t.status)}>{t.status}</Badge>
                      <span className="font-medium">{t.kind}</span>
                      <span className="ml-auto text-xs text-muted">{shortTime(t.updated_at ?? t.created_at)}</span>
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted">{JSON.stringify(t.payload)}</div>
                    {t.error && <div className="text-xs text-bad">{t.error}</div>}
                    {t.verification != null && <JsonView value={t.verification} label="verification" />}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
