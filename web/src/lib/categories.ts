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

/** Category family colour (identity, never the only cue): shooting %, scoring, big-man, guard, turnovers. */
export const CAT_COLOR: Record<CatKey, string> = {
  fg_pct: "var(--cat-pct)",
  ft_pct: "var(--cat-pct)",
  fg3m: "var(--cat-score)",
  pts: "var(--cat-score)",
  reb: "var(--cat-big)",
  blk: "var(--cat-big)",
  ast: "var(--cat-guard)",
  stl: "var(--cat-guard)",
  tov: "var(--cat-tov)",
};

/** One-line plain-English gloss per category (for a quant new to basketball). */
export const CAT_GLOSS: Record<CatKey, string> = {
  fg_pct: "field-goal % = makes / attempts over the week (volume-weighted)",
  ft_pct: "free-throw % = makes / attempts over the week",
  fg3m: "three-pointers made",
  pts: "points",
  reb: "rebounds",
  ast: "assists",
  stl: "steals",
  blk: "blocks",
  tov: "turnovers — fewer is better",
};

/** Format a weekly category total (percentages as .xxx, counts as integers). */
export function fmtCat(c: CatKey, v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (c === "fg_pct" || c === "ft_pct") return v.toFixed(3).replace(/^0/, "");
  return v >= 100 ? Math.round(v).toString() : v.toFixed(1);
}
