// Inbox: advice ⋈ approval join (both directions), section grouping, thread linkage + feed de-dup, legacy redirects,
// the user's comment validation. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseAdvice } from "../src/lib/moves.ts";
import {
  approvalAdviceId,
  cardIndex,
  groupInbox,
  inboxHref,
  isExpired,
  joinDecisions,
  linkedCards,
  linkRowsOr,
  linkThreads,
  mentions,
  withoutLinked,
} from "../src/lib/inbox.ts";
import { buildFeed } from "../src/lib/feed.ts";
import { LEGACY_REDIRECTS, legacyTarget } from "../src/lib/routes.ts";
import { commentEventPayload, validateComment } from "../src/lib/agentPure.ts";

const adv = (id, over = {}, created = "2026-10-20T12:00:00Z") =>
  parseAdvice({
    run_id: `adv-${id}`,
    kind: "advice",
    created_at: created,
    summary: { advice_id: id, version: 1, type: "add_drop", title: `T ${id}`, status: "idea", requires_user_yes: true, ...over },
  });

const apr = (id, over = {}) => ({
  approval_id: id,
  run_id: null,
  action_type: "add_drop",
  summary: `S ${id}`,
  payload: {},
  status: "pending",
  note: null,
  requested_at: "2026-10-20T06:00:00Z",
  decided_at: null,
  expires_at: null,
  ...over,
});

test("join: approval.payload.advice_id -> advice (the real Champagnie shape)", () => {
  const a = adv("2026-10-21-champagnie-claim", { status: "awaiting_yes" });
  const p = apr("apr-4931627b321e4826b67c4e8f63c1305b", { payload: { add: "julian-champagnie", advice_id: "2026-10-21-champagnie-claim" } });
  assert.equal(approvalAdviceId(p), "2026-10-21-champagnie-claim");
  const cards = joinDecisions([a], [p]);
  assert.equal(cards.length, 1, "one card, not two");
  assert.equal(cards[0].key, "2026-10-21-champagnie-claim");
  assert.equal(cards[0].approval.approval_id, p.approval_id);
});

test("join: advice.summary.approval_id -> approval (other direction)", () => {
  const a = adv("x-trade", { approval_id: "apr-1", status: "awaiting_yes" });
  const cards = joinDecisions([a], [apr("apr-1")]);
  assert.equal(cards.length, 1);
  assert.equal(cards[0].approval.approval_id, "apr-1");
});

test("join: unmatched approvals and unmatched advice each keep their own card", () => {
  const cards = joinDecisions([adv("lonely-idea"), adv("awaiting", { status: "awaiting_yes" })], [apr("apr-orphan"), apr("apr-unknown", { payload: { advice_id: "not-loaded" } })]);
  assert.deepEqual(cards.map((c) => c.key).sort(), ["apr-orphan", "apr-unknown", "awaiting", "lonely-idea"]);
  assert.equal(cards.find((c) => c.key === "apr-orphan").advice, null);
});

test("join: several approvals on one item -> pending first, then the one the advice names, then newest", () => {
  const a = adv("multi", { approval_id: "apr-named" });
  const cards = joinDecisions(
    [a],
    [
      apr("apr-old", { status: "rejected", payload: { advice_id: "multi" }, requested_at: "2026-10-19T00:00:00Z" }),
      apr("apr-named", { status: "rejected", requested_at: "2026-10-18T00:00:00Z" }),
      apr("apr-new", { status: "pending", payload: { advice_id: "multi" }, requested_at: "2026-10-20T00:00:00Z" }),
    ],
  );
  assert.equal(cards.length, 1);
  assert.deepEqual(cards[0].approvals.map((x) => x.approval_id), ["apr-new", "apr-named", "apr-old"]);
  assert.equal(cards[0].approval.approval_id, "apr-new");
});

