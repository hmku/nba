"use client";
/* Lazy JSON expander for one model run field: the row's params / inputs / summary are fetched from
   GET /api/history/run on first open (one request per run, shared by its three expanders). */
import { useState } from "react";

type Field = "params" | "inputs" | "summary";
type Detail = Partial<Record<Field, unknown>>;

const loads = new Map<string, Promise<Detail>>();

function loadRun(runId: string): Promise<Detail> {
  let p = loads.get(runId);
  if (!p) {
    p = fetch(`/api/history/run?id=${encodeURIComponent(runId)}`).then(async (r) => {
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.error ?? `HTTP ${r.status}`);
      return (await r.json()) as Detail;
    });
    p.catch(() => loads.delete(runId)); // retry on the next open
    loads.set(runId, p);
  }
  return p;
}

type State = { s: "idle" } | { s: "loading" } | { s: "done"; v: unknown } | { s: "error"; e: string };

export function RunJson({ runId, field, label }: { runId: string; field: Field; label: string }) {
  const [st, setSt] = useState<State>({ s: "idle" });
  const onToggle = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (!e.currentTarget.open || st.s === "loading" || st.s === "done") return;
    setSt({ s: "loading" });
    loadRun(runId).then(
      (d) => setSt({ s: "done", v: d[field] ?? null }),
      (err: unknown) => setSt({ s: "error", e: err instanceof Error ? err.message : String(err) }),
    );
  };
  return (
    <details className="group" onToggle={onToggle}>
      <summary className="cursor-pointer select-none text-xs text-accent">{label}</summary>
      {st.s === "idle" ? null : st.s === "loading" ? (
        <div className="mt-1 space-y-1.5 rounded border border-line bg-panel-2 p-2" role="status" aria-label={`Loading ${label}`}>
          <div className="h-2.5 w-4/5 rounded bg-line motion-safe:animate-pulse" />
          <div className="h-2.5 w-3/5 rounded bg-line motion-safe:animate-pulse" />
          <div className="h-2.5 w-2/3 rounded bg-line motion-safe:animate-pulse" />
        </div>
      ) : st.s === "error" ? (
        <p className="mt-1 text-xs text-bad">Could not load ({st.e}); close and reopen to retry.</p>
      ) : st.v == null ? (
        <p className="mt-1 text-xs text-muted">—</p>
      ) : (
        <pre className="scroll-thin mt-1 max-h-80 overflow-auto rounded border border-line bg-panel-2 p-2 text-[11px] leading-snug">
          {JSON.stringify(st.v, null, 2)}
        </pre>
      )}
    </details>
  );
}
