/**
 * `week_review` model_runs rows (schema `week_review/1`, docs/research/dashboard_data.md section 8): the weekly
 * matchup retrospective written by `fantasy review week --save`. Pure parsing helpers (no Supabase here).
 */
import type { CatKey } from "./types";

export type ReviewCat = {
  cat: CatKey;
  label: string;
  winner: "me" | "opp" | "tie";
  final_me: number | null;
  final_opp: number | null;
  me_actual: number | null;
  me_proj_given_games: number | null;
  me_over_under: number | null;
  me_z: number | null;
  me_volume_effect: number | null;
  me_makes_above_expected: number | null;
  opp_actual: number | null;
  opp_proj_given_games: number | null;
  opp_over_under: number | null;
  opp_z: number | null;
  opp_volume_effect: number | null;
  opp_makes_above_expected: number | null;
};

export type ReviewPlayer = {
  player_id: string;
  name: string;
  games: number;
  score: number;
  top: { cat: CatKey; contrib_z: number; delta: number }[];
};

export type ReviewTeam = {
  name: string | null;
  games_counted: number;
  games_benched: number;
  games_expected_week_start: number | null;
  games_expected_covered_days: number | null;
  games_planned_lineup_plan?: number | null;
  top_over: ReviewPlayer[];
  top_under: ReviewPlayer[];
};

export type Missed = { at: string | null; type: string; kind: string | null; what: string | null; detail: string | null };

export type WeekReview = {
  runId: string;
  createdAt: string;
  week: number;
  weekStart: string;
  weekEnd: string;
  opponent: { name: string | null; slot: number | null };
  headline: string;
  bullets: string[];
  final: { score: { me: number; opp: number; ties: number }; result: "W" | "L" | "T"; complete: boolean; basis: string; through: string };
  categories: ReviewCat[];
  me: ReviewTeam;
  opp: ReviewTeam;
  decisions: {
    available: boolean;
    error?: string;
    transactions: { at: string | null; kind: string; status: string; what: string; error: string | null }[];
    decisions: { at: string | null; kind: string; agrees_with_model: boolean | null; choice: string | null; rationale: string | null }[];
    missed: Missed[];
  };
  pWin: { date: string | null; created_at: string | null; p_win: number; se: number | null; method: string }[];
  trajectory: { date_et: string; observed_at: string; score: { me: number; opp: number; ties: number } }[];
  projectionSource: { kind?: string; path?: string; commit?: string; data_as_of?: string | null; as_of_week_start?: boolean | null };
  analysisTo: string | null;
  warnings: string[];
  modelVersion: string;
};

type Row = { run_id: string; created_at: string; summary: unknown };

type Loose = Record<string, unknown>;
const obj = (v: unknown): Loose => (v && typeof v === "object" ? (v as Loose) : {});
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

function team(v: unknown): ReviewTeam {
  const t = obj(v);
  return {
    name: str(t.name),
    games_counted: num(t.games_counted) ?? 0,
    games_benched: num(t.games_benched) ?? 0,
    games_expected_week_start: num(t.games_expected_week_start),
    games_expected_covered_days: num(t.games_expected_covered_days),
    games_planned_lineup_plan: num(t.games_planned_lineup_plan),
    top_over: arr<ReviewPlayer>(t.top_over),
    top_under: arr<ReviewPlayer>(t.top_under),
  };
}

/** A saved row -> WeekReview (null for another schema). */
export function parseReview(row: Row): WeekReview | null {
  const s = obj(row.summary);
  if (s.schema !== "week_review/1") return null;
  const sum = obj(s.summary);
  const teams = obj(s.teams);
  const opp = obj(s.opponent);
  const dec = obj(s.decisions);
  return {
    runId: row.run_id,
    createdAt: row.created_at,
    week: num(s.week) ?? 0,
    weekStart: str(s.week_start) ?? "",
    weekEnd: str(s.week_end) ?? "",
    opponent: { name: str(opp.name), slot: num(opp.slot) },
    headline: str(sum.headline) ?? "",
    bullets: arr<string>(sum.bullets),
    final: s.final as WeekReview["final"],
    categories: arr<ReviewCat>(s.categories),
    me: team(teams.me),
    opp: team(teams.opp),
    decisions: {
      available: dec.available === true,
      error: str(dec.error) ?? undefined,
      transactions: arr(dec.transactions),
      decisions: arr(dec.decisions),
      missed: arr<Missed>(dec.missed),
    },
    pWin: arr(s.p_win_trajectory),
    trajectory: arr(s.trajectory),
    projectionSource: obj(s.projection_source) as WeekReview["projectionSource"],
    analysisTo: str(obj(s.analysis_days).to),
    warnings: arr<string>(s.warnings),
    modelVersion: str(s.model_version) ?? "",
  };
}

/** Newest review per week (rows newest first by created_at). */
export function latestByWeek(rows: Row[]): Map<number, WeekReview> {
  const out = new Map<number, WeekReview>();
  for (const r of rows) {
    const p = parseReview(r);
    if (p && !out.has(p.week)) out.set(p.week, p);
  }
  return out;
}

/** PostgREST select for the light review rows the week page needs (week, headline, final score/totals). */
export const REVIEW_BRIEF_COLS = "run_id,created_at,schema:summary->>schema,week:summary->week,headline:summary->summary->>headline,final:summary->final";

export type ReviewBrief = {
  runId: string;
  createdAt: string | null;
  week: number;
  headline: string;
  final: {
    score: { me: number; opp: number; ties: number };
    result: "W" | "L" | "T";
    complete: boolean;
    through: string | null;
    categories: { cat: string; me: number | null; opp: number | null; winner: string | null }[];
    totals: { me: Record<string, number>; opp: Record<string, number> } | null;
  } | null;
};

/** A light row (REVIEW_BRIEF_COLS) or a full row (`summary`) -> ReviewBrief; null for another schema / no week. */
export function parseReviewBrief(row: Loose): ReviewBrief | null {
  const full = row.summary !== undefined;
  const s = obj(row.summary);
  const schema = full ? s.schema : row.schema;
  const week = num(full ? s.week : row.week);
  if (schema !== "week_review/1" || week == null) return null;
  const f = obj(full ? s.final : row.final);
  const sc = obj(f.score);
  const me = num(sc.me);
  const opp = num(sc.opp);
  const result = f.result === "W" || f.result === "L" || f.result === "T" ? f.result : null;
  const tot = obj(f.totals);
  return {
    runId: str(row.run_id) ?? "",
    createdAt: str(row.created_at),
    week,
    headline: str(full ? obj(s.summary).headline : row.headline) ?? "",
    final:
      me != null && opp != null && result
        ? {
            score: { me, opp, ties: num(sc.ties) ?? 0 },
            result,
            complete: f.complete === true,
            through: str(f.through),
            categories: arr<Loose>(f.categories).map((c) => ({ cat: str(c.cat) ?? "", me: num(c.me), opp: num(c.opp), winner: str(c.winner) })),
            totals: f.totals ? { me: obj(tot.me) as Record<string, number>, opp: obj(tot.opp) as Record<string, number> } : null,
          }
        : null,
  };
}

/** Newest brief per week (rows newest first by created_at). */
export function briefsByWeek(rows: Loose[]): Map<number, ReviewBrief> {
  const out = new Map<number, ReviewBrief>();
  for (const r of rows) {
    const p = parseReviewBrief(r);
    if (p && !out.has(p.week)) out.set(p.week, p);
  }
  return out;
}
