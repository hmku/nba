"use client";

import { useState } from "react";

/** Collapsed raw JSON whose <pre> mounts on first open: the value travels once (RSC payload), not also as HTML. */
export function LazyJson({ value, label }: { value: unknown; label: string }) {
  const [shown, setShown] = useState(false);
  if (value == null) return null;
  return (
    <details onToggle={(e) => e.currentTarget.open && setShown(true)}>
      <summary className="sm tg">{label}</summary>
      {shown && (
        <pre className="scroll-thin mt-1 max-h-80 overflow-auto rounded border border-line bg-panel-2 p-2 text-[11px] leading-snug">
          {JSON.stringify(value, null, 2)}
        </pre>
      )}
    </details>
  );
}
