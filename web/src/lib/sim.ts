/**
 * Defensive readers for simulation model_runs (kind `season_sim` / `draft_sim`).
 *
 * The summary JSON is produced by the Python backend (`fantasy sim season`, `fantasy draft simulate`;
 * contract in docs/research/dashboard_data.md). Everything here tolerates missing / renamed fields:
 * a reader returns null/[] and the page hides the section instead of crashing.
 */
import type { SupabaseClient } from "@supabase/supabase-js";
import type { CatKey, ModelRun } from "./types";
import { CATS } from "./categories";
import { isSeasonScenario, parseH2HMatrix, parseScheduleInfo, parseVsOpponents, type H2HWeek, type OppSummary, type ScheduleInfo } from "./postDraft";

export type Obj = Record<string, unknown>;

function obj(v: unknown): Obj | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null;
}
export function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}
export function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : typeof v === "number" ? String(v) : null;
}
/** First present value among several candidate keys (shapes evolve; accept aliases). */
function pick(o: Obj | null | undefined, ...keys: string[]): unknown {
  if (!o) return undefined;
  for (const k of keys) if (o[k] !== undefined && o[k] !== null) return o[k];
  return undefined;
}
function catMap(v: unknown): Partial<Record<CatKey, number>> {
  const o = obj(v);
  const out: Partial<Record<CatKey, number>> = {};
  if (!o) return out;
  for (const c of CATS) {
    const x = num(o[c]);
    if (x != null) out[c] = x;
  }
  return out;
}

/** Latest model run of a kind, optionally restricted to runs whose params.command matches. */
export async function latestSimRuns(db: SupabaseClient, kind: string, limit = 10): Promise<{ runs: ModelRun[]; error?: string }> {
  const { data, error } = await db
    .from("model_runs")
    .select("*")
    .eq("kind", kind)
    .order("created_at", { ascending: false })
    .limit(limit);
  return { runs: (data as ModelRun[]) ?? [], error: error?.message };
}

/** Only the small fields `runCommand` / `runScenario` read (a full season_sim row is ~75 KB of jsonb). */
const META_COLS =
  "run_id,kind,model_version,created_at,p_command:params->command,p_scenario:params->scenario,s_command:summary->command,s_scenario:summary->scenario,s_label:summary->label";

interface MetaRow {
  run_id: string;
  kind: string;
  model_version: string;
  created_at: string | null;
  p_command?: unknown;
  p_scenario?: unknown;
  s_command?: unknown;
  s_scenario?: unknown;
  s_label?: unknown;
}

/** A metadata row reshaped as a ModelRun carrying only the fields the pickers inspect. */
function metaRun(m: MetaRow): ModelRun {
  return {
    run_id: m.run_id,
    kind: m.kind,
    model_version: m.model_version,
    git_sha: null,
    inputs: null,
    created_at: m.created_at,
    params: { command: m.p_command ?? null, scenario: m.p_scenario ?? null },
    summary: { command: m.s_command ?? null, scenario: m.s_scenario ?? null, label: m.s_label ?? null },
  } as ModelRun;
}

export interface SimPicks {
  /** newest run of the kind (full row), if requested */
  latest: ModelRun | null;
  /** pickSeasonRun over the newest `limit` runs (full row), if requested */
  season: ModelRun | null;
  /** pickPreseasonRun over the newest `limit` runs (full row), if requested */
  preseason: ModelRun | null;
  /** model_version of the newest run (from metadata) */
  latestVersion: string | null;
  error?: string;
}

/**
 * Same selection as `latestSimRuns(db, kind, limit)` + pickSeasonRun / pickPreseasonRun / runs[0], but in
 * two steps: a live metadata query (a few KB, decides which runs are newest) and then only the chosen
 * full rows by run_id via `rows` (may be a cached client: a run row is immutable for its id).
 */
