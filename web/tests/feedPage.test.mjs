// Inbox feed cursor pagination (src/lib/feedPage.ts) + page assembly with threads. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  assemblePage,
  canonTs,
  cmpFeed,
  decodeCursor,
  encodeCursor,
  feedHref,
  feedRowsOr,
  indexToEvent,
  parsePageMode,
} from "../src/lib/feedPage.ts";
import { buildFeed, filterFeed } from "../src/lib/feed.ts";

/* ------------------------------------------------------------------ cursor */

test("canonTs: offsets, trimmed fractions and microseconds compare correctly as strings", () => {
  assert.equal(canonTs("2026-10-20T13:15:46.936+00:00"), "2026-10-20T13:15:46.936000000Z");
  assert.equal(canonTs("2026-10-20T13:15:46+00:00"), "2026-10-20T13:15:46.000000000Z");
  assert.equal(canonTs("2026-10-20 15:15:46.5+02"), "2026-10-20T13:15:46.500000000Z");
  assert.equal(canonTs("2026-10-20T13:15:46.936Z"), canonTs("2026-10-20T13:15:46.936000+00:00"));
  // PostgREST trims trailing zeros: .12 < .123 and .9 > .123456 must hold after canonicalisation
  assert.ok(canonTs("2026-10-20T00:00:00.12+00:00") < canonTs("2026-10-20T00:00:00.123+00:00"));
  assert.ok(canonTs("2026-10-20T00:00:00.9+00:00") > canonTs("2026-10-20T00:00:00.123456+00:00"));
  assert.ok(canonTs("2026-10-20T00:00:00.000001+00:00") > canonTs("2026-10-20T00:00:00+00:00"), "microseconds kept");
  assert.equal(canonTs("yesterday"), null);
  assert.equal(canonTs(null), null);
});

test("cursor encode/decode round-trips and rejects junk", () => {
  const c = { ts: "2026-10-20T13:15:46.936+00:00", key: "muse-ev-2e7a8960fba4463baff7d8369e3c1192" };
  const t = encodeCursor(c);
  assert.match(t, /^[A-Za-z0-9_-]+$/, "URL-safe, no padding");
  assert.deepEqual(decodeCursor(t), c);
  const run = { ts: "2026-10-20T04:48:29.081625+00:00", key: "run:run-d9c20fdb" };
  assert.deepEqual(decodeCursor(encodeCursor(run)), run);
  for (const bad of [null, "", "!!!", "a".repeat(500), encodeCursor({ ts: "nope", key: "x" }), btoa("2026-10-20T00:00:00Z|bad key")])
    assert.equal(decodeCursor(bad), null, String(bad).slice(0, 20));
  assert.equal(decodeCursor(btoa("no-separator")), null);
});

test("parsePageMode: before > at > after; bad cursors fall back to the newest page", () => {
  const c = encodeCursor({ ts: "2026-10-20T00:00:00Z", key: "e1" });
  assert.equal(parsePageMode({}).dir, "first");
  assert.equal(parsePageMode({ before: c }).dir, "before");
  assert.equal(parsePageMode({ at: c }).dir, "at");
  assert.equal(parsePageMode({ after: c }).dir, "after");
  assert.equal(parsePageMode({ before: "garbage" }).dir, "first");
});

test("feedHref keeps filters and one cursor param", () => {
  assert.equal(feedHref({}), "/inbox");
  assert.equal(feedHref({ author: "muse", kind: "trade_offer", before: "abc" }), "/inbox?author=muse&kind=trade_offer&before=abc");
  assert.equal(feedHref({ kind: "decision", after: "x" }, "e1"), "/inbox?kind=decision&after=x#e1");
});

test("feedRowsOr: private chat kinds excluded only when locked; replies only for the facet index", () => {
  const locked = feedRowsOr({ canSeePrivate: false });
  assert.match(locked, /not\.in\.\(observed_pick,reply,chat_digest,chat_note\)/);
  assert.doesNotMatch(locked, /report_kind\.eq\.reply/);
  const open = feedRowsOr({ canSeePrivate: true, withReplies: true });
  assert.match(open, /not\.in\.\(observed_pick,reply\)\)/);
  assert.match(open, /payload->>report_kind\.eq\.reply$/);
  assert.match(open, /run_id\.not\.like\.muse-\*/);
});

