/**
 * Cursor pagination for the Inbox "Agent updates" feed (pure, no runtime imports: unit-tested by `npm test`).
 *
 * Order: newest POSTED first (an update's own timestamp, then key, descending). Unlike "newest activity", that order
 * is stable under inserts, so a cursor `(ts, key)` in the URL always lands on the same page boundary; a thread's
 * replies stay with their root wherever it is (page 1 lists "new replies on earlier updates" instead of bumping them).
 *
 * The page reads light index rows (ids, timestamps, kinds, thread links; no payload/message) from several sources
 * (agent_events, agent_runs), merges them here, then fetches full rows only for the page's roots and their replies.
 */
import type { Author } from "./feed";

export const FEED_PAGE_SIZE = 12;

/** A position in the feed: an item's posted timestamp and its feed key (event id, or `run:<run_id>`). */
export interface Cursor {
  ts: string;
  key: string;
}
export type Entry = Cursor;

const KEY_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}$/;
const TS_RE = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}(?::?\d{2})?)?$/;

/**
 * Canonical, string-comparable UTC form of a Postgres/ISO timestamp with full sub-millisecond precision
 * (`2026-10-20T13:15:46.936000000Z`). PostgREST trims trailing zeros and may vary the offset, so raw strings must
 * not be compared directly; Date.parse alone would drop microseconds (ties that Postgres orders apart).
 */
export function canonTs(ts: string | null | undefined): string | null {
  const m = TS_RE.exec((ts ?? "").trim());
  if (!m) return null;
  let off = m[4] ?? "Z";
  if (off !== "Z") {
    const d = off.replace(":", "");
    off = `${d.slice(0, 3)}:${d.slice(3, 5) || "00"}`;
  }
  const ms = Date.parse(`${m[1]}T${m[2]}${off}`);
  if (Number.isNaN(ms)) return null;
  return `${new Date(ms).toISOString().slice(0, 19)}.${(m[3] ?? "").padEnd(9, "0")}Z`;
}

const cmpStr = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/** Feed order comparator: < 0 when `a` is shown before (is newer than) `b`. Unparseable timestamps sort last. */
export function cmpFeed(a: Entry, b: Entry): number {
  return cmpStr(canonTs(b.ts) ?? "", canonTs(a.ts) ?? "") || cmpStr(b.key, a.key);
}

const b64url = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64url = (s: string) => atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));

/** Opaque URL token for a cursor (base64url of `ts|key`). */
export function encodeCursor(c: Cursor): string {
  return b64url(`${c.ts}|${c.key}`);
}

/** Inverse of encodeCursor; null for anything malformed (the page then falls back to the newest page). */
export function decodeCursor(token: string | null | undefined): Cursor | null {
  if (!token || token.length > 400 || !/^[A-Za-z0-9_-]+$/.test(token)) return null;
  let raw: string;
  try {
    raw = unb64url(token);
  } catch {
    return null;
  }
  const i = raw.indexOf("|");
  if (i < 0) return null;
  const ts = raw.slice(0, i);
  const key = raw.slice(i + 1);
  return canonTs(ts) && KEY_RE.test(key) ? { ts, key } : null;
}

/** Which page: the newest, strictly older than a cursor ("Older →"), starting at a cursor (a link to one update,
 *  inclusive), or strictly newer than a cursor ("← Newer"). */
export type PageMode =
  | { dir: "first" }
  | { dir: "before"; c: Cursor }
  | { dir: "at"; c: Cursor }
  | { dir: "after"; c: Cursor };

export function parsePageMode(sp: { before?: string | null; at?: string | null; after?: string | null }): PageMode {
  const b = decodeCursor(sp.before);
  if (b) return { dir: "before", c: b };
  const at = decodeCursor(sp.at);
  if (at) return { dir: "at", c: at };
  const a = decodeCursor(sp.after);
  if (a) return { dir: "after", c: a };
  return { dir: "first" };
}

/** One source's fetched candidates. `items` may be filtered in JS; `truncatedAt` = the last RAW row fetched when the
 *  query hit its limit (rows beyond it were not read, so nothing past it may be shown from any source). */
export interface Source<T extends Entry> {
  items: T[];
  truncatedAt?: Entry | null;
}

