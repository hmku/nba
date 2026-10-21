/**
 * "This week" page readers (pure, type-only imports: unit-tested by `npm test`).
 *
 * - `kind = 'matchup_score'` model_runs rows (schema `matchup_score/1`, data side): the H2H score as Yahoo showed it at a
 *   capture (normally the morning sync). The newest row of a week is the current score. Shape:
 *   {schema, week, date_et, observed_at, source,
 *    me|opp: {slot, yahoo_team_id, name, totals: {fgm, fga, ftm, fta, fg3m, pts, reb, ast, stl, blk, tov}, fg_pct, ft_pct,
 *             games_played|null, games_remaining|null},
 *    categories: [{cat, me, opp, leader: me|opp|tie}], score: {me, opp, ties}}
 * - Category win odds by Sunday: the newest `lineup_plan` of the week (`expected.cat_win_prob`, given banked stats),
 *   else the season sim's weekly odds for this pairing (pre-week, no banked stats).
 * - Games left: remaining scheduled games of rostered players who are not on IL and not tagged out.
 * - The opponent's lineup: a `lineup_actual` row for their team (if recorded), else the bundled league snapshot
 *   (src/content/league_state.json, from `fantasy league sync`) with the NBA schedule's game markers per day.
 */
import type { LineupActual, LineupPlan, RunRow, SlotEntry } from "./lineup";
import type { H2HWeek } from "./postDraft";
import type { WeekMatchup } from "./sim";

type Obj = Record<string, unknown>;

const o = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);
const a = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const s = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The 9 categories in Yahoo's column order (same as categories.ts CATS; duplicated to keep this module import-free). */
export const WEEK_CATS = ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"] as const;
export type WeekCat = (typeof WEEK_CATS)[number];
const TOTAL_KEYS = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"] as const;

const OUT_TAGS = new Set(["O", "OUT", "INJ", "OFS", "NA", "SUSP", "IR"]);
const IL_SLOTS = new Set(["IL", "IL+"]);
const STARTING = ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util"];
const BENCH_ORDER = ["BN", "IL", "IL+"];

export type Leader = "me" | "opp" | "tie";

/** Who leads a category: more is better except turnovers (fewer). Null when either side is missing. */
export function catLeader(cat: string, me: number | null, opp: number | null): Leader | null {
  if (me == null || opp == null) return null;
  if (me === opp) return "tie";
  const meAhead = cat === "tov" ? me < opp : me > opp;
  return meAhead ? "me" : "opp";
}

export interface SideScore {
  slot: number | null;
  yahooTeamId: number | null;
  name: string | null;
  totals: Partial<Record<(typeof TOTAL_KEYS)[number], number>>;
  fgPct: number | null;
  ftPct: number | null;
  gamesPlayed: number | null;
  gamesRemaining: number | null;
}

export interface CatScore {
  cat: WeekCat;
  me: number | null;
  opp: number | null;
  leader: Leader | null;
}

export interface MatchupScore {
  runId: string;
  createdAt: string | null;
  week: number;
  dateEt: string | null;
  observedAt: string | null;
  source: string | null;
  me: SideScore;
  opp: SideScore;
  /** all 9 categories in Yahoo order (missing ones have null values) */
  categories: CatScore[];
  score: { me: number; opp: number; ties: number };
}

function ratio(m: number | undefined, att: number | undefined): number | null {
  return m != null && att != null && att > 0 ? m / att : null;
}

function side(v: unknown): SideScore {
  const x = o(v) ?? {};
  const t = o(x.totals) ?? {};
  const totals: SideScore["totals"] = {};
  for (const k of TOTAL_KEYS) {
    const q = n(t[k]);
    if (q != null) totals[k] = q;
  }
  return {
    slot: n(x.slot),
    yahooTeamId: n(x.yahoo_team_id),
    name: s(x.name),
    totals,
    // percent categories are always makes / attempts; the stored ratio is only a fallback
    fgPct: ratio(totals.fgm, totals.fga) ?? n(x.fg_pct),
    ftPct: ratio(totals.ftm, totals.fta) ?? n(x.ft_pct),
    gamesPlayed: n(x.games_played),
    gamesRemaining: n(x.games_remaining),
  };
}

