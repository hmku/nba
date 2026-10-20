import { useMemo, useState } from "react";
import type { Draft, DraftPick, RecPayload } from "@/lib/types";
import { customRoundsOf, orderForDraft } from "@/lib/draftOrder";
import { CATS, CAT_LABEL, heatStyle } from "@/lib/categories";
import { fmt, pct, signed } from "@/lib/format";
import { matchesPos, normName, type PlayerRow } from "@/lib/players";
import { probAvailableFromAdp } from "@/lib/fallback";
import { Empty } from "@/components/ui";
import { Ago } from "@/components/client";
import { Expander } from "./parts";

/* Draft room panels: my roster, available players table, draft board. */

export function RosterCard({
  myPicks,
  rowById,
  rowByName,
  myRoster,
  payload,
}: {
  myPicks: DraftPick[];
  rowById: Map<string, PlayerRow>;
  rowByName: Map<string, PlayerRow>;
  myRoster: PlayerRow[];
  payload: RecPayload | null;
}) {
  const totals = CATS.map((c) => ({ c, v: myRoster.reduce((s, p) => s + (p.cat_values[c] ?? 0), 0) }));
  const ranked = [...totals].sort((a, b) => b.v - a.v).map((t) => t.c);
  const profile = payload?.team_profile?.categories ?? {};
  const hasProfile = Object.keys(profile).length > 0;
  // strong / weak: model win probability when present, else top/bottom 3 of my summed category values
  const level = (c: (typeof CATS)[number]): "strong" | "weak" | "mid" | "none" => {
    if (!myRoster.length) return "none";
    const wp = profile[c]?.win_prob;
    if (hasProfile && wp != null) return wp >= 0.6 ? "strong" : wp <= 0.4 ? "weak" : "mid";
    const i = ranked.indexOf(c);
    return i < 3 ? "strong" : i >= ranked.length - 3 ? "weak" : "mid";
  };
  const STYLE = {
    strong: "bg-good/20 text-good border-good/40",
    weak: "bg-bad/15 text-bad border-bad/40",
    mid: "bg-panel-2 text-muted border-line",
    none: "bg-panel-2 text-muted border-line",
  };

  return (
    <section className="rounded-xl border border-line bg-panel px-4 py-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-medium">Your roster</h2>
        <span className="text-xs text-muted">{myPicks.length} players</span>
      </div>
      {myPicks.length === 0 ? (
        <p className="py-2 text-sm text-muted">No picks yet.</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {myPicks.map((p) => {
            const row = (p.player_id && rowById.get(p.player_id)) || rowByName.get(normName(p.player_name));
            return (
              <li key={p.overall_pick} className="flex items-baseline gap-2 text-[15px]">
                <span className="num w-8 shrink-0 text-xs text-muted">R{p.round}</span>
                <span className="min-w-0 truncate">{p.player_name ?? row?.name ?? p.player_id}</span>
                <span className="shrink-0 text-xs text-muted">{row?.positions.join("/")}</span>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-3 grid grid-cols-9 gap-1">
        {totals.map(({ c, v }) => {
          const wp = profile[c]?.win_prob;
          return (
            <div
              key={c}
              title={`${CAT_LABEL[c]}: Σ value ${signed(v, 1)}${wp != null ? ` · win ${pct(wp)}` : ""}`}
              className={`rounded border py-1 text-center text-[10px] leading-none font-medium ${STYLE[level(c)]}`}
            >
              {CAT_LABEL[c]}
            </div>
          );
        })}
      </div>
      {myRoster.length > 0 && (
        <p className="mt-1.5 text-[11px] text-muted">
          green = strength, red = weakness ({hasProfile ? "model weekly win %" : "sum of category values"})
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ available */

type SortKey = "value" | "adp" | "rank" | (typeof CATS)[number];

export function AvailableTable({
  rows,
  runId,
  runAt,
  picksUntil,
  currentPick,
  onSelect,
  errors,
}: {
  rows: PlayerRow[];
  runId: string | null;
  runAt: string | null;
  picksUntil: number;
  currentPick: number;
  onSelect: (p: PlayerRow) => void;
  errors: string[];
}) {
  const [q, setQ] = useState("");
  const [pos, setPos] = useState("ALL");
  const [sort, setSort] = useState<SortKey>("value");
  const [limit, setLimit] = useState(40);

  const filtered = useMemo(() => {
    const n = normName(q);
    const out = rows.filter((r) => matchesPos(r.positions, pos) && (!n || normName(r.name).includes(n) || normName(r.team).includes(n)));
    const key = (r: PlayerRow): number | null =>
      sort === "value" ? r.value : sort === "adp" ? r.adp : sort === "rank" ? r.rank : r.cat_values[sort] ?? null;
    const asc = sort === "adp" || sort === "rank";
    return out.sort((a, b) => {
      const ka = key(a);
      const kb = key(b);
      if (ka == null && kb == null) return 0;
      if (ka == null) return 1;
      if (kb == null) return -1;
      return asc ? ka - kb : kb - ka;
    });
  }, [rows, q, pos, sort]);

  const th = (k: SortKey, label: string, cls = "") => (
    <th key={k} className={`cursor-pointer select-none ${cls} ${sort === k ? "text-accent!" : ""}`} onClick={() => setSort(k)}>
      {label}
      {sort === k ? (k === "adp" || k === "rank" ? " ↑" : " ↓") : ""}
    </th>
  );

  return (
    <div>
      <div className="space-y-2 border-b border-line px-3 py-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name / team"
          aria-label="Search available players by name or team"
          className="w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-base outline-none focus:border-accent"
        />
        <div className="flex flex-wrap gap-1">
          {["ALL", "PG", "SG", "G", "SF", "PF", "F", "C"].map((p) => (
            <button
              key={p}
              onClick={() => setPos(p)}
              aria-pressed={pos === p}
              className={`rounded-md border px-2 py-1 text-xs ${pos === p ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-fg"}`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
      {errors.length > 0 && <div className="px-3 py-1 text-xs text-bad">{errors.join("; ")}</div>}
      {rows.length === 0 ? (
        <Empty>No player_values for the latest valuation run.</Empty>
      ) : (
        <div className="scroll-thin max-h-[70vh] overflow-auto">
          <table className="dense w-full text-sm">
            <thead className="sticky top-0 z-10 bg-panel">
              <tr className="text-left">
                {th("rank", "Rk", "w-8")}
                <th className="sticky left-0 z-10 bg-panel">Player</th>
                {th("value", "Val", "text-right")}
                {CATS.map((c) => th(c, CAT_LABEL[c], "text-center"))}
                {th("adp", "ADP", "text-right")}
                <th className="text-right" title="Rough ADP-based probability the player lasts to my next pick">
                  P@next
                </th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, limit).map((r) => (
                <tr key={r.player_id} className="hover:bg-panel-2">
                  <td className="num text-muted">{r.rank ?? "—"}</td>
                  <td className="sticky left-0 max-w-[44vw] bg-panel sm:max-w-none">
                    <div className="truncate font-medium">{r.name}</div>
                    <div className="truncate text-[11px] text-muted">
                      {r.team} · {r.positions.join("/")}
                      {r.status && r.status !== "healthy" && <span className="ml-1 text-bad">{r.status}</span>}
                    </div>
                  </td>
                  <td className="num text-right font-semibold">{fmt(r.value)}</td>
                  {CATS.map((c) => (
                    <td key={c} className="num text-center text-xs" style={heatStyle(r.cat_values[c], 1.5)}>
                      {fmt(r.cat_values[c], 1)}
                    </td>
                  ))}
                  <td className="num text-right text-muted">{fmt(r.adp, 1)}</td>
                  <td className="num text-right text-xs text-muted">{pct(probAvailableFromAdp(r.adp, picksUntil, currentPick))}</td>
                  <td className="text-right">
                    <button
                      onClick={() => onSelect(r)}
                      title="Enter as a pick"
                      className="rounded border border-line px-1.5 text-xs text-muted hover:border-accent hover:text-accent"
                    >
                      +
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > limit && (
            <button onClick={() => setLimit((l) => l + 100)} className="w-full border-t border-line py-2.5 text-sm text-accent">
              Show more ({filtered.length - limit} more)
            </button>
          )}
        </div>
      )}
      <p className="border-t border-line px-3 py-1.5 text-[11px] text-muted">
        run <code>{runId ?? "none"}</code> {runAt && <Ago ts={runAt} />} · tap a column to sort · + enters a pick
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ board */

export function BoardPanel({
  draft,
  picks,
  pickMap,
  currentPick,
  teamName,
  myUpcoming,
  total,
}: {
  draft: Draft;
  picks: DraftPick[];
  pickMap: Map<number, DraftPick>;
  currentPick: number | null;
  teamName: (s: number) => string;
  myUpcoming: number[];
  total: number;
}) {
  const recent = [...picks].sort((a, b) => b.overall_pick - a.overall_pick).slice(0, 8);
  return (
    <div>
      <div className="px-4 py-2 text-xs text-muted">
        {picks.length}/{total} picks · your upcoming:{" "}
        <span className="num text-fg">{myUpcoming.slice(0, 8).join(", ") || "—"}</span>
        {myUpcoming.length > 8 ? ` +${myUpcoming.length - 8}` : ""}
      </div>
      {recent.length === 0 ? (
        <p className="px-4 pb-3 text-sm text-muted">No picks yet.</p>
      ) : (
        <ul className="border-t border-line">
          {recent.map((p) => (
            <li
              key={p.overall_pick}
              className={`flex items-baseline gap-3 border-b border-line px-4 py-2 text-sm last:border-b-0 ${p.team_slot === draft.my_slot ? "bg-mine" : ""}`}
            >
              <span className="num w-8 shrink-0 text-xs text-muted">#{p.overall_pick}</span>
              <span className="min-w-0 flex-1 truncate font-medium">{p.player_name ?? p.player_id}</span>
              <span className="shrink-0 truncate text-xs text-muted">
                {teamName(p.team_slot)}
                {p.source && p.source !== "yahoo_sync" && <span className="ml-1 text-warn">{p.source}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="border-t border-line px-4">
        <Expander bare summary="Full board grid">
          <div className="-mx-4">
            <DraftBoard draft={draft} pickMap={pickMap} currentPick={currentPick} teamName={teamName} />
          </div>
        </Expander>
      </div>
    </div>
  );
}

function DraftBoard({
  draft,
  pickMap,
  currentPick,
  teamName,
}: {
  draft: Draft;
  pickMap: Map<number, DraftPick>;
  currentPick: number | null;
  teamName: (s: number) => string;
}) {
  const N = draft.num_teams;
  const slots = Array.from({ length: N }, (_, i) => i + 1);
  const rounds = Array.from({ length: draft.num_rounds }, (_, i) => i + 1);
  const custom = customRoundsOf(draft.config, N, draft.num_rounds);
  const order = orderForDraft(draft);
  return (
    <div className="scroll-thin overflow-x-auto border-t border-line">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            <th className="sticky left-0 z-10 w-10 bg-panel px-1 py-1.5 text-[10px] font-medium text-muted">Rd</th>
            {slots.map((s) => (
              <th
                key={s}
                className={`min-w-[88px] border-l border-line px-1 py-1.5 text-left text-[10.5px] font-medium ${s === draft.my_slot ? "bg-mine text-accent" : "text-muted"}`}
              >
                <span className="num">{s}</span> <span className="truncate">{teamName(s)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rounds.map((r) => {
            const inRound = order.filter((p) => p.round === r);
            const bySlot = new Map(inRound.map((p) => [p.slot, p.overall]));
            const rev = custom ? false : inRound[0]?.slot !== 1;
            return (
              <tr key={r} className="border-t border-line">
                <td className="sticky left-0 z-10 bg-panel px-1 py-1 text-center text-[10.5px] text-muted">
                  <span className="num">{r}</span>
                  <span className="ml-0.5">{custom ? "*" : rev ? "←" : "→"}</span>
                </td>
                {slots.map((s) => {
                  const overall = bySlot.get(s) ?? 0;
                  const p = pickMap.get(overall);
                  const isCur = overall === currentPick;
                  const mine = s === draft.my_slot;
                  return (
                    <td
                      key={s}
                      className={`h-11 border-l border-line px-1 py-0.5 align-top ${mine ? "bg-mine" : ""} ${isCur ? "outline-2 -outline-offset-2 outline-accent" : ""}`}
                    >
                      <div className="num text-[9.5px] text-muted">
                        {overall}
                        {p?.source && p.source !== "yahoo_sync" && <span className="ml-1 text-warn">{p.source}</span>}
                      </div>
                      <div className={`truncate leading-tight ${p ? "font-medium" : "text-muted"}`} title={p?.player_name ?? ""}>
                        {p ? p.player_name ?? p.player_id : isCur ? "on clock" : ""}
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
