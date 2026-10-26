/* Fantasy Desk service worker — deliberately minimal and safe.
 *
 * - Cache-first ONLY for immutable static assets: /_next/static/* (content-hashed) and /icons/*.
 * - Page navigations: always the network (never cached — pages carry live Supabase data and
 *   cookie-dependent private sections); if the network fails, show the pre-cached /offline.html.
 * - Everything else (/api/*, RSC/data fetches, Supabase and other cross-origin requests, non-GET):
 *   not intercepted at all, so it goes straight to the network with no caching.
 *
 * Bump VERSION to drop old caches.
 */
const VERSION = "v3"; // v2: new basketball icon; v3: push notifications
const STATIC_CACHE = `fdesk-static-${VERSION}`;
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((c) => c.addAll(PRECACHE.map((u) => new Request(u, { cache: "reload" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("fdesk-") && k !== STATIC_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function isStaticAsset(url) {
  return url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/");
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // Supabase etc.: never touched
  if (url.pathname.startsWith("/api/")) return; // tokens / passphrase-gated endpoints: never touched

  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (isStaticAsset(url) && !req.headers.has("authorization")) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok && res.type === "basic") cache.put(req, res.clone());
        return res;
      }),
    );
  }
  // anything else: default browser handling (network, no SW cache)
});

/* ---- Web Push: new pending approvals (POST /api/push/notify -> web-push -> here). See docs/runbooks/dashboard.md. */

/** Only same-origin paths are opened on tap (the payload comes from our server, but never trust a URL blindly). */
function safePath(u) {
  try {
    const url = new URL(u || "/inbox", self.location.origin);
    return url.origin === self.location.origin ? url.pathname + url.search + url.hash : "/inbox";
  } catch {
    return "/inbox";
  }
}

self.addEventListener("push", (event) => {
  let d = {};
  try {
    d = event.data ? event.data.json() : {};
  } catch {
    d = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(d.title || "Fantasy Desk", {
      body: d.body || "A decision needs your yes.",
      tag: d.tag || undefined,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: safePath(d.url) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = safePath(event.notification.data && event.notification.data.url);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (wins) => {
      const win = wins.find((w) => new URL(w.url).origin === self.location.origin);
      if (win) {
        await win.focus();
        if (win.navigate) return win.navigate(path);
      }
      return self.clients.openWindow(path);
    }),
  );
});