function sideValue(sd: SideScore, cat: WeekCat): number | null {
  if (cat === "fg_pct") return sd.fgPct;
  if (cat === "ft_pct") return sd.ftPct;
  return sd.totals[cat] ?? null;
}

/** One `matchup_score` row -> MatchupScore (null for other kinds / rows without a week). */
export function parseMatchupScore(row: RunRow): MatchupScore | null {
  const sm = o(row.summary);
  const week = n(sm?.week);
  if (!sm || row.kind !== "matchup_score" || week == null) return null;
  const schema = s(sm.schema);
  if (schema && !schema.startsWith("matchup_score/")) return null;
  const me = side(sm.me);
  const opp = side(sm.opp);
  const given = new Map<string, Obj>();
  for (const c of a(sm.categories)) {
    const x = o(c);
    const cat = s(x?.cat);
    if (x && cat) given.set(cat, x);
  }
  const categories: CatScore[] = WEEK_CATS.map((cat) => {
    const g = given.get(cat);
    // percentages from makes/attempts when the totals are there; else the row's own values
    const mv = (cat === "fg_pct" || cat === "ft_pct" ? sideValue(me, cat) : null) ?? n(g?.me) ?? sideValue(me, cat);
    const ov = (cat === "fg_pct" || cat === "ft_pct" ? sideValue(opp, cat) : null) ?? n(g?.opp) ?? sideValue(opp, cat);
    const stated = s(g?.leader);
    const leader = catLeader(cat, mv, ov) ?? (stated === "me" || stated === "opp" || stated === "tie" ? stated : null);
    return { cat, me: mv, opp: ov, leader };
  });
  const counted = { me: 0, opp: 0, ties: 0 };
  for (const c of categories) if (c.leader === "me") counted.me++;
  else if (c.leader === "opp") counted.opp++;
  else if (c.leader === "tie") counted.ties++;
  const sc = o(sm.score);
  const stated = sc && n(sc.me) != null && n(sc.opp) != null ? { me: n(sc.me)!, opp: n(sc.opp)!, ties: n(sc.ties) ?? 0 } : null;
  // categories are recomputed from the totals; when every category is known that count wins over a stated score
  const allKnown = categories.every((c) => c.leader != null);
  return {
    runId: row.run_id,
    createdAt: row.created_at,
    week,
    dateEt: s(sm.date_et),
    observedAt: s(sm.observed_at),
    source: s(sm.source),
    me,
    opp,
    categories,
    score: allKnown || !stated ? counted : stated,
  };
}

/** The current score of `week`: the newest matchup_score row for it (by created_at, then observed_at). */
export function latestScore(rows: RunRow[], week: number): MatchupScore | null {
  return (
    rows
      .map(parseMatchupScore)
      .filter((x): x is MatchupScore => x != null && x.week === week)
      .sort((x, y) => (y.createdAt ?? "").localeCompare(x.createdAt ?? "") || (y.observedAt ?? "").localeCompare(x.observedAt ?? ""))[0] ?? null
  );
}

// ------------------------------------------------------------------ odds

export type OddsSource = "lineup_plan" | "season_sim";

export interface WeekOdds {
  /** P(win the matchup) */
  pWin: number | null;
  pTie: number | null;
  catWin: Partial<Record<WeekCat, number>>;
  source: OddsSource | null;
  /** the plan / sim run the numbers come from */
  runId: string | null;
  /** plan only: "Monte Carlo" (with SE) or "analytic" */
  method: string | null;
  pWinSe: number | null;
  asOf: string | null;
}

