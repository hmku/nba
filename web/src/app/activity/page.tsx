import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import type { AgentEvent, AgentRun, Approval, Decision, Lock, Transaction } from "@/lib/types";
import { Badge, Disclosure, ErrorNote, JsonView, statusTone } from "@/components/ui";
import { Ago, AutoRefresh } from "@/components/client";
import { KindIcon } from "@/components/KindIcon";
import { ageSeconds, shortTime } from "@/lib/format";
import Link from "next/link";
import { DecideButtons } from "../approvals/DecideButtons";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Activity" };

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

const DOT: Record<string, string> = {
  accent: "bg-accent",
  good: "bg-good",
  bad: "bg-bad",
  warn: "bg-warn",
  neutral: "bg-line",
};

const REPORT_TONE: Record<string, string> = {
  draft_order: "bad",
  trade: "accent",
  rule: "warn",
  injury_news: "warn",
  schedule: "neutral",
  chat_note: "neutral",
  observed_pick: "good",
  other: "neutral",
};

/** Report metadata for events logged through /api/agent/report or /api/agent/pick (payload.report_kind). */
function reportOf(e: AgentEvent): { kind: string; source: string | null; details: unknown } | null {
  const p = e.payload;
  if (!p || typeof p !== "object" || Array.isArray(p)) return null;
  const o = p as Record<string, unknown>;
  if (typeof o.report_kind !== "string") return null;
  return { kind: o.report_kind, source: typeof o.source === "string" ? o.source : null, details: o.details ?? null };
}

function ReportBadges({ e }: { e: AgentEvent }) {
  const r = reportOf(e);
  if (!r) return null;
  return (
    <>
      <Badge tone={REPORT_TONE[r.kind] ?? "neutral"}>{r.kind.replace(/_/g, " ")}</Badge>
      {r.source && <Badge title="where Muse saw it">{r.source}</Badge>}
    </>
  );
}

