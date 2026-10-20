/* Pieces of a recommendation (advice/1) card: evidence chips, Why / What changed, metrics, scenarios, risks, links.
 * Server components; shapes from src/lib/moves.ts. */
import Link from "next/link";
import { EVIDENCE_HELP, formatMetric, isLongWhy, linkHref, type Advice, type Cell, type Evidence } from "@/lib/moves";
import { Badge, statusTone } from "@/components/ui";

const EVIDENCE_TONE: Record<Evidence, string> = {
  fitted: "good",
  simulated: "accent",
  prior: "warn",
  literature: "neutral",
};

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

export function EvidenceChip({ e }: { e: Evidence | null }) {
  if (!e) return <Badge tone="bad" title="No evidence label: treat as unverified">unlabelled</Badge>;
  return (
    <Badge tone={EVIDENCE_TONE[e]} title={EVIDENCE_HELP[e]}>
      {e}
    </Badge>
  );
}

export function EvidenceLegend() {
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-1 text-[11px] text-muted">
      Evidence:
      {(Object.keys(EVIDENCE_TONE) as Evidence[]).map((e) => (
        <span key={e} title={EVIDENCE_HELP[e]}>
          <EvidenceChip e={e} />
        </span>
      ))}
      <span>± = Monte Carlo SE (not model error)</span>
    </div>
  );
}

function fmtCell(v: Cell): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

export const SUBHEAD = "text-xs font-semibold tracking-wide text-muted uppercase";

