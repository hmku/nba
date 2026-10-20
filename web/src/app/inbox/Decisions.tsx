/* Inbox decision cards: one card per decision = recommendation (advice) + its approval + its discussion threads.
 * Collapsed by default: type + title, a one-line verdict, up to 3 key-metric chips, Approve / Reject; everything else
 * sits behind "Details" and "💬 n". Server components; data joined by src/lib/inbox.ts. */
import Link from "next/link";
import type { ReactNode } from "react";
import type { Decision } from "@/lib/types";
import type { FeedItem } from "@/lib/feed";
import { splitVerdict, STATUS_LABEL, TYPE_LABEL } from "@/lib/moves";
import { isExpired, type ApprovalRow, type InboxCard } from "@/lib/inbox";
import { shortTime } from "@/lib/format";
import { Badge, statusTone } from "@/components/ui";
import { LazyJson } from "./LazyJson";
import { Ago } from "@/components/client";
import { SectionTitle } from "@/components/viz";
import { Chips, TYPE_TONE } from "./AdviceParts";
import { AdviceDetails, type DetailsMeta } from "./AdviceDetails";
import { DecideButtons } from "./DecideButtons";
import { FeedCard } from "./Feed";
import { ReplyBox } from "./ReplyBox";

export type Variant = "yes" | "queued" | "idea" | "closed";

function titleOf(c: InboxCard): string {
  return c.advice?.title ?? c.approval?.summary ?? c.key;
}

/** Expiry next to Approve / Reject. */
function Expiry({ a, now }: { a: ApprovalRow; now: number }) {
  if (!a.expires_at) return null;
  if (isExpired(a, now)) return <span className="font-medium text-warn">expired {shortTime(a.expires_at)}: the agent re-asks</span>;
  return (
    <span title={`${shortTime(a.expires_at)} ET`}>
      expires <Ago ts={a.expires_at} />
    </span>
  );
}

