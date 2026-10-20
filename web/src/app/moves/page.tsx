import Link from "next/link";
import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { todayET } from "@/lib/sim";
import { shortTime } from "@/lib/format";
import {
  EVIDENCE_HELP,
  STATUS_LABEL,
  TYPE_LABEL,
  formatMetric,
  groupMoves,
  latestAdvice,
  linkHref,
  type Advice,
  type AdviceRow,
  type Cell,
  type Evidence,
} from "@/lib/moves";
import { Badge, Disclosure, ErrorNote, statusTone } from "@/components/ui";
import { Ago } from "@/components/client";
import { EmptyState, SectionTitle } from "@/components/viz";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Moves" };

const EVIDENCE_TONE: Record<Evidence, string> = {
  fitted: "good",
  simulated: "accent",
  prior: "warn",
  literature: "neutral",
};

const TYPE_TONE: Record<string, string> = {
  trade: "accent",
  add_drop: "warn",
  stream: "neutral",
  lineup: "good",
  il: "warn",
};

function statusBadgeTone(status: string): string {
  return status === "awaiting_yes" ? "accent" : status === "idea" ? "neutral" : statusTone(status);
}

function EvidenceChip({ e }: { e: Evidence | null }) {
  if (!e) return <Badge tone="bad" title="No evidence label: treat as unverified">unlabelled</Badge>;
  return (
    <Badge tone={EVIDENCE_TONE[e]} title={EVIDENCE_HELP[e]}>
      {e}
    </Badge>
  );
}