/**
 * The lineup plan whose odds describe the week as of `today`: the plan for the latest date inside [start, end] that is
 * not after today (then latest created); for a week that hasn't started, its earliest-dated plan. Never a plan for a
 * later day of the week: a `--week` run saves one plan per remaining day, and a later day's plan scores only that
 * day's games on top of the banked totals, so its P(win) is not the week's odds (Sunday's plan read 8% on Wed).
 */
export function latestPlanInWeek(plans: LineupPlan[], start: string, end: string, today: string): LineupPlan | null {
  // skip plans flagged as not covering the week (banked totals read before the plan date); unflagged = older rows
  const inWeek = plans.filter((p) => p.date >= start && p.date <= end && p.expected?.coversWeek !== false);
  const byNewest = (x: LineupPlan, y: LineupPlan) => y.date.localeCompare(x.date) || (y.createdAt ?? "").localeCompare(x.createdAt ?? "");
  const upToToday = inWeek.filter((p) => p.date <= today).sort(byNewest);
  if (upToToday.length) return upToToday[0];
  const first = inWeek.reduce<string | null>((m, p) => (m == null || p.date < m ? p.date : m), null);
  return inWeek.filter((p) => p.date === first).sort(byNewest)[0] ?? null;
}

/** Season-sim odds of `week` against `opponent`: the H2H matrix cell, else the weekly row when it is that pairing. */
export function simWeekOdds(weeks: WeekMatchup[], h2h: H2HWeek[], week: number, opponent: number | null): { pWin: number | null; pTie: number | null; catWin: Record<string, number> } | null {
  const cell = opponent != null ? h2h.find((x) => x.week === week)?.vs[String(opponent)] : undefined;
  if (cell) return { pWin: cell.pWin, pTie: cell.pTie, catWin: cell.catWin };
  const w = weeks.find((x) => x.week === week);
  if (w && (opponent == null || w.opponent === opponent)) return { pWin: w.pWin, pTie: w.pTie, catWin: w.catWin as Record<string, number> };
  return null;
}

/**
 * Odds for the week: the plan's per-category odds given banked stats when it has them (newer plans), else the season
 * sim's pre-week odds. P(win the week) likewise: the plan's Monte Carlo (else analytic) estimate, else the sim's.
 */
export function weekOdds(plan: LineupPlan | null, sim: { pWin: number | null; pTie: number | null; catWin: Record<string, number> } | null, simRunId: string | null): WeekOdds {
  const ex = plan?.expected ?? null;
  const pick = (m: Record<string, number> | null | undefined): Partial<Record<WeekCat, number>> => {
    const out: Partial<Record<WeekCat, number>> = {};
    for (const c of WEEK_CATS) if (m?.[c] != null) out[c] = m[c];
    return out;
  };
  const planWin = ex?.mc?.pWin ?? ex?.optimized?.pWin ?? null;
  if (plan && ex?.catWinProb && Object.keys(pick(ex.catWinProb)).length) {
    return {
      pWin: planWin ?? sim?.pWin ?? null,
      pTie: null,
      catWin: pick(ex.catWinProb),
      source: "lineup_plan",
      runId: plan.runId,
      method: ex.mc?.pWin != null ? "Monte Carlo" : ex.optimized?.pWin != null ? "analytic" : null,
      pWinSe: ex.mc?.pWin != null ? (ex.mc.pWinSe ?? null) : null,
      asOf: plan.createdAt,
    };
  }
  if (sim) return { pWin: sim.pWin, pTie: sim.pTie, catWin: pick(sim.catWin), source: "season_sim", runId: simRunId, method: null, pWinSe: null, asOf: null };
  return { pWin: null, pTie: null, catWin: {}, source: null, runId: null, method: null, pWinSe: null, asOf: null };
}

// ------------------------------------------------------------------ schedule / games left

