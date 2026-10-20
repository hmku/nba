import type { NextConfig } from "next";
import { LEGACY_REDIRECTS } from "./src/lib/routes";

const nextConfig: NextConfig = {
  async redirects() {
    // /matchup and /lineup -> /week (query string passed through)
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
