import type { Player, PlayerValue } from "./types";
import { asStringArray, slugToName } from "./format";

export interface PlayerRow {
  player_id: string;
  name: string;
  team: string;
  positions: string[];
  status: string | null;
  rank: number | null;
  value: number | null;
  adp: number | null;
  cat_values: Record<string, number>;
}

export function toRows(values: PlayerValue[], players: Record<string, Player>): PlayerRow[] {
  return values.map((v) => {
    const p = players[v.player_id];
    const extra = (v.extra ?? {}) as Record<string, unknown>;
    const positions = asStringArray(v.positions ?? p?.positions ?? extra.positions);
    return {
      player_id: v.player_id,
      name: p?.name ?? (extra.name as string) ?? slugToName(v.player_id),
      team: p?.team ?? (extra.team as string) ?? "",
      positions,
      status: p?.status ?? (extra.status as string) ?? null,
      rank: v.rank,
      value: v.value,
      adp: v.adp,
      cat_values: (v.cat_values ?? {}) as Record<string, number>,
    };
  });
}

export function normName(s: string | null | undefined): string {
  return (s ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

/** Position filter incl. G (PG/SG) and F (SF/PF). */
export function matchesPos(positions: string[], filter: string): boolean {
  if (!filter || filter === "ALL") return true;
  if (filter === "G") return positions.some((p) => p === "PG" || p === "SG" || p === "G");
  if (filter === "F") return positions.some((p) => p === "SF" || p === "PF" || p === "F");
  return positions.includes(filter);
}
