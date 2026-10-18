import { CATS, CAT_LABEL } from "./categories";
import type { CatKey } from "./types";
import type { PlayerRow } from "./players";

export interface FallbackItem {
  row: PlayerRow;
  score: number;
  needBonus: number;
  probAvail: number | null;
  reason: string;
}

/** Rough P(player still available at my next pick) from ADP (normal approx, sd grows with ADP). */
export function probAvailableFromAdp(adp: number | null, picksUntil: number, currentPick: number): number | null {
  if (adp == null || picksUntil <= 0) return picksUntil <= 0 ? 1 : null;
  const target = currentPick + picksUntil; // the pick number at which I select
  const sd = Math.max(3, adp * 0.18);
  const z = (adp - target + 0.5) / sd;
  // logistic approximation of the normal CDF
  return 1 / (1 + Math.exp(-1.702 * z));
}

/**
 * Client-side fallback ranking: best available by value with a simple roster-need tweak:
 *  +0.25 × the player's value in my 3 weakest categories, and +0.4 if he fills a position group I lack.
 */
export function fallbackRanking(
  available: PlayerRow[],
  myRoster: PlayerRow[],
  picksUntil: number,
  currentPick: number,
  limit = 10,
): { items: FallbackItem[]; weakest: string[] } {
  const totals: Record<string, number> = {};
  for (const c of CATS) totals[c] = myRoster.reduce((s, p) => s + (p.cat_values[c] ?? 0), 0);
  const weakest = myRoster.length ? [...CATS].sort((a, b) => totals[a] - totals[b]).slice(0, 3) : [];
  const has = (grp: string[]) => myRoster.some((p) => p.positions.some((x) => grp.includes(x)));
  const groups: Record<string, string[]> = { G: ["PG", "SG"], F: ["SF", "PF"], C: ["C"] };
  const missing = myRoster.length ? Object.entries(groups).filter(([, g]) => !has(g)) : [];

  const items = available
    .filter((r) => r.value != null)
    .slice(0, 150)
    .map((row) => {
      const catBonus = weakest.reduce((s, c) => s + (row.cat_values[c] ?? 0), 0) * 0.25;
      const fills = missing.filter(([, g]) => row.positions.some((x) => g.includes(x))).map(([k]) => k);
      const posBonus = fills.length ? 0.4 : 0;
      const needBonus = catBonus + posBonus;
      const parts: string[] = [];
      if (weakest.length) parts.push(`need ${weakest.map((c) => CAT_LABEL[c as CatKey]).join("/")} ${catBonus >= 0 ? "+" : ""}${catBonus.toFixed(2)}`);
      if (fills.length) parts.push(`fills ${fills.join("/")}`);
      return {
        row,
        score: (row.value ?? 0) + needBonus,
        needBonus,
        probAvail: probAvailableFromAdp(row.adp, picksUntil, currentPick),
        reason: parts.join(" · ") || "best value available",
      };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
  return { items, weakest };
}