/** "Why" (rationale) and "What changed" (why_now: before -> changed). */
function WhyBody({ m }: { m: Advice }) {
  const bullets = m.rationale.filter((r) => r.bullet);
  return (
    <div className="space-y-2.5">
      {m.rationale.length > 0 && (
        <div>
          <div className={SUBHEAD}>Why</div>
          {m.rationale
            .filter((r) => !r.bullet)
            .map((r, i) => (
              <p key={i} className="mt-1 text-[13.5px] leading-snug">
                {r.text}
              </p>
            ))}
          {bullets.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[13.5px] leading-snug">
              {bullets.map((r, i) => (
                <li key={i}>{r.text}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {m.whyNow && (
        <div>
          <div className={SUBHEAD}>What changed</div>
          {m.whyNow.before && (
            <p className="mt-1 text-[13px] leading-snug text-muted">
              <span className="font-medium text-fg">Before: </span>
              {m.whyNow.before}
            </p>
          )}
          {m.whyNow.changed.length > 0 && (
            <ul className="mt-1 space-y-1 text-[13.5px] leading-snug">
              {m.whyNow.changed.map((c, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-accent">→</span>
                  <span>
                    {c.kind && (
                      <span className="mr-1">
                        <Badge tone="neutral">{c.kind}</Badge>
                      </span>
                    )}
                    {c.text}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {m.whyNow.evidence.length > 0 && (
            <div className="mt-1 text-xs text-muted">Backed by: {m.whyNow.evidence.join(" · ")} (metrics below)</div>
          )}
        </div>
      )}
    </div>
  );
}

/** Long explanations collapse to a tap-to-expand block on phones only (`collapse` forces the collapsed form). */
export function WhySection({ m, collapse = false }: { m: Advice; collapse?: boolean }) {
  if (!m.rationale.length && !m.whyNow) return null;
  const box = "mt-2 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2";
  if (!collapse && !isLongWhy(m.rationale, m.whyNow))
    return (
      <div className={box}>
        <WhyBody m={m} />
      </div>
    );
  const teaser = m.rationale[0]?.text ?? m.whyNow?.changed[0]?.text ?? m.whyNow?.before ?? "";
  const details = (
    <details className={`${box} group ${collapse ? "" : "sm:hidden"}`}>
      <summary className="cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden">
        <span className={SUBHEAD}>Why · what changed</span>
        <span className="ml-1 text-xs text-accent group-open:hidden">show</span>
        <span className="ml-1 hidden text-xs text-accent group-open:inline">hide</span>
        <p className="mt-1 line-clamp-2 text-[13.5px] leading-snug group-open:hidden">{teaser}</p>
      </summary>
      <div className="mt-1.5">
        <WhyBody m={m} />
      </div>
    </details>
  );
  if (collapse) return details;
  return (
    <>
      <div className={`${box} hidden sm:block`}>
        <WhyBody m={m} />
      </div>
      {details}
    </>
  );
}

function MetricRows({ m, rows }: { m: Advice; rows: Advice["metrics"] }) {
  return (
    <table className="w-full text-[13px]">
      <tbody>
        {rows.map((x, i) => (
          <tr key={i} className="border-t border-line align-top first:border-t-0">
            <td className="py-1.5 pr-3">
              {x.label}
              {x.note && <div className="text-xs text-muted">{x.note}</div>}
            </td>
            <td className="num py-1.5 pr-3 text-right whitespace-nowrap">{formatMetric(x)}</td>
            <td className="py-1.5 text-right">
              <EvidenceChip e={x.evidence} />
            </td>
          </tr>
        ))}
        {rows.length === 0 && m.metrics.length === 0 && (
          <tr>
            <td className="py-1 text-xs text-muted">No metrics posted.</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function Scenarios({ m }: { m: Advice }) {
  if (!m.scenarios.length) return null;
  return (
    <div className="scroll-thin mt-2 overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-[12.5px]">
        <thead className="bg-panel-2/60">
          <tr className="text-left text-[11px] text-muted">
            {m.scenarioColumns.map((c) => (
              <th key={c} className="px-2.5 py-1.5 font-medium whitespace-nowrap">
                {c === "name" ? "Scenario" : c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {m.scenarios.map((r, i) => (
            <tr key={i} className="border-t border-line">
              {m.scenarioColumns.map((c) => (
                <td key={c} className={`px-2.5 py-1.5 ${c === "name" ? "" : "num whitespace-nowrap"}`}>
                  {fmtCell(r[c] ?? null)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Key metrics (the first `keyN`) inline; the full table + scenarios behind a disclosure. `keyN = 0` collapses all. */
export function MetricsBlock({ m, keyN = 3 }: { m: Advice; keyN?: number }) {
  if (!m.metrics.length && !m.scenarios.length) return null;
  const head = m.metrics.slice(0, keyN);
  const more = m.metrics.length > head.length || m.scenarios.length > 0;
  return (
    <div className="mt-2">
      {head.length > 0 && (
        <>
          <div className={SUBHEAD}>Key numbers</div>
          <div className="scroll-thin overflow-x-auto">
            <MetricRows m={m} rows={head} />
          </div>
        </>
      )}
      {more && (
        <details className="group/m mt-1">
          <summary className="cursor-pointer list-none text-xs text-accent select-none [&::-webkit-details-marker]:hidden">
            <span className="group-open/m:hidden">
              {head.length ? "All" : "Show"} {m.metrics.length} metric{m.metrics.length === 1 ? "" : "s"}
              {m.scenarios.length ? ` · ${m.scenarios.length} scenario${m.scenarios.length === 1 ? "" : "s"}` : ""} ▸
            </span>
            <span className="hidden group-open/m:inline">Hide full table ▾</span>
          </summary>
          <div className="scroll-thin mt-1 overflow-x-auto">
            <MetricRows m={m} rows={m.metrics} />
          </div>
          <Scenarios m={m} />
        </details>
      )}
    </div>
  );
}

export function Risks({ m }: { m: Advice }) {
  if (!m.risks.length) return null;
  return (
    <div className="mt-2">
      <div className={SUBHEAD}>Risks</div>
      <ul className="mt-1 space-y-1 text-[13px] leading-snug">
        {m.risks.map((r, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="text-warn">!</span>
            <span>{r}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AdviceLinks({ m }: { m: Advice }) {
  return (
    <>
      {m.links.map((l) => {
        const { href, internal } = linkHref(l.path);
        return internal ? (
          <Link key={l.path} href={href} className="text-accent">
            {l.label}
          </Link>
        ) : (
          <a key={l.path} href={href} target="_blank" rel="noreferrer" className="text-accent">
            {l.label} ↗
          </a>
        );
      })}
    </>
  );
}

export function Verdict({ m }: { m: Advice }) {
  if (!m.verdict) return null;
  return (
    <p className="mt-2 rounded-lg border border-line bg-panel-2/60 px-3 py-2 text-[13.5px] leading-snug whitespace-pre-line">
      <span className="mr-1 text-xs font-semibold tracking-wide text-muted uppercase">Verdict</span>
      {m.verdict}
    </p>
  );
}
