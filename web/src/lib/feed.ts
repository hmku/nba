/**
 * Activity "Agent updates" feed (pure, no runtime imports: unit-tested by `npm test`).
 *
 * One chronological feed of everything the agents tell the user: Muse reports (incl. trade offers), Muse's latest
 * Yahoo snapshots (one compact card), group-chat digests (private: only after the passphrase unlock), advisor
 * suggestions, Claude's notable events (decisions, verifications, errors), finished Claude run summaries, and
 * Claude's own posted updates. Each update can carry a thread: replies are agent_events rows with
 * payload `{report_kind: "reply", reply_to, thread_root, stance, text, evidence?}` (docs/research/dashboard_data.md §5).
 *
 * Author = the run the row was filed under (muse-*, advisor-*, user-*; everything else is Claude), never anything
 * the payload claims.
 */
import type { AgentEvent, AgentRun, LeagueSnapshot } from "./types";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

export type Author = "muse" | "advisor" | "claude" | "user";
export const AUTHORS: readonly Author[] = ["muse", "advisor", "claude", "user"];
export const AUTHOR_LABEL: Record<Author, string> = { muse: "Muse", advisor: "Advisor", claude: "Claude", user: "You" };
export const FEED_STANCES = ["agree", "disagree", "comment", "question"] as const;
export type FeedStance = (typeof FEED_STANCES)[number];

/** Claude's event kinds worth a feed card (the rest stay in the run timeline under "System"). */
export const NOTABLE_EVENT_KINDS: readonly string[] = ["decision", "verification", "error"];
/** Report kinds that are group-chat content (RLS hides them from the anon key; see agentPure.isPrivateEvent). */
const PRIVATE_KINDS: readonly string[] = ["chat_digest", "chat_note"];
/** Report kinds never shown as cards: draft-night pick backups and the replies themselves. */
const HIDDEN_KINDS: readonly string[] = ["observed_pick", "reply"];
/** Runs that are only log containers for one author's posts (their summaries are not feed items). */
const PASSIVE_AGENTS: readonly string[] = ["muse", "advisor", "user", "claude"];

export function authorOf(runId: string | null | undefined): Author {
  const r = runId ?? "";
  if (r.startsWith("muse-")) return "muse";
  if (r.startsWith("advisor-")) return "advisor";
  if (r.startsWith("user-")) return "user";
  return "claude";
}

const payloadOf = (e: { payload?: unknown }): Obj => (isObj(e.payload) ? e.payload : {});
export const reportKind = (e: { payload?: unknown }): string | null => str(payloadOf(e).report_kind);

export function isReply(e: { payload?: unknown }): boolean {
  const p = payloadOf(e);
  return p.report_kind === "reply" && typeof p.reply_to === "string";
}

/** Root event id of the thread a reply belongs to. */
export function threadRootOf(e: { payload?: unknown }): string | null {
  const p = payloadOf(e);
  if (p.report_kind !== "reply") return null;
  return str(p.thread_root) ?? str(p.reply_to);
}

/** `fantasy txn mark … verified` logs "verified txn-…" automatically; the agent's own verification note says more. */
export function isAutoVerification(e: { kind?: string; message?: string | null }): boolean {
  return e.kind === "verification" && /^verified txn-\S+$/.test((e.message ?? "").trim());
}

/** decision_id of a `decision` event (ledger.record_decision logs it in the payload). */
export function decisionIdOf(e: { kind?: string; payload?: unknown }): string | null {
  return e.kind === "decision" ? str(payloadOf(e).decision_id) : null;
}

export function isPrivate(e: { payload?: unknown }): boolean {
  return PRIVATE_KINDS.includes(reportKind(e) ?? "");
}

export interface FeedReply {
  event: AgentEvent;
  author: Author;
  stance: FeedStance;
  text: string;
  evidence: Obj[];
  /** Author of the message this one answers, when it is not the thread root (shown as "↳ re Muse"). */
  replyToAuthor: Author | null;
}

export type FeedType = "report" | "suggestion" | "digest" | "event" | "run" | "snapshots";

export interface FeedItem {
  key: string;
  type: FeedType;
  author: Author;
  /** Filter / badge kind: report_kind, event kind, "run summary" or "yahoo snapshots". */
  kind: string;
  /** When the update itself was posted. */
  ts: string;
  /** Newest of the update and its replies (feed order). */
  lastActivity: string;
  event?: AgentEvent;
  run?: AgentRun;
  snapshots?: LeagueSnapshot[];
  replies: FeedReply[];
  /** Agents (and the unlocked user) may reply: any visible agent_events row that is not private chat content. */
  replyable: boolean;
}

function toReply(e: AgentEvent, byId: Map<string, AgentEvent>, root: string): FeedReply {
  const p = payloadOf(e);
  const stance = (FEED_STANCES as readonly string[]).includes(String(p.stance)) ? (p.stance as FeedStance) : "comment";
  const to = str(p.reply_to);
  const target = to && to !== root ? byId.get(to) : undefined;
  return {
    event: e,
    author: authorOf(e.run_id),
    stance,
    text: str(p.text) ?? e.message ?? "",
    evidence: Array.isArray(p.evidence) ? p.evidence.filter(isObj) : [],
    replyToAuthor: target ? authorOf(target.run_id) : null,
  };
}

/** Root ids referenced by replies that are not among `events` (the page fetches them so old threads still render). */
export function missingRoots(events: AgentEvent[]): string[] {
  const have = new Set(events.map((e) => e.event_id));
  const out = new Set<string>();
  for (const e of events) {
    const r = threadRootOf(e);
    if (r && !have.has(r)) out.add(r);
  }
  return [...out];
}

