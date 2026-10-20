/**
 * Loading placeholders for the route-level `loading.tsx` files: shown the instant a tab is tapped (they are
 * prefetched with the layout), then streamed over by the server-rendered page. Pure server components.
 */

export function Bone({ className = "" }: { className?: string }) {
  return <div className={`rounded-md bg-panel-2 motion-safe:animate-pulse ${className}`} />;
}

/** Page title (real text, so the tab switch is unambiguous) + optional subtitle bone. */
export function SkeletonTitle({ title, sub = true }: { title: string; sub?: boolean }) {
  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      {sub && <Bone className="mt-1.5 h-3.5 w-2/3 max-w-md" />}
    </div>
  );
}

/** A panel with `rows` list lines. */
export function SkeletonPanel({ rows = 4, head = true, className = "" }: { rows?: number; head?: boolean; className?: string }) {
  return (
    <div className={`rounded-xl border border-line bg-panel p-4 ${className}`}>
      {head && <Bone className="mb-3 h-4 w-40" />}
      <div className="space-y-2.5">
        {Array.from({ length: rows }, (_, i) => (
          <Bone key={i} className={`h-3.5 ${i % 3 === 2 ? "w-3/5" : i % 2 ? "w-4/5" : "w-full"}`} />
        ))}
      </div>
    </div>
  );
}

/** Screen-reader announcement for the loading state. */
export function LoadingLabel({ what }: { what: string }) {
  return (
    <p className="sr-only" role="status">
      Loading {what}…
    </p>
  );
}
