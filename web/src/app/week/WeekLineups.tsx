"use client";

import { useState } from "react";
import type { GamesLeft, LineupRow } from "@/lib/week";
import { shortName } from "@/lib/lineup";
import { BENCHY, CellBody, LineupGrid, StatusTag, SummaryLine, cellTitle, fmtTime, type DayView } from "./LineupGrid";

/** The opponent's lineup for one day (serialisable). */
export interface OppDay {
  date: string;
  rows: LineupRow[];
  /** a recorded Yahoo read of their team for that day (else: the league snapshot's slots) */
  fromYahoo: boolean;
  observedAt: string | null;
}

export interface SideMeta {
  name: string;
  gamesLeft: GamesLeft | null;
  /** Yahoo's own "games remaining" from the newest score capture, when present */
  yahooGamesLeft: number | null;
}

function OppRowBody({ r }: { r: LineupRow }) {
  if (!r.playerId && !r.name) return <span className="text-[11px] text-muted/60">—</span>;
  const dim = r.hasGame === false;
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1">
        <span className={`truncate text-[13.5px] ${dim ? "text-muted" : ""}`}>{shortName(r.name ?? r.playerId ?? "?")}</span>
        <StatusTag status={r.status} />
      </div>
      <div className="text-[10.5px] text-muted">
        {r.game ?? (r.hasGame === false ? <span className="text-muted/70">no game</span> : <span className="text-muted/70">?</span>)}
        {r.nbaTeam && <span className="ml-1 text-muted/70">{r.nbaTeam}</span>}
      </div>
    </div>
  );
}

function GamesLeftLine({ g, yahoo, past }: { g: GamesLeft | null; yahoo: number | null; past: boolean }) {
  if (past) return null;
  return (
    <div className="flex items-baseline justify-between border-t border-line px-3 py-2 text-[12.5px]" title="Remaining scheduled games (today included) of rostered players who are not on IL and not tagged out">
      <span className="text-muted">Games left</span>
      <span>
        <span className="num font-semibold">{g ? g.total : "—"}</span>
        {g && g.unknown > 0 && <span className="ml-1 text-[11px] text-muted">+{g.unknown} unknown</span>}
        {yahoo != null && <span className="ml-1.5 text-[11px] text-muted">Yahoo {yahoo}</span>}
      </span>
    </div>
  );
}

function MyColumn({ d, meta, past, snapshot, snapshotAt }: { d: DayView; meta: SideMeta; past: boolean; snapshot: OppDay | null; snapshotAt: string | null }) {
  if (!d.plan && !d.actual && snapshot) return <TeamColumn d={snapshot} meta={meta} snapshotAt={snapshotAt} past={past} who="my" />;
  const rows = d.rows.filter((r) => !(BENCHY.has(r.slot) && !r.shown && r.state !== "mismatch"));
  return (
    <div className="flex min-w-0 flex-col rounded-xl border border-line bg-panel">
      <div className="border-b border-line px-3 py-2">
        <div className="truncate text-[13px] font-semibold">{meta.name}</div>
        <div className="truncate text-[11px] text-muted">
          {d.actual ? `✓ Yahoo ${d.actual.observedAt ? fmtTime(d.actual.observedAt) : "read"}` : d.plan ? "◌ plan (Yahoo not read this day)" : "no plan or Yahoo read"}
          {(d.plan || d.actual) && (
            <>
              {" · "}
              <SummaryLine s={d.summary} />
            </>
          )}
        </div>
      </div>
      <ul className="flex-1 divide-y divide-line/60">
        {rows.map((r, i) => (
          <li
            key={i}
            className={`flex items-center gap-2 px-3 py-1.5 ${r.state === "mismatch" ? "bg-warn/10" : ""} ${r.slot === "BN" && rows[i - 1]?.slot !== "BN" ? "border-t-2 border-t-line" : ""}`}
            title={cellTitle(r)}
          >
            <span className={`w-9 shrink-0 text-[11px] font-medium ${BENCHY.has(r.slot) ? "text-muted" : ""}`}>{r.slot}</span>
            <CellBody r={r} />
          </li>
        ))}
      </ul>
      <GamesLeftLine g={meta.gamesLeft} yahoo={meta.yahooGamesLeft} past={past} />
    </div>
  );
}

