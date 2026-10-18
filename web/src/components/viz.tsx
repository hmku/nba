/** Small, dependency-free chart primitives (HTML/CSS bars; native title tooltips). Server-safe. */
import type { ReactNode } from "react";
import type { CatKey } from "@/lib/types";
import { CATS, CAT_COLOR, CAT_GLOSS, CAT_LABEL, heatStyle } from "@/lib/categories";
import { pct } from "@/lib/format";

export function CatLabel({ c, className = "" }: { c: CatKey; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`} title={CAT_GLOSS[c]}>
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: CAT_COLOR[c] }} />
      <span className="font-medium">{CAT_LABEL[c]}</span>
    </span>
  );
}

/** Text tone for a win probability. */
export function probText(p: number | null | undefined): string {
  if (p == null) return "text-muted";
  if (p >= 0.6) return "text-good";
  if (p <= 0.4) return "text-bad";
  return "text-fg";
}

/**
 * Probability track 0..1 with a 50% midline: optional p10–p90 band, a mean dot and an optional
 * comparison tick (e.g. the BPA baseline).
 */
export function ProbTrack({
  mean,
  lo,
  hi,
  compare,
  title,
}: {
  mean: number | null;
  lo?: number | null;
  hi?: number | null;
  compare?: number | null;
  title?: string;
}) {
  const x = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`;
  return (
    <div className="relative h-4 w-full" title={title}>
      <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-panel-2" />
      <div className="absolute top-0 bottom-0 w-px bg-line" style={{ left: "50%" }} />
      {lo != null && hi != null && (
        <div
          className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-accent/35"
          style={{ left: x(lo), width: `calc(${x(hi)} - ${x(lo)})` }}
        />
      )}
      {compare != null && (
        <div className="absolute top-0.5 bottom-0.5 w-0.5 rounded bg-muted" style={{ left: x(compare) }} />
      )}
      {mean != null && (
        <div
          className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-panel bg-accent"
          style={{ left: x(mean) }}
        />
      )}
    </div>
  );
}

/** Two-sided bar centred at 50%: grows right (green) above .5, left (red) below. */
export function EdgeBar({ p, title }: { p: number | null; title?: string }) {
  if (p == null) return <div className="h-2" />;
  const d = Math.max(-0.5, Math.min(0.5, p - 0.5));
  return (
    <div className="relative h-2 w-full rounded-full bg-panel-2" title={title}>
      <div className="absolute top-[-2px] bottom-[-2px] w-px bg-line" style={{ left: "50%" }} />
      <div
        className={`absolute top-0 bottom-0 rounded-full ${d >= 0 ? "bg-good/70" : "bg-bad/70"}`}
        style={d >= 0 ? { left: "50%", width: `${d * 100}%` } : { right: "50%", width: `${-d * 100}%` }}
      />
    </div>
  );
}

/** Simple left-anchored share bar (0..1). */
export function ShareBar({ v, tone = "accent", title }: { v: number | null; tone?: "accent" | "muted" | "good"; title?: string }) {
  const bg = tone === "accent" ? "bg-accent/70" : tone === "good" ? "bg-good/70" : "bg-muted/50";
  return (
    <div className="h-1.5 w-full rounded-full bg-panel-2" title={title}>
      {v != null && v > 0 && <div className={`h-full rounded-full ${bg}`} style={{ width: `${Math.min(1, v) * 100}%` }} />}
    </div>
  );
}

/** Tiny column histogram (e.g. P(finish k-th)); highlights bars <= `highlightUpTo`. */
export function MiniHist({
  bins,
  highlightUpTo,
  height = 44,
}: {
  bins: { label: string; p: number }[];
  highlightUpTo?: number;
  height?: number;
}) {
  const max = Math.max(0.0001, ...bins.map((b) => b.p));
  return (
    <div>
      <div className="flex items-end gap-[2px]" style={{ height }}>
        {bins.map((b, i) => (
          <div key={b.label} className="flex h-full flex-1 flex-col justify-end" title={`${b.label}: ${pct(b.p, 1)}`}>
            <div
              className={`rounded-t-[3px] ${highlightUpTo != null && i < highlightUpTo ? "bg-accent/80" : "bg-muted/40"}`}
              style={{ height: `${Math.max(b.p > 0 ? 2 : 0, (b.p / max) * 100)}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mt-0.5 flex gap-[2px] text-[9.5px] text-muted">
        {bins.map((b) => (
          <div key={b.label} className="num flex-1 text-center">
            {b.label}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Big headline number with a label and optional ± / sub-line. */
export function Hero({ label, value, sub, tone }: { label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] tracking-wider text-muted uppercase">{label}</div>
      <div className={`num text-2xl leading-tight font-semibold ${tone ?? ""}`}>{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** "± x" helper: renders `pct(v) ± pct(se)` compactly. */
export function PctSe({ v, se, digits = 0 }: { v: number | null; se?: number | null; digits?: number }) {
  return (
    <>
      {pct(v, digits)}
      {se != null && <span className="text-[0.7em] font-normal text-muted"> ±{pct(se, se < 0.01 ? 1 : digits)}</span>}
    </>
  );
}

/** Help text drawn from the data's own `explanations` (collapsed "?" on phones). */
export function Explain({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  return <p className="text-[13px] leading-snug text-muted">{text}</p>;
}

/** Section heading used inside the model tabs. */
export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-2 px-1 pt-2">
      <h2 className="shrink-0 text-[13px] font-semibold tracking-wide text-muted uppercase">{children}</h2>
      {right && <span className="min-w-0 truncate text-right text-xs text-muted">{right}</span>}
    </div>
  );
}

/** Rounded panel. */
export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-panel ${className}`}>{children}</section>;
}

/** Panel with a polished empty state. */
export function EmptyState({ icon = "◌", title, children }: { icon?: string; title: ReactNode; children?: ReactNode }) {
  return (
    <Panel className="px-5 py-6 text-center">
      <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-panel-2 text-lg text-muted">{icon}</div>
      <div className="mt-3 text-[15px] font-semibold">{title}</div>
      {children && <div className="mx-auto mt-1.5 max-w-md text-[13.5px] leading-relaxed text-muted">{children}</div>}
    </Panel>
  );
}

/** 9 cells, one per category: big number (e.g. rank) on a diverging background (z). */
export function CatStrip({
  cells,
  digits = 1,
}: {
  cells: Partial<Record<CatKey, { value: number | null; z: number | null }>>;
  digits?: number;
}) {
  return (
    <div className="grid grid-cols-9 gap-1 text-center">
      {CATS.map((c) => {
        const x = cells[c];
        return (
          <div key={c} className="min-w-0" title={`${CAT_LABEL[c]} — ${CAT_GLOSS[c]}`}>
            <div className="flex items-center justify-center gap-1 truncate text-[10px] text-muted">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: CAT_COLOR[c] }} />
              {CAT_LABEL[c]}
            </div>
            <div className="num mt-0.5 rounded py-1.5 text-[12.5px]" style={heatStyle(x?.z ?? null, 2)}>
              {x?.value == null ? "—" : Number.isInteger(x.value) ? x.value : x.value.toFixed(digits)}
            </div>
          </div>
        );
      })}
    </div>
  );
}