/** "💬 n": the card's linked threads + the comment box, collapsed. */
function Discussion({ c, items, decisions }: { c: InboxCard; items: FeedItem[]; decisions: Map<string, Decision> }) {
  const ref = c.advice ? { advice_id: c.advice.adviceId } : { approval_id: c.key };
  const n = items.reduce((t, it) => t + 1 + it.replies.length, 0);
  return (
    <details className="min-w-0 max-w-full open:basis-full">
      <summary className="sm tg" title="Discussion">
        💬 {n}
      </summary>
      <div className="mt-1.5 space-y-1.5">
        {items.length > 0 ? (
          <ul className="fl">
            {items.map((it) => (
              <FeedCard key={it.key} it={it} decisions={decisions} nested />
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted">No discussion yet.</p>
        )}
        <ReplyBox comment={ref} label="Comment" />
      </div>
    </details>
  );
}

/** Small print at the bottom of Details (times formatted here, ET): approval request text, posted/updated, ids. */
function metaOf(c: InboxCard): DetailsMeta {
  const m = c.advice;
  return {
    approvalSummary: m ? (c.approval?.summary ?? null) : null,
    approvalIds: c.approvals.map((a) => ({ id: a.approval_id, status: a.status })),
    when: m
      ? `posted ${shortTime(m.firstCreatedAt)}${m.versions > 1 ? ` · updated ${shortTime(m.createdAt)} (v${m.version})` : ""}`
      : `requested ${shortTime(c.approval?.requested_at)}`,
    adviceId: m?.adviceId ?? null,
  };
}

/** The same small print for an approval-only card (server-rendered: no advice to defer). */
function Ids({ c }: { c: InboxCard }) {
  const meta = metaOf(c);
  return (
    <p className="flex flex-wrap gap-x-3 text-[11px] text-muted">
      <span>{meta.when}</span>
      {meta.approvalIds.map((a) => (
        <code key={a.id} title={`approval ${a.status}`}>
          {a.id}
        </code>
      ))}
    </p>
  );
}

/** Line under the chips: what happened / what is needed (no paragraphs of policy text: one line at the section top). */
function StateLine({ c, variant, now }: { c: InboxCard; variant: Variant; now: number }): ReactNode {
  const a = c.approval;
  if (a?.status === "pending") return <DecideButtons id={a.approval_id}>{<Expiry a={a} now={now} />}</DecideButtons>;
  if (variant === "queued") {
    const ok = a?.status === "approved" ? a : null;
    return (
      <p className="mt-1.5 text-xs text-good">
        ✓ You said yes{ok?.decided_at && <> {shortTime(ok.decided_at)}</>}
        {ok?.note && <> (“{ok.note}”)</>} · queued for the next run
      </p>
    );
  }
  if (variant === "yes" && c.advice) return <p className="mt-1.5 text-xs text-muted">No approval request filed yet (or say yes in chat).</p>;
  if (variant === "closed" && a && a.status !== "pending")
    return (
      <p className="mt-1.5 text-xs text-muted">
        Approval {a.status}
        {a.decided_at && <> {shortTime(a.decided_at)}</>}
        {a.decided_by && <> by {a.decided_by}</>}
        {a.note && <> · “{a.note}”</>}
      </p>
    );
  return null;
}

/** Anchors for every id the card answers to (advice_id is the card's own id; approvals get an empty anchor). */
function Anchors({ c }: { c: InboxCard }) {
  return (
    <>
      {c.approvals
        .filter((a) => a.approval_id !== c.key)
        .map((a) => (
          <span key={a.approval_id} id={a.approval_id} className="block scroll-mt-16" />
        ))}
    </>
  );
}

/** One decision card, collapsed. Ideas use a single-line verdict; the others allow two lines. */
export function DecisionCardView({
  c,
  variant,
  threads,
  decisions,
  now,
}: {
  c: InboxCard;
  variant: Variant;
  threads: FeedItem[];
  decisions: Map<string, Decision>;
  now: number;
}) {
  const m = c.advice;
  const a = c.approval;
  const { first, rest } = splitVerdict(m?.verdict ?? m?.headline ?? null);
  const verdictRest = m?.verdict ? rest : "";
  const frame = variant === "yes" ? "border-accent/50 bg-accent/5" : "border-line bg-panel";
  const expired = a?.status === "pending" && isExpired(a, now);
  return (
    <article id={c.key} className={`scroll-mt-16 rounded-xl border px-3.5 py-2.5 ${frame}`}>
      <Anchors c={c} />
      {/* closed cards sit under a one-line summary that already shows status + title */}
      {variant !== "closed" && <div className="flex items-center gap-1.5">
        <Badge tone={TYPE_TONE[m?.type ?? ""] ?? "neutral"}>{m ? (TYPE_LABEL[m.type] ?? m.type) : (a?.action_type.replace(/_/g, " ") ?? "?")}</Badge>
        {expired && <Badge tone="warn">expired</Badge>}
        <h3 className="line-clamp-2 min-w-0 flex-1 text-[15px] leading-snug font-semibold sm:line-clamp-1" title={titleOf(c)}>
          {titleOf(c)}
        </h3>
        <span className="shrink-0 text-xs text-muted">
          <Ago ts={m?.firstCreatedAt ?? m?.createdAt ?? a?.requested_at} />
        </span>
      </div>}
      {first && <p className={`mt-0.5 text-[13.5px] leading-snug ${variant === "idea" ? "t1" : "line-clamp-2"}`}>{first}</p>}
      {m && <Chips m={m} />}
      <StateLine c={c} variant={variant} now={now} />
      <div className="mt-1.5 flex flex-wrap items-start gap-x-4 gap-y-1">
        {m ? (
          <AdviceDetails
            m={{
              headline: m.headline,
              rationale: m.rationale,
              whyNow: m.whyNow,
              metrics: m.metrics,
              scenarioColumns: m.scenarioColumns,
              scenarios: m.scenarios,
              risks: m.risks,
              links: m.links,
            }}
            verdictRest={verdictRest}
            meta={metaOf(c)}
          />
        ) : (
          <details className="min-w-0 max-w-full open:basis-full">
            <summary className="sm tg">Details</summary>
            <div className="mt-1.5 space-y-1">
              {a?.payload != null && <LazyJson value={a.payload} label="approval payload" />}
              <Ids c={c} />
            </div>
          </details>
        )}
        <Discussion c={c} items={threads} decisions={decisions} />
      </div>
    </article>
  );
}

export function DecisionSection({
  title,
  sub,
  cards,
  variant,
  threadsOf,
  decisions,
  now,
  empty,
}: {
  title: string;
  sub?: ReactNode;
  cards: InboxCard[];
  variant: Variant;
  threadsOf: (key: string) => FeedItem[];
  decisions: Map<string, Decision>;
  now: number;
  /** Shown instead of hiding the section when it has no cards. */
  empty?: string;
}) {
  if (!cards.length && !empty) return null;
  return (
    <section className="space-y-2">
      <div>
        <SectionTitle>
          {title} · {cards.length}
        </SectionTitle>
        {sub && <p className="text-xs text-muted">{sub}</p>}
      </div>
      {cards.length === 0 && <p className="rounded-xl border border-line bg-panel px-4 py-2 text-[13px] text-muted">{empty}</p>}
      {cards.map((c) => (
        <DecisionCardView key={c.key} c={c} variant={variant} threads={threadsOf(c.key)} decisions={decisions} now={now} />
      ))}
    </section>
  );
}

/** Collapsed "Done / rejected" list: one line per decision (newest first), each expanding to its compact card. */
export function ClosedDecisions({
  cards,
  threadsOf,
  decisions,
  now,
}: {
  cards: InboxCard[];
  threadsOf: (key: string) => FeedItem[];
  decisions: Map<string, Decision>;
  now: number;
}) {
  if (!cards.length) return null;
  return (
    <details className="rounded-xl border border-line bg-panel">
      <summary className="sm flex items-center gap-2 px-4 py-2.5 text-[15px] font-medium">
        <span className="flex-1">Done / rejected · {cards.length}</span>
        <Link href="/week#decisions" className="text-xs font-normal text-accent">
          by week →
        </Link>
      </summary>
      <ul className="divide-y divide-line border-t border-line">
        {cards.map((c) => {
          const st = c.advice?.status ?? c.approval?.status ?? "";
          const when = c.approval?.decided_at ?? c.advice?.createdAt ?? c.approval?.requested_at;
          return (
            <li key={c.key}>
              <details>
                <summary className="sm flex items-center gap-2 px-4 py-2">
                  <Badge tone={statusTone(st)}>{STATUS_LABEL[st] ?? st}</Badge>
                  <span className="t1 min-w-0 flex-1 text-[13.5px]">{titleOf(c)}</span>
                  <span className="shrink-0 text-xs text-muted">{shortTime(when)}</span>
                </summary>
                <div className="px-3 pb-3">
                  <DecisionCardView c={c} variant="closed" threads={threadsOf(c.key)} decisions={decisions} now={now} />
                </div>
              </details>
            </li>
          );
        })}
      </ul>
    </details>
  );
}
