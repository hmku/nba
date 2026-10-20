import { useState, type ReactNode } from "react";
import type { RecItem } from "@/lib/types";
import { CATS, CAT_LABEL, heatStyle } from "@/lib/categories";
import { signed } from "@/lib/format";
import type { PlayerRow } from "@/lib/players";

/* Small pieces shared by the draft room views (candidate rows, category strip, expander). */

/** One row in the "what should I pick" list: either a model recommendation or a client-side fallback item. */
export interface Candidate {
  key: string;
  name: string;
  team: string;
  positions: string[];
  reason: string | null;
  prob: number | null;
  probAt: number | null; // the pick number prob refers to
  taken: boolean;
  row: PlayerRow | null;
  rec?: RecItem;
}

export function CatStrip({ impact, scale = 0.6 }: { impact?: Record<string, number | undefined> | null; scale?: number }) {
  return (
    <div className="grid grid-cols-9 gap-px overflow-hidden rounded border border-line text-center">
      {CATS.map((c) => {
        const v = impact?.[c];
        return (
          <div key={c} className="min-w-0 overflow-hidden bg-panel-2 py-0.5" style={heatStyle(v, scale)}>
            <div className="text-[9px] leading-3 text-muted">{CAT_LABEL[c]}</div>
            <div className="num text-[9.5px] leading-4 tracking-tighter">{v == null ? "·" : signed(v, 1)}</div>
          </div>
        );
      })}
    </div>
  );
}

export function probTone(p: number | null | undefined) {
  if (p == null) return "text-muted";
  return p >= 0.6 ? "text-good" : p >= 0.3 ? "text-warn" : "text-bad";
}

export function Pos({ positions }: { positions: string[] }) {
  return <span className="text-muted">{positions.join(" / ")}</span>;
}

/** Lazily rendered expander (children mount only once opened, keeps the phone DOM light). */
export function Expander({
  summary,
  right,
  children,
  bare = false,
}: {
  summary: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  bare?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className={bare ? "" : "rounded-xl border border-line bg-panel"}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={`flex w-full items-center gap-2 text-left ${bare ? "py-2 text-sm text-accent" : "px-4 py-3"}`}
      >
        <span className={`text-[10px] text-muted transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
        <span className={`min-w-0 flex-1 ${bare ? "" : "text-[15px] font-medium"}`}>{summary}</span>
        {right != null && <span className="shrink-0 text-xs text-muted">{right}</span>}
      </button>
      {open && <div className={bare ? "" : "border-t border-line"}>{children}</div>}
    </section>
  );
}