/* ------------------------------------------------------------------ assembly */

const at = (i) => `2026-10-${String(1 + Math.floor(i / 24)).padStart(2, "0")}T${String(i % 24).padStart(2, "0")}:00:00+00:00`;
const E = (i, key = `ev-${String(i).padStart(3, "0")}`) => ({ ts: at(i), key });
// 60 events (hourly) and 5 run summaries interleaved at half-hours
const EVENTS = Array.from({ length: 60 }, (_, i) => E(i));
const RUNS = [3, 17, 29, 41, 55].map((i) => ({ ts: at(i).replace(":00:00+", ":30:00+"), key: `run:r${i}` }));
const ALL = [...EVENTS, ...RUNS].sort(cmpFeed);

/** Simulate the DB queries: rows with ts <= cursor (desc) / ts >= cursor (asc), limited, with truncation marks. */
function olderSource(rows, c, limit) {
  const got = rows.filter((r) => !c || canonTs(r.ts) <= canonTs(c.ts)).sort(cmpFeed);
  const page = got.slice(0, limit);
  return { items: page, truncatedAt: got.length > limit ? page.at(-1) : null };
}
function newerSource(rows, c, limit) {
  const got = rows.filter((r) => canonTs(r.ts) >= canonTs(c.ts)).sort((a, b) => cmpFeed(b, a));
  const page = got.slice(0, limit);
  return { items: page, truncatedAt: got.length > limit ? page.at(-1) : null };
}
function page(modeParams, size = 10, limit = 12) {
  const mode = parsePageMode(modeParams);
  const c = mode.dir === "first" ? null : mode.c;
  return assemblePage({
    mode,
    size,
    older: [olderSource(EVENTS, c, limit), olderSource(RUNS, c, limit)],
    newer: mode.dir === "after" ? [newerSource(EVENTS, c, limit), newerSource(RUNS, c, limit)] : [],
  });
}

test("assemblePage: walking Older → visits every item exactly once, in order, across sources", () => {
  const seen = [];
  let p = page({});
  assert.equal(p.isFirst, true);
  assert.equal(p.newer, null);
  let guard = 0;
  for (;;) {
    seen.push(...p.items.map((x) => x.key));
    if (!p.older || ++guard > 50) break;
    p = page({ before: p.older });
    assert.equal(p.isFirst, false);
    assert.ok(p.newer, "older pages link back to newer");
  }
  assert.deepEqual(seen, ALL.map((x) => x.key));
});

test("assemblePage: ← Newer from page 3 returns page 2 exactly; near the top it becomes the newest page", () => {
  const p1 = page({});
  const p2 = page({ before: p1.older });
  const p3 = page({ before: p2.older });
  const back = page({ after: p3.newer });
  assert.deepEqual(back.items.map((x) => x.key), p2.items.map((x) => x.key));
  assert.equal(back.isFirst, false);
  // from page 2, Newer has exactly a page of newer items → still paginated; one step further is the top
  const top = page({ after: back.newer });
  assert.deepEqual(top.items.map((x) => x.key), p1.items.map((x) => x.key));
  assert.equal(top.isFirst, true);
  assert.equal(top.newer, null);
  // a cursor 3 items from the top: fewer than a page newer → newest page filled from the cursor down
  const near = page({ after: encodeCursor(ALL[3]) });
  assert.equal(near.isFirst, true);
  assert.deepEqual(near.items.map((x) => x.key), ALL.slice(0, 10).map((x) => x.key));
});

test("assemblePage: stable under inserts (a new item only shifts page 1)", () => {
  const p1 = page({});
  const p2 = page({ before: p1.older });
  EVENTS.push(E(70, "ev-new"));
  try {
    const again = page({ before: p1.older });
    assert.deepEqual(again.items, p2.items, "the same cursor gives the same page after an insert");
    assert.equal(page({}).items[0].key, "ev-new");
  } finally {
    EVENTS.pop();
  }
});

