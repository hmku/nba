// Threads (replies to agent updates) + the Inbox "Agent updates" feed. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ADVISOR_ALLOWED_ENDPOINTS,
  ADVISOR_REPORT_KINDS,
  authorizeAgentRequest,
  isReplyBody,
  REPLY_MAX_TEXT,
  replyEventPayload,
  replyTarget,
  userRunId,
  validateAdvisorSuggestion,
  validateReply,
  validateReport,
} from "../src/lib/agentPure.ts";
import { authorOf, buildFeed, decisionIdOf, feedFacets, filterFeed, isAutoVerification, missingRoots, splitLong, trimPayload } from "../src/lib/feed.ts";

const TOKENS = { agent: "muse-secret-token", advisor: "advisor-secret-token" };

/* ------------------------------------------------------------------ API: auth + validation */

test("advisor scope: replies go through the existing report endpoint; nothing new is opened", () => {
  assert.deepEqual([...ADVISOR_ALLOWED_ENDPOINTS], ["GET /api/agent/state", "GET /api/agent/players", "POST /api/agent/report"]);
  assert.deepEqual([...ADVISOR_REPORT_KINDS], ["advisor_suggestion", "reply"]);
  assert.equal(authorizeAgentRequest("Bearer advisor-secret-token", TOKENS, "POST", "/api/agent/report").identity, "advisor");
  for (const p of ["/api/agent/snapshot", "/api/agent/pick", "/api/threads/reply", "/api/agent/reply"])
    assert.equal(authorizeAgentRequest("Bearer advisor-secret-token", TOKENS, "POST", p).status, 403, p);
});

test("isReplyBody routes report_kind or kind 'reply' (both tokens)", () => {
  assert.equal(isReplyBody({ report_kind: "reply" }), true);
  assert.equal(isReplyBody({ kind: "reply" }), true);
  assert.equal(isReplyBody({ kind: "trade" }), false);
  assert.equal(isReplyBody({ report_kind: "advisor_suggestion" }), false);
  assert.equal(isReplyBody(null), false);
  assert.equal(isReplyBody(["reply"]), false);
});

test("advisor_suggestion validation still refuses any other kind (replies are validated separately)", () => {
  const v = validateAdvisorSuggestion({ report_kind: "reply", category: "process", title: "t", summary: "s", confidence: "low", urgency: "low" });
  assert.equal(v.ok, false);
  assert.ok(v.errors.some((e) => e.startsWith("report_kind")));
});

test("validateReply: valid reply keeps only known fields (client-claimed identity dropped)", () => {
  const v = validateReply({
    report_kind: "reply",
    reply_to: "muse-ev-abc123",
    stance: "disagree",
    text: "  The trade loses 2 cats for us.  ",
    evidence: [{ claim: "ΔP(playoffs)", value: -0.031, se: 0.008, label: "simulated", command: "fantasy inseason trade ..." }],
    author: "user",
    reported_by: "claude",
    thread_root: "evil",
  });
  assert.equal(v.ok, true);
  assert.deepEqual(v.value, {
    reply_to: "muse-ev-abc123",
    stance: "disagree",
    text: "The trade loses 2 cats for us.",
    evidence: [{ claim: "ΔP(playoffs)", value: -0.031, se: 0.008, label: "simulated", command: "fantasy inseason trade ..." }],
  });
  // kind defaults to reply on the user's route
  assert.equal(validateReply({ reply_to: "evt-1", stance: "agree", text: "yes" }).ok, true);
});

test("validateReply: every problem is listed", () => {
  const v = validateReply({ report_kind: "reply", reply_to: "bad id; drop table", stance: "maybe", text: "", evidence: [{ claim: "x", label: "vibes" }] });
  assert.equal(v.ok, false);
  const all = v.errors.join("\n");
  for (const s of ["reply_to", "stance", "text", "evidence[0].label"]) assert.ok(all.includes(s), s);
  assert.equal(validateReply({ reply_to: "e1", stance: "agree", text: "x".repeat(REPLY_MAX_TEXT + 1) }).ok, false);
  assert.equal(validateReply({ reply_to: "e1", stance: "agree", text: "x", evidence: new Array(11).fill({ claim: "c", label: "prior" }) }).ok, false);
  assert.equal(validateReply({ reply_to: "e1", stance: "agree", text: "x", junk: "y".repeat(13000) }).ok, false, "body size cap");
  assert.equal(validateReply({ report_kind: "trade", reply_to: "e1", stance: "agree", text: "x" }).ok, false);
});

