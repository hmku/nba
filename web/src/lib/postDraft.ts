/**
 * Post-draft season-simulation readers (pure, no runtime imports: unit-tested by `npm test`).
 *
 * Producer: `fantasy sim season --league-file data/manual/league_rosters_2026-27.json --scenario "post-draft: actual rosters"`
 * (docs/research/dashboard_data.md section 3). Shapes read here:
 *   summary.scenario                     "post-draft: actual rosters"
 *   summary.matchup_schedule             {source, assumed_weeks: number[], all_assumed, note}
 *   my_team.weekly_matchups[].opponent_assumed
 *   my_team.weekly_h2h_matrix[]          {week, start, end, scheduled_opponent, opponent_assumed, my_player_games,
 *                                         vs: {"<team>": {p_win, p_tie, exp_cats_won, opp_player_games, cat_win_probs}}}
 *   my_team.vs_opponents_all_play        {"<team>": {name, p_win_matchup_any_week, exp_cats_won, cat_win_rate}}
 */

type Obj = Record<string, unknown>;

const o = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);
const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const a = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const s = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

const POST_DRAFT_PREFIX = "post-draft";

/** Season runs on real rosters: no scenario (older runs) or a "post-draft…" scenario. */
export function isSeasonScenario(scenario: string | null | undefined): boolean {
  return scenario == null || scenario.startsWith(POST_DRAFT_PREFIX);
}

function numMap(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  const m = o(v);
  if (m) for (const [k, x] of Object.entries(m)) if (n(x) != null) out[k] = x as number;
  return out;
}

export interface ScheduleInfo {
  source: string | null;
  assumedWeeks: number[];
  allAssumed: boolean;
  note: string | null;
}

export function parseScheduleInfo(summary: unknown): ScheduleInfo | null {
  const m = o(o(summary)?.matchup_schedule);
  if (!m) return null;
  const assumedWeeks = a(m.assumed_weeks).map(n).filter((x): x is number => x != null);
  return {
    source: s(m.source),
    assumedWeeks,
    allAssumed: m.all_assumed === true,
    note: s(m.note),
  };
}

export interface H2HCell {
  team: number;
  pWin: number | null;
  pTie: number | null;
  expCatsWon: number | null;
  oppGames: number | null;
  catWin: Record<string, number>;
}

export interface H2HWeek {
  week: number;
  start: string | null;
  end: string | null;
  scheduledOpponent: number | null;
  opponentAssumed: boolean;
  myGames: number | null;
  vs: Record<string, H2HCell>;
}

export function parseH2HMatrix(myTeam: unknown): H2HWeek[] {
  const out: H2HWeek[] = [];
  for (const r of a(o(myTeam)?.weekly_h2h_matrix)) {
    const w = o(r);
    const week = n(w?.week);
    if (!w || week == null) continue;
    const vs: Record<string, H2HCell> = {};
    for (const [k, v] of Object.entries(o(w.vs) ?? {})) {
      const c = o(v);
      const team = Number(k);
      if (!c || !Number.isFinite(team)) continue;
      vs[k] = {
        team,
        pWin: n(c.p_win),
        pTie: n(c.p_tie),
        expCatsWon: n(c.exp_cats_won),
        oppGames: n(c.opp_player_games),
        catWin: numMap(c.cat_win_probs),
      };
    }
    out.push({
      week,
      start: s(w.start),
      end: s(w.end),
      scheduledOpponent: n(w.scheduled_opponent),
      opponentAssumed: w.opponent_assumed === true,
      myGames: n(w.my_player_games),
      vs,
    });
  }
  return out.sort((x, y) => x.week - y.week);
}

export interface OppSummary {
  team: number;
  name: string | null;
  pWinAnyWeek: number | null;
  expCatsWon: number | null;
  catWinRate: Record<string, number>;
}

/** Season-average ("all-play") odds vs each opponent, toughest (lowest P(win)) first. */
export function parseVsOpponents(myTeam: unknown): OppSummary[] {
  const out: OppSummary[] = [];
  for (const [k, v] of Object.entries(o(o(myTeam)?.vs_opponents_all_play) ?? {})) {
    const c = o(v);
    const team = Number(k);
    if (!c || !Number.isFinite(team)) continue;
    out.push({
      team,
      name: s(c.name),
      pWinAnyWeek: n(c.p_win_matchup_any_week),
      expCatsWon: n(c.exp_cats_won),
      catWinRate: numMap(c.cat_win_rate),
    });
  }
  return out.sort((x, y) => (x.pWinAnyWeek ?? 2) - (y.pWinAnyWeek ?? 2) || x.team - y.team);
}

/** Opponents of one matrix week, toughest first. */
export function weekOpponents(w: H2HWeek): H2HCell[] {
  return Object.values(w.vs).sort((x, y) => (x.pWin ?? 2) - (y.pWin ?? 2) || x.team - y.team);
}
