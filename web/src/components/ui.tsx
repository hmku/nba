import type { ReactNode } from "react";

export function Card({
  title,
  right,
  children,
  className = "",
  bodyClassName = "",
  id,
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <section id={id} className={`rounded-lg border border-line bg-panel min-w-0 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted">{title}</h2>
          {right && <div className="flex items-center gap-2 text-xs text-muted">{right}</div>}
        </header>
      )}
      <div className={bodyClassName || "p-3"}>{children}</div>
    </section>
  );
}

const TONES: Record<string, string> = {
  neutral: "bg-panel-2 text-muted border-line",
  good: "bg-good/10 text-good border-good/30",
  bad: "bg-bad/10 text-bad border-bad/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  accent: "bg-accent/10 text-accent border-accent/30",
};

export function Badge({ tone = "neutral", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-px text-[11px] font-medium leading-4 whitespace-nowrap ${TONES[tone] ?? TONES.neutral}`}
    >
      {children}
    </span>
  );
}

export function statusTone(status: string | null | undefined): string {
  switch ((status || "").toLowerCase()) {
    case "running":
    case "live":
    case "in_progress":
    case "submitted":
    case "pending":
    case "awaiting_approval":
      return "accent";
    case "succeeded":
    case "complete":
    case "verified":
    case "approved":
    case "done":
      return "good";
    case "failed":
    case "rejected":
    case "error":
    case "blocked":
      return "bad";
    case "abandoned":
    case "expired":
    case "cancelled":
      return "warn";
    default:
      return "neutral";
  }
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-4 text-center text-sm text-muted">{children}</div>;
}

export function JsonView({ value, label = "JSON" }: { value: unknown; label?: string }) {
  if (value == null) return <span className="text-xs text-muted">—</span>;
  return (
    <details className="group">
      <summary className="cursor-pointer select-none text-xs text-accent">{label}</summary>
      <pre className="scroll-thin mt-1 max-h-80 overflow-auto rounded border border-line bg-panel-2 p-2 text-[11px] leading-snug">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

export function ErrorNote({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <div className="rounded border border-bad/30 bg-bad/10 px-3 py-2 text-xs text-bad">Supabase error: {error}</div>
  );
}

/** Collapsed-by-default expander (native <details>, works in server components). */
export function Disclosure({
  summary,
  right,
  children,
  open,
  className = "",
}: {
  summary: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  open?: boolean;
  className?: string;
}) {
  return (
    <details open={open} className={`group rounded-xl border border-line bg-panel ${className}`}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 select-none [&::-webkit-details-marker]:hidden">
        <span className="text-xs text-muted transition-transform group-open:rotate-90">▶</span>
        <span className="min-w-0 flex-1 text-[15px] font-medium">{summary}</span>
        {right && <span className="shrink-0 text-xs text-muted">{right}</span>}
      </summary>
      <div className="border-t border-line">{children}</div>
    </details>
  );
}