/** bbref codes are the canonical ones (the plan's game markers use them); common alternates map onto them. */
const TEAM_ALIASES: Record<string, string> = { CHA: "CHO", PHX: "PHO", BKN: "BRK", GS: "GSW", NO: "NOP", NY: "NYK", SA: "SAS", UTAH: "UTA", WSH: "WAS" };
export const teamCode = (t: string | null | undefined): string | null => (t ? (TEAM_ALIASES[t.toUpperCase()] ?? t.toUpperCase()) : null);

/** {team: {date: "@DET" | "vs BOS"}} for `days` from the bundled schedule ({date: ["VIS@HOME", ...]}). */
export function teamGames(schedule: Record<string, string[]>, days: string[]): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {};
  for (const d of days)
    for (const g of schedule[d] ?? []) {
      const [vis, home] = g.split("@");
      if (!vis || !home) continue;
      (out[vis] ??= {})[d] = `@${home}`;
      (out[home] ??= {})[d] = `vs ${vis}`;
    }
  return out;
}

export interface RosterEntry {
  id: string;
  nbaTeam: string | null;
  slot: string | null;
  status: string | null;
}

export interface GamesLeft {
  total: number;
  perPlayer: Record<string, number>;
  /** players counted (rostered, not on IL, not tagged out) */
  counted: number;
  /** counted players whose schedule is unknown (no game map, no NBA team) */
  unknown: number;
}

/** Players who can still add stats: not in an IL slot and not tagged out (O / INJ / NA / SUSP ...). */
export function canPlay(p: { slot: string | null; status: string | null }): boolean {
  return !(p.slot && IL_SLOTS.has(p.slot)) && !(p.status && OUT_TAGS.has(p.status.toUpperCase()));
}

/**
 * Remaining scheduled games over `days` (pass the remaining days of the week, today included) of rostered players who
 * can play. A player's games come from `explicit[id]` (the plan's games map; absent date = no game) when present, else
 * from their NBA team's schedule.
 */
export function gamesLeft(players: RosterEntry[], days: string[], explicit: Record<string, Record<string, string>>, byTeam: Record<string, Record<string, string>>): GamesLeft {
  const out: GamesLeft = { total: 0, perPlayer: {}, counted: 0, unknown: 0 };
  for (const p of players) {
    if (!canPlay(p)) continue;
    out.counted++;
    const code = teamCode(p.nbaTeam);
    const g = explicit[p.id] ?? (code ? (byTeam[code] ?? {}) : null);
    if (!g) {
      out.unknown++;
      continue;
    }
    const k = days.filter((d) => g[d]).length;
    out.perPlayer[p.id] = k;
    out.total += k;
  }
  return out;
}

/** Every plan's games map and player info merged, newer plans winning (the plan lists every game of every roster player). */
export function mergePlanGames(plans: LineupPlan[]): { games: Record<string, Record<string, string>>; nbaTeam: Record<string, string | null> } {
  const games: Record<string, Record<string, string>> = {};
  const nbaTeam: Record<string, string | null> = {};
  for (const p of [...plans].sort((x, y) => (x.createdAt ?? "").localeCompare(y.createdAt ?? ""))) {
    for (const [pid, g] of Object.entries(p.games)) games[pid] = { ...(games[pid] ?? {}), ...g };
    for (const [pid, info] of Object.entries(p.players)) {
      nbaTeam[pid] = info.nbaTeam;
      games[pid] ??= {};
    }
  }
  return { games, nbaTeam };
}

/** Days of the week still to play (today included) — none for a past week, all for a future one. */
export function remainingDays(days: string[], today: string): string[] {
  return days.filter((d) => d >= today);
}

// ------------------------------------------------------------------ lineups side by side

export interface LeagueTeamPlayer {
  id: string;
  name: string;
  nba_team: string | null;
  positions?: string[];
  slot: string | null;
  status: string | null;
}

export interface LeagueTeam {
  name: string;
  yahoo_team_id: number | null;
  players: LeagueTeamPlayer[];
}