test("replyTarget: missing → 404, private chat content → 403, reply → its thread root", () => {
  assert.equal(replyTarget(null).status, 404);
  const digest = { event_id: "muse-ev-d", payload: { report_kind: "chat_digest" } };
  const note = { event_id: "muse-ev-n", payload: { report_kind: "chat_note" } };
  assert.equal(replyTarget(digest).status, 403);
  assert.equal(replyTarget(note).status, 403);
  assert.deepEqual(replyTarget({ event_id: "muse-ev-t", payload: { report_kind: "trade_offer" } }), { ok: true, threadRoot: "muse-ev-t" });
  assert.deepEqual(replyTarget({ event_id: "evt-decision", payload: { choice: "x" } }), { ok: true, threadRoot: "evt-decision" });
  assert.deepEqual(replyTarget({ event_id: "evt-nopayload" }), { ok: true, threadRoot: "evt-nopayload" });
  const r1 = { event_id: "advisor-ev-r", payload: { report_kind: "reply", reply_to: "muse-ev-t", thread_root: "muse-ev-t" } };
  assert.deepEqual(replyTarget(r1), { ok: true, threadRoot: "muse-ev-t" });
  const legacy = { event_id: "x-r", payload: { report_kind: "reply", reply_to: "muse-ev-t" } };
  assert.deepEqual(replyTarget(legacy), { ok: true, threadRoot: "muse-ev-t" });
});

test("replyEventPayload: identity set by the server", () => {
  const { message, payload } = replyEventPayload({ reply_to: "advisor-ev-1", stance: "agree", text: "ok", evidence: [] }, "muse", "muse-ev-root");
  assert.equal(message, "ok");
  assert.deepEqual(payload, {
    report_kind: "reply",
    reply_to: "advisor-ev-1",
    thread_root: "muse-ev-root",
    stance: "agree",
    text: "ok",
    author: "muse",
    reported_by: "muse",
    source: "muse",
  });
  assert.equal(userRunId(new Date("2026-10-21T02:00:00Z")), "user-2026-10-20", "US/Eastern day");
});

test("trade_offer reports need parties and players", () => {
  const ok = validateReport({
    kind: "trade_offer",
    message: "Jesse offered Murray for Ware + VanVleet",
    details: { from_team: "Yeet", to_team: "Tip-Top", from_gives: ["Jamal Murray"], to_gives: ["Jalen Duren", "Fred VanVleet"], status: "proposed", involves_me: true },
    source: "yahoo",
  });
  assert.equal(ok.ok, true);
  assert.equal(validateReport({ kind: "trade_offer", message: "x" }).ok, false);
  assert.match(validateReport({ kind: "trade_offer", message: "x", details: { from_team: "A", to_team: "B", from_gives: [], to_gives: [] } }).error, /both empty/);
  assert.match(validateReport({ kind: "trade_offer", message: "x", details: { from_team: "A", to_team: "B", from_gives: [1], to_gives: [] } }).error, /from_gives/);
  assert.match(
    validateReport({ kind: "trade_offer", message: "x", details: { from_team: "A", to_team: "B", from_gives: ["p"], to_gives: [], status: "maybe" } }).error,
    /status/,
  );
  assert.equal(validateReport({ kind: "reply", message: "x" }).ok, false, "replies never go through validateReport");
});

/* ------------------------------------------------------------------ feed */

const ev = (event_id, run_id, ts, payload, extra = {}) => ({ event_id, run_id, ts, kind: "observation", message: payload?.text ?? event_id, payload, ...extra });
const reply = (id, run, ts, to, root, stance = "agree") =>
  ev(id, run, ts, { report_kind: "reply", reply_to: to, thread_root: root, stance, text: `${id} says ${stance}` }, { kind: "note" });

