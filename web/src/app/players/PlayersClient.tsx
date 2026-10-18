"use client";

import { useMemo, useState } from "react";
import type { ValuesBundle } from "@/lib/data";
import type { ModelRun, Projection } from "@/lib/types";
import { CATS, CAT_LABEL, heatStyle } from "@/lib/categories";
import { fmt, shortTime, slugToName } from "@/lib/format";
import { matchesPos, normName, toRows, type PlayerRow } from "@/lib/players";
import { Badge, Card, Empty, JsonView } from "@/components/ui";

type Row = PlayerRow & { proj: Projection | null };

const PROJ_COLS: { k: string; label: string; get: (p: Projection) => number | null; d?: number }[] = [
  { k: "gp", label: "GP", get: (p) => p.gp, d: 0 },
  { k: "mpg", label: "MPG", get: (p) => p.mpg, d: 1 },
  { k: "fgp", label: "FG%", get: (p) => (p.fga ? (p.fgm ?? 0) / p.fga : null), d: 3 },
  { k: "fga", label: "FGA", get: (p) => p.fga, d: 1 },
  { k: "ftp", label: "FT%", get: (p) => (p.fta ? (p.ftm ?? 0) / p.fta : null), d: 3 },
  { k: "fta", label: "FTA", get: (p) => p.fta, d: 1 },
  { k: "fg3m", label: "3PM", get: (p) => p.fg3m, d: 1 },
  { k: "pts", label: "PTS", get: (p) => p.pts, d: 1 },
  { k: "reb", label: "REB", get: (p) => p.reb, d: 1 },
  { k: "ast", label: "AST", get: (p) => p.ast, d: 1 },
  { k: "stl", label: "STL", get: (p) => p.stl, d: 1 },
  { k: "blk", label: "BLK", get: (p) => p.blk, d: 1 },
  { k: "tov", label: "TO", get: (p) => p.tov, d: 1 },
];