/** A team's lineup from a Yahoo read of it or the league snapshot (the opponent; me on days without plan or read). */
function TeamColumn({ d, meta, snapshotAt, past, who = "their" }: { d: OppDay | null; meta: SideMeta; snapshotAt: string | null; past: boolean; who?: string }) {
  return (
    <div className="flex min-w-0 flex-col rounded-xl border border-line bg-panel">
      <div className="border-b border-line px-3 py-2">
        <div className="truncate text-[13px] font-semibold">{meta.name}</div>
        <div className="truncate text-[11px] text-muted">
          {d?.fromYahoo
            ? `✓ Yahoo ${d.observedAt ? fmtTime(d.observedAt) : "read"}`
            : `${who} lineup as of last capture${snapshotAt ? ` (${fmtTime(snapshotAt)})` : ""}`}
        </div>
      </div>
      {d && d.rows.length ? (
        <ul className="flex-1 divide-y divide-line/60">
          {d.rows.map((r, i) => (
            <li key={i} className={`flex items-center gap-2 px-3 py-1.5 ${r.slot === "BN" && d.rows[i - 1]?.slot !== "BN" ? "border-t-2 border-t-line" : ""}`}>
              <span className={`w-9 shrink-0 text-[11px] font-medium ${BENCHY.has(r.slot) ? "text-muted" : ""}`}>{r.slot}</span>
              <OppRowBody r={r} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="flex-1 px-3 py-3 text-[12.5px] text-muted">Their roster isn&apos;t in the league snapshot.</p>
      )}
      <GamesLeftLine g={meta.gamesLeft} yahoo={meta.yahooGamesLeft} past={past} />
    </div>
  );
}

function DayNotes({ d }: { d: DayView }) {
  const p = d.plan;
  if (!p || (p.tipoff.length === 0 && p.ilMoves.length === 0)) return null;
  return (
    <div className="space-y-2 rounded-xl border border-line bg-panel px-4 py-3 text-[13px]">
      {p.tipoff.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold tracking-wider text-muted uppercase">Decide at tip-off · {d.label}</div>
          <ul className="mt-1 space-y-1">
            {p.tipoff.map((t) => (
              <li key={t.playerId}>
                <span className="font-medium">{t.name}</span>
                <StatusTag status={t.status} />
                {t.swapWithName && <span className="ml-1 text-xs">↔ {t.swapWithName}</span>}
                {t.deadline && <div className="text-xs text-accent">{t.deadline}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {p.ilMoves.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold tracking-wider text-muted uppercase">IL moves · {d.label}</div>
          <ul className="mt-1 space-y-0.5">
            {p.ilMoves.map((m) => (
              <li key={m.playerId}>
                {m.name} <StatusTag status={m.status} /> <span className="text-muted">{m.from ?? "?"} → {m.to}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * Lineups for the week: "Today" (default) puts my lineup next to the opponent's for the picked day (side by side on
 * wide screens, Me | Them tabs on phones); "Week" shows my plan-vs-Yahoo grid for every day.
 */
export function WeekLineups({
  days,
  oppDays,
  mySnapshot = [],
  me,
  opp,
  snapshotAt,
  initial,
  today,
}: {
  days: DayView[];
  oppDays: OppDay[] | null;
  /** my roster from the league snapshot, for days with no plan and no Yahoo read */
  mySnapshot?: OppDay[];
  me: SideMeta;
  opp: SideMeta | null;
  snapshotAt: string | null;
  initial: number;
  today: string;
}) {
  const [sel, setSel] = useState(Math.min(Math.max(initial, 0), Math.max(days.length - 1, 0)));
  const [view, setView] = useState<"today" | "week">("today");
  const [side, setSide] = useState<"me" | "them">("me");
  const d = days[sel];
  if (!d) return null;
  const past = (days[days.length - 1]?.date ?? "") < today;
  const od = oppDays?.find((x) => x.date === d.date) ?? null;
  const tab = (on: boolean) => `rounded-md px-3 py-1 text-[12.5px] ${on ? "bg-panel font-semibold text-fg shadow-sm" : "text-muted"}`;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 px-1 pt-2">
        <h2 className="text-[13px] font-semibold tracking-wide text-muted uppercase">Lineups</h2>
        <div className="flex rounded-lg bg-panel-2 p-0.5" role="tablist" aria-label="Lineup view">
          <button role="tab" aria-selected={view === "today"} onClick={() => setView("today")} className={tab(view === "today")}>
            Day
          </button>
          <button role="tab" aria-selected={view === "week"} onClick={() => setView("week")} className={tab(view === "week")}>
            Week
          </button>
        </div>
      </div>

      {view === "today" && (
        <div className="scroll-thin -mx-4 overflow-x-auto px-4">
          <div className="flex gap-1.5 pb-1">
            {days.map((x, i) => (
              <button
                key={x.date}
                onClick={() => setSel(i)}
                aria-pressed={i === sel}
                className={`shrink-0 rounded-lg border px-2.5 py-1 text-[12px] ${i === sel ? "border-accent bg-accent/10 font-semibold text-accent" : "border-line bg-panel text-muted"}`}
              >
                {x.isToday ? "Today" : x.label}
                {x.mismatches > 0 && <span className="ml-1 text-warn">⚠</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {view === "today" ? (
        <>
          {opp && (
            <div className="flex rounded-lg bg-panel-2 p-0.5 md:hidden" role="tablist" aria-label="Team">
              <button role="tab" aria-selected={side === "me"} onClick={() => setSide("me")} className={`flex-1 ${tab(side === "me")}`}>
                Me
              </button>
              <button role="tab" aria-selected={side === "them"} onClick={() => setSide("them")} className={`flex-1 ${tab(side === "them")}`}>
                Them
              </button>
            </div>
          )}
          <div className={`grid gap-3 ${opp ? "md:grid-cols-2" : ""}`}>
            <div className={opp && side !== "me" ? "hidden md:block" : ""}>
              <MyColumn d={d} meta={me} past={past} snapshot={mySnapshot.find((x) => x.date === d.date) ?? null} snapshotAt={snapshotAt} />
            </div>
            {opp && (
              <div className={side !== "them" ? "hidden md:block" : ""}>
                <TeamColumn d={od} meta={opp} snapshotAt={snapshotAt} past={past} />
              </div>
            )}
          </div>
        </>
      ) : (
        <LineupGrid days={days} sel={sel} onSel={setSel} />
      )}
      <DayNotes d={d} />
    </div>
  );
}