function fmtCell(v: Cell): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function MoveCard({ m, highlight = false }: { m: Advice; highlight?: boolean }) {
  return (
    <article className={`rounded-xl border px-4 py-3 ${highlight ? "border-accent/50 bg-accent/5" : "border-line bg-panel"}`}>
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Badge tone={TYPE_TONE[m.type] ?? "neutral"}>{TYPE_LABEL[m.type] ?? m.type}</Badge>
        <Badge tone={statusBadgeTone(m.status)}>{STATUS_LABEL[m.status] ?? m.status}</Badge>
        {m.requiresUserYes && m.status !== "awaiting_yes" && <Badge title="Policy: this move needs your explicit yes">needs yes</Badge>}
        {m.forDate && <span className="text-muted">for {m.forDate}</span>}
        {m.week != null && <span className="text-muted">week {m.week}</span>}
        <span className="ml-auto text-muted" title={m.createdAt ?? undefined}>
          <Ago ts={m.firstCreatedAt ?? m.createdAt} />
        </span>
      </div>
      <h3 className="mt-1.5 text-[15px] leading-snug font-semibold">{m.title}</h3>
      {m.headline && <p className="mt-0.5 text-[14px] leading-snug">{m.headline}</p>}
      {m.verdict && (
        <p className="mt-2 rounded-lg border border-line bg-panel-2/60 px-3 py-2 text-[13.5px] leading-snug whitespace-pre-line">
          <span className="mr-1 text-xs font-semibold tracking-wide text-muted uppercase">Verdict</span>
          {m.verdict}
        </p>
      )}

      {m.metrics.length > 0 && (
        <div className="scroll-thin mt-2 overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px] tracking-wide text-muted uppercase">
                <th className="py-1 pr-3 font-medium">Metric</th>
                <th className="py-1 pr-3 text-right font-medium">Value</th>
                <th className="py-1 font-medium">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {m.metrics.map((x, i) => (
                <tr key={i} className="border-t border-line align-top">
                  <td className="py-1.5 pr-3">
                    {x.label}
                    {x.note && <div className="text-xs text-muted">{x.note}</div>}
                  </td>
                  <td className="num py-1.5 pr-3 text-right whitespace-nowrap">{formatMetric(x)}</td>
                  <td className="py-1.5">
                    <EvidenceChip e={x.evidence} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {m.scenarios.length > 0 && (
        <div className="scroll-thin mt-2 overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-[12.5px]">
            <thead className="bg-panel-2/60">
              <tr className="text-left text-[11px] text-muted">
                {m.scenarioColumns.map((c) => (
                  <th key={c} className="px-2.5 py-1.5 font-medium whitespace-nowrap">
                    {c === "name" ? "Scenario" : c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {m.scenarios.map((r, i) => (
                <tr key={i} className="border-t border-line">
                  {m.scenarioColumns.map((c) => (
                    <td key={c} className={`px-2.5 py-1.5 ${c === "name" ? "" : "num whitespace-nowrap"}`}>
                      {fmtCell(r[c] ?? null)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {m.risks.length > 0 && (
        <div className="mt-2">
          <div className="text-xs font-semibold tracking-wide text-muted uppercase">Risks</div>
          <ul className="mt-1 space-y-1 text-[13px] leading-snug">
            {m.risks.map((r, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="text-warn">!</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
        {m.links.map((l) => {
          const { href, internal } = linkHref(l.path);
          return internal ? (
            <Link key={l.path} href={href} className="text-accent">
              {l.label}
            </Link>
          ) : (
            <a key={l.path} href={href} target="_blank" rel="noreferrer" className="text-accent">
              {l.label} ↗
            </a>
          );
        })}
        <span className="ml-auto">
          posted {shortTime(m.firstCreatedAt)}
          {m.versions > 1 && <> · updated {shortTime(m.createdAt)} (v{m.version})</>}
        </span>
        {m.approvalId && (
          <span>
            approval <code>{m.approvalId}</code>
          </span>
        )}
      </div>
    </article>
  );
}

function Section({ title, sub, items, highlight = false }: { title: string; sub?: string; items: Advice[]; highlight?: boolean }) {
  if (!items.length) return null;
  return (
    <section className="space-y-2">
      <SectionTitle right={sub}>
        {title} · {items.length}
      </SectionTitle>
      {items.map((m) => (
        <MoveCard key={m.adviceId} m={m} highlight={highlight} />
      ))}
    </section>
  );
}

export default async function MovesPage() {
  const db = anonClient();
  const { data, error } = await db
    .from("model_runs")
    .select("run_id,kind,created_at,summary")
    .eq("kind", "advice")
    .order("created_at", { ascending: false })
    .limit(500);
  const items = latestAdvice((data as AdviceRow[]) ?? []);
  const today = todayET();
  const g = groupMoves(items, today);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Moves</h1>
        <p className="mt-0.5 text-[13px] text-muted">
          What the agents recommend and why. Read-only: nothing here changes Yahoo. Say yes on the{" "}
          <Link href="/activity" className="text-accent">
            Activity
          </Link>{" "}
          tab (approvals) or in chat.
        </p>
      </div>
      <ErrorNote error={error?.message} />
      <div className="flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-muted">
        Evidence:
        {(Object.keys(EVIDENCE_TONE) as Evidence[]).map((e) => (
          <span key={e} title={EVIDENCE_HELP[e]}>
            <EvidenceChip e={e} />
          </span>
        ))}
        <span>± = Monte Carlo SE (not model error)</span>
      </div>

      {items.length === 0 ? (
        <EmptyState icon="⇄" title="No recommendations yet">
          <p>
            Trade ideas, add/drop and streaming suggestions, IL moves and daily lineup plans appear here, each with its numbers and where they
            come from (fitted, simulated, prior or literature).
          </p>
        </EmptyState>
      ) : (
        <>
          <Section title="Needs your yes" sub="add/drops and trades always wait for you" items={g.needsYes} highlight />
          <Section title="Today's lineup" sub={today} items={g.lineup} />
          <Section title="Ideas" items={g.ideas} />
          {g.closed.length > 0 && (
            <Disclosure summary="Done / rejected" right={`${g.closed.length}`}>
              <div className="space-y-2 p-3">
                {g.closed.map((m) => (
                  <MoveCard key={m.adviceId} m={m} />
                ))}
              </div>
            </Disclosure>
          )}
        </>
      )}
    </div>
  );
}