const tsOf = (v: string | null | undefined): string => v ?? "";

/** Build the feed. `events` may contain duplicates (several queries); `canSeePrivate` = passphrase unlocked.
 *  `order`: "activity" (default: newest of the update and its replies; the agent API) or "posted" (the update's
 *  own time: stable for the Activity page's cursor pagination, replies never move a thread). */
export function buildFeed({
  events,
  runs = [],
  snapshots = [],
  canSeePrivate = false,
  order = "activity",
}: {
  events: AgentEvent[];
  runs?: AgentRun[];
  snapshots?: LeagueSnapshot[];
  canSeePrivate?: boolean;
  order?: "posted" | "activity";
}): FeedItem[] {
  const byId = new Map<string, AgentEvent>();
  for (const e of events) if (e && e.event_id && (canSeePrivate || !isPrivate(e))) byId.set(e.event_id, e);
  const items = new Map<string, FeedItem>();
  for (const e of byId.values()) {
    if (isReply(e)) continue;
    const author = authorOf(e.run_id);
    const rk = reportKind(e);
    const base = { key: e.event_id, author, ts: tsOf(e.ts), lastActivity: tsOf(e.ts), event: e, replies: [] as FeedReply[] };
    if (rk) {
      if (HIDDEN_KINDS.includes(rk)) continue;
      if (rk === "chat_digest") items.set(e.event_id, { ...base, type: "digest", kind: rk, replyable: false });
      else if (rk === "advisor_suggestion" && author === "advisor") items.set(e.event_id, { ...base, type: "suggestion", kind: rk, replyable: true });
      else items.set(e.event_id, { ...base, type: "report", kind: rk, replyable: !isPrivate(e) });
    } else if (author === "claude" && NOTABLE_EVENT_KINDS.includes(e.kind) && !isAutoVerification(e)) {
      items.set(e.event_id, { ...base, type: "event", kind: e.kind, replyable: true });
    }
  }
  // threads: replies hang under their root, oldest first
  const replies = [...byId.values()].filter(isReply).sort((a, b) => tsOf(a.ts).localeCompare(tsOf(b.ts)));
  for (const r of replies) {
    const root = threadRootOf(r);
    const item = root ? items.get(root) : undefined;
    if (!item) continue; // root not visible (private, too old, or not a feed item)
    item.replies.push(toReply(r, byId, root!));
    if (tsOf(r.ts) > item.lastActivity) item.lastActivity = tsOf(r.ts);
  }
  const out = [...items.values()];
  for (const r of runs) {
    if (PASSIVE_AGENTS.includes(r.agent) || !str(r.summary) || r.status === "running") continue;
    const t = tsOf(r.ended_at ?? r.heartbeat_at ?? r.started_at);
    out.push({ key: `run:${r.run_id}`, type: "run", author: "claude", kind: "run summary", ts: t, lastActivity: t, run: r, replies: [], replyable: false });
  }
  if (snapshots.length) {
    const t = snapshots.map((s) => tsOf(s.last_seen_at ?? s.observed_at ?? s.ts)).sort().at(-1) ?? "";
    out.push({ key: "snapshots", type: "snapshots", author: "muse", kind: "yahoo snapshots", ts: t, lastActivity: t, snapshots, replies: [], replyable: false });
  }
  const at = (it: FeedItem) => (order === "activity" ? it.lastActivity : it.ts);
  return out.sort((a, b) => at(b).localeCompare(at(a)) || a.key.localeCompare(b.key));
}

export interface FeedFilter {
  author?: string | null;
  kind?: string | null;
}

/** Author filter matches threads the author started OR replied in; kind matches the update's kind. */
export function filterFeed(items: FeedItem[], f: FeedFilter): FeedItem[] {
  return items.filter(
    (it) =>
      (!f.author || it.author === f.author || it.replies.some((r) => r.author === f.author)) && (!f.kind || it.kind === f.kind),
  );
}

/** Chip counts for the filters (computed on the unfiltered feed). */
export function feedFacets(items: FeedItem[]): { authors: [Author, number][]; kinds: [string, number][] } {
  const a = new Map<Author, number>();
  const k = new Map<string, number>();
  for (const it of items) {
    const who = new Set<Author>([it.author, ...it.replies.map((r) => r.author)]);
    for (const w of who) a.set(w, (a.get(w) ?? 0) + 1);
    k.set(it.kind, (k.get(it.kind) ?? 0) + 1);
  }
  return {
    authors: AUTHORS.filter((x) => a.has(x)).map((x) => [x, a.get(x)!]),
    kinds: [...k.entries()].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0])),
  };
}

/** Cap a JSON payload for display (keeps the page light: the run timeline can hold hundreds of events). Payloads
 *  over `max` chars of JSON become `{_truncated, chars, preview}`; the full row stays in the DB (`fantasy run events`). */
export function trimPayload(v: unknown, max = 2000): unknown {
  if (v == null) return v;
  const s = JSON.stringify(v);
  if (s.length <= max) return v;
  return { _truncated: true, chars: s.length, preview: s.slice(0, max) + "…" };
}

/** Text longer than this is collapsed behind "more" on the card. */
export const COLLAPSE_AT = 420;
export function splitLong(text: string, at = COLLAPSE_AT): { head: string; rest: string | null } {
  if (text.length <= at) return { head: text, rest: null };
  const cut = text.lastIndexOf(" ", at);
  const i = cut > at * 0.6 ? cut : at;
  return { head: text.slice(0, i), rest: text.slice(i) };
}