const EVENTS = [
  ev("muse-ev-trade", "muse-2026-10-20", "2026-10-20T10:00:00Z", { report_kind: "trade_offer", details: { from_team: "Yeet", to_team: "Me", from_gives: ["Murray"], to_gives: ["Ware"] } }),
  ev("muse-ev-digest", "muse-2026-10-20", "2026-10-20T09:00:00Z", { report_kind: "chat_digest", details: { excerpts: [] } }),
  ev("muse-ev-pick", "muse-2026-10-19", "2026-10-19T23:00:00Z", { report_kind: "observed_pick" }),
  ev("advisor-ev-1", "advisor-2026-10-20", "2026-10-20T11:00:00Z", { report_kind: "advisor_suggestion", details: { title: "x" } }),
  ev("muse-ev-fakeadv", "muse-2026-10-20", "2026-10-20T11:30:00Z", { report_kind: "advisor_suggestion", reported_by: "advisor" }),
  ev("evt-dec", "run-abc", "2026-10-20T08:00:00Z", { choice: "x" }, { kind: "decision", message: "Started Duren over Ware" }),
  ev("evt-cmd", "run-abc", "2026-10-20T08:01:00Z", null, { kind: "command" }),
  reply("advisor-ev-r1", "advisor-2026-10-20", "2026-10-20T12:00:00Z", "muse-ev-trade", "muse-ev-trade", "disagree"),
  reply("evt-r2", "claude-2026-10-20", "2026-10-20T12:30:00Z", "advisor-ev-r1", "muse-ev-trade", "agree"),
  reply("user-ev-r3", "user-2026-10-20", "2026-10-20T13:00:00Z", "muse-ev-trade", "muse-ev-trade", "question"),
  reply("muse-ev-r4", "muse-2026-10-20", "2026-10-20T07:00:00Z", "evt-old-root", "evt-old-root", "comment"),
];

test("authorOf: identity is the run, never the payload", () => {
  assert.equal(authorOf("muse-2026-10-20"), "muse");
  assert.equal(authorOf("advisor-2026-10-20"), "advisor");
  assert.equal(authorOf("user-2026-10-20"), "user");
  assert.equal(authorOf("claude-2026-10-20"), "claude");
  assert.equal(authorOf("run-abc123"), "claude");
  assert.equal(authorOf(null), "claude");
});

test("feed: one item per update, replies threaded under the root, newest activity first", () => {
  const feed = buildFeed({ events: [...EVENTS, EVENTS[0]], canSeePrivate: false });
  const keys = feed.map((f) => f.key);
  assert.deepEqual(keys, ["muse-ev-trade", "muse-ev-fakeadv", "advisor-ev-1", "evt-dec"]);
  const trade = feed[0];
  assert.equal(trade.type, "report");
  assert.equal(trade.author, "muse");
  assert.equal(trade.kind, "trade_offer");
  assert.equal(trade.lastActivity, "2026-10-20T13:00:00Z", "bumped by the newest reply");
  assert.deepEqual(
    trade.replies.map((r) => [r.author, r.stance, r.replyToAuthor]),
    [
      ["advisor", "disagree", null],
      ["claude", "agree", "advisor"],
      ["user", "question", null],
    ],
  );
  const fake = feed.find((f) => f.key === "muse-ev-fakeadv");
  assert.equal(fake.type, "report", "a Muse-run row claiming to be an advisor suggestion stays a Muse report");
  assert.equal(fake.author, "muse");
  assert.equal(feed.find((f) => f.key === "advisor-ev-1").type, "suggestion");
  assert.equal(feed.find((f) => f.key === "evt-dec").type, "event");
  assert.ok(!keys.includes("muse-ev-digest"), "private digest hidden when locked");
  assert.ok(!keys.includes("muse-ev-pick") && !keys.includes("evt-cmd"), "low-level rows are not feed cards");
});

