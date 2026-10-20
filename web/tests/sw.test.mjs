// Service worker caching rules (public/sw.js), run in a stub worker scope. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ORIGIN = "https://fantasy.harrisonku.com";
const SRC = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");

/** Load sw.js with a fake `self`; returns its fetch handler and a log of cache/network use. */
function worker() {
  const handlers = {};
  const log = { fetched: [], put: [] };
  const cache = {
    match: async () => undefined,
    put: async (req) => log.put.push(req.url),
    addAll: async () => {},
  };
  const ctx = {
    self: { addEventListener: (t, fn) => (handlers[t] = fn), location: { origin: ORIGIN }, skipWaiting: () => {} },
    caches: { open: async () => cache, match: async () => "OFFLINE", keys: async () => [], delete: async () => true },
    fetch: async (req) => {
      log.fetched.push(req.url);
      return { ok: true, type: "basic", clone: () => ({}) };
    },
    URL,
    Request: class {
      constructor(url) {
        this.url = url;
      }
    },
    Promise,
  };
  vm.runInNewContext(SRC, ctx);
  return { onFetch: handlers.fetch, log };
}

function request(path, { method = "GET", mode = "cors", auth = false, origin = ORIGIN } = {}) {
  return { url: origin + path, method, mode, headers: { has: (h) => auth && h === "authorization" } };
}

/** Dispatch a fetch event; returns the promise given to respondWith, or null when the SW does not intercept. */
function dispatch(onFetch, req) {
  let responded = null;
  onFetch({ request: req, respondWith: (p) => (responded = Promise.resolve(p)) });
  return responded;
}

test("API, cross-origin, non-GET and RSC/data requests are never intercepted", () => {
  const { onFetch } = worker();
  for (const req of [
    request("/api/agent/state"),
    request("/api/agent/state", { mode: "navigate" }), // even a navigation to an API URL
    request("/api/approvals", { method: "POST" }),
    request("/_next/static/chunks/a.js", { method: "POST" }),
    request("/rest/v1/model_runs", { origin: "https://x.supabase.co" }),
    request("/matchup?_rsc=abc"),
    request("/_next/image?url=%2Ficons%2Fa.png&w=64"),
    request("/manifest.webmanifest"),
  ])
    assert.equal(dispatch(onFetch, req), null, `${req.method} ${req.url}`);
});

test("page navigations always go to the network (offline page only when it fails), never into the cache", async () => {
  const { onFetch, log } = worker();
  await dispatch(onFetch, request("/matchup", { mode: "navigate" }));
  assert.deepEqual(log.fetched, [`${ORIGIN}/matchup`]);
  assert.deepEqual(log.put, []);
});

test("only content-hashed static assets and icons are cached, and not when the request carries Authorization", async () => {
  const { onFetch, log } = worker();
  await dispatch(onFetch, request("/_next/static/chunks/app.js"));
  await dispatch(onFetch, request("/icons/icon-192.png"));
  assert.deepEqual(log.put, [`${ORIGIN}/_next/static/chunks/app.js`, `${ORIGIN}/icons/icon-192.png`]);
  assert.equal(dispatch(onFetch, request("/_next/static/chunks/app.js", { auth: true })), null);
});
