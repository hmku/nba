"use client";

/* The decision card's "Details" (Why, What changed, metrics + scenarios, risks, links, ids). A client component that
 * renders its body only once opened: the advice arrives once as JSON in the RSC payload instead of twice as markup
 * (server HTML + RSC tree), which keeps /inbox small. Opening needs no request. */
import { useState, type ReactNode } from "react";
import Link from "next/link";
import { EVIDENCE_HELP, formatMetric, linkHref, type Advice, type AdviceMetric, type Cell } from "@/lib/moves";

/** Evidence dot (colour = label; legend once at the top of the page). */
export function Dot({ e }: { e: AdviceMetric["evidence"] }) {
  return <span className={`d ${e ?? ""}`} title={e ? EVIDENCE_HELP[e] : "No evidence label: treat as unverified"} />;
}

/** The advice fields the Details body reads (the server passes only these: they travel as JSON in the RSC payload). */
export type DetailsAdvice = Pick<Advice, "headline" | "rationale" | "whyNow" | "metrics" | "scenarioColumns" | "scenarios" | "risks" | "links">;

/** Small print at the bottom: approval request text, posted/updated, ids (times pre-formatted on the server, ET). */
export interface DetailsMeta {
  approvalSummary: string | null;
  approvalIds: { id: string; status: string }[];
  when: string; // "posted Oct 20, 2:04 AM · updated … (v2)" or "requested …"
  adviceId: string | null;
}

function fmtCell(v: Cell): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function Block({ h, children }: { h: string; children: ReactNode }) {
  return (
    <div>
      <div className="sh">{h}</div>
      {children}
    </div>
  );
}

const bullets = (xs: ReactNode[], cls = "") => (
  <ul className={`list-disc space-y-0.5 pl-4 ${cls}`}>
    {xs.map((x, i) => (
      <li key={i}>{x}</li>
    ))}
  </ul>
);

function Body({ m, verdictRest, meta }: { m: DetailsAdvice; verdictRest: string; meta: DetailsMeta }) {
  const w = m.whyNow;
  return (
    <div className="mt-1.5 space-y-2 text-[13.5px] leading-snug">
      {(m.headline || verdictRest) && (
        <p className="whitespace-pre-line">
          {m.headline}
          {m.headline && verdictRest ? " " : ""}
          {verdictRest && <span className="text-muted">{verdictRest}</span>}
        </p>
      )}
      {m.rationale.length > 0 && <Block h="Why">{bullets(m.rationale.map((r) => r.text))}</Block>}
      {w && (
        <Block h="What changed">
          {w.before && <p className="text-muted">Before: {w.before}</p>}
          {w.changed.length > 0 &&
            bullets(
              w.changed.map((c) => (
                <>
                  {c.kind && <span className="text-muted">[{c.kind}] </span>}
                  {c.text}
                </>
              )),
            )}
          {w.evidence.length > 0 && <p className="text-xs text-muted">Backed by: {w.evidence.join(" · ")}</p>}
        </Block>
      )}
      {(m.metrics.length > 0 || m.scenarios.length > 0) && (
        <Block h="Numbers">
          {m.metrics.length > 0 && (
            <table className="mt">
              <tbody>
                {m.metrics.map((x, i) => (
                  <tr key={i}>
                    <td>
                      <Dot e={x.evidence} /> {x.label}
                      {x.note && <i> · {x.note}</i>}
                    </td>
                    <td>{formatMetric(x)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {m.scenarios.length > 0 && (
            <div className="scroll-thin overflow-x-auto">
              {/* .sc: every column after the first is numeric (no-wrap, tabular) */}
              <table className="dense sc">
                <thead>
                  <tr>
                    {m.scenarioColumns.map((c) => (
                      <th key={c}>{c === "name" ? "scenario" : c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {m.scenarios.map((r, i) => (
                    <tr key={i}>
                      {m.scenarioColumns.map((c) => (
                        <td key={c}>{fmtCell(r[c] ?? null)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Block>
      )}
      {m.risks.length > 0 && <Block h="Risks">{bullets(m.risks, "marker:text-warn")}</Block>}
      <div className="space-y-0.5 text-[11px] text-muted">
        {m.links.length > 0 && (
          <p className="flex flex-wrap gap-x-3 text-xs">
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
          </p>
        )}
        {meta.approvalSummary && <p>Approval request: {meta.approvalSummary}</p>}
        <p className="flex flex-wrap gap-x-3">
          <span>{meta.when}</span>
          {meta.approvalIds.map((a) => (
            <code key={a.id} title={`approval ${a.status}`}>
              {a.id}
            </code>
          ))}
          {meta.adviceId && <code title="advice_id">{meta.adviceId}</code>}
        </p>
      </div>
    </div>
  );
}

/** "Details ▸" disclosure; the body mounts on first open (and stays mounted). */
export function AdviceDetails({ m, verdictRest, meta }: { m: DetailsAdvice; verdictRest: string; meta: DetailsMeta }) {
  const [shown, setShown] = useState(false);
  return (
    <details className="min-w-0 max-w-full open:basis-full" onToggle={(e) => e.currentTarget.open && setShown(true)}>
      <summary className="sm tg">Details</summary>
      {shown && <Body m={m} verdictRest={verdictRest} meta={meta} />}
    </details>
  );
}
