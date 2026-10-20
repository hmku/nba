/* Inbox decision cards: one card per decision = recommendation (advice) + its approval + its discussion threads.
 * Server components; data joined by src/lib/inbox.ts. */
import Link from "next/link";
import type { Decision } from "@/lib/types";
import type { FeedItem } from "@/lib/feed";
import { STATUS_LABEL, TYPE_LABEL } from "@/lib/moves";
import { isExpired, type ApprovalRow, type InboxCard } from "@/lib/inbox";
import { shortTime } from "@/lib/format";
import { Badge, Disclosure, JsonView, statusTone } from "@/components/ui";
import { Ago } from "@/components/client";
import { SectionTitle } from "@/components/viz";
import { AdviceLinks, MetricsBlock, Risks, statusBadgeTone, SUBHEAD, TYPE_TONE, Verdict, WhySection } from "./AdviceParts";
import { DecideButtons } from "./DecideButtons";
import { FeedCard } from "./Feed";
import { ReplyBox } from "./ReplyBox";

export type Variant = "yes" | "queued" | "idea" | "closed";

/** The policy line next to Approve / Reject (user decision 2026-10-20). */
const APPROVE_MEANS =
  "Approving counts as your yes; the next daily run (7:10 ET) or the main session executes it after re-checking Yahoo.";

function titleOf(c: InboxCard): string {
  return c.advice?.title ?? c.approval?.summary ?? c.key;
}

function ApprovalBadge({ a, now }: { a: ApprovalRow; now: number }) {
  const exp = a.status === "pending" && isExpired(a, now);
  return (
    <Badge tone={exp ? "warn" : statusTone(a.status)} title={`approval ${a.approval_id}`}>
      {a.action_type.replace(/_/g, " ")} · {exp ? "expired" : a.status === "pending" ? "waiting for you" : a.status}
    </Badge>
  );
}

function Header({ c, now }: { c: InboxCard; now: number }) {
  const m = c.advice;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      {m && <Badge tone={TYPE_TONE[m.type] ?? "neutral"}>{TYPE_LABEL[m.type] ?? m.type}</Badge>}
      {m && <Badge tone={statusBadgeTone(m.status)}>{STATUS_LABEL[m.status] ?? m.status}</Badge>}
      {c.approval && <ApprovalBadge a={c.approval} now={now} />}
      {m?.requiresUserYes && !c.approval && m.status === "idea" && <Badge title="Policy: this move needs your explicit yes">needs yes if pursued</Badge>}
      {m?.forDate && <span className="text-muted">for {m.forDate}</span>}
      {m?.week != null && <span className="text-muted">week {m.week}</span>}
      <span className="ml-auto text-muted" title={m?.createdAt ?? c.approval?.requested_at ?? undefined}>
        <Ago ts={m?.firstCreatedAt ?? m?.createdAt ?? c.approval?.requested_at} />
      </span>
    </div>
  );
}

/** Approve / Reject with the policy line and the expiry (pending approvals only). */
function ApprovalBox({ a, now }: { a: ApprovalRow; now: number }) {
  const exp = isExpired(a, now);
  return (
    <div className="mt-3 rounded-lg border border-accent/40 bg-panel px-3 py-2.5">
      <div className="text-[13px] leading-snug">
        <span className="font-semibold">Your call: </span>
        {a.summary ?? a.approval_id}
      </div>
      <p className="mt-1 text-xs leading-snug text-muted">{APPROVE_MEANS}</p>
      {a.expires_at && (
        <p className={`mt-1 text-xs ${exp ? "font-medium text-warn" : "text-muted"}`}>
          {exp ? (
            <>Expired {shortTime(a.expires_at)} ET: the executor will not act on it; the agent re-asks if the move still makes sense.</>
          ) : (
            <>
              Expires <Ago ts={a.expires_at} /> ({shortTime(a.expires_at)} ET).
            </>
          )}
        </p>
      )}
      <DecideButtons id={a.approval_id} />
    </div>
  );
}

function Queued({ c }: { c: InboxCard }) {
  const a = c.approval?.status === "approved" ? c.approval : null;
  return (
    <p className="mt-2 rounded-lg border border-good/40 bg-good/10 px-3 py-2 text-[13px] leading-snug">
      <span className="font-semibold text-good">You said yes</span>
      {a?.decided_at && <> {shortTime(a.decided_at)} ET</>}
      {a?.note && <> (“{a.note}”)</>}. Queued: the next daily run (7:10 ET) or the main session executes it after re-checking
      Yahoo, then marks it done here.
    </p>
  );
}

function NoApprovalYet() {
  return (
    <p className="mt-3 rounded-lg border border-line bg-panel-2/60 px-3 py-2 text-xs leading-snug text-muted">
      No approval request filed yet: the agent files one (it appears here with Approve / Reject) before anything changes
      on Yahoo. You can also say yes in chat.
    </p>
  );
}

