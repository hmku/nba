import type { AgentEvent } from "@/lib/types";
import { Badge } from "@/components/ui";
import { Ago } from "@/components/client";

/** "Suggestions from the advisor": advisor_suggestion reports posted with ADVISOR_API_TOKEN (independent, read-only
 *  external agent; see /advisor). Shown as-is and read-only: they are suggestions for the main Claude agent, which
 *  re-checks them with its own tools; nothing here is executed. Kept visually distinct from Muse's league intel. */

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

const LEVEL_TONE: Record<string, string> = { high: "bad", medium: "warn", low: "neutral" };
const LABEL_TONE: Record<string, string> = { fitted: "good", simulated: "accent", prior: "warn", literature: "neutral" };

export function isAdvisorSuggestion(e: AgentEvent): boolean {
  return e.run_id.startsWith("advisor-") && isObj(e.payload) && e.payload.report_kind === "advisor_suggestion";
}

function fmt(v: unknown): string {
  if (typeof v === "number") return Math.abs(v) >= 100 || Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000);
  return String(v);
}

function Suggestion({ e }: { e: AgentEvent }) {
  const p = isObj(e.payload) ? e.payload : {};
  const d = isObj(p.details) ? p.details : {};
  const evidence: Obj[] = Array.isArray(d.evidence) ? (d.evidence as unknown[]).filter((x): x is Obj => isObj(x)) : [];
  const links = Array.isArray(d.links) ? d.links.filter((l): l is string => typeof l === "string" && l.startsWith("https://")) : [];
  const change = str(d.proposed_change);
  const urgency = str(d.urgency);
  const confidence = str(d.confidence);
  return (
    <li className="border-t border-dashed border-line px-4 py-3 first:border-t-0">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Badge tone="accent">{(str(d.category) ?? "other").replace(/_/g, " ")}</Badge>
        {urgency && <Badge tone={LEVEL_TONE[urgency] ?? "neutral"} title="urgency">urgency {urgency}</Badge>}
        {confidence && <Badge title="the advisor's own confidence">conf. {confidence}</Badge>}
        <span className="ml-auto text-muted">
          <Ago ts={e.ts} />
        </span>
      </div>
      <div className="mt-1.5 text-[14.5px] font-medium leading-snug">{str(d.title) ?? e.message}</div>
      {str(d.summary) && <p className="mt-1 text-[13.5px] leading-snug whitespace-pre-wrap text-muted">{str(d.summary)}</p>}
      {evidence.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs">
          {evidence.map((x, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-1.5">
              <Badge tone={LABEL_TONE[String(x.label)] ?? "neutral"}>{String(x.label ?? "?")}</Badge>
              <span>{String(x.claim ?? "")}</span>
              {x.value != null && (
                <span className="num font-medium">
                  {fmt(x.value)}
                  {x.se != null && <span className="text-muted"> ± {fmt(x.se)} SE</span>}
                </span>
              )}
              {str(x.command) && <code className="rounded bg-panel-2 px-1 text-[11px] text-muted">{str(x.command)}</code>}
            </li>
          ))}
        </ul>
      )}
      {change && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-muted select-none">proposed change</summary>
          <pre className="scroll-thin mt-1 max-h-80 overflow-auto rounded border border-line bg-panel-2 p-2 text-[11.5px] leading-relaxed whitespace-pre">
            {change}
          </pre>
        </details>
      )}
      {links.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 text-xs">
          {links.map((l) => (
            <a key={l} href={l} target="_blank" rel="noreferrer nofollow" className="truncate text-accent hover:underline">
              {l.replace(/^https:\/\//, "")}
            </a>
          ))}
        </div>
      )}
    </li>
  );
}

export function AdvisorSuggestions({ events }: { events: AgentEvent[] }) {
  if (!events.length) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-medium text-muted">
        Suggestions from the advisor{" "}
        <span className="font-normal">· independent read-only reviewer (unverified until Claude checks them; nothing is executed)</span>
      </h2>
      <ul className="overflow-hidden rounded-xl border border-dashed border-accent/50 bg-panel">
        {events.map((e) => (
          <Suggestion key={e.event_id} e={e} />
        ))}
      </ul>
    </section>
  );
}