export interface LeagueState {
  source?: string;
  synced_at: string | null;
  captured_at: string | null;
  my_team: number | null;
  teams: Record<string, LeagueTeam>;
}

export interface LineupRow {
  slot: string;
  playerId: string | null;
  name: string | null;
  nbaTeam: string | null;
  status: string | null;
  /** "@DET" / "vs BOS"; null = no game (when `hasGame` is false) or unknown */
  game: string | null;
  hasGame: boolean | null;
}

/** Order rows like Yahoo: the 10 starting slots (empty ones kept), then BN, then IL; unknown labels last. */
export function orderRows(rows: LineupRow[]): LineupRow[] {
  const pool = [...rows];
  const take = (label: string) => {
    const i = pool.findIndex((r) => r.slot === label);
    return i >= 0 ? pool.splice(i, 1)[0] : null;
  };
  const out: LineupRow[] = [];
  for (const label of STARTING) out.push(take(label) ?? { slot: label, playerId: null, name: null, nbaTeam: null, status: null, game: null, hasGame: null });
  for (const label of BENCH_ORDER) for (let r = take(label); r; r = take(label)) out.push(r);
  return [...out, ...pool];
}

/**
 * A team's lineup on `day`: a recorded Yahoo read of that team for the day when there is one, else the league snapshot's
 * slots (as of its capture; future days keep those slots) with that day's game markers from the NBA schedule.
 */
export function teamLineup(team: LeagueTeam | null, day: string, byTeam: Record<string, Record<string, string>>, actual: LineupActual | null = null): LineupRow[] {
  const info = new Map((team?.players ?? []).map((p) => [p.id, p]));
  const game = (nbaTeam: string | null) => {
    const code = teamCode(nbaTeam);
    if (!code) return { game: null, hasGame: null };
    const g = byTeam[code]?.[day] ?? null;
    return { game: g, hasGame: g != null };
  };
  if (actual && actual.slots.length) {
    return orderRows(
      actual.slots.map((e: SlotEntry) => {
        const p = e.playerId ? info.get(e.playerId) : undefined;
        const g = e.game ? { game: e.game, hasGame: true } : game(p?.nba_team ?? null);
        return { slot: e.slot, playerId: e.playerId, name: e.name ?? p?.name ?? e.playerId, nbaTeam: p?.nba_team ?? null, status: e.status ?? p?.status ?? null, ...g };
      }),
    );
  }
  if (!team) return [];
  return orderRows(team.players.map((p) => ({ slot: p.slot ?? "?", playerId: p.id, name: p.name, nbaTeam: p.nba_team, status: p.status, ...game(p.nba_team) })));
}

/** Split lineup_actual rows: mine (team = my slot, or rows without a team) and the opponent's (team = their slot). */
export function splitActuals(actuals: LineupActual[], myTeam: number | null, oppTeam: number | null): { mine: LineupActual[]; theirs: LineupActual[] } {
  const mine: LineupActual[] = [];
  const theirs: LineupActual[] = [];
  for (const x of actuals) {
    if (x.team == null || (myTeam != null && x.team === myTeam)) mine.push(x);
    else if (oppTeam != null && x.team === oppTeam) theirs.push(x);
  }
  return { mine, theirs };
}

// ------------------------------------------------------------------ links

export const YAHOO_LEAGUE = "https://basketball.fantasysports.yahoo.com/nba/44495";

/** Yahoo's live matchup page for the week (both team ids when known, else the league's matchup page). */
export function yahooMatchupUrl(week: number, meYahooId: number | null, oppYahooId: number | null): string {
  if (meYahooId != null && oppYahooId != null) return `${YAHOO_LEAGUE}/matchup?week=${week}&mid1=${meYahooId}&mid2=${oppYahooId}`;
  return `${YAHOO_LEAGUE}/matchup?week=${week}`;
}
