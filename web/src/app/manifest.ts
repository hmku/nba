import type { MetadataRoute } from "next";

/**
 * Web app manifest (served at /manifest.webmanifest) — makes the dashboard installable
 * (Android/desktop Chrome & Edge "Install app"; iOS uses the apple-touch-icon + appleWebApp metadata in layout.tsx).
 * Colours = the dark theme tokens in globals.css (the app's default); the light/dark browser chrome colour is
 * handled per-scheme by `viewport.themeColor` in layout.tsx. Icons: `node scripts/make-icons.mjs` (from icon.svg).
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "Fantasy Desk",
    short_name: "Fantasy",
    description: "Fantasy basketball agent dashboard: this week, league, inbox (decisions, approvals and agent updates), draft room.",
    start_url: "/week", // a 200 page, not "/" (a server redirect → /week): launch without a redirect hop
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#0a0c0f",
    theme_color: "#0a0c0f",
    categories: ["sports", "productivity"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