function DecisionCard({ d }: { d: Decision }) {
  const dis = d.agrees_with_model === false;
  return (
    <div className={`rounded-lg border p-2.5 text-sm ${dis ? "border-warn/50 bg-warn/10" : "border-line"}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge>{d.kind}</Badge>
        {dis ? <Badge tone="warn">agent ≠ model</Badge> : d.agrees_with_model ? <Badge tone="good">agrees</Badge> : null}
        <span className="ml-auto text-muted">{shortTime(d.created_at)}</span>
      </div>
      <div className="mt-1.5 grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-muted">model</div>
          <div className="truncate">{short(d.model_recommendation)}</div>
        </div>
        <div>
          <div className="text-muted">chose</div>
          <div className={`truncate ${dis ? "font-semibold text-warn" : ""}`}>{short(d.choice)}</div>
        </div>
      </div>
      {d.rationale && <p className="mt-1 text-xs text-muted">{d.rationale}</p>}
    </div>
  );
}

export default async function ActivityPage() {
  const db = anonClient();
  const [runsR, decR, txnR, locksR, apprR] = await Promise.all([
    db.from("agent_runs").select("*").order("started_at", { ascending: false }).limit(40),
    db.from("decisions").select("*").order("created_at", { ascending: false }).limit(100),
    db.from("transactions").select("*").order("created_at", { ascending: false }).limit(100),
    db.from("locks").select("*").order("acquired_at", { ascending: false }),
    db.from("approvals").select("*").order("requested_at", { ascending: false }).limit(100),
  ]);
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
  const events = group((evR.data as AgentEvent[]) ?? []);
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
  const FRESH: [string, string, number][] = [
    ["projections", "projections", 36 * 3600],
    ["valuation", "values", 36 * 3600],
    ["draft_sim", "draft sim", 72 * 3600],
    ["season_sim", "season sim", 72 * 3600],
  ];
  // muse's daily run is a passive log (heartbeat only on reports), so it is never "stuck"
  const stuck = runs.filter((r) => r.agent !== "muse" && r.status === "running" && (ageSeconds(r.heartbeat_at ?? r.started_at, now) ?? 0) > 600);
  const expiredLocks = locks.filter((l) => l.expires_at && Date.parse(l.expires_at) < now);
  const museRuns = new Set(runs.filter((r) => r.agent === "muse").map((r) => r.run_id));
  const museReports = ((evR.data as AgentEvent[]) ?? [])
    .filter((e) => museRuns.has(e.run_id) && reportOf(e))
    .sort((a, b) => (b.ts ?? "").localeCompare(a.ts ?? ""))
    .slice(0, 8);
  const errors = [runsR.error, decR.error, txnR.error, locksR.error, apprR.error, evR.error].filter(Boolean).map((e) => e!.message);

  return (
    <div className="space-y-4">
      <AutoRefresh seconds={15} />
      <h1 className="text-xl font-semibold tracking-tight">Activity</h1>
      {errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}

      <Link href="/history" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-line bg-panel px-4 py-2.5 text-xs text-muted hover:border-accent/50">
        {FRESH.map(([k, label, limit]) => {
          const ts = latestByKind.get(k);
          const age = ageSeconds(ts, now);
          const stale = age == null ? k === "projections" || k === "valuation" : age > limit;
          return (
            <span key={k} className={stale ? "text-warn" : ""}>
              {stale ? "⚠ " : ""}
              {label} {ts ? <Ago ts={ts} /> : "none"}
            </span>
          );
        })}
        <span className="ml-auto text-muted">›</span>
      </Link>
      {(stuck.length > 0 || expiredLocks.length > 0) && (
        <div className="space-y-1 rounded-xl border border-warn/40 bg-warn/10 px-4 py-2.5 text-[13px] text-warn">
          {stuck.map((r) => (
            <div key={r.run_id}>
              ⚠ {r.agent} is &quot;running&quot; but its heartbeat is <Ago ts={r.heartbeat_at ?? r.started_at} /> — it may have died.
            </div>
          ))}
          {expiredLocks.length > 0 && (
            <div>
              ⚠ {expiredLocks.length} expired lock{expiredLocks.length === 1 ? "" : "s"} not released: {expiredLocks.map((l) => l.resource).join(", ")}
            </div>
          )}
        </div>
      )}

      {/* pending approvals pinned */}
      {pending.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium text-accent">Waiting for you · {pending.length}</h2>
          {pending.map((a) => {
            const expired = a.expires_at && Date.parse(a.expires_at) < Date.now();
            return (
              <div key={a.approval_id} className="rounded-xl border border-accent/50 bg-accent/5 px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="accent">{a.action_type}</Badge>
                  {expired && <Badge tone="warn">expired</Badge>}
                  <span className="ml-auto text-xs text-muted">
                    <Ago ts={a.requested_at} />
                  </span>
                </div>
                <div className="mt-1.5 text-[15px] font-medium leading-snug">{a.summary}</div>
                <details className="mt-1 text-xs text-muted">
                  <summary className="cursor-pointer select-none">details</summary>
                  <div className="mt-1 space-y-1">
                    <div>
                      id <code>{a.approval_id}</code>
                      {a.run_id && (
                        <>
                          {" "}
                          · run <code>{a.run_id}</code>
                        </>
                      )}
                      {a.expires_at && <> · expires {shortTime(a.expires_at)}</>}
                    </div>
                    <JsonView value={a.payload} label="payload" />
                  </div>
                </details>
                <DecideButtons id={a.approval_id} />
              </div>
            );
          })}
        </section>
      )}

      {museReports.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium text-muted">League intel from Muse</h2>
          <ul className="overflow-hidden rounded-xl border border-line bg-panel">
            {museReports.map((e) => {
              const r = reportOf(e)!;
              return (
                <li key={e.event_id} className="border-t border-line px-4 py-2.5 first:border-t-0">
                  <div className="flex flex-wrap items-center gap-1.5 text-xs">
                    <ReportBadges e={e} />
                    <span className="ml-auto text-muted">
                      <Ago ts={e.ts} />
                    </span>
                  </div>
                  <div className="mt-1 text-[14px] leading-snug whitespace-pre-wrap">{e.message}</div>
                  {r.details != null && <JsonView value={r.details} label="details" />}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* run timeline */}
      <section className="overflow-hidden rounded-xl border border-line bg-panel">
        {runs.length === 0 ? (
          <div className="px-5 py-6 text-center">
            <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-panel-2 text-lg text-muted">≋</div>
            <div className="mt-3 text-[15px] font-semibold">No agent activity yet</div>
            <div className="mx-auto mt-1.5 max-w-md text-left text-[13.5px] leading-relaxed text-muted">
              <p>This is the agents&apos; logbook. Once they start working (draft night, then daily in-season checks) you&apos;ll see:</p>
              <ul className="mt-2 space-y-1">
                <li>· <span className="text-fg">Runs</span> — one line per agent session (what it was doing, status, when); tap for its events.</li>
                <li>
                  · <span className="text-fg">Decisions vs the model</span> — what the model recommended and what the agent chose;
                  disagreements are tinted amber with the rationale.
                </li>
                <li>
                  · <span className="text-fg">Approvals</span> — add/drops and trades always wait for you here (Approve / Reject) before
                  anything changes on Yahoo.
                </li>
                <li>· <span className="text-fg">Transactions</span> — every Yahoo write, planned → submitted → verified.</li>
              </ul>
            </div>
          </div>
        ) : (
          <ul>
            {runs.map((r) => {
              const evs = events.get(r.run_id) ?? [];
              const decs = decByRun.get(r.run_id) ?? [];
              const tx = txnByRun.get(r.run_id) ?? [];
              const errs = evs.filter((e) => e.kind === "error").length;
              const disagree = decs.some((d) => d.agrees_with_model === false);
              return (
                <li key={r.run_id} className={`border-t border-line first:border-t-0 ${disagree ? "bg-warn/5" : ""}`}>
                  <details className="group">
                    <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 select-none [&::-webkit-details-marker]:hidden">
                      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[statusTone(r.status)]}`} title={r.status} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[15px]">
                          <span className="font-medium">{r.agent}</span>
                          {r.mode === "live" && <span className="ml-1.5 text-xs text-bad">live</span>}
                          {disagree && <span className="ml-1.5 text-xs text-warn">≠ model</span>}
                          {errs > 0 && <span className="ml-1.5 text-xs text-bad">{errs} err</span>}
                        </span>
                        <span className="block truncate text-xs text-muted">{r.task ?? "—"}</span>
                      </span>
                      <span className="shrink-0 text-right text-xs text-muted">
                        <span className="block">{r.status}</span>
                        <Ago ts={r.heartbeat_at ?? r.started_at} />
                      </span>
                    </summary>
                    <div className="space-y-2 border-t border-line bg-panel-2/40 px-4 py-3">
                      {r.summary && <p className="text-sm">{r.summary}</p>}
                      {decs.map((d) => (
                        <DecisionCard key={d.decision_id} d={d} />
                      ))}
                      {evs.length === 0 ? (
                        <div className="text-xs text-muted">No events.</div>
                      ) : (
                        <ol className="space-y-1.5 border-l border-line pl-3">
                          {evs.map((e) => (
                            <li key={e.event_id} className={`text-xs ${e.kind === "error" ? "text-bad" : ""}`}>
                              <div className="flex items-start gap-1.5">
                                <KindIcon kind={e.kind} />
                                <span className="num shrink-0 text-muted">{shortTime(e.ts)}</span>
                                <span className={`min-w-0 flex-1 ${e.kind === "decision" ? "font-medium" : ""}`}>
                                  {reportOf(e) && (
                                    <span className="mr-1.5 inline-flex gap-1 align-middle">
                                      <ReportBadges e={e} />
                                    </span>
                                  )}
                                  {e.message}
                                </span>
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
                      {tx.map((t) => (
                        <div key={t.txn_id} className="text-xs">
                          <Badge tone={statusTone(t.status)}>{t.status}</Badge> <span className="font-medium">{t.kind}</span>{" "}
                          <span className="text-muted">{shortTime(t.updated_at ?? t.created_at)}</span>
                          {t.error && <div className="text-bad">{t.error}</div>}
                          <JsonView value={t.payload} label="payload" />
                        </div>
                      ))}
                      <div className="flex flex-wrap gap-x-3 text-[11px] text-muted">
                        <span>
                          run <code>{r.run_id}</code>
                        </span>
                        <span>{r.mode ?? "?"}</span>
                        <span>started {shortTime(r.started_at)}</span>
                        {r.ended_at && <span>ended {shortTime(r.ended_at)}</span>}
                        {r.git_sha && <code>{r.git_sha.slice(0, 8)}</code>}
                        {r.session_ref?.startsWith("http") && (
                          <a href={r.session_ref} className="text-accent" target="_blank" rel="noreferrer">
                            session
                          </a>
                        )}
                      </div>
                    </div>
                  </details>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {locks.length > 0 && (
        <div className="px-1 text-xs text-muted">
          Locks held:{" "}
          {locks.map((l, i) => {
            const expired = l.expires_at && Date.parse(l.expires_at) < Date.now();
            return (
              <span key={l.resource} className={expired ? "text-warn" : ""}>
                {i > 0 && ", "}
                <code>{l.resource}</code> ({expired ? "expired" : `until ${shortTime(l.expires_at)}`})
              </span>
            );
          })}
        </div>
      )}

      {(decided.length > 0 || orphanDecisions.length > 0 || orphanTxns.length > 0) && (
        <Disclosure summary="Past approvals & other records" right={`${decided.length + orphanDecisions.length + orphanTxns.length}`}>
          <ul className="text-sm">
            {decided.map((a) => (
              <li key={a.approval_id} className="flex items-center gap-2 border-t border-line px-4 py-2.5 first:border-t-0">
                <Badge tone={statusTone(a.status)}>{a.status}</Badge>
                <span className="min-w-0 flex-1 truncate">{a.summary}</span>
                <span className="shrink-0 text-xs text-muted">{shortTime(a.decided_at)}</span>
              </li>
            ))}
          </ul>
          {orphanDecisions.length > 0 && (
            <div className="space-y-2 border-t border-line p-3">
              {orphanDecisions.map((d) => (
                <DecisionCard key={d.decision_id} d={d} />
              ))}
            </div>
          )}
          {orphanTxns.map((t) => (
            <div key={t.txn_id} className="border-t border-line px-4 py-2 text-xs">
              <Badge tone={statusTone(t.status)}>{t.status}</Badge> <span className="font-medium">{t.kind}</span>{" "}
              <span className="text-muted">{shortTime(t.updated_at ?? t.created_at)}</span>
              {t.error && <div className="text-bad">{t.error}</div>}
            </div>
          ))}
        </Disclosure>
      )}
    </div>
  );
}
