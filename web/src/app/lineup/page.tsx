import Link from "next/link";
import type { Metadata } from "next";
import { anonClient, cachedClient } from "@/lib/supabase";
import { todayET } from "@/lib/sim";
import { latestAdvice, type AdviceRow } from "@/lib/moves";
import {
  buildTimeline,
  buildWeek,
  daysBetween,
  parseActual,
  parsePlan,
  pickWeek,
  weekSummary,
  type AdviceLite,
  type ApprovalRow,
  type DecisionRow,
  type LeagueWeek,
  type LineupActual,
  type LineupPlan,
  type RunRow,
  type TimelineItem,
  type TxnRow,
} from "@/lib/lineup";
import leagueWeeks from "@/content/league_weeks.json";
import { shortTime } from "@/lib/format";
import { Badge, ErrorNote, statusTone } from "@/components/ui";
import { EmptyState, Panel, SectionTitle } from "@/components/viz";
import { LineupGrid, type DayView } from "./LineupGrid";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Lineup" };

const RUN_COLS = "run_id,kind,created_at,summary";
const TXN_COLS = "txn_id,run_id,kind,status,payload,verification,error,approval_id,created_at,updated_at";
const APPROVAL_COLS = "approval_id,action_type,summary,payload,status,decided_by,note,requested_at,decided_at";
const DECISION_COLS = "decision_id,run_id,kind,choice,agrees_with_model,rationale,created_at";
const LINEUP_KINDS = ["lineup_plan", "lineup_actual"];

const WEEKS = (leagueWeeks.weeks ?? []) as LeagueWeek[];

/** ISO midnight UTC of `day` shifted by `k` days (query windows; the timeline filters by ET date itself). */
function shift(day: string, k: number): string {
  const t = new Date(`${day}T00:00:00Z`);
  t.setUTCDate(t.getUTCDate() + k);
  return t.toISOString();
}

function dayLabel(d: string): string {
  const t = new Date(`${d}T12:00:00Z`);
  return t.toLocaleDateString("en-US", { weekday: "short", day: "numeric", timeZone: "UTC" });
}