export async function loadSimPicks(
  db: SupabaseClient,
  rows: SupabaseClient,
  kind: string,
  limit: number,
  want: { latest?: boolean; season?: boolean; preseason?: boolean },
): Promise<SimPicks> {
  const out: SimPicks = { latest: null, season: null, preseason: null, latestVersion: null };
  const { data, error } = await db
    .from("model_runs")
    .select(META_COLS)
    .eq("kind", kind)
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) return { ...out, error: error.message };
  const meta = ((data as unknown as MetaRow[]) ?? []).map(metaRun);
  out.latestVersion = meta[0]?.model_version ?? null;
  const full = async (ids: string[]): Promise<ModelRun[]> => {
    if (!ids.length) return [];
    const r = await rows.from("model_runs").select("*").in("run_id", ids);
    if (r.error) throw new Error(r.error.message);
    const byId = new Map(((r.data as ModelRun[]) ?? []).map((x) => [x.run_id, x]));
    return ids.map((id) => byId.get(id)).filter((x): x is ModelRun => !!x); // keep newest-first order
  };
  // season: metadata decides command/scenario; summary.my_team is only checkable on the full row, so walk
  // the candidates newest-first in small batches (normally the first one matches).
  const seasonCands = want.season
    ? meta.filter((r) => runCommand(r) === "season" && isSeasonScenario(runScenario(r))).map((r) => r.run_id)
    : [];
  const preId = want.preseason ? (pickPreseasonRun(meta)?.run_id ?? null) : null;
  const latestId = want.latest ? (meta[0]?.run_id ?? null) : null;
  try {
    const first = [...new Set([...seasonCands.slice(0, 2), ...(preId ? [preId] : []), ...(latestId ? [latestId] : [])])];
    const got = new Map((await full(first)).map((r) => [r.run_id, r]));
    out.preseason = preId ? (got.get(preId) ?? null) : null;
    out.latest = latestId ? (got.get(latestId) ?? null) : null;
    if (want.season) {
      out.season = pickSeasonRun(seasonCands.slice(0, 2).map((id) => got.get(id)).filter((x): x is ModelRun => !!x));
      for (let i = 2; !out.season && i < seasonCands.length; i += 4) out.season = pickSeasonRun(await full(seasonCands.slice(i, i + 4)));
    }
  } catch (e) {
    out.error = String(e instanceof Error ? e.message : e);
  }
  return out;
}

function runCommand(r: ModelRun): string | null {
  return str(obj(r.params)?.command) ?? str(obj(r.summary)?.command) ?? null;
}

function runScenario(r: ModelRun): string | null {
  const s = obj(r.summary);
  return str(pick(s, "scenario", "label")) ?? str(obj(r.params)?.scenario) ?? null;
}

const PRESEASON = "pre-draft projection";

/** Newest pre-draft projection (`fantasy sim preseason`). */
export function pickPreseasonRun(runs: ModelRun[]): ModelRun | null {
  return runs.find((r) => runScenario(r) === PRESEASON || runCommand(r) === "preseason") ?? null;
}

/** Newest whole-season simulation from actual (draft / Yahoo) rosters (`fantasy sim season`; no scenario, or
 * scenario "post-draft: actual rosters"). */
export function pickSeasonRun(runs: ModelRun[]): ModelRun | null {
  return (
    runs.find((r) => {
      const cmd = runCommand(r);
      const s = obj(r.summary);
      return cmd === "season" && isSeasonScenario(runScenario(r)) && obj(s?.my_team) != null;
    }) ?? null
  );
}

/* ------------------------------------------------------------------ season sim */

export interface WeekMatchup {
  week: number;
  start: string | null;
  end: string | null;
  opponent: number | null;
  opponentName: string | null;
  pWin: number | null;
  pTie: number | null;
  pLoss: number | null;
  expCatsWon: number | null;
  myGames: number | null;
  oppGames: number | null;
  catWin: Partial<Record<CatKey, number>>;
  /** optional richer per-category block (matchup command shape) */
  cats: Partial<Record<CatKey, { pWin: number | null; me: number | null; opp: number | null }>>;
  /** the opponent comes from an assumed round robin, not Yahoo's schedule */
  opponentAssumed: boolean;
  raw: Obj;
}

