import type { CSSProperties } from "react";
import type { CatKey } from "./types";

export const CATS: CatKey[] = ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"];

export const CAT_LABEL: Record<CatKey, string> = {
  fg_pct: "FG%",
  ft_pct: "FT%",
  fg3m: "3PM",
  pts: "PTS",
  reb: "REB",
  ast: "AST",
  stl: "STL",
  blk: "BLK",
  tov: "TO",
};

export const POSITIONS = ["PG", "SG", "SF", "PF", "C"] as const;

/** Background color for a z-like value (higher = better). Diverging green/red, alpha scaled. */
export function heatStyle(v: number | null | undefined, scale = 2): CSSProperties {
  if (v == null || Number.isNaN(v)) return {};
  const a = Math.min(1, Math.abs(v) / scale) * 0.55;
  const color = v >= 0 ? `rgba(16, 185, 129, ${a})` : `rgba(244, 63, 94, ${a})`;
  return { backgroundColor: color };
}