test("groupInbox: needs-yes / queued / ideas / closed", () => {
  const cards = joinDecisions(
    [
      adv("pending-ap", { status: "idea" }),
      adv("awaiting-no-ap", { status: "awaiting_yes" }),
      adv("approved-ap", { status: "awaiting_yes" }),
      adv("approved-advice", { status: "approved" }),
      adv("rejected-ap", { status: "awaiting_yes" }),
      adv("plain-idea", { status: "idea" }),
      adv("done", { status: "done" }),
      adv("expired", { status: "expired" }),
      adv("lineup-today", { type: "lineup", for_date: "2026-10-21", status: "done" }),
      adv("lineup-today-open", { type: "lineup", for_date: "2026-10-21", status: "idea" }),
      adv("lineup-old", { type: "lineup", for_date: "2026-10-20", status: "idea" }),
    ],
    [
      apr("a1", { payload: { advice_id: "pending-ap" } }),
      apr("a2", { payload: { advice_id: "approved-ap" }, status: "approved", decided_at: "2026-10-20T08:00:00Z" }),
      apr("a3", { payload: { advice_id: "rejected-ap" }, status: "rejected", decided_at: "2026-10-20T08:00:00Z" }),
      apr("a-orphan-pending"),
      apr("a-orphan-decided", { status: "approved", decided_at: "2026-10-20T09:00:00Z" }),
    ],
  );
  const g = groupInbox(cards, "2026-10-21");
  const ids = (xs) => xs.map((c) => c.key).sort();
  assert.deepEqual(ids(g.needsYes), ["a-orphan-pending", "awaiting-no-ap", "pending-ap"]);
  assert.deepEqual(ids(g.queued), ["approved-advice", "approved-ap"]);
  assert.deepEqual(ids(g.ideas), ["lineup-today-open", "plain-idea"]);
  assert.deepEqual(ids(g.closed), ["a-orphan-decided", "done", "expired", "lineup-old", "lineup-today", "rejected-ap"]);
});

test("groupInbox: needs-yes sorted by soonest expiry; closed newest first", () => {
  const cards = joinDecisions(
    [],
    [
      apr("late", { expires_at: "2026-10-23T00:00:00Z" }),
      apr("none"),
      apr("soon", { expires_at: "2026-10-21T00:00:00Z" }),
      apr("d-old", { status: "rejected", decided_at: "2026-10-19T00:00:00Z" }),
      apr("d-new", { status: "approved", decided_at: "2026-10-20T10:00:00Z" }),
    ],
  );
  const g = groupInbox(cards, "2026-10-21");
  assert.deepEqual(g.needsYes.map((c) => c.key), ["soon", "late", "none"]);
  assert.deepEqual(g.closed.map((c) => c.key), ["d-new", "d-old"]);
});

test("isExpired", () => {
  const now = Date.parse("2026-10-21T12:00:00Z");
  assert.equal(isExpired(apr("x", { expires_at: "2026-10-21T11:00:00Z" }), now), true);
  assert.equal(isExpired(apr("x", { expires_at: "2026-10-21T13:00:00Z" }), now), false);
  assert.equal(isExpired(apr("x"), now), false);
  assert.equal(isExpired(null, now), false);
});

test("mentions: whole-token match, trailing sentence dot allowed", () => {
  assert.equal(mentions("see 2026-10-21-champagnie-claim.", "2026-10-21-champagnie-claim"), true);
  assert.equal(mentions("(2026-10-21-champagnie-claim)", "2026-10-21-champagnie-claim"), true);
  assert.equal(mentions("2026-10-21-champagnie-claim-2 is different", "2026-10-21-champagnie-claim"), false);
  assert.equal(mentions("x2026-10-21-champagnie-claim", "2026-10-21-champagnie-claim"), false);
  assert.equal(mentions("2026-10-21-champagnie-claim.v2", "2026-10-21-champagnie-claim"), false);
});

const ev = (id, payload, message = null, run_id = "claude-2026-10-20", ts = "2026-10-20T13:00:00Z") => ({ event_id: id, run_id, ts, kind: "note", message, payload });

test("linkedCards: explicit details.advice_id wins; text mentions of advice or approval ids; replies/private never", () => {
  const cards = joinDecisions(
    [adv("2026-10-21-champagnie-claim", { status: "awaiting_yes" }), adv("2026-10-20-murray-trade")],
    [apr("apr-4931627b321e4826b67c4e8f63c1305b", { payload: { advice_id: "2026-10-21-champagnie-claim" } })],
  );
  const idx = cardIndex(cards);
  assert.deepEqual(linkedCards(ev("e1", { report_kind: "recommendation", details: { advice_id: "2026-10-20-murray-trade" } }, "also 2026-10-21-champagnie-claim"), idx), ["2026-10-20-murray-trade"]);
  assert.deepEqual(linkedCards(ev("e2", { report_kind: "recommendation" }, "Claim per 2026-10-21-champagnie-claim."), idx), ["2026-10-21-champagnie-claim"]);
  assert.deepEqual(linkedCards(ev("e3", { report_kind: "update", details: { approval: "apr-4931627b321e4826b67c4e8f63c1305b" } }, "approved?"), idx), ["2026-10-21-champagnie-claim"], "approval id in details text -> its advice card");
  assert.deepEqual(linkedCards(ev("e4", { report_kind: "comment", details: { approval_id: "apr-4931627b321e4826b67c4e8f63c1305b" } }), idx), ["2026-10-21-champagnie-claim"]);
  assert.deepEqual(linkedCards(ev("e5", { report_kind: "reply", reply_to: "e2", text: "2026-10-20-murray-trade" }, "2026-10-20-murray-trade"), idx), []);
  assert.deepEqual(linkedCards(ev("e6", { report_kind: "chat_digest" }, "2026-10-20-murray-trade"), idx), []);
  assert.deepEqual(linkedCards(ev("e7", { report_kind: "update", details: { advice_id: "unknown-id" } }, "nothing"), idx), []);
  assert.deepEqual(linkedCards(ev("e8", { report_kind: "update", evidence: [{ claim: "see 2026-10-20-murray-trade" }] }), idx).sort(), ["2026-10-20-murray-trade"]);
});