function parseWeek(v: unknown): WeekMatchup | null {
  const o = obj(v);
  if (!o) return null;
  const week = num(o.week);
  if (week == null) return null;
  const cats: WeekMatchup["cats"] = {};
  const cb = obj(o.categories);
  if (cb)
    for (const c of CATS) {
      const x = obj(cb[c]);
      if (x) cats[c] = { pWin: num(x.p_win), me: num(pick(x, "me_mean", "me")), opp: num(pick(x, "opp_mean", "opp")) };
    }
  const catWin = catMap(o.cat_win_probs);
  if (!Object.keys(catWin).length) for (const c of CATS) if (cats[c]?.pWin != null) catWin[c] = cats[c]!.pWin!;
  return {
    week,
    start: str(o.start),
    end: str(o.end),
    opponent: num(o.opponent),
    opponentName: str(o.opponent_name),
    pWin: num(o.p_win),
    pTie: num(o.p_tie),
    pLoss: num(o.p_loss),
    expCatsWon: num(o.exp_cats_won),
    myGames: num(o.my_player_games),
    oppGames: num(o.opp_player_games),
    catWin,
    cats,
    opponentAssumed: o.opponent_assumed === true,
    raw: o,
  };
}

export interface TeamRow {
  team: number;
  name: string;
  pPlayoffs: number | null;
  pPlayoffsSe: number | null;
  pBye: number | null;
  pFinal: number | null;
  pTitle: number | null;
  pTitleSe: number | null;
  expCatWins: number | null;
  expCatLosses: number | null;
  expRank: number | null;
  projectedPlayers: number | null;
  players: number | null;
  isMe: boolean;
}

function parseLeagueTable(v: unknown): TeamRow[] {
  const out: TeamRow[] = [];
  for (const r of arr(v)) {
    const o = obj(r);
    const team = num(o?.team);
    if (!o || team == null) continue;
    out.push({
      team,
      name: str(o.name) ?? `Team ${team}`,
      pPlayoffs: num(o.p_playoffs),
      pPlayoffsSe: num(o.p_playoffs_se),
      pBye: num(o.p_bye),
      pFinal: num(o.p_final),
      pTitle: num(o.p_title),
      pTitleSe: num(o.p_title_se),
      expCatWins: num(o.exp_cat_wins),
      expCatLosses: num(o.exp_cat_losses),
      expRank: num(o.exp_rank),
      projectedPlayers: num(o.projected_players),
      players: num(o.players),
      isMe: o.is_me === true,
    });
  }
  return out;
}

export interface TeamProfile {
  team: number;
  name: string;
  weeklyMean: Partial<Record<CatKey, number>>;
  rank: Partial<Record<CatKey, number>>;
  z: Partial<Record<CatKey, number>>;
  strengths: string[];
  weaknesses: string[];
  gamesPerWeek: number | null;
}

function parseProfiles(v: unknown): Record<string, TeamProfile> {
  const o = obj(v);
  const out: Record<string, TeamProfile> = {};
  if (!o) return out;
  for (const [k, p] of Object.entries(o)) {
    const po = obj(p);
    const team = num(k);
    if (!po || team == null) continue;
    out[k] = {
      team,
      name: str(po.name) ?? `Team ${team}`,
      weeklyMean: catMap(po.weekly_mean),
      rank: catMap(po.rank),
      z: catMap(po.z),
      strengths: arr(po.strengths).map(String),
      weaknesses: arr(po.weaknesses).map(String),
      gamesPerWeek: num(po.mean_player_games_per_week),
    };
  }
  return out;
}

