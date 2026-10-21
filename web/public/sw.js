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
const VERSION = "v2"; // v2: new basketball icon
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
