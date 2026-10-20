/**
 * Inbox readers (pure, no runtime imports: unit-tested by `npm test`).
 *
 * The Inbox (`/inbox`) shows each decision ONCE: a recommendation (`model_runs` kind `advice`, newest version per
 * `advice_id`, see moves.ts), the approvals row that carries the user's yes/no, and the discussion threads that refer to
 * it. Linkage (docs/research/dashboard_data.md §4 "Advice ↔ approval ↔ thread"):
 *  - advice → approval: `summary.approval_id`;
 *  - approval → advice: `payload.advice_id` (either direction is enough; both are joined);
 *  - thread → decision: a root post whose `payload.details.advice_id|approval_id` (or `payload.advice_id|approval_id`)
 *    names it (explicit, preferred: `fantasy intel post --details '{"advice_id": "…"}'`), else whose message / details /
 *    evidence text mentions a known advice_id or approval_id. Replies follow their root.
 */
import type { Advice } from "./moves";

type Obj = Record<string, unknown>;
const o = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);
const s = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

/** The approvals columns the Inbox reads (= lib/types Approval minus nothing it needs). */
export interface ApprovalRow {
  approval_id: string;
  run_id: string | null;
  action_type: string;
  summary: string | null;
  payload: unknown;
  status: string;
  note: string | null;
  requested_at: string | null;
  decided_at: string | null;
  expires_at: string | null;
  decided_by?: string | null;
}

/** One decision card: an advice item and/or its approval(s), plus linked thread roots (event ids). */
export interface InboxCard {
  /** Stable key + DOM anchor: the advice_id, else the approval_id. */
  key: string;
  advice: Advice | null;
  /** The approval that decides the card (pending first, then the one the advice names, then the newest). */
  approval: ApprovalRow | null;
  /** Every approval matched to this card, `approval` first. */
  approvals: ApprovalRow[];
}

/** `payload.advice_id` of an approval (the approval → advice direction of the join). */
export function approvalAdviceId(a: Pick<ApprovalRow, "payload">): string | null {
  return s(o(a.payload)?.advice_id);
}

const tsOf = (v: string | null | undefined) => v ?? "";

/**
 * Join advice items and approvals both ways. Every advice item gets a card; every approval joins the card of the advice
 * it names (`payload.advice_id`) or that names it (`summary.approval_id`), else gets its own card.
 */
export function joinDecisions(advice: Advice[], approvals: ApprovalRow[]): InboxCard[] {
  const cards = new Map<string, InboxCard>();
  const byApproval = new Map<string, string>(); // approval_id named by an advice -> advice_id
  for (const a of advice) {
    cards.set(a.adviceId, { key: a.adviceId, advice: a, approval: null, approvals: [] });
    if (a.approvalId) byApproval.set(a.approvalId, a.adviceId);
  }
  for (const ap of approvals) {
    const named = approvalAdviceId(ap);
    const aid = named && cards.get(named)?.advice ? named : byApproval.get(ap.approval_id);
    const card = aid ? cards.get(aid) : undefined;
    if (card) card.approvals.push(ap);
    else if (!cards.has(ap.approval_id)) cards.set(ap.approval_id, { key: ap.approval_id, advice: null, approval: ap, approvals: [ap] });
  }
  for (const c of cards.values()) {
    if (!c.advice) continue;
    const named = c.advice.approvalId;
    c.approvals.sort(
      (x, y) =>
        Number(y.status === "pending") - Number(x.status === "pending") ||
        Number(y.approval_id === named) - Number(x.approval_id === named) ||
        tsOf(y.requested_at).localeCompare(tsOf(x.requested_at)),
    );
    c.approval = c.approvals[0] ?? null;
  }
  return [...cards.values()];
}

/** A pending approval past its `expires_at` (the executor will not act on it; the agent re-asks). */
export function isExpired(a: Pick<ApprovalRow, "expires_at"> | null, nowMs: number): boolean {
  if (!a?.expires_at) return false;
  const t = Date.parse(a.expires_at);
  return Number.isFinite(t) && t < nowMs;
}

const CLOSED_ADVICE: ReadonlySet<string> = new Set(["done", "rejected", "expired"]);

export interface InboxGroups {
  /** A pending approval, or advice awaiting_yes with no decided approval. */
  needsYes: InboxCard[];
  /** The user said yes (approval approved / advice approved) and the move is not done yet. */
  queued: InboxCard[];
  /** Advice ideas (status idea, not awaiting a yes), today's lineup plan. */
  ideas: InboxCard[];
  /** done / rejected / expired advice, decided approvals without open advice, other days' lineup plans. */
  closed: InboxCard[];
}

/** When a card last changed (for sorting). */
export function cardTs(c: InboxCard): string {
  const xs = [c.advice?.createdAt, c.approval?.decided_at, c.approval?.requested_at].map(tsOf);
  return xs.sort().at(-1) ?? "";
}