export interface SeasonSim {
  run: ModelRun;
  summary: Obj;
  scenario: string | null;
  nSims: number | null;
  draftId: string | null;
  myTeam: Obj | null;
  myTeamId: number | null;
  table: TeamRow[];
  profiles: Record<string, TeamProfile>;
  weeks: WeekMatchup[];
  standingDist: { rank: number; p: number }[];
  rosters: Record<string, { players: string[]; projected: string[] }>;
  warnings: string[];
  explanations: Obj | null;
  isPreseason: boolean;
  nLeagues: number | null;
  acrossLeagues: Obj | null;
  categoryProfile: Partial<Record<CatKey, { rank: number | null; z: number | null }>>;
  leagues: { league: number; pPlayoffs: number | null; pTitle: number | null; expRank: number | null; roster: { id: string; name: string }[] }[];
  isPostDraft: boolean;
  schedule: ScheduleInfo | null;
  h2h: H2HWeek[];
  vsOpponents: OppSummary[];
}

function parseRosters(inputs: Obj | null, summary: Obj): SeasonSim["rosters"] {
  const out: SeasonSim["rosters"] = {};
  const src = obj(pick(summary, "rosters")) ?? obj(inputs?.rosters);
  if (!src) return out;
  for (const [k, v] of Object.entries(src)) {
    const o = obj(v);
    if (Array.isArray(v)) out[k] = { players: v.map(String), projected: [] };
    else if (o)
      out[k] = {
        players: arr(pick(o, "players", "roster")).map((x) => str(obj(x)?.player_id) ?? String(x)),
        projected: arr(pick(o, "projected_players", "projected")).map((x) => str(obj(x)?.player_id) ?? String(x)),
      };
  }
  return out;
}

export function parseSeasonSim(run: ModelRun | null): SeasonSim | null {
  if (!run) return null;
  const s = obj(run.summary);
  if (!s) return null;
  const my = obj(s.my_team) ?? obj(s.my_outlook);
  const dist = obj(my?.final_standing_dist);
  const standingDist = dist
    ? Object.entries(dist)
        .map(([k, v]) => ({ rank: Number(k), p: num(v) ?? 0 }))
        .filter((x) => Number.isFinite(x.rank))
        .sort((a, b) => a.rank - b.rank)
    : [];
  const inputs = obj(run.inputs);
  return {
    run,
    summary: s,
    scenario: runScenario(run),
    nSims: num(pick(s, "n_sims")) ?? num(obj(run.params)?.n_sims),
    draftId: str(pick(s, "draft_id")) ?? str(inputs?.draft_id),
    myTeam: my,
    myTeamId: num(my?.team) ?? num(s.my_team),
    table: parseLeagueTable(s.league_table),
    profiles: parseProfiles(s.team_profiles),
    weeks: arr(my?.weekly_matchups).map(parseWeek).filter((w): w is WeekMatchup => !!w),
    standingDist,
    rosters: parseRosters(inputs, s),
    warnings: arr(s.warnings).map(String),
    explanations: obj(s.explanations),
    isPreseason: runScenario(run) === PRESEASON || runCommand(run) === "preseason",
    isPostDraft: (runScenario(run) ?? "").startsWith("post-draft"),
    schedule: parseScheduleInfo(s),
    h2h: parseH2HMatrix(my),
    vsOpponents: parseVsOpponents(my),
    nLeagues: num(s.n_leagues),
    acrossLeagues: obj(my?.across_leagues),
    categoryProfile: parseCatProfile(my?.category_profile),
    leagues: arr(s.leagues)
      .map((l, i) => {
        const o = obj(l);
        if (!o) return null;
        return {
          league: num(o.league) ?? i,
          pPlayoffs: num(o.my_p_playoffs),
          pTitle: num(o.my_p_title),
          expRank: num(o.my_exp_rank),
          roster: parsePlayerRefs(o.my_roster),
        };
      })
      .filter((x): x is NonNullable<typeof x> => !!x),
  };
}