/** The card's threads (feed items linked to the decision) and the "comment" box. */
function Discussion({ c, items, decisions, open }: { c: InboxCard; items: FeedItem[]; decisions: Map<string, Decision>; open: boolean }) {
  const ref = c.advice ? { advice_id: c.advice.adviceId } : { approval_id: c.key };
  const n = items.reduce((t, it) => t + 1 + it.replies.length, 0);
  const body = (
    <div className="space-y-2">
      {items.length > 0 && (
        <ul className="space-y-1.5">
          {items.map((it) => (
            <FeedCard key={it.key} it={it} decisions={decisions} nested />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <ReplyBox comment={ref} label={items.length ? "New comment on this decision" : "Comment on this decision"} />
      </div>
    </div>
  );
  if (!items.length)
    return (
      <div className="mt-2 border-t border-line pt-2">
        <div className="mb-1 text-xs text-muted">No discussion yet.</div>
        {body}
      </div>
    );
  return (
    <details open={open} className="group/d mt-2 border-t border-line pt-1">
      <summary className="cursor-pointer list-none py-1 select-none [&::-webkit-details-marker]:hidden">
        <span className={SUBHEAD}>Discussion</span>{" "}
        <span className="text-xs text-muted">
          · {n} message{n === 1 ? "" : "s"} <span className="text-accent group-open/d:hidden">show ▸</span>
        </span>
      </summary>
      {body}
    </details>
  );
}

function Footer({ c }: { c: InboxCard }) {
  const m = c.advice;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
      {m && <AdviceLinks m={m} />}
      <span className="ml-auto">
        {m ? (
          <>
            posted {shortTime(m.firstCreatedAt)}
            {m.versions > 1 && <> · updated {shortTime(m.createdAt)} (v{m.version})</>}
          </>
        ) : (
          <>requested {shortTime(c.approval?.requested_at)}</>
        )}
      </span>
      {c.approvals.map((a) => (
        <span key={a.approval_id} title={`approval ${a.status}`}>
          approval <code>{a.approval_id}</code>
        </span>
      ))}
      {m && <code title="advice_id (fantasy advise status <id> …)">{m.adviceId}</code>}
    </div>
  );
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

/** The full decision card. `variant` sets how much is open: needs-yes cards show everything; ideas / queued / closed
 *  collapse the explanation and the discussion. */
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
  const yes = variant === "yes";
  const frame = yes ? "border-accent/50 bg-accent/5" : "border-line bg-panel";
  return (
    <article id={c.key} className={`scroll-mt-16 rounded-xl border px-4 py-3 ${frame}`}>
      <Anchors c={c} />
      <Header c={c} now={now} />
      <h3 className="mt-1.5 text-[15px] leading-snug font-semibold">{titleOf(c)}</h3>
      {m?.headline && <p className="mt-0.5 text-[14px] leading-snug">{m.headline}</p>}
      {m && <Verdict m={m} />}
      {variant === "queued" && <Queued c={c} />}
      {m && (yes ? (
        <>
          <WhySection m={m} />
          <MetricsBlock m={m} keyN={3} />
          <Risks m={m} />
        </>
      ) : (
        <details className="group/x mt-2">
          <summary className="cursor-pointer list-none text-xs text-accent select-none [&::-webkit-details-marker]:hidden">
            <span className="group-open/x:hidden">Why, numbers and risks ▸</span>
            <span className="hidden group-open/x:inline">Hide details ▾</span>
          </summary>
          <WhySection m={m} />
          <MetricsBlock m={m} keyN={3} />
          <Risks m={m} />
        </details>
      ))}
      {!m && a?.payload != null && (
        <div className="mt-1">
          <JsonView value={a.payload} label="approval details" />
        </div>
      )}
      {a?.status === "pending" ? <ApprovalBox a={a} now={now} /> : yes && m && <NoApprovalYet />}
      {a && a.status !== "pending" && variant === "closed" && (
        <p className="mt-2 text-xs text-muted">
          Approval {a.status}
          {a.decided_at && <> {shortTime(a.decided_at)} ET</>}
          {a.decided_by && <> by {a.decided_by}</>}
          {a.note && <> · “{a.note}”</>}
        </p>
      )}
      <Discussion c={c} items={threads} decisions={decisions} open={yes} />
      <Footer c={c} />
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
}: {
  title: string;
  sub?: React.ReactNode;
  cards: InboxCard[];
  variant: Variant;
  threadsOf: (key: string) => FeedItem[];
  decisions: Map<string, Decision>;
  now: number;
}) {
  if (!cards.length) return null;
  return (
    <section className="space-y-2">
      <SectionTitle right={sub}>
        {title} · {cards.length}
      </SectionTitle>
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
    <Disclosure summary="Done / rejected" right={`${cards.length}`}>
      <div className="flex items-center justify-between gap-2 px-4 pt-2 text-xs text-muted">
        <span>Newest first. Week by week, with Yahoo verification:</span>
        <Link href="/week#decisions" className="shrink-0 text-accent">
          This week · Decisions →
        </Link>
      </div>
      <ul className="divide-y divide-line">
        {cards.map((c) => {
          const st = c.advice?.status ?? c.approval?.status ?? "";
          const when = c.approval?.decided_at ?? c.advice?.createdAt ?? c.approval?.requested_at;
          return (
            <li key={c.key}>
              <details className="group/c">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 select-none [&::-webkit-details-marker]:hidden">
                  <span className="text-[10px] text-muted transition-transform group-open/c:rotate-90">▶</span>
                  <Badge tone={statusTone(st)}>{STATUS_LABEL[st] ?? st}</Badge>
                  <span className="min-w-0 flex-1 truncate text-[13.5px]">{titleOf(c)}</span>
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
    </Disclosure>
  );
}