/** Inbox sections. `today` = ET date (YYYY-MM-DD) for lineup plans. */
export function groupInbox(cards: InboxCard[], today: string): InboxGroups {
  const g: InboxGroups = { needsYes: [], queued: [], ideas: [], closed: [] };
  for (const c of cards) {
    const ap = c.approval;
    const st = c.advice?.status ?? null;
    const decided = ap && ap.status !== "pending" ? ap.status : null; // approved | rejected | expired | …
    if (ap?.status === "pending") g.needsYes.push(c);
    else if (!c.advice) g.closed.push(c); // a decided approval with no advice item
    else if (st && CLOSED_ADVICE.has(st)) g.closed.push(c);
    else if (c.advice.type === "lineup") {
      const isToday = !c.advice.forDate || c.advice.forDate === today;
      (isToday ? g.ideas : g.closed).push(c);
    } else if (st === "approved" || decided === "approved") g.queued.push(c);
    else if (decided) g.closed.push(c); // rejected / expired approval on an open item
    else if (st === "awaiting_yes") g.needsYes.push(c);
    else g.ideas.push(c);
  }
  const newest = (x: InboxCard, y: InboxCard) => cardTs(y).localeCompare(cardTs(x));
  // needs-yes: soonest expiry first (no expiry last), then newest
  const exp = (c: InboxCard) => c.approval?.expires_at ?? "9999";
  g.needsYes.sort((x, y) => exp(x).localeCompare(exp(y)) || newest(x, y));
  g.queued.sort(newest);
  g.ideas.sort(newest);
  g.closed.sort(newest);
  return g;
}

/* ------------------------------------------------------------------ thread linkage */

/** Report kinds that can never be a decision's thread (private chat, draft-night pick backups, replies themselves). */
const NOT_LINKABLE: readonly string[] = ["reply", "observed_pick", "chat_digest", "chat_note"];

/** Text pattern the DB pre-filter uses (PostgREST `match`, POSIX): something shaped like an approval or advice id. */
export const LINK_TEXT_RE = "(apr-[0-9a-f]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z])";

/** PostgREST `or=` tree for candidate thread roots: explicit link fields, or id-shaped text in message/details/evidence. */
export function linkRowsOr(): string {
  const re = `"${LINK_TEXT_RE}"`;
  return [
    "payload->details->>advice_id.not.is.null",
    "payload->details->>approval_id.not.is.null",
    "payload->>advice_id.not.is.null",
    "payload->>approval_id.not.is.null",
    `message.match.${re}`,
    `payload->>details.match.${re}`,
    `payload->>evidence.match.${re}`,
  ].join(",");
}
export const NOT_LINKABLE_IN = `(${NOT_LINKABLE.join(",")})`;

/** Index from every id a card answers to (advice_id, its approval ids) to the card key. */
export function cardIndex(cards: InboxCard[]): Map<string, string> {
  const m = new Map<string, string>();
  for (const c of cards) {
    if (c.advice) m.set(c.advice.adviceId, c.key);
    for (const a of c.approvals) m.set(a.approval_id, c.key);
  }
  return m;
}

const ID_CHAR = /[a-z0-9_-]/i;
/** `id` occurs in `text` as a whole token (a trailing "." counts as punctuation unless a word follows it). */
export function mentions(text: string, id: string): boolean {
  let i = text.indexOf(id);
  while (i >= 0) {
    const before = i > 0 ? text[i - 1] : "";
    const after = text[i + id.length] ?? "";
    const after2 = text[i + id.length + 1] ?? "";
    const okBefore = !before || !ID_CHAR.test(before);
    const okAfter = !after || (after === "." ? !after2 || !/[a-z0-9]/i.test(after2) : !ID_CHAR.test(after));
    if (okBefore && okAfter) return true;
    i = text.indexOf(id, i + 1);
  }
  return false;
}

/** Card keys an event (a candidate thread root) refers to: the explicit link if it names a known card, else every
 *  known id mentioned in its message / details / evidence. Replies and private / hidden kinds link to nothing. */
export function linkedCards(e: { message?: string | null; payload?: unknown }, index: Map<string, string>): string[] {
  const p = o(e.payload) ?? {};
  const rk = s(p.report_kind);
  if (!rk || NOT_LINKABLE.includes(rk)) return [];
  const d = o(p.details) ?? {};
  for (const id of [s(d.advice_id), s(d.approval_id), s(p.advice_id), s(p.approval_id)]) {
    const k = id ? index.get(id) : undefined;
    if (k) return [k];
  }
  const text = [e.message ?? "", p.details != null ? JSON.stringify(p.details) : "", p.evidence != null ? JSON.stringify(p.evidence) : ""].join("\n");
  const out = new Set<string>();
  for (const [id, key] of index) if (mentions(text, id)) out.add(key);
  return [...out];
}

/** card key -> linked root event ids (newest first, as given), and the set of all linked roots (hidden from the feed). */
export function linkThreads(
  cards: InboxCard[],
  events: { event_id: string; message?: string | null; payload?: unknown }[],
): { byCard: Map<string, string[]>; linked: Set<string> } {
  const index = cardIndex(cards);
  const byCard = new Map<string, string[]>();
  const linked = new Set<string>();
  for (const e of events) {
    for (const k of linkedCards(e, index)) {
      const xs = byCard.get(k) ?? [];
      if (!xs.includes(e.event_id)) xs.push(e.event_id);
      byCard.set(k, xs);
      linked.add(e.event_id);
    }
  }
  return { byCard, linked };
}

/** Drop feed items shown inside a decision card (and run summaries etc. pass through). */
export function withoutLinked<T extends { key: string }>(items: T[], linked: ReadonlySet<string>): T[] {
  return linked.size ? items.filter((it) => !linked.has(it.key)) : items;
}

/* ------------------------------------------------------------------ legacy routes */

/** /inbox URL for an advice item or approval card. */
export function inboxHref(key: string | null | undefined): string {
  return key ? `/inbox#${encodeURIComponent(key)}` : "/inbox";
}
