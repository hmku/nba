"use client";

import { useRef, type TouchEvent } from "react";
import type { CellState, DaySummary, Expected, GridCell, GridRow, IlMove, TipoffItem } from "@/lib/lineup";

/** One day of the grid as the page hands it over (serialisable). */
export interface DayView {
  date: string;
  label: string; // "Wed 21"
  isToday: boolean;
  rows: GridRow[];
  summary: DaySummary;
  mismatches: number;
  plan: {
    runId: string;
    date: string;
    sameDay: boolean;
    createdAt: string | null;
    modelVersion: string | null;
    tipoff: TipoffItem[];
    ilMoves: IlMove[];
    benchWithGame: { playerId: string; name: string; game: string | null }[];
    expected: Expected | null;
    warnings: string[];
  } | null;
  actual: {
    runId: string;
    source: string;
    observedAt: string | null;
    how: string | null;
    txnId: string | null;
    complete: boolean | null;
  } | null;
}

export const STATE_ICON: Record<CellState, string> = { verified: "✓", planned: "◌", mismatch: "⚠", empty: "" };
export const STATE_TONE: Record<CellState, string> = { verified: "text-good", planned: "text-muted", mismatch: "text-warn", empty: "text-muted" };
const STATE_HELP: Record<CellState, string> = {
  verified: "on Yahoo (read that day)",
  planned: "planned only (Yahoo not read for this day)",
  mismatch: "Yahoo differs from the plan",
  empty: "empty slot",
};

export function StatusTag({ status }: { status: string | null }) {
  if (!status) return null;
  const out = ["O", "OUT", "INJ", "OFS", "NA", "SUSP", "IR"].includes(status.toUpperCase());
  return (
    <span className={`ml-1 rounded px-1 text-[10px] leading-4 font-semibold ${out ? "bg-bad/15 text-bad" : "bg-warn/15 text-warn"}`}>{status}</span>
  );
}

export function cellTitle(r: GridRow): string {
  const bits = [STATE_HELP[r.state]];
  if (r.shown) bits.unshift(`${r.slot}: ${r.shown.name}${r.shown.game ? ` (${r.shown.game})` : r.shown.hasGame === false ? " (no game)" : ""}`);
  if (r.state === "mismatch") bits.push(`plan: ${r.planned?.name ?? "empty"} · Yahoo: ${r.actual?.name ?? "empty"}`);
  if (r.shown?.pPlay != null && r.shown.pPlay < 1) bits.push(`P(plays) ${Math.round(r.shown.pPlay * 100)}% (prior)`);
  return bits.join(" — ");
}

function Game({ c }: { c: GridCell }) {
  if (c.game) return <span className="text-[10.5px] text-muted">{c.game}</span>;
  if (c.hasGame === false) return <span className="text-[10.5px] text-muted/70">no game</span>;
  return <span className="text-[10.5px] text-muted/70">?</span>;
}

/** Desktop table cell / phone list row content. */
export function CellBody({ r, compact = false }: { r: GridRow; compact?: boolean }) {
  const c = r.shown;
  if (!c) return <span className="text-[11px] text-muted/60">{r.state === "mismatch" ? <span className="text-warn">⚠ empty (plan: {r.planned?.short})</span> : "—"}</span>;
  const dim = c.hasGame === false || (BENCHY.has(r.slot) && !c.hasGame);
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1">
        <span className={`shrink-0 text-[11px] ${STATE_TONE[r.state]}`} aria-hidden>
          {STATE_ICON[r.state]}
        </span>
        <span className={`truncate ${compact ? "text-[12px]" : "text-[13.5px]"} ${dim ? "text-muted" : ""}`}>{c.short}</span>
        <StatusTag status={c.status} />
      </div>
      <div className="flex items-center gap-1 pl-3.5">
        <Game c={c} />
        {r.state === "mismatch" && <span className="truncate text-[10.5px] text-warn">plan: {r.planned?.short ?? "empty"}</span>}
      </div>
    </div>
  );
}

export const BENCHY = new Set(["BN", "IL", "IL+"]);

export function SummaryLine({ s }: { s: DaySummary }) {
  if (!s.known && s.started === 0 && s.available === 0) return <span className="text-muted">games unknown</span>;
  return (
    <span>
      <span className="num">{s.started}</span>/<span className="num">{s.available}</span> started
      {s.unused > 0 && <span className="text-warn"> · {s.unused} unused</span>}
      {!s.known && <span className="text-muted"> · some games unknown</span>}
    </span>
  );
}

export function fmtTime(ts: string): string {
  const t = new Date(ts);
  return Number.isNaN(t.getTime())
    ? ts
    : t.toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " ET";
}