export interface FeedPage<T extends Entry> {
  items: T[];
  /** Token for "Older →" (`?before=`), or null at the end of the feed. */
  older: string | null;
  /** Token for "← Newer" (`?after=`), or null on the newest page. */
  newer: string | null;
  /** The newest page (approvals, the snapshot card and the "new replies" strip only show here). */
  isFirst: boolean;
}

function dedupe<T extends Entry>(xs: T[]): T[] {
  const m = new Map<string, T>();
  for (const x of xs) if (!m.has(x.key)) m.set(x.key, x);
  return [...m.values()];
}

/** Merge older-side sources (newest first), cut at the most restrictive truncation bound. */
function mergeOlder<T extends Entry>(sources: Source<T>[], keep: (x: T) => boolean): { items: T[]; truncated: boolean } {
  const bounds = sources.map((s) => s.truncatedAt).filter((b): b is Entry => !!b);
  // the newest of the per-source "last row read": anything older might be preceded by unread rows of that source
  const bound = bounds.sort(cmpFeed)[0];
  const items = dedupe(sources.flatMap((s) => s.items))
    .filter(keep)
    .filter((x) => !bound || cmpFeed(x, bound) <= 0)
    .sort(cmpFeed);
  return { items, truncated: !!bound };
}

/** Merge newer-side sources (oldest first, i.e. closest to the cursor), cut at the most restrictive bound. */
function mergeNewer<T extends Entry>(sources: Source<T>[], keep: (x: T) => boolean): { items: T[]; truncated: boolean } {
  const bounds = sources.map((s) => s.truncatedAt).filter((b): b is Entry => !!b);
  const bound = bounds.sort(cmpFeed).at(-1); // the oldest of the per-source "newest row read"
  const items = dedupe(sources.flatMap((s) => s.items))
    .filter(keep)
    .filter((x) => !bound || cmpFeed(x, bound) >= 0)
    .sort((a, b) => cmpFeed(b, a));
  return { items, truncated: !!bound };
}

/**
 * Assemble one page from candidate rows.
 * - first / before / at: `older` sources hold rows with ts <= cursor (all rows for "first"), newest first.
 * - after: `newer` sources hold rows with ts >= cursor (oldest first); `older` sources hold rows with ts <= cursor and
 *   fill the page when fewer than `size` newer rows exist (that page IS the newest page).
 */
export function assemblePage<T extends Entry>({
  mode,
  older,
  newer = [],
  size = FEED_PAGE_SIZE,
}: {
  mode: PageMode;
  older: Source<T>[];
  newer?: Source<T>[];
  size?: number;
}): FeedPage<T> {
  const tok = (x: T | undefined) => (x ? encodeCursor({ ts: x.ts, key: x.key }) : null);
  if (mode.dir === "after") {
    const nw = mergeNewer(newer, (x) => cmpFeed(x, mode.c) < 0);
    if (nw.items.length > size || (nw.truncated && nw.items.length >= size)) {
      const items = nw.items.slice(0, size).reverse();
      return { items, older: tok(items.at(-1)), newer: tok(items[0]), isFirst: false };
    }
    // fewer than a page of newer rows: this is the newest page, filled up with the cursor row and older ones
    const od = mergeOlder(older, (x) => cmpFeed(x, mode.c) >= 0);
    const all = [...[...nw.items].reverse(), ...od.items];
    const items = all.slice(0, size);
    const more = all.length > size || od.truncated;
    return { items, older: more ? tok(items.at(-1)) : null, newer: null, isFirst: true };
  }
  const keep =
    mode.dir === "before"
      ? (x: T) => cmpFeed(x, mode.c) > 0
      : mode.dir === "at"
        ? (x: T) => cmpFeed(x, mode.c) >= 0
        : () => true;
  const od = mergeOlder(older, keep);
  const items = od.items.slice(0, size);
  const more = od.items.length > size || od.truncated;
  const isFirst = mode.dir === "first";
  return { items, older: more ? tok(items.at(-1)) : null, newer: isFirst ? null : tok(items[0]) ?? null, isFirst };
}

/* ------------------------------------------------------------------ PostgREST filters (pure strings) */

/** Report kinds never shown as cards (draft-night pick backups; replies hang under their root). */
const HIDDEN_REPORT_KINDS = ["observed_pick", "reply"];
const PRIVATE_REPORT_KINDS = ["chat_digest", "chat_note"];
const PRIVATE_RUN_PREFIXES = ["muse-", "advisor-", "user-"];