export default function PlayersClient({
  values,
  projRun,
  projections,
}: {
  values: ValuesBundle;
  projRun: ModelRun | null;
  projections: Projection[];
}) {
  const rows: Row[] = useMemo(() => {
    const pm = new Map(projections.map((p) => [p.player_id, p]));
    const base = toRows(values.values, values.players).map((r) => ({ ...r, proj: pm.get(r.player_id) ?? null }));
    // include projected players without values
    const seen = new Set(base.map((r) => r.player_id));
    for (const p of projections)
      if (!seen.has(p.player_id)) {
        const pl = values.players[p.player_id];
        const ex = (p.extra ?? {}) as Record<string, unknown>;
        base.push({
          player_id: p.player_id,
          name: pl?.name ?? (ex.name as string) ?? slugToName(p.player_id),
          team: pl?.team ?? (ex.team as string) ?? "",
          positions: (pl?.positions as string[]) ?? [],
          status: pl?.status ?? null,
          rank: null,
          value: null,
          adp: null,
          cat_values: {},
          proj: p,
        });
      }
    return base;
  }, [values, projections]);

  const [view, setView] = useState<"values" | "proj">("values");
  const [q, setQ] = useState("");
  const [pos, setPos] = useState("ALL");
  const [sort, setSort] = useState<{ k: string; asc: boolean }>({ k: "value", asc: false });
  const [open, setOpen] = useState<Row | null>(null);
  const [limit, setLimit] = useState(120);

  const sorted = useMemo(() => {
    const n = normName(q);
    const f = rows.filter((r) => matchesPos(r.positions, pos) && (!n || normName(r.name).includes(n) || normName(r.team).includes(n)));
    const get = (r: Row): number | string | null => {
      if (sort.k === "name") return r.name;
      if (sort.k === "value") return r.value;
      if (sort.k === "rank") return r.rank;
      if (sort.k === "adp") return r.adp;
      if (sort.k.startsWith("cat:")) return r.cat_values[sort.k.slice(4)] ?? null;
      const col = PROJ_COLS.find((c) => c.k === sort.k);
      return col && r.proj ? col.get(r.proj) : null;
    };
    return f.sort((a, b) => {
      const x = get(a);
      const y = get(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      const c = typeof x === "string" ? x.localeCompare(String(y)) : (x as number) - (y as number);
      return sort.asc ? c : -c;
    });
  }, [rows, q, pos, sort]);

  const th = (k: string, label: string, cls = "", defAsc = false) => (
    <th
      key={k}
      className={`cursor-pointer select-none ${cls} ${sort.k === k ? "text-accent!" : ""}`}
      onClick={() => setSort((s) => (s.k === k ? { k, asc: !s.asc } : { k, asc: defAsc }))}
    >
      {label}
      {sort.k === k ? (sort.asc ? " ↑" : " ↓") : ""}
    </th>
  );

  return (
    <>
      <Card
        title="Projections & values"
        bodyClassName="p-0"
        right={
          <span className="truncate">
            values <code>{values.run?.run_id ?? "none"}</code> · proj <code>{projRun?.run_id ?? "none"}</code>
          </span>
        }
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <div className="flex overflow-hidden rounded border border-line text-xs">
            {(["values", "proj"] as const).map((v) => (
              <button key={v} onClick={() => setView(v)} className={`px-2.5 py-1 ${view === v ? "bg-accent text-accent-fg" : "text-muted"}`}>
                {v === "values" ? "Category values" : "Per-game projections"}
              </button>
            ))}
          </div>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search"
            className="min-w-0 flex-1 rounded border border-line bg-panel-2 px-2 py-1 text-sm outline-none focus:border-accent sm:max-w-48"
          />
          <div className="flex flex-wrap gap-1">
            {["ALL", "PG", "SG", "G", "SF", "PF", "F", "C"].map((p) => (
              <button
                key={p}
                onClick={() => setPos(p)}
                className={`rounded border px-2 py-0.5 text-xs ${pos === p ? "border-accent bg-accent/10 text-accent" : "border-line text-muted"}`}
              >
                {p}
              </button>
            ))}
          </div>
          <span className="ml-auto text-xs text-muted">{sorted.length} players</span>
        </div>
        {rows.length === 0 ? (
          <Empty>No values or projections yet.</Empty>
        ) : (
          <div className="scroll-thin max-h-[78vh] overflow-auto">
            <table className="dense w-full text-sm">
              <thead className="sticky top-0 z-10 bg-panel">
                <tr className="text-left">
                  {th("rank", "Rk", "", true)}
                  {th("name", "Player", "sticky left-0 z-10 bg-panel", true)}
                  {th("value", "Val", "text-right")}
                  {view === "values"
                    ? CATS.map((c) => th(`cat:${c}`, CAT_LABEL[c], "text-center"))
                    : PROJ_COLS.map((c) => th(c.k, c.label, "text-right"))}
                  {th("adp", "ADP", "text-right", true)}
                </tr>
              </thead>
              <tbody>
                {sorted.slice(0, limit).map((r) => (
                  <tr key={r.player_id} className="cursor-pointer hover:bg-panel-2" onClick={() => setOpen(r)}>
                    <td className="num text-muted">{r.rank ?? "—"}</td>
                    <td className="sticky left-0 bg-panel">
                      <div className="max-w-[40vw] truncate font-medium sm:max-w-none">{r.name}</div>
                      <div className="text-[11px] text-muted">
                        {r.team} · {r.positions.join("/")}
                        {r.status && r.status !== "healthy" && <span className="ml-1 text-bad">{r.status}</span>}
                      </div>
                    </td>
                    <td className="num text-right font-semibold">{fmt(r.value)}</td>
                    {view === "values"
                      ? CATS.map((c) => (
                          <td key={c} className="num text-center text-xs" style={heatStyle(r.cat_values[c], 1.5)}>
                            {fmt(r.cat_values[c], 2)}
                          </td>
                        ))
                      : PROJ_COLS.map((c) => (
                          <td key={c.k} className="num text-right text-xs">
                            {r.proj ? fmt(c.get(r.proj), c.d) : "—"}
                          </td>
                        ))}
                    <td className="num text-right text-muted">{fmt(r.adp, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {sorted.length > limit && (
              <button onClick={() => setLimit((l) => l + 200)} className="w-full border-t border-line py-2 text-xs text-accent">
                Show more ({sorted.length - limit} more)
              </button>
            )}
          </div>
        )}
      </Card>
      {open && <Drawer row={open} onClose={() => setOpen(null)} projRun={projRun} valuesRun={values.run} />}
    </>
  );
}

function Drawer({ row, onClose, projRun, valuesRun }: { row: Row; onClose: () => void; projRun: ModelRun | null; valuesRun: ModelRun | null }) {
  const p = row.proj;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        className="scroll-thin h-full w-full max-w-md overflow-y-auto border-l border-line bg-panel p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold">{row.name}</h2>
            <div className="flex flex-wrap items-center gap-1 text-sm text-muted">
              {row.team}
              {row.positions.map((x) => (
                <Badge key={x}>{x}</Badge>
              ))}
              {row.status && <Badge tone={row.status === "healthy" ? "good" : "bad"}>{row.status}</Badge>}
            </div>
            <code className="text-[11px] text-muted">{row.player_id}</code>
          </div>
          <button onClick={onClose} className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-fg">
            close
          </button>
        </div>
        <div className="mt-4 grid grid-cols-3 gap-3">
          <div>
            <div className="text-[10.5px] uppercase text-muted">Rank</div>
            <div className="num text-xl font-semibold">{row.rank ?? "—"}</div>
          </div>
          <div>
            <div className="text-[10.5px] uppercase text-muted">Value</div>
            <div className="num text-xl font-semibold">{fmt(row.value)}</div>
          </div>
          <div>
            <div className="text-[10.5px] uppercase text-muted">ADP</div>
            <div className="num text-xl font-semibold">{fmt(row.adp, 1)}</div>
          </div>
        </div>
        <h3 className="mt-4 mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Category values</h3>
        <div className="space-y-1">
          {CATS.map((c) => {
            const v = row.cat_values[c];
            const w = Math.min(50, (Math.abs(v ?? 0) / 3) * 50);
            return (
              <div key={c} className="flex items-center gap-2 text-xs">
                <span className="w-8 text-muted">{CAT_LABEL[c]}</span>
                <div className="relative h-3 flex-1 rounded bg-panel-2">
                  <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                  <div
                    className={`absolute inset-y-0 rounded ${(v ?? 0) >= 0 ? "bg-good/70" : "bg-bad/70"}`}
                    style={(v ?? 0) >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }}
                  />
                </div>
                <span className="num w-12 text-right">{fmt(v)}</span>
              </div>
            );
          })}
        </div>
        <h3 className="mt-4 mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
          Per-game projection {p?.season && `(${p.season})`}
        </h3>
        {p ? (
          <div className="grid grid-cols-4 gap-2 text-sm">
            {PROJ_COLS.map((c) => (
              <div key={c.k} className="rounded border border-line px-2 py-1">
                <div className="text-[10px] text-muted">{c.label}</div>
                <div className="num">{fmt(c.get(p), c.d)}</div>
              </div>
            ))}
            {p.gp_sd != null && (
              <div className="rounded border border-line px-2 py-1">
                <div className="text-[10px] text-muted">GP sd</div>
                <div className="num">{fmt(p.gp_sd, 1)}</div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-sm text-muted">No projection row.</div>
        )}
        <div className="mt-4 space-y-1 text-xs text-muted">
          <div>
            values run <code>{valuesRun?.run_id ?? "—"}</code> · {shortTime(valuesRun?.created_at)}
          </div>
          <div>
            projections run <code>{projRun?.run_id ?? "—"}</code> · {shortTime(projRun?.created_at)}
          </div>
          {p?.extra != null && <JsonView value={p.extra} label="projection extra" />}
        </div>
      </aside>
    </div>
  );
}
