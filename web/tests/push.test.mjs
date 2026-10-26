// Web Push for new pending approvals: notify selection, message format, subscription validation, and the service
// worker's push/notificationclick handlers. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import {
  BODY_MAX,
  isGone,
  MAX_PER_CALL,
  notificationFor,
  parseSubscription,
  selectToNotify,
} from "../src/lib/pushPure.ts";

const NOW = Date.parse("2026-10-26T18:00:00Z");
const hoursAgo = (h) => new Date(NOW - h * 3600e3).toISOString();
const apr = (id, over = {}) => ({
  approval_id: id,
  action_type: "add_drop",
  summary: `Add X, drop ${id}`,
  payload: {},
  status: "pending",
  requested_at: hoursAgo(1),
  expires_at: null,
  ...over,
});

test("selects pending, unexpired, recent, not-yet-notified approvals, oldest first", () => {
  const rows = [
    apr("apr-new", { requested_at: hoursAgo(0.5) }),
    apr("apr-old", { requested_at: hoursAgo(2) }),
    apr("apr-sent"),
    apr("apr-decided", { status: "approved" }),
    apr("apr-expired", { expires_at: hoursAgo(0.1) }),
    apr("apr-backlog", { requested_at: hoursAgo(30) }), // outside the 24 h window: never floods
    apr("apr-nots", { requested_at: null }),
    apr("apr-future-exp", { expires_at: new Date(NOW + 3600e3).toISOString() }),
  ];
  const got = selectToNotify(rows, new Set(["apr-sent"]), NOW).map((a) => a.approval_id);
  assert.deepEqual(got, ["apr-old", "apr-future-exp", "apr-new"]);
});

test("caps the number of notifications per call", () => {
  const rows = Array.from({ length: MAX_PER_CALL + 5 }, (_, i) => apr(`apr-${i}`, { requested_at: hoursAgo(1 + i / 100) }));
  assert.equal(selectToNotify(rows, new Set(), NOW).length, MAX_PER_CALL);
});

test("message: action title, body <= 100 chars, link to the card anchor, one tag per approval", () => {
  const m = notificationFor(apr("apr-0123abcd", { summary: "Add Jalen Duren, drop Fears ".repeat(10) }));
  assert.equal(m.title, "Add/drop: needs your yes");
  assert.ok(m.body.length <= BODY_MAX && m.body.endsWith("…"), m.body);
  assert.equal(m.url, "/inbox#apr-0123abcd");
  assert.equal(m.tag, "approval-apr-0123abcd");
  assert.equal(notificationFor(apr("x", { action_type: "weird", summary: null })).body, "x");
  assert.equal(notificationFor(apr("x", { action_type: "trade" })).title, "Trade: needs your yes");
});

test("subscription validation: https endpoint + base64url keys only", () => {
  const ok = { endpoint: "https://web.push.apple.com/abc", keys: { p256dh: "BEl6-_x", auth: "k2_-Q==" } };
  assert.deepEqual(parseSubscription(ok), { endpoint: ok.endpoint, p256dh: "BEl6-_x", auth: "k2_-Q==" });
  for (const bad of [
    null,
    {},
    { ...ok, endpoint: "http://insecure.example/x" },
    { ...ok, endpoint: "not a url" },
    { ...ok, keys: { p256dh: "a b", auth: "x" } },
    { ...ok, keys: { p256dh: "abc" } },
  ])
    assert.equal(parseSubscription(bad), null, JSON.stringify(bad));
});

test("404/410 mean the subscription is gone", () => {
  assert.ok(isGone(404) && isGone(410));
  assert.ok(!isGone(500) && !isGone(429) && !isGone(undefined));
});

// ---- service worker push handlers (public/sw.js) in a stub worker scope
const ORIGIN = "https://fantasy.harrisonku.com";
function worker(windows = []) {
  const handlers = {};
  const log = { shown: [], opened: [], navigated: [], focused: 0 };
  const self = {
    addEventListener: (t, fn) => (handlers[t] = fn),
    location: { origin: ORIGIN },
    registration: { showNotification: async (title, opts) => log.shown.push({ title, ...opts }) },
    clients: {
      matchAll: async () =>
        windows.map((url) => ({ url, focus: async () => log.focused++, navigate: async (p) => log.navigated.push(p) })),
      openWindow: async (p) => log.opened.push(p),
    },
  };
  vm.runInNewContext(readFileSync(new URL("../public/sw.js", import.meta.url), "utf8"), { self, caches: {}, URL, Promise });
  const fire = async (type, ev) => {
    let p;
    handlers[type]({ ...ev, waitUntil: (x) => (p = x) });
    await p;
  };
  return { fire, log };
}

test("push shows the notification with the card link; foreign URLs fall back to /inbox", async () => {
  const { fire, log } = worker();
  const payload = (o) => ({ data: { json: () => o } });
  await fire("push", payload({ title: "Add/drop: needs your yes", body: "Add X", url: "/inbox#apr-1", tag: "approval-apr-1" }));
  await fire("push", payload({ title: "t", url: "https://evil.example/phish" }));
  assert.equal(log.shown[0].title, "Add/drop: needs your yes");
  assert.equal(log.shown[0].tag, "approval-apr-1");
  assert.equal(log.shown[0].data.url, "/inbox#apr-1");
  assert.equal(log.shown[1].data.url, "/inbox");
});

test("tap focuses an open app window and navigates it to the card, else opens one", async () => {
  const note = (url) => ({ notification: { close: () => {}, data: { url } } });
  const open = worker([`${ORIGIN}/week`]);
  await open.fire("notificationclick", note("/inbox#apr-1"));
  assert.deepEqual(open.log.navigated, ["/inbox#apr-1"]);
  assert.equal(open.log.focused, 1);
  assert.deepEqual(open.log.opened, []);
  const closed = worker();
  await closed.fire("notificationclick", note("/inbox#apr-2"));
  assert.deepEqual(closed.log.opened, ["/inbox#apr-2"]);
});