/**
 * `or=` logic tree selecting agent_events rows that can be feed roots (mirrors buildFeed): any report except hidden
 * kinds (and, when locked, private chat kinds), or one of Claude's notable events (decision / verification / error,
 * minus the automatic "verified txn-…" notes). `withReplies` adds thread replies (facet index).
 */
export function feedRowsOr({ canSeePrivate, withReplies = false }: { canSeePrivate: boolean; withReplies?: boolean }): string {
  const hidden = [...HIDDEN_REPORT_KINDS, ...(canSeePrivate ? [] : PRIVATE_REPORT_KINDS)].join(",");
  const claude = PRIVATE_RUN_PREFIXES.map((p) => `run_id.not.like.${p}*`).join(",");
  const autoVerification = `"^[[:space:]]*verified txn-[^[:space:]]+[[:space:]]*$"`;
  const branches = [
    `and(payload->>report_kind.not.is.null,payload->>report_kind.not.in.(${hidden}))`,
    `and(payload->>report_kind.is.null,kind.in.(decision,verification,error),${claude},or(kind.neq.verification,message.is.null,message.not.match.${autoVerification}))`,
  ];
  if (withReplies) branches.push("payload->>report_kind.eq.reply");
  return branches.join(",");
}

/** Light index columns (no message / payload bodies): enough to build feed membership, facets and threads. */
export const FEED_INDEX_COLS =
  "event_id,run_id,ts,kind,rk:payload->>report_kind,tr:payload->>thread_root,rt:payload->>reply_to,did:payload->>decision_id";

export interface FeedIndexRow {
  event_id: string;
  run_id: string | null;
  ts: string | null;
  kind: string;
  rk: string | null;
  tr: string | null;
  rt: string | null;
  did: string | null;
}

/** Index row → AgentEvent-shaped row (payload holds only the thread/kind fields), so buildFeed can be reused. */
export function indexToEvent(r: FeedIndexRow) {
  const payload: Record<string, unknown> = {};
  if (r.rk) payload.report_kind = r.rk;
  if (r.tr) payload.thread_root = r.tr;
  if (r.rt) payload.reply_to = r.rt;
  if (r.did) payload.decision_id = r.did;
  return { event_id: r.event_id, run_id: r.run_id ?? "", ts: r.ts ?? "", kind: r.kind, message: null, payload };
}

/** Quote a value for an `in.(…)` list inside an `or=` tree. Ids are validated elsewhere; quotes are stripped. */
export const quoteIn = (ids: string[]) => ids.map((i) => `"${i.replace(/["\\]/g, "")}"`).join(",");

/* ------------------------------------------------------------------ filters in the URL */

export interface FeedParams {
  author?: string | null;
  kind?: string | null;
  before?: string | null;
  at?: string | null;
  after?: string | null;
}

/** /inbox URL keeping the filters (cursor params only when given). */
export function feedHref(p: FeedParams, hash?: string): string {
  const q = new URLSearchParams();
  if (p.author) q.set("author", p.author);
  if (p.kind) q.set("kind", p.kind);
  if (p.before) q.set("before", p.before);
  else if (p.at) q.set("at", p.at);
  else if (p.after) q.set("after", p.after);
  const s = q.toString();
  return `${s ? `/inbox?${s}` : "/inbox"}${hash ? `#${hash}` : ""}`;
}

export type Facets = { authors: [Author, number][]; kinds: [string, number][] };

/** Add counted cards that are not agent_events rows (run summaries, the snapshot card) to feedFacets() output. */
export function addFacets(f: Facets, extra: { author: Author; kind: string; n: number }[]): Facets {
  const a = new Map(f.authors);
  const k = new Map(f.kinds);
  for (const x of extra) {
    if (x.n <= 0) continue;
    a.set(x.author, (a.get(x.author) ?? 0) + x.n);
    k.set(x.kind, (k.get(x.kind) ?? 0) + x.n);
  }
  return {
    authors: (AUTHOR_IDS as Author[]).filter((x) => a.has(x)).map((x) => [x, a.get(x)!]),
    kinds: [...k.entries()].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0])),
  };
}

const AUTHOR_IDS: readonly string[] = ["muse", "advisor", "claude", "user"]; // = feed.AUTHORS (no runtime import)
export function isAuthor(a: string | null | undefined): a is Author {
  return AUTHOR_IDS.includes(a ?? "");
}
