/**
 * Old dashboard routes folded into "This week" (`/week`): Matchup and Lineup became one page. Used by next.config.ts
 * `redirects()` (temporary 307, so a later split can reuse the paths); Next passes the query string through, so
 * `/lineup?week=2` lands on `/week?week=2`. Pure (unit-tested by `npm test`).
 */
export const WEEK_PATH = "/week";

export const LEGACY_REDIRECTS = [
  { source: "/matchup", destination: WEEK_PATH, permanent: false },
  { source: "/lineup", destination: WEEK_PATH, permanent: false },
] as const;

/** Where a request for `path` + `search` ends up (`search` with or without "?"); null when it is not redirected. */
export function legacyTarget(path: string, search = ""): string | null {
  const clean = path.replace(/\/+$/, "") || "/";
  const r = LEGACY_REDIRECTS.find((x) => x.source === clean);
  if (!r) return null;
  const q = search.replace(/^\?/, "");
  return q ? `${r.destination}?${q}` : r.destination;
}