function fmtDate(d: string): string {
  const t = new Date(`${d}T12:00:00Z`);
  return t.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export default async function LineupPage({ searchParams }: { searchParams: Promise<{ week?: string }> }) {
  const { week: wq } = await searchParams;
  const today = todayET();
  const w = pickWeek(WEEKS, today, wq ? Number(wq) : null);
  if (!w)
    return (
      <div className="space-y-3">
        <h1 className="text-xl font-semibold tracking-tight">Lineup</h1>
        <EmptyState icon="☰" title="No league schedule loaded">
          <p>Regenerate web/src/content/league_weeks.json with node scripts/league-weeks.mjs.</p>
        </EmptyState>
      </div>
    );
  const days = daysBetween(w.start, w.end);
  const past = days.filter((d) => d < today);
  const live = days.filter((d) => d >= today);
  const db = anonClient();
  const from = shift(w.start, -2);
  const to = shift(w.end, 2);
  const none = Promise.resolve({ data: [] as RunRow[], error: null });
  // one round trip: past days' plans/actuals are effectively immutable -> Data Cache (5 min); today/future live
  const [pastR, liveR, txnR, apprR, advR, decR] = await Promise.all([
    past.length
      ? cachedClient(300)
          .from("model_runs")
          .select(RUN_COLS)
          .in("kind", LINEUP_KINDS)
          .gte("summary->>date", past[0])
          .lte("summary->>date", past[past.length - 1])
          .order("created_at", { ascending: false })
          .limit(200)
      : none,
    live.length
      ? db
          .from("model_runs")
          .select(RUN_COLS)
          .in("kind", LINEUP_KINDS)
          .gte("summary->>date", live[0])
          .lte("summary->>date", live[live.length - 1])
          .order("created_at", { ascending: false })
          .limit(200)
      : none,
    db.from("transactions").select(TXN_COLS).gte("created_at", from).lte("created_at", to).order("created_at", { ascending: false }).limit(200),
    db.from("approvals").select(APPROVAL_COLS).gte("requested_at", from).lte("requested_at", to).order("requested_at", { ascending: false }).limit(100),
    db.from("model_runs").select(RUN_COLS).eq("kind", "advice").gte("created_at", shift(w.start, -7)).lte("created_at", to).order("created_at", { ascending: false }).limit(300),
    db.from("decisions").select(DECISION_COLS).gte("created_at", from).lte("created_at", to).order("created_at", { ascending: false }).limit(200),
  ]);
  const error = [pastR, liveR, txnR, apprR, advR, decR].map((r) => r.error?.message).find(Boolean) ?? null;
  const rows = [...((pastR.data as RunRow[]) ?? []), ...((liveR.data as RunRow[]) ?? [])];
  const plans = rows.map(parsePlan).filter((p): p is LineupPlan => p != null);
  const actuals = rows.map(parseActual).filter((x): x is LineupActual => x != null);
  const grid = buildWeek(days, plans, actuals);
  const ws = weekSummary(grid);
  const advice: AdviceLite[] = latestAdvice((advR.data as AdviceRow[]) ?? []).map((x) => ({
    adviceId: x.adviceId,
    type: x.type,
    title: x.title,
    status: x.status,
    headline: x.headline,
    createdAt: x.createdAt,
    firstCreatedAt: x.firstCreatedAt,
    forDate: x.forDate,
    week: x.week,
    rationale: x.rationale.map((r) => (r.bullet ? `• ${r.text}` : r.text)),
    whyNow: x.whyNow ? [x.whyNow.before, ...x.whyNow.changed.map((c) => `→ ${c.text}`)].join("\n") : null,
  }));
  const timeline = buildTimeline(
    {
      txns: (txnR.data as TxnRow[]) ?? [],
      approvals: (apprR.data as ApprovalRow[]) ?? [],
      advice,
      decisions: (decR.data as DecisionRow[]) ?? [],
      names: playerNames(plans, actuals),
    },
    w,
  );
  const views: DayView[] = grid.map((g) => ({
    date: g.date,
    label: dayLabel(g.date),
    isToday: g.date === today,
    rows: g.rows,
    summary: g.summary,
    mismatches: g.mismatches,
    plan: g.plan
      ? {
          runId: g.plan.runId,
          date: g.plan.date,
          sameDay: g.planSameDay,
          createdAt: g.plan.createdAt,
          modelVersion: g.plan.modelVersion,
          tipoff: g.planSameDay ? g.plan.tipoff : [],
          ilMoves: g.planSameDay ? g.plan.ilMoves : [],
          benchWithGame: g.planSameDay ? g.plan.benchWithGame : [],
          expected: g.plan.expected,
          warnings: g.planSameDay ? g.plan.warnings : [],
        }
      : null,
    actual: g.actual
      ? { runId: g.actual.runId, source: g.actual.source, observedAt: g.actual.observedAt, how: g.actual.how, txnId: g.actual.txnId, complete: g.actual.complete }
      : null,
  }));
  const initial = Math.max(0, days.indexOf(today));
  const opp = w.opponent_name ?? (w.opponent != null ? `Team ${w.opponent}` : null);
  const empty = plans.length === 0 && actuals.length === 0;
  return (
    <div className="space-y-3">
      <WeekStrip selected={w.week} current={pickWeek(WEEKS, today)?.week ?? null} />
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Lineup</h1>
        <p className="text-[13px] text-muted">
          Week {w.week} · {fmtDate(w.start)} – {fmtDate(w.end)}
          {opp ? ` · vs ${opp}` : ""}
        </p>
      </div>
      <ErrorNote error={error} />
      <Panel className="flex flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2.5 text-[13px]">
        <span>
          Starts <span className="num font-semibold">{ws.started}</span>/<span className="num">{ws.available}</span>
          <span className="ml-1 text-xs text-muted">used / possible{ws.daysWithData < days.length ? ` (${ws.daysWithData} of ${days.length} days with data)` : ""}</span>
        </span>
        <span className={ws.unused ? "text-warn" : "text-muted"}>
          <span className="num font-semibold">{ws.unused}</span> unused start{ws.unused === 1 ? "" : "s"}
        </span>
        {ws.mismatchDays > 0 && <span className="text-warn">⚠ {ws.mismatchDays} day{ws.mismatchDays > 1 ? "s" : ""} differ from the plan</span>}
        {!ws.known && <span className="text-xs text-muted">some games unknown</span>}
        <span className="ml-auto text-xs text-muted">✓ on Yahoo · ◌ planned · ⚠ mismatch</span>
      </Panel>
      {empty ? (
        <EmptyState icon="☰" title={`Nothing recorded for week ${w.week} yet`}>
          <p>
            Plans appear when the daily routine runs <code className="num">fantasy lineup plan --save</code>; what Yahoo actually shows comes from{" "}
            <code className="num">fantasy league sync --write --record-lineup</code> and <code className="num">fantasy lineup record</code> after each
            verified change.
          </p>
        </EmptyState>
      ) : (
        <LineupGrid days={views} initial={initial} />
      )}
      <Timeline items={timeline} week={w.week} />
    </div>
  );
}

/** player_id -> name from the week's plans and Yahoo reads (txn `changes` often carry only ids). */
function playerNames(plans: LineupPlan[], actuals: LineupActual[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const x of actuals) for (const e of x.slots) if (e.playerId && e.name) out[e.playerId] = e.name;
  for (const p of plans) for (const [pid, info] of Object.entries(p.players)) out[pid] = info.name;
  return out;
}

function WeekStrip({ selected, current }: { selected: number; current: number | null }) {
  return (
    <div className="scroll-thin -mx-4 overflow-x-auto px-4">
      <div className="flex gap-1.5 pb-1">
        {WEEKS.map((x) => (
          <Link
            key={x.week}
            href={`/lineup?week=${x.week}`}
            scroll={false}
            className={`flex w-12 shrink-0 flex-col items-center rounded-lg border py-1 ${x.week === selected ? "border-accent bg-accent/10" : "border-line bg-panel"}`}
          >
            <span className={`text-[10.5px] ${x.week === current ? "font-semibold text-accent" : "text-muted"}`}>W{x.week}</span>
            <span className="num text-[11px] text-muted">{fmtDate(x.start).replace(/^\w+ /, "")}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  lineup: "lineup",
  il: "IL",
  add_drop: "add/drop",
  trade: "trade",
  stream: "stream",
  approval: "approval",
  decision: "decision",
  other: "other",
};

function Timeline({ items, week }: { items: TimelineItem[]; week: number }) {
  return (
    <>
      <SectionTitle
        right={
          <Link href="/activity" className="text-accent">
            all activity →
          </Link>
        }
      >
        Decisions · week {week}
      </SectionTitle>
      <Panel>
        {items.length === 0 ? (
          <p className="px-4 py-3 text-sm text-muted">No lineup transactions, approvals, recommendations or logged decisions this week.</p>
        ) : (
          <ul className="divide-y divide-line">
            {items.map((it) => (
              <li key={`${it.kind}-${it.id}`} className="px-4 py-2.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="rounded bg-panel-2 px-1.5 text-[10.5px] tracking-wide text-muted uppercase">{KIND_LABEL[it.kind] ?? it.kind}</span>
                  <span className="min-w-0 text-[13.5px] font-medium">{it.title}</span>
                  {it.status && <Badge tone={statusTone(it.status)}>{it.status}</Badge>}
                  <span className="ml-auto text-[11px] text-muted">{shortTime(it.at)}</span>
                </div>
                {it.detail.length > 0 && (
                  <ul className="mt-1 text-[12.5px] text-muted">
                    {it.detail.map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                )}
                {it.rationale.length > 0 && (
                  <div className="mt-1 line-clamp-3 text-[12.5px]" title={it.rationale.join(" ")}>
                    <span className="font-medium">Why: </span>
                    {it.rationale.join(" ")}
                  </div>
                )}
                {it.whyNow && (
                  <div className="mt-1 line-clamp-3 text-[12.5px] whitespace-pre-line" title={it.whyNow}>
                    <span className="font-medium">Why now: </span>
                    {it.whyNow}
                  </div>
                )}
                {it.evidence && <div className="mt-1 text-[11.5px] text-good">{it.evidence}</div>}
                <div className="mt-0.5 flex gap-3 text-[11px] text-muted">
                  <code className="num">{it.id}</code>
                  {it.href && (
                    <Link href={it.href} className="text-accent">
                      {it.href.startsWith("/moves") ? "Moves →" : "Activity →"}
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}
