import type { AgentEvent } from "@/lib/types";
import { Badge } from "@/components/ui";
import { LongText } from "./LongText";

/** Body of an advisor_suggestion card in the Agent updates feed: reports posted with ADVISOR_API_TOKEN (independent,
 *  read-only external agent; see /advisor). Shown as-is: they are suggestions for the main Claude agent, which re-checks
 *  them with its own tools; nothing here is executed. */

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

const LEVEL_TONE: Record<string, string> = { high: "bad", medium: "warn", low: "neutral" };
const LABEL_TONE: Record<string, string> = { fitted: "good", simulated: "accent", prior: "warn", literature: "neutral" };

function fmt(v: unknown): string {
  if (typeof v === "number") return Math.abs(v) >= 100 || Number.isInteger(v) ? String(v) : String(Math.round(v * 10000) / 10000);
  return String(v);
}

/** Labelled evidence items ({claim, value?, se?, label, command?}): advisor suggestions and thread replies.
 *  Collapsed to a count ("3 evidence items ▸"). */
export function EvidenceList({ items }: { items: Obj[] }) {
  if (!items.length) return null;
  return (
    <details className="mt-1">
      <summary className="sm tg">
        {items.length} evidence item{items.length === 1 ? "" : "s"}
      </summary>
      <EvidenceItems items={items} />
    </details>
  );
}

function EvidenceItems({ items }: { items: Obj[] }) {
  return (
    <ul className="mt-1 space-y-1 text-xs">
      {items.map((x, i) => (
        <li key={i} className="flex flex-wrap items-baseline gap-1.5">
          <Badge tone={LABEL_TONE[String(x.label)] ?? "neutral"}>{String(x.label ?? "?")}</Badge>
          <span className="min-w-0 break-words">{String(x.claim ?? "")}</span>
          {x.value != null && (
            <span className="num font-medium">
              {fmt(x.value)}
              {x.se != null && <span className="text-muted"> ± {fmt(x.se)} SE</span>}
            </span>
          )}
          {str(x.command) && <code className="max-w-full truncate rounded bg-panel-2 px-1 text-[11px] text-muted">{str(x.command)}</code>}
        </li>
      ))}
    </ul>
  );
}

export function SuggestionBadges({ e }: { e: AgentEvent }) {
  const p = isObj(e.payload) ? e.payload : {};
  const d = isObj(p.details) ? p.details : {};
  const urgency = str(d.urgency);
  const confidence = str(d.confidence);
  return (
    <>
      <Badge>{(str(d.category) ?? "other").replace(/_/g, " ")}</Badge>
      {urgency && (
        <Badge tone={LEVEL_TONE[urgency] ?? "neutral"} title="urgency">
          urgency {urgency}
        </Badge>
      )}
      {confidence && <Badge title="the advisor's own confidence">conf. {confidence}</Badge>}
    </>
  );
}

export function SuggestionBody({ e }: { e: AgentEvent }) {
  const p = isObj(e.payload) ? e.payload : {};
  const d = isObj(p.details) ? p.details : {};
  const evidence: Obj[] = Array.isArray(d.evidence) ? (d.evidence as unknown[]).filter(isObj) : [];
  const links = Array.isArray(d.links) ? d.links.filter((l): l is string => typeof l === "string" && l.startsWith("https://")) : [];
  const change = str(d.proposed_change);
  const summary = str(d.summary);
  return (
    <>
      <div className="text-[14.5px] font-medium leading-snug">{str(d.title) ?? e.message}</div>
      {summary && <LongText text={summary} className="mt-1 text-[13.5px] leading-snug text-muted" />}
      <EvidenceList items={evidence} />
      {change && (
        <details className="mt-1 text-xs">
          <summary className="sm tg">proposed change</summary>
          <pre className="scroll-thin mt-1 max-h-80 overflow-auto rounded border border-line bg-panel-2 p-2 text-[11.5px] leading-relaxed whitespace-pre">
            {change}
          </pre>
        </details>
      )}
      {links.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 text-xs">
          {links.map((l) => (
            <a key={l} href={l} target="_blank" rel="noreferrer nofollow" className="max-w-full truncate text-accent hover:underline">
              {l.replace(/^https:\/\//, "")}
            </a>
          ))}
        </div>
      )}
    </>
  );
}
