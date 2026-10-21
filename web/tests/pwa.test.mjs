// Installed-app (PWA) invariants that keep iOS navigating inside the standalone app instead of an in-app Safari sheet.
// See docs/runbooks/dashboard.md ("Install as an app"). Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import manifest from "../src/app/manifest.ts";
import { LEGACY_REDIRECTS } from "../src/lib/routes.ts";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const REDIRECTED = new Set(["/", ...LEGACY_REDIRECTS.map((r) => r.source)]); // "/" redirects to /week or /draft

test("metadata (manifest link, web-app-capable metas) is rendered inside <head> for every user agent", () => {
  // Without this, Next >= 15.2 streams metadata into <body> for non-bot UAs and iOS never sees the manifest.
  const cfg = read("../next.config.ts");
  const m = cfg.match(/htmlLimitedBots:\s*\/(.*)\/([a-z]*),/);
  assert.ok(m, "next.config.ts must set htmlLimitedBots");
  const re = new RegExp(m[1], m[2]);
  for (const ua of [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
  ])
    assert.ok(re.test(ua), ua);
  assert.match(read("../src/app/layout.tsx"), /"apple-mobile-web-app-capable":\s*"yes"/);
});

test("manifest: same-origin scope '/', standalone, start_url in scope and not a redirect", () => {
  const m = manifest();
  assert.equal(m.scope, "/");
  assert.equal(m.id, "/"); // the app identity: never change it (iOS/Android treat a new id as a different app)
  assert.equal(m.display, "standalone");
  assert.match(m.start_url, /^\/[^/]/, "relative, same-origin path");
  assert.ok(!REDIRECTED.has(m.start_url.split(/[?#]/)[0]), `${m.start_url} redirects`);
});

test("nav links are same-origin relative paths without target/rel=external and never hit a redirect", () => {
  const src = read("../src/components/Nav.tsx");
  assert.doesNotMatch(src, /target=|rel=["{]?["']?external|https?:\/\/(?!\$\{)/, "no target, rel=external or absolute URLs");
  const hrefs = [...src.matchAll(/href:\s*"([^"]+)"/g)].map((x) => x[1]);
  assert.ok(hrefs.length >= 6, `found nav hrefs: ${hrefs}`);
  for (const h of hrefs) {
    assert.match(h, /^\/[a-z]/, h);
    assert.ok(!REDIRECTED.has(h), `${h} is a redirect source; link the final path`);
  }
});
