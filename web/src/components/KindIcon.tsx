const KINDS: Record<string, { g: string; c: string; label: string }> = {
  observation: { g: "◉", c: "text-sky-400", label: "observation" },
  command: { g: "›", c: "text-muted", label: "command" },
  model_output: { g: "∑", c: "text-violet-400", label: "model output" },
  decision: { g: "◆", c: "text-accent", label: "decision" },
  browser_action: { g: "⌖", c: "text-amber-400", label: "browser action" },
  verification: { g: "✓", c: "text-good", label: "verification" },
  error: { g: "✕", c: "text-bad", label: "error" },
  note: { g: "•", c: "text-muted", label: "note" },
};

export function KindIcon({ kind }: { kind: string }) {
  const k = KINDS[kind] ?? { g: "•", c: "text-muted", label: kind };
  return (
    <span title={k.label} className={`inline-grid w-4 shrink-0 place-items-center font-mono text-[12px] font-bold ${k.c}`}>
      {k.g}
    </span>
  );
}