function parseCatProfile(v: unknown): SeasonSim["categoryProfile"] {
  const o = obj(v);
  const out: SeasonSim["categoryProfile"] = {};
  if (!o) return out;
  for (const c of CATS) {
    const x = obj(o[c]);
    if (x) out[c] = { rank: num(pick(x, "rank_mean", "rank")), z: num(pick(x, "z_mean", "z")) };
  }
  return out;
}

/** [{player_id, name}] or ["slug", ...] → [{id, name}] */
function parsePlayerRefs(v: unknown): { id: string; name: string }[] {
  return arr(v)
    .map((x) => {
      const o = obj(x);
      if (o) {
        const id = str(o.player_id) ?? str(o.name);
        return id ? { id, name: str(o.name) ?? id } : null;
      }
      const id = str(x);
      return id ? { id, name: id } : null;
    })
    .filter((x): x is { id: string; name: string } => !!x);
}

/** The fantasy week containing `date` (YYYY-MM-DD), else the next upcoming week, else null. */
export function currentWeek(weeks: WeekMatchup[], today: string): WeekMatchup | null {
  const sorted = [...weeks].sort((a, b) => a.week - b.week);
  for (const w of sorted) if (w.start && w.end && w.start <= today && today <= w.end) return w;
  return sorted.find((w) => w.start && w.start > today) ?? null;
}

