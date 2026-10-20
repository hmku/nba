/**
 * Old dashboard routes folded into newer pages. Used by next.config.ts `redirects()` (temporary 307, so a later split
 * can reuse the paths); Next passes the query string through and the browser keeps the #anchor, so
 * `/lineup?week=2` lands on `/week?week=2` and `/activity?author=muse#ev-1` on `/inbox?author=muse#ev-1`.
 *  - "This week" (`/week`): Matchup and Lineup became one page.
 *  - "Inbox" (`/inbox`): Moves and Activity became one page (and the older /approvals and /agents URLs with them).
 * Pure (unit-tested by `npm test`).
 */
export const WEEK_PATH = "/week";
export const INBOX_PATH = "/inbox";

export const LEGACY_REDIRECTS = [
  { source: "/matchup", destination: WEEK_PATH, permanent: false },
  { source: "/lineup", destination: WEEK_PATH, permanent: false },
  { source: "/moves", destination: INBOX_PATH, permanent: false },
  { source: "/activity", destination: INBOX_PATH, permanent: false },
  { source: "/approvals", destination: INBOX_PATH, permanent: false },
  { source: "/agents", destination: INBOX_PATH, permanent: false },
] as const;

/** Where a request for `path` + `search` ends up (`search` with or without "?"); null when it is not redirected. */
export function legacyTarget(path: string, search = ""): string | null {
  const clean = path.replace(/\/+$/, "") || "/";
  const r = LEGACY_REDIRECTS.find((x) => x.source === clean);
  if (!r) return null;
  const q = search.replace(/^\?/, "");
  return q ? `${r.destination}?${q}` : r.destination;
}