test("linkThreads + withoutLinked: a linked root (and its replies) leaves the feed", () => {
  const cards = joinDecisions([adv("2026-10-21-champagnie-claim", { status: "awaiting_yes" })], []);
  const root = ev("claude-ev-1", { report_kind: "recommendation" }, "Recommendation: 2026-10-21-champagnie-claim.");
  const other = ev("muse-ev-2", { report_kind: "injury_news" }, "Podziemski Q", "muse-2026-10-20", "2026-10-20T12:00:00Z");
  const reply = ev("user-ev-3", { report_kind: "reply", reply_to: "claude-ev-1", thread_root: "claude-ev-1", stance: "agree", text: "ok" }, "ok", "user-2026-10-20", "2026-10-20T14:00:00Z");
  const { byCard, linked } = linkThreads(cards, [root, other]);
  assert.deepEqual(byCard.get("2026-10-21-champagnie-claim"), ["claude-ev-1"]);
  assert.deepEqual([...linked], ["claude-ev-1"]);
  const feed = buildFeed({ events: [root, other, reply], order: "posted" });
  assert.equal(feed.length, 2);
  const shown = withoutLinked(feed, linked);
  assert.deepEqual(shown.map((it) => it.key), ["muse-ev-2"]);
  // the reply hangs under its root, which the card renders
  assert.equal(feed.find((it) => it.key === "claude-ev-1").replies.length, 1);
  assert.equal(withoutLinked(feed, new Set()), feed);
});

test("linkRowsOr is a PostgREST or-tree with quoted regex", () => {
  const s = linkRowsOr();
  assert.match(s, /payload->details->>advice_id\.not\.is\.null/);
  assert.match(s, /message\.match\."\(apr-\[0-9a-f\]\{8\}\|/);
  assert.equal(s.includes(" "), false);
});

test("legacy routes /moves, /activity, /approvals, /agents -> /inbox keeping the query", () => {
  const m = Object.fromEntries(LEGACY_REDIRECTS.map((r) => [r.source, r.destination]));
  for (const p of ["/moves", "/activity", "/approvals", "/agents"]) assert.equal(m[p], "/inbox");
  assert.ok(LEGACY_REDIRECTS.every((r) => r.permanent === false));
  assert.equal(legacyTarget("/activity", "?author=muse&before=abc"), "/inbox?author=muse&before=abc");
  assert.equal(legacyTarget("/moves/"), "/inbox");
  assert.equal(legacyTarget("/inbox"), null);
  assert.equal(inboxHref("2026-10-21-champagnie-claim"), "/inbox#2026-10-21-champagnie-claim");
  assert.equal(inboxHref(null), "/inbox");
});

test("validateComment: exactly one reference, stance, text; payload links via details", () => {
  const ok = validateComment({ advice_id: "2026-10-21-champagnie-claim", stance: "question", text: " why Bailey? " });
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.value, { ref: { advice_id: "2026-10-21-champagnie-claim" }, stance: "question", text: "why Bailey?" });
  const p = commentEventPayload(ok.value);
  assert.equal(p.payload.report_kind, "comment");
  assert.deepEqual(p.payload.details, { advice_id: "2026-10-21-champagnie-claim" });
  assert.equal(p.payload.author, "user");
  assert.equal(validateComment({ approval_id: "apr-1", text: "x" }).ok, true, "stance defaults to comment");
  assert.equal(validateComment({ advice_id: "a-b", approval_id: "apr-1", text: "x" }).ok, false);
  assert.equal(validateComment({ text: "x" }).ok, false);
  assert.equal(validateComment({ advice_id: "Bad Id!", text: "x" }).ok, false);
  assert.equal(validateComment({ advice_id: "abc", text: "  " }).ok, false);
  assert.equal(validateComment({ advice_id: "abc", text: "x", stance: "shout" }).ok, false);
  assert.equal(validateComment("nope").ok, false);
});
