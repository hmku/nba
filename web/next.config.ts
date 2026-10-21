import type { NextConfig } from "next";
import { LEGACY_REDIRECTS } from "./src/lib/routes";

const nextConfig: NextConfig = {
  // Render <head> metadata (manifest link, web-app-capable metas, title) blocking, inside <head>, for EVERY user agent.
  // Next >= 15.2 otherwise streams metadata into <body> for non-bot UAs (it only moves icon links back to <head>), so
  // iOS Safari's Add to Home Screen never saw the manifest (scope "/", display standalone) or the web-app-capable meta:
  // the installed app then opened each tab in an in-app Safari sheet. See docs/runbooks/dashboard.md (PWA). Tested.
  htmlLimitedBots: /.*/,
  async redirects() {
    // /matchup, /lineup -> /week; /moves, /activity, /approvals, /agents -> /inbox (query string passed through)
    return LEGACY_REDIRECTS.map((r) => ({ ...r }));
  },
  async headers() {
    return [
      {
        // Always revalidate the service worker script so updates roll out on the next visit.
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Content-Type", value: "application/javascript; charset=utf-8" },
        ],
      },
    ];
  },
};

export default nextConfig;