test("feed: unlocked shows digests (not replyable); snapshots and run summaries become cards", () => {
  const feed = buildFeed({
    events: EVENTS,
    canSeePrivate: true,
    snapshots: [
      { snapshot_id: "snap_1", kind: "my_team", last_seen_at: "2026-10-20T14:00:00Z" },
      { snapshot_id: "snap_2", kind: "matchup", observed_at: "2026-10-20T09:00:00Z" },
    ],
    runs: [
      { run_id: "run-daily", agent: "daily-routine", status: "succeeded", summary: "Lineup set; 1 add/drop awaiting yes", ended_at: "2026-10-20T15:00:00Z" },
      { run_id: "run-live", agent: "daily-routine", status: "running", summary: "in progress" },
      { run_id: "muse-2026-10-20", agent: "muse", status: "succeeded", summary: "passive" },
      { run_id: "run-nosummary", agent: "league-loop", status: "succeeded", summary: null },
    ],
  });
  assert.deepEqual(
    feed.slice(0, 3).map((f) => [f.key, f.type, f.author]),
    [
      ["run:run-daily", "run", "claude"],
      ["snapshots", "snapshots", "muse"],
      ["muse-ev-trade", "report", "muse"],
    ],
  );
  const digest = feed.find((f) => f.key === "muse-ev-digest");
  assert.equal(digest.type, "digest");
  assert.equal(digest.replyable, false);
  assert.equal(feed.find((f) => f.key === "snapshots").snapshots.length, 2);
  assert.equal(feed.filter((f) => f.type === "run").length, 1);
});

test("missingRoots: old roots referenced by replies are fetched separately", () => {
  assert.deepEqual(missingRoots(EVENTS), ["evt-old-root"]);
  const withRoot = [...EVENTS, ev("evt-old-root", "run-old", "2026-10-01T00:00:00Z", null, { kind: "decision" })];
  assert.deepEqual(missingRoots(withRoot), []);
  const feed = buildFeed({ events: withRoot });
  const old = feed.find((f) => f.key === "evt-old-root");
  assert.equal(old.replies.length, 1);
  assert.equal(old.lastActivity, "2026-10-20T07:00:00Z");
});

test("filters: author matches starters and repliers; kind matches the update; facets count threads", () => {
  const feed = buildFeed({ events: EVENTS });
  assert.deepEqual(
    filterFeed(feed, { author: "user" }).map((f) => f.key),
    ["muse-ev-trade"],
  );
  assert.deepEqual(
    filterFeed(feed, { author: "advisor" }).map((f) => f.key),
    ["muse-ev-trade", "advisor-ev-1"],
  );
  assert.deepEqual(
    filterFeed(feed, { kind: "decision" }).map((f) => f.key),
    ["evt-dec"],
  );
  assert.deepEqual(filterFeed(feed, { author: "user", kind: "decision" }), []);
  const f = feedFacets(feed);
  assert.deepEqual(f.authors, [
    ["muse", 2],
    ["advisor", 2],
    ["claude", 2],
    ["user", 1],
  ]);
  assert.equal(f.kinds.find(([k]) => k === "trade_offer")[1], 1);
});

test("feed skips the ledger's automatic 'verified txn-…' events; decisions expose their decision_id", () => {
  const auto = ev("evt-auto", "run-abc", "2026-10-20T09:00:00Z", { evidence: {} }, { kind: "verification", message: "verified txn-0e73" });
  const human = ev("evt-ver", "run-abc", "2026-10-20T09:01:00Z", null, { kind: "verification", message: "Lineup matches the plan (server re-read)" });
  const dec = ev("evt-d", "run-abc", "2026-10-20T09:02:00Z", { decision_id: "dec-1" }, { kind: "decision" });
  assert.equal(isAutoVerification(auto), true);
  assert.equal(isAutoVerification(human), false);
  assert.deepEqual(
    buildFeed({ events: [auto, human, dec] }).map((f) => f.key),
    ["evt-d", "evt-ver"],
  );
  assert.equal(decisionIdOf(dec), "dec-1");
  assert.equal(decisionIdOf(human), null);
});

test("trimPayload caps big payloads", () => {
  assert.deepEqual(trimPayload({ a: 1 }), { a: 1 });
  assert.equal(trimPayload(null), null);
  const big = trimPayload({ x: "y".repeat(5000) }, 100);
  assert.equal(big._truncated, true);
  assert.equal(big.preview.length, 101);
  assert.ok(big.chars > 5000);
});

test("splitLong collapses long text at a word boundary", () => {
  assert.deepEqual(splitLong("short"), { head: "short", rest: null });
  const long = "word ".repeat(200);
  const { head, rest } = splitLong(long);
  assert.ok(head.length <= 420 && head.length > 250);
  assert.equal(head + rest, long);
});
