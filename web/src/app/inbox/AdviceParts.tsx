/* Pieces of a recommendation (advice/1) card shown collapsed: key-metric chips with evidence dots and the evidence
 * legend. The "Details" body lives in AdviceDetails.tsx (client, rendered on open). Shapes from src/lib/moves.ts. */
import { chipValue, EVIDENCE, EVIDENCE_HELP, formatMetric, keyMetrics, type Advice, type AdviceMetric } from "@/lib/moves";
import { statusTone } from "@/components/ui";
import { Dot } from "./AdviceDetails";

export const TYPE_TONE: Record<string, string> = {
  trade: "accent",
  add_drop: "warn",
  stream: "neutral",
  lineup: "good",
  il: "warn",
};

export function statusBadgeTone(status: string): string {
  return status === "awaiting_yes" ? "accent" : status === "idea" ? "neutral" : statusTone(status);
}

export function EvidenceLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted">
      {EVIDENCE.map((e) => (
        <span key={e} title={EVIDENCE_HELP[e]} className="inline-flex items-center gap-1">
          <Dot e={e} />
          {e}
        </span>
      ))}
      <span>± = Monte Carlo SE</span>
    </div>
  );
}

const tip = (x: AdviceMetric) => `${x.label}: ${formatMetric(x)}${x.evidence ? ` (${x.evidence})` : " (unlabelled)"}${x.note ? ` · ${x.note}` : ""}`;

/** Up to 3 key-metric chips (`key: true` metrics, else the first three); value ± SE and the note on hover. */
export function Chips({ m }: { m: Advice }) {
  const xs = keyMetrics(m.metrics);
  if (!xs.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {xs.map((x, i) => (
        <span key={i} className="ch" title={tip(x)}>
          <Dot e={x.evidence} />
          <i>{x.label}</i>
          <b>{chipValue(x)}</b>
        </span>
      ))}
    </div>
  );
}