test("assemblePage: 'at' includes the cursor item; a truncated source bounds the merge", () => {
  const target = ALL[23];
  const p = page({ at: encodeCursor(target) });
  assert.equal(p.items[0].key, target.key);
  // events truncated after 4 rows: runs older than the 4th event must wait for the next page
  const mode = { dir: "first" };
  const ev = { items: ALL.filter((x) => !x.key.startsWith("run:")).slice(0, 4), truncatedAt: ALL.filter((x) => !x.key.startsWith("run:"))[3] };
  const runs = { items: RUNS.slice().sort(cmpFeed), truncatedAt: null };
  const q = assemblePage({ mode, size: 10, older: [ev, runs] });
  assert.ok(q.items.every((x) => cmpFeed(x, ev.truncatedAt) <= 0));
  assert.ok(q.older, "more to read");
});

test("assemblePage: empty feed and stale cursors", () => {
  const empty = assemblePage({ mode: { dir: "first" }, older: [{ items: [] }] });
  assert.deepEqual(empty, { items: [], older: null, newer: null, isFirst: true });
  const past = assemblePage({ mode: parsePageMode({ before: encodeCursor({ ts: "2000-01-01T00:00:00Z", key: "x" }) }), older: [{ items: [] }] });
  assert.equal(past.items.length, 0);
  assert.equal(past.older, null);
  assert.equal(past.newer, null);
});

/* ------------------------------------------------------------------ roots + replies */

const ix = (event_id, run_id, ts, extra = {}) => ({ event_id, run_id, ts, kind: "observation", rk: null, tr: null, rt: null, did: null, ...extra });

test("page assembly keeps replies with their root: a root on page 2 shows replies newer than page 1", () => {
  // light index rows (the page's first query round)
  const index = [
    ix("muse-ev-old", "muse-2026-10-18", "2026-10-18T10:00:00+00:00", { rk: "trade_offer" }),
    ...Array.from({ length: 4 }, (_, i) => ix(`claude-ev-${i}`, "claude-2026-10-20", `2026-10-20T0${i}:00:00+00:00`, { rk: "update" })),
    ix("user-ev-r", "user-2026-10-20", "2026-10-20T09:00:00+00:00", { kind: "note", rk: "reply", tr: "muse-ev-old", rt: "muse-ev-old" }),
    ix("advisor-ev-r", "advisor-2026-10-20", "2026-10-20T09:30:00+00:00", { kind: "note", rk: "reply", tr: "muse-ev-old", rt: "user-ev-r" }),
  ].map(indexToEvent);
  const roots = buildFeed({ events: index, order: "posted" });
  assert.deepEqual(roots.map((r) => r.key), ["claude-ev-3", "claude-ev-2", "claude-ev-1", "claude-ev-0", "muse-ev-old"], "posted order: replies do not bump");
  const p1 = assemblePage({ mode: { dir: "first" }, size: 3, older: [{ items: roots }] });
  const p2 = assemblePage({ mode: parsePageMode({ before: p1.older }), size: 3, older: [{ items: roots }] });
  assert.deepEqual(p2.items.map((x) => x.key), ["claude-ev-0", "muse-ev-old"]);
  const old = p2.items[1];
  assert.deepEqual(old.replies.map((r) => [r.author, r.event.event_id]), [["user", "user-ev-r"], ["advisor", "advisor-ev-r"]]);
  assert.equal(old.replies[1].replyToAuthor, "user", "reply to a reply keeps its target");
  // author filter: the user only replied, so the old root matches "user" through its replies
  assert.deepEqual(filterFeed(roots, { author: "user" }).map((r) => r.key), ["muse-ev-old"]);
});

test("indexToEvent: decision ids survive; private chat stays hidden when locked", () => {
  const rows = [
    ix("evt-d", "run-1", "2026-10-20T01:00:00+00:00", { kind: "decision", did: "dec-1" }),
    ix("muse-ev-chat", "muse-2026-10-20", "2026-10-20T02:00:00+00:00", { rk: "chat_note" }),
  ].map(indexToEvent);
  assert.equal(rows[0].payload.decision_id, "dec-1");
  assert.deepEqual(buildFeed({ events: rows, order: "posted" }).map((f) => f.key), ["evt-d"]);
  assert.deepEqual(buildFeed({ events: rows, order: "posted", canSeePrivate: true }).map((f) => f.key), ["muse-ev-chat", "evt-d"]);
});