/** My week grid (desktop: every day; phone: one day, swipe). Controlled: the selected day is shared with the page. */
export function LineupGrid({ days, sel, onSel }: { days: DayView[]; sel: number; onSel: (i: number) => void }) {
  const setSel = onSel;
  const touch = useRef<number | null>(null);
  const d = days[sel];
  if (!d) return null;
  const go = (k: number) => setSel(Math.min(Math.max(sel + k, 0), days.length - 1));
  const onStart = (e: TouchEvent) => (touch.current = e.touches[0]?.clientX ?? null);
  const onEnd = (e: TouchEvent) => {
    const x0 = touch.current;
    const x1 = e.changedTouches[0]?.clientX;
    touch.current = null;
    if (x0 == null || x1 == null || Math.abs(x1 - x0) < 50) return;
    go(x1 < x0 ? 1 : -1);
  };
  const nRows = days[0]?.rows.length ?? 0;
  return (
    <div className="space-y-3">
      {/* desktop: the whole week */}
      <div className="hidden overflow-hidden rounded-xl border border-line bg-panel md:block">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full table-fixed border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                <th className="w-14 px-2 py-2 text-[10.5px] font-medium text-muted">Slot</th>
                {days.map((x, i) => (
                  <th key={x.date} className="px-1 py-1">
                    <button
                      onClick={() => setSel(i)}
                      aria-pressed={i === sel}
                      className={`w-full rounded-md px-1.5 py-1 text-left text-[12px] ${i === sel ? "bg-accent/10 font-semibold text-accent" : x.isToday ? "font-semibold" : "text-muted hover:bg-panel-2"}`}
                    >
                      {x.label}
                      {x.isToday && <span className="ml-1 text-[10px]">today</span>}
                      {x.mismatches > 0 && <span className="ml-1 text-warn">⚠{x.mismatches}</span>}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: nRows }, (_, ri) => {
                const slot = days[0].rows[ri]?.slot ?? "";
                return (
                  <tr key={ri} className={`border-b border-line/60 ${slot === "BN" && days[0].rows[ri - 1]?.slot !== "BN" ? "border-t-2 border-t-line" : ""}`}>
                    <td className={`px-2 py-1 text-[11px] font-medium ${BENCHY.has(slot) ? "text-muted" : ""}`}>{slot}</td>
                    {days.map((x, i) => {
                      const r = x.rows[ri];
                      return (
                        <td key={x.date} className={`px-1.5 py-1 align-top ${i === sel ? "bg-accent/5" : ""} ${r?.state === "mismatch" ? "bg-warn/10" : ""}`} title={r ? cellTitle(r) : undefined}>
                          {r ? <CellBody r={r} compact /> : null}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
              <tr>
                <td className="px-2 py-1.5 text-[10.5px] text-muted">Starts</td>
                {days.map((x) => (
                  <td key={x.date} className="px-1.5 py-1.5 text-[11px]">
                    {x.plan || x.actual ? <SummaryLine s={x.summary} /> : <span className="text-muted">no data</span>}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* phone: one day at a time (swipe or prev/next) */}
      <div className="rounded-xl border border-line bg-panel md:hidden" onTouchStart={onStart} onTouchEnd={onEnd}>
        <div className="flex items-center justify-between border-b border-line px-2 py-1.5">
          <button onClick={() => go(-1)} disabled={sel === 0} className="rounded-md px-3 py-1.5 text-lg disabled:opacity-30" aria-label="Previous day">
            ‹
          </button>
          <div className="text-center">
            <div className="text-[14px] font-semibold">
              {d.label}
              {d.isToday && <span className="ml-1 text-[11px] font-normal text-accent">today</span>}
            </div>
            <div className="text-[11.5px] text-muted">{d.plan || d.actual ? <SummaryLine s={d.summary} /> : "no plan or Yahoo read"}</div>
          </div>
          <button onClick={() => go(1)} disabled={sel === days.length - 1} className="rounded-md px-3 py-1.5 text-lg disabled:opacity-30" aria-label="Next day">
            ›
          </button>
        </div>
        <div className="flex justify-center gap-1 py-1.5">
          {days.map((x, i) => (
            <button
              key={x.date}
              onClick={() => setSel(i)}
              aria-label={x.label}
              className={`h-1.5 rounded-full ${i === sel ? "w-4 bg-accent" : x.mismatches ? "w-1.5 bg-warn" : "w-1.5 bg-line"}`}
            />
          ))}
        </div>
        <ul className="divide-y divide-line/60">
          {d.rows.map((r, i) => (
            <li key={i} className={`flex items-center gap-2 px-3 py-1.5 ${r.state === "mismatch" ? "bg-warn/10" : ""} ${r.slot === "BN" && d.rows[i - 1]?.slot !== "BN" ? "border-t-2 border-t-line" : ""}`} title={cellTitle(r)}>
              <span className={`w-9 shrink-0 text-[11px] font-medium ${BENCHY.has(r.slot) ? "text-muted" : ""}`}>{r.slot}</span>
              <CellBody r={r} />
            </li>
          ))}
        </ul>
      </div>

    </div>
  );
}