export function todayET(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

export function explanationText(e: Obj | null, ...keys: string[]): string | null {
  if (!e) return null;
  for (const k of keys) {
    const v = e[k];
    if (typeof v === "string" && v) return v;
  }
  return null;
}

/* ------------------------------------------------------------------ draft sim */

export interface CatBand {
  label: string | null;
  mean: number | null;
  p10: number | null;
  p90: number | null;
}

export interface DraftSim {
  run: ModelRun;
  summary: Obj;
  nSims: number | null;
  mySlot: number | null;
  myPicks: number[];
  optimizerVersion: string | null;
  opt: { ecw: number | null; ecwSd: number | null; mwp: number | null };
  bpa: { ecw: number | null; ecwSd: number | null; mwp: number | null };
  edgeEcw: { mean: number | null; se: number | null; pBetter: number | null };
  edgeMwp: { mean: number | null; se: number | null; pBetter: number | null };
  strategyCounts: { name: string; count: number }[];
  picksDist: { pick: number; round: number | null; nDistinct: number | null; top: { id: string; name: string; freq: number; count: number | null }[] }[];
  availPicks: number[];
  availPlayers: {
    id: string;
    name: string;
    team: string | null;
    positions: string[];
    adp: number | null;
    marketRank: number | null;
    value: number | null;
    valueRank: number | null;
    p: (number | null)[];
    takenByMe: number | null;
  }[];
  catOpt: Partial<Record<CatKey, CatBand>>;
  catBpa: Partial<Record<CatKey, CatBand>>;
  examples: {
    label: string;
    ecw: number | null;
    mwp: number | null;
    strategy: string | null;
    catWin: Partial<Record<CatKey, number>>;
    picks: { pick: number | null; round: number | null; id: string; name: string; positions: string[] }[];
  }[];
  explanations: Obj | null;
  warnings: string[];
}

function catBands(v: unknown): Partial<Record<CatKey, CatBand>> {
  const o = obj(v);
  const out: Partial<Record<CatKey, CatBand>> = {};
  if (!o) return out;
  for (const c of CATS) {
    const x = obj(o[c]);
    if (x) out[c] = { label: str(x.label), mean: num(x.mean), p10: num(x.p10), p90: num(x.p90) };
    else if (num(o[c]) != null) out[c] = { label: null, mean: num(o[c]), p10: null, p90: null };
  }
  return out;
}

function edge(v: unknown, flatMean?: unknown, flatSe?: unknown) {
  const o = obj(v);
  return {
    mean: num(o?.mean) ?? num(flatMean),
    se: num(o?.se) ?? num(flatSe),
    pBetter: num(o?.p_optimizer_better),
  };
}

export function parseDraftSim(run: ModelRun | null): DraftSim | null {
  if (!run) return null;
  const s = obj(run.summary);
  if (!s) return null;
  const opt = obj(s.optimizer);
  const bpa = obj(s.baseline_bpa);
  const e = obj(s.edge);
  const av = obj(s.availability);
  const cwp = obj(s.category_win_probs);
  const sc = obj(s.strategy_counts);
  return {
    run,
    summary: s,
    nSims: num(s.n_sims) ?? num(obj(run.params)?.n_sims),
    mySlot: num(s.my_slot),
    myPicks: arr(s.my_picks).map(num).filter((x): x is number => x != null),
    optimizerVersion: str(s.optimizer_version) ?? run.model_version,
    opt: { ecw: num(opt?.expected_category_wins_mean), ecwSd: num(opt?.expected_category_wins_sd), mwp: num(opt?.matchup_win_prob_mean) },
    bpa: { ecw: num(bpa?.expected_category_wins_mean), ecwSd: num(bpa?.expected_category_wins_sd), mwp: num(bpa?.matchup_win_prob_mean) },
    edgeEcw: edge(e?.expected_category_wins, s.edge_category_wins, s.edge_category_wins_se),
    edgeMwp: edge(e?.matchup_win_prob),
    strategyCounts: sc
      ? Object.entries(sc)
          .map(([name, c]) => ({ name, count: num(c) ?? 0 }))
          .sort((a, b) => b.count - a.count)
      : [],
    picksDist: arr(s.picks_distribution)
      .map((x) => {
        const o = obj(x);
        const p = num(o?.pick);
        if (!o || p == null) return null;
        return {
          pick: p,
          round: num(o.round),
          nDistinct: num(o.n_distinct),
          top: arr(o.top)
            .map((t) => {
              const to = obj(t);
              const id = str(to?.player_id) ?? str(to?.name);
              if (!to || !id) return null;
              return { id, name: str(to.name) ?? id, freq: num(to.freq) ?? 0, count: num(to.count) };
            })
            .filter((t): t is NonNullable<typeof t> => !!t),
        };
      })
      .filter((x): x is NonNullable<typeof x> => !!x),
    availPicks: arr(av?.picks).map(num).filter((x): x is number => x != null),
    availPlayers: arr(av?.players)
      .map((x) => {
        const o = obj(x);
        const id = str(o?.player_id) ?? str(o?.name);
        if (!o || !id) return null;
        return {
          id,
          name: str(o.name) ?? id,
          team: str(o.team),
          positions: arr(o.positions).map(String),
          adp: num(o.adp),
          marketRank: num(o.market_rank),
          value: num(o.value),
          valueRank: num(o.value_rank),
          p: arr(o.p_available).map(num),
          takenByMe: num(o.sim_taken_by_me_freq),
        };
      })
      .filter((x): x is NonNullable<typeof x> => !!x),
    catOpt: catBands(cwp?.optimizer),
    catBpa: catBands(cwp?.baseline_bpa),
    examples: arr(s.example_rosters)
      .map((x, i) => {
        const o = obj(x);
        if (!o) return null;
        return {
          label: str(o.label) ?? `roster ${i + 1}`,
          ecw: num(o.expected_category_wins),
          mwp: num(o.matchup_win_prob),
          strategy: str(o.strategy),
          catWin: catMap(o.cat_win_probs),
          picks: arr(o.picks)
            .map((p) => {
              const po = obj(p);
              if (po) {
                const id = str(po.player_id) ?? str(po.name);
                return id ? { pick: num(po.pick), round: num(po.round), id, name: str(po.name) ?? id, positions: arr(po.positions).map(String) } : null;
              }
              const n = str(p);
              return n ? { pick: null, round: null, id: n, name: n, positions: [] } : null;
            })
            .filter((p): p is NonNullable<typeof p> => !!p),
        };
      })
      .filter((x): x is NonNullable<typeof x> => !!x),
    explanations: obj(s.explanations),
    warnings: arr(s.warnings).map(String),
  };
}
