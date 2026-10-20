/**
 * Lineup view readers and grid assembly (pure, no runtime imports: unit-tested by `npm test`).
 *
 * Producers (docs/research/dashboard_data.md section 6), both immutable `model_runs` rows:
 * - `kind = 'lineup_plan'` (schema `lineup_plan/1`): `fantasy lineup plan --save` — the optimizer's slots for
 *   `summary.date` plus `week_plan` for the rest of the week, decide-at-tip-off items, IL moves, expected cats.
 * - `kind = 'lineup_actual'` (schema `lineup_actual/1`): `fantasy lineup record`, `fantasy league sync --write
 *   --record-lineup`, `fantasy lineup backfill` — what Yahoo showed for my team on `summary.date`.
 * Per day the grid shows the actual lineup when one was recorded (✓, or ⚠ where it differs from the plan),
 * otherwise the plan (◌).
 */

type Obj = Record<string, unknown>;

const o = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);
const a = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const s = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const b = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

/** Grid rows, in Yahoo's order (league 44495: 10 starters, 3 bench, 3 IL). */
export const GRID_SLOTS = ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util", "BN", "BN", "BN", "IL", "IL", "IL"] as const;
export const BENCH = new Set(["BN", "IL", "IL+"]);
const OUT_TAGS = new Set(["O", "OUT", "INJ", "OFS", "NA", "SUSP", "IR"]);

export interface RunRow {
  run_id: string;
  kind: string;
  created_at: string | null;
  summary: unknown;
}

export interface PlayerInfo {
  name: string;
  positions: string[];
  nbaTeam: string | null;
  status: string | null;
}

export interface SlotEntry {
  slot: string;
  playerId: string | null;
  name: string | null;
  game: string | null;
  hasGame: boolean | null;
  pPlay: number | null;
  status: string | null;
}

export interface TipoffItem {
  playerId: string;
  name: string;
  status: string | null;
  pPlay: number | null;
  slot: string | null;
  swapWith: string | null;
  swapWithName: string | null;
  note: string | null;
  deadline: string | null;
  game: string | null;
}

export interface IlMove {
  playerId: string;
  name: string;
  status: string | null;
  from: string | null;
  to: string;
}

export interface Expected {
  method: string | null;
  availability: string | null;
  evidence: string | null;
  optimized: { cats: number | null; pWin: number | null } | null;
  staticCurrent: { cats: number | null; pWin: number | null } | null;
  mc: { cats: number | null; catsSe: number | null; pWin: number | null; pWinSe: number | null; nSims: number | null } | null;
  /** chance to win each category by Sunday given the banked stats (newer plans; absent in older rows) */
  catWinProb: Record<string, number> | null;
}

export interface LineupPlan {
  runId: string;
  createdAt: string | null;
  date: string;
  week: number | null;
  opponent: { teamId: number | null; name: string | null } | null;
  players: Record<string, PlayerInfo>;
  /** slots per day: the plan's own date and (week_plan) the rest of the week */
  days: Record<string, SlotEntry[]>;
  games: Record<string, Record<string, string>>;
  tipoff: TipoffItem[];
  ilMoves: IlMove[];
  benchWithGame: { playerId: string; name: string; game: string | null }[];
  expected: Expected | null;
  warnings: string[];
  modelVersion: string | null;
}

export interface LineupActual {
  runId: string;
  createdAt: string | null;
  date: string;
  /** league team (draft slot) the lineup belongs to; null in rows that predate the field (= my team) */
  team: number | null;
  source: string;
  observedAt: string | null;
  how: string | null;
  txnId: string | null;
  complete: boolean | null;
  /** the row carries game markers for every player with a game that day (absent marker = no game) */
  gamesKnown: boolean;
  slots: SlotEntry[];
}

function slotEntry(v: unknown, players: Record<string, PlayerInfo>, games: Record<string, Record<string, string>>, day: string): SlotEntry | null {
  const e = o(v);
  const slot = s(e?.slot);
  if (!e || !slot) return null;
  const pid = s(e.player_id);
  const info = pid ? players[pid] : undefined;
  const game = s(e.game) ?? (pid ? (games[pid]?.[day] ?? null) : null);
  return {
    slot: slot === "IL+" ? "IL" : slot,
    playerId: pid,
    name: s(e.name) ?? info?.name ?? pid,
    game,
    hasGame: b(e.has_game) ?? (game ? true : null),
    pPlay: n(e.p_play),
    status: s(e.status) ?? info?.status ?? null,
  };
}

function parseGames(v: unknown): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {};
  for (const [pid, m] of Object.entries(o(v) ?? {})) {
    const days: Record<string, string> = {};
    for (const [d, g] of Object.entries(o(m) ?? {})) if (s(g)) days[d] = g as string;
    out[pid] = days;
  }
  return out;
}

function pair(v: unknown): { cats: number | null; pWin: number | null } | null {
  const x = o(v);
  return x ? { cats: n(x.expected_cats), pWin: n(x.p_win_matchup) } : null;
}

function probMap(v: unknown): Record<string, number> | null {
  const out: Record<string, number> = {};
  for (const [k, x] of Object.entries(o(v) ?? {})) {
    const p = n(x);
    if (p != null && p >= 0 && p <= 1) out[k] = p;
  }
  return Object.keys(out).length ? out : null;
}

export function parsePlan(row: RunRow): LineupPlan | null {
  const sm = o(row.summary);
  const date = s(sm?.date);
  if (!sm || row.kind !== "lineup_plan" || !date) return null;
  const players: Record<string, PlayerInfo> = {};
  for (const [pid, p] of Object.entries(o(sm.players) ?? {})) {
    const x = o(p);
    if (!x) continue;
    players[pid] = {
      name: s(x.name) ?? pid,
      positions: a(x.positions).filter((q): q is string => typeof q === "string"),
      nbaTeam: s(x.nba_team),
      status: s(x.status),
    };
  }
  const games = parseGames(sm.games);
  const days: Record<string, SlotEntry[]> = {};
  for (const d of a(sm.week_plan)) {
    const dd = o(d);
    const day = s(dd?.date);
    if (!dd || !day) continue;
    days[day] = a(dd.slots).map((e) => slotEntry(e, players, games, day)).filter((e): e is SlotEntry => e != null);
  }
  const today = a(sm.slots).map((e) => slotEntry(e, players, games, date)).filter((e): e is SlotEntry => e != null);
  if (today.length) days[date] = today;
  const ex = o(sm.expected);
  const mcOpt = o(o(o(ex?.monte_carlo)?.plans)?.optimized);
  const expected: Expected | null = ex
    ? {
        method: s(ex.method),
        availability: s(ex.availability_assumption),
        evidence: s(ex.evidence),
        optimized: pair(ex.optimized),
        staticCurrent: pair(ex.static_current_lineup),
        mc: mcOpt
          ? {
              cats: n(mcOpt.expected_cats),
              catsSe: n(mcOpt.expected_cats_se),
              pWin: n(mcOpt.p_win_matchup),
              pWinSe: n(mcOpt.p_win_matchup_se),
              nSims: n(o(ex.monte_carlo)?.n_sims),
            }
          : null,
        catWinProb: probMap(ex.cat_win_prob),
      }
    : null;
  const opp = o(sm.opponent);
  return {
    runId: row.run_id,
    createdAt: row.created_at,
    date,
    week: n(sm.week),
    opponent: opp ? { teamId: n(opp.team_id), name: s(opp.name) } : null,
    players,
    days,
    games,
    tipoff: a(sm.decide_at_tipoff)
      .map((v) => o(v))
      .filter((x): x is Obj => !!x && !!s(x.player_id))
      .map((x) => ({
        playerId: x.player_id as string,
        name: s(x.name) ?? (x.player_id as string),
        status: s(x.status),
        pPlay: n(x.p_play),
        slot: s(x.slot),
        swapWith: s(x.swap_with),
        swapWithName: s(x.swap_with_name),
        note: s(x.note),
        deadline: s(x.deadline),
        game: s(x.game),
      })),
    ilMoves: a(sm.il_moves)
      .map((v) => o(v))
      .filter((x): x is Obj => !!x && !!s(x.player_id))
      .map((x) => ({ playerId: x.player_id as string, name: s(x.name) ?? (x.player_id as string), status: s(x.status), from: s(x.from), to: s(x.to) ?? "IL" })),
    benchWithGame: a(sm.bench_with_game)
      .map((v) => o(v))
      .filter((x): x is Obj => !!x && !!s(x.player_id))
      .map((x) => ({ playerId: x.player_id as string, name: s(x.name) ?? (x.player_id as string), game: s(x.game) })),
    expected,
    warnings: a(sm.warnings).filter((w): w is string => typeof w === "string"),
    modelVersion: s(sm.model_version),
  };
}

export function parseActual(row: RunRow): LineupActual | null {
  const sm = o(row.summary);
  const date = s(sm?.date);
  if (!sm || row.kind !== "lineup_actual" || !date) return null;
  const slots = a(sm.slots).map((e) => slotEntry(e, {}, {}, date)).filter((e): e is SlotEntry => e != null);
  if (!slots.length) return null;
  return {
    runId: row.run_id,
    createdAt: row.created_at,
    date,
    team: n(sm.team),
    source: s(sm.source) ?? "unknown",
    observedAt: s(sm.observed_at),
    how: s(sm.how),
    txnId: s(sm.txn_id),
    complete: b(sm.complete),
    gamesKnown: sm.games_known === true,
    slots,
  };
}

const newestFirst = <T extends { createdAt: string | null }>(xs: T[]) => [...xs].sort((x, y) => (y.createdAt ?? "").localeCompare(x.createdAt ?? ""));

/** The plan that covers `day`: the newest plan made for that date, else the newest earlier plan whose week plan includes it. */
export function planForDay(plans: LineupPlan[], day: string): { plan: LineupPlan; slots: SlotEntry[]; sameDay: boolean } | null {
  const sorted = newestFirst(plans);
  const own = sorted.find((p) => p.date === day && p.days[day]?.length);
  if (own) return { plan: own, slots: own.days[day], sameDay: true };
  const earlier = sorted
    .filter((p) => p.date < day && p.days[day]?.length)
    .sort((x, y) => y.date.localeCompare(x.date) || (y.createdAt ?? "").localeCompare(x.createdAt ?? ""))[0];
  return earlier ? { plan: earlier, slots: earlier.days[day], sameDay: false } : null;
}

/** The newest actual (Yahoo) lineup recorded for `day`; on equal times a verified re-read beats a capture. */
export function actualForDay(actuals: LineupActual[], day: string): LineupActual | null {
  const rank = (x: LineupActual) => (x.source === "yahoo_verified" ? 1 : 0);
  return (
    actuals
      .filter((x) => x.date === day)
      .sort((x, y) => (y.createdAt ?? "").localeCompare(x.createdAt ?? "") || rank(y) - rank(x))[0] ?? null
  );
}

export type CellState = "verified" | "planned" | "mismatch" | "empty";

export interface GridCell {
  playerId: string | null;
  name: string;
  short: string;
  game: string | null;
  hasGame: boolean | null;
  status: string | null;
  pPlay: number | null;
}

export interface GridRow {
  slot: string;
  planned: GridCell | null;
  actual: GridCell | null;
  /** what the cell shows: the actual player when Yahoo was read that day, else the planned one */
  shown: GridCell | null;
  state: CellState;
}

export interface DaySummary {
  /** starters with a game (in a starting slot) */
  started: number;
  /** players with a game who could start (not tagged out), capped by the number of starting slots */
  available: number;
  /** available - started: starts left on the bench */
  unused: number;
  /** false when some shown player's game is unknown (no schedule in the rows) */
  known: boolean;
}

export interface DayGrid {
  date: string;
  rows: GridRow[];
  plan: LineupPlan | null;
  /** the plan was made on this date (else it is an earlier plan's week-plan entry) */
  planSameDay: boolean;
  actual: LineupActual | null;
  mismatches: number;
  summary: DaySummary;
}

/** "Nikola Jokić" -> "N. Jokić"; "Jaren Jackson Jr." -> "J. Jackson Jr."; single names unchanged. */
export function shortName(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length < 2) return name.trim();
  const suffix = /^(jr\.?|sr\.?|ii|iii|iv)$/i.test(parts[parts.length - 1]) ? ` ${parts.pop()}` : "";
  if (parts.length < 2) return name.trim();
  return `${parts[0][0]}. ${parts.slice(1).join(" ")}${suffix}`;
}

function cell(e: SlotEntry, day: string, games: Record<string, Record<string, string>>, players: Record<string, PlayerInfo>, dayKnown: boolean): GridCell {
  const info = e.playerId ? players[e.playerId] : undefined;
  const name = e.name ?? info?.name ?? e.playerId ?? "?";
  const game = e.game ?? (e.playerId ? (games[e.playerId]?.[day] ?? null) : null);
  const scheduleKnown = dayKnown && e.playerId != null && games[e.playerId] !== undefined;
  return {
    playerId: e.playerId,
    name,
    short: shortName(name),
    game,
    hasGame: game ? true : (e.hasGame ?? (scheduleKnown ? false : null)),
    status: e.status ?? info?.status ?? null,
    pPlay: e.pPlay,
  };
}

const key = (c: GridCell | null) => (c ? (c.playerId ?? `name:${c.name}`) : null);

function rowCounts(...lists: string[][]): Map<string, number> {
  const m = new Map<string, number>();
  for (const l of GRID_SLOTS) m.set(l, (m.get(l) ?? 0) + 1);
  // a slot the default layout lacks (other league settings) or more players than rows: add rows
  for (const labels of lists) {
    const seen = new Map<string, number>();
    for (const l of labels) seen.set(l, (seen.get(l) ?? 0) + 1);
    for (const [l, c] of seen) if ((m.get(l) ?? 0) < c) m.set(l, c);
  }
  return m;
}

/**
 * Merge the plan and the actual lineup for one day into grid rows. Within a slot label (two C, two Util, three
 * BN/IL rows) players in both lists share a row; the rest pair up in order. A row is
 * - `verified` when Yahoo shows the planned player (or there is no plan and Yahoo was read),
 * - `mismatch` when Yahoo was read and someone starts/sits differently from the plan (a slot swap between two
 *   starters, or two bench players, is not a mismatch),
 * - `planned` when Yahoo was not read for that day (or a partial read did not include this player),
 * - `empty` when nobody is in it.
 */
export function buildDay(
  day: string,
  planned: SlotEntry[] | null,
  actual: LineupActual | null,
  games: Record<string, Record<string, string>> = {},
  players: Record<string, PlayerInfo> = {},
  scheduleKnown = true,
): Omit<DayGrid, "plan" | "planSameDay" | "actual"> {
  // a missing game marker means "no game" only when that day's schedule is in the rows (a plan covering it,
  // or a Yahoo read recorded with markers); otherwise the game is unknown
  const P = (planned ?? []).map((e) => ({ slot: e.slot, c: cell(e, day, games, players, scheduleKnown) }));
  let A = actual
    ? actual.slots.map((e) => ({ slot: e.slot, c: cell(e, day, games, players, scheduleKnown || actual.gamesKnown), inferred: false }))
    : null;
  if (A && actual?.complete === false) {
    // partial read: players Yahoo did not report keep their planned slot, shown as planned
    const seen = new Set(A.map((x) => key(x.c)));
    A = [...A, ...P.filter((x) => !seen.has(key(x.c))).map((x) => ({ ...x, inferred: true }))];
  }
  const labels = [...P.map((x) => x.slot), ...(A ?? []).map((x) => x.slot)];
  const counts = rowCounts(
    P.map((x) => x.slot),
    (A ?? []).map((x) => x.slot),
  );
  // role = starting vs BN/IL: a swap between two starting slots (e.g. Fears PG <-> Sheppard Util) is not a mismatch
  const roleP = new Map(P.map((x) => [key(x.c), !BENCH.has(x.slot)]));
  const roleA = new Map((A ?? []).filter((x) => !x.inferred).map((x) => [key(x.c), !BENCH.has(x.slot)]));
  const sameRole = (c: GridCell | null) => c == null || (roleP.has(key(c)) && roleA.has(key(c)) && roleP.get(key(c)) === roleA.get(key(c)));
  const order = [...new Set([...GRID_SLOTS, ...labels])];
  const rows: GridRow[] = [];
  for (const label of order) {
    const k = counts.get(label) ?? 0;
    const pl = P.filter((x) => x.slot === label);
    const al = (A ?? []).filter((x) => x.slot === label);
    const alKeys = new Set(al.map((x) => key(x.c)));
    const both = pl.filter((x) => alKeys.has(key(x.c)));
    const bothKeys = new Set(both.map((x) => key(x.c)));
    const restP = pl.filter((x) => !bothKeys.has(key(x.c)));
    const restA = al.filter((x) => !bothKeys.has(key(x.c)));
    const pairs: { p: GridCell | null; a: (typeof al)[number] | null }[] = both.map((x) => ({
      p: x.c,
      a: al.find((y) => key(y.c) === key(x.c)) ?? null,
    }));
    for (let i = 0; i < Math.max(restP.length, restA.length); i++) pairs.push({ p: restP[i]?.c ?? null, a: restA[i] ?? null });
    while (pairs.length < k) pairs.push({ p: null, a: null });
    for (const { p, a: act } of pairs) {
      const ac = act && !act.inferred ? act.c : null;
      let state: CellState;
      if (!A) state = p ? "planned" : "empty";
      else if (act?.inferred) state = "planned";
      else if (!p && !ac) state = "empty";
      else if (!planned) state = "verified";
      else state = key(p) === key(ac) || (sameRole(p) && sameRole(ac)) ? "verified" : "mismatch";
      rows.push({ slot: label, planned: p, actual: ac, shown: A ? (act ? act.c : null) : p, state });
    }
  }
  return { date: day, rows, mismatches: rows.filter((r) => r.state === "mismatch").length, summary: summarize(rows) };
}

export function summarize(rows: GridRow[]): DaySummary {
  const starting = rows.filter((r) => !BENCH.has(r.slot));
  const shown = rows.map((r) => r.shown).filter((c): c is GridCell => c != null);
  const known = shown.every((c) => c.hasGame != null);
  const started = starting.filter((r) => r.shown?.hasGame).length;
  const canPlay = shown.filter((c) => c.hasGame && !(c.status && OUT_TAGS.has(c.status.toUpperCase()))).length;
  const available = Math.min(canPlay, starting.length);
  return { started, available, unused: Math.max(available - started, 0), known };
}

/** Days from `start` to `end` inclusive (YYYY-MM-DD, calendar arithmetic in UTC). */
export function daysBetween(start: string, end: string): string[] {
  const out: string[] = [];
  const t = new Date(`${start}T00:00:00Z`);
  const stop = new Date(`${end}T00:00:00Z`);
  if (Number.isNaN(t.getTime()) || Number.isNaN(stop.getTime())) return out;
  for (let i = 0; t <= stop && i < 31; i++) {
    out.push(t.toISOString().slice(0, 10));
    t.setUTCDate(t.getUTCDate() + 1);
  }
  return out;
}

/** Every day of the week: plan vs actual. */
export function buildWeek(days: string[], plans: LineupPlan[], actuals: LineupActual[]): DayGrid[] {
  const games: Record<string, Record<string, string>> = {};
  const players: Record<string, PlayerInfo> = {};
  for (const p of [...plans].sort((x, y) => (x.createdAt ?? "").localeCompare(y.createdAt ?? ""))) {
    for (const [pid, g] of Object.entries(p.games)) games[pid] = { ...(games[pid] ?? {}), ...g };
    Object.assign(players, p.players);
  }
  // players a plan knows: no marker on a day = no game
  for (const pid of Object.keys(players)) games[pid] ??= {};
  for (const x of actuals)
    for (const e of x.slots)
      if (e.playerId && e.game) games[e.playerId] = { ...(games[e.playerId] ?? {}), [x.date]: e.game };
  return days.map((d) => {
    const pf = planForDay(plans, d);
    const act = actualForDay(actuals, d);
    const known = plans.some((p) => (p.days[d]?.length ?? 0) > 0);
    return { ...buildDay(d, pf?.slots ?? null, act, games, players, known), plan: pf?.plan ?? null, planSameDay: !!pf?.sameDay, actual: act };
  });
}

export interface WeekSummary {
  started: number;
  available: number;
  unused: number;
  daysWithData: number;
  mismatchDays: number;
  known: boolean;
}

export function weekSummary(grid: DayGrid[]): WeekSummary {
  const withData = grid.filter((d) => d.plan || d.actual);
  return {
    started: withData.reduce((t, d) => t + d.summary.started, 0),
    available: withData.reduce((t, d) => t + d.summary.available, 0),
    unused: withData.reduce((t, d) => t + d.summary.unused, 0),
    daysWithData: withData.length,
    mismatchDays: grid.filter((d) => d.mismatches > 0).length,
    known: withData.every((d) => d.summary.known),
  };
}

// ------------------------------------------------------------------ decisions timeline

export interface TxnRow {
  txn_id: string;
  run_id: string | null;
  kind: string;
  status: string;
  payload: unknown;
  verification: unknown;
  error: string | null;
  approval_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ApprovalRow {
  approval_id: string;
  action_type: string;
  summary: string | null;
  payload: unknown;
  status: string;
  decided_by: string | null;
  note: string | null;
  requested_at: string | null;
  decided_at: string | null;
}

export interface DecisionRow {
  decision_id: string;
  run_id: string | null;
  kind: string;
  choice: unknown;
  agrees_with_model: boolean | null;
  rationale: string | null;
  created_at: string | null;
}

/** The fields of a (parsed, newest-version) advice item the timeline needs; mapped from `Advice` by the page. */
export interface AdviceLite {
  adviceId: string;
  type: string;
  title: string;
  status: string;
  headline: string | null;
  createdAt: string | null;
  firstCreatedAt: string | null;
  forDate: string | null;
  week: number | null;
  rationale: string[];
  whyNow: string | null;
}

export type TimelineKind = "lineup" | "il" | "add_drop" | "trade" | "stream" | "approval" | "decision" | "other";

export interface TimelineItem {
  id: string;
  at: string | null;
  kind: TimelineKind;
  title: string;
  status: string | null;
  detail: string[];
  rationale: string[];
  whyNow: string | null;
  evidence: string | null;
  href: string | null;
}

const KIND_OF: Record<string, TimelineKind> = { lineup: "lineup", il: "il", add: "add_drop", drop: "add_drop", add_drop: "add_drop", trade: "trade", stream: "stream" };

/** ET calendar date of an ISO timestamp. */
export function etDate(ts: string | null): string | null {
  if (!ts) return null;
  const t = new Date(ts);
  return Number.isNaN(t.getTime()) ? null : t.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

function texts(v: unknown): string[] {
  if (typeof v === "string" && v.trim()) return [v.trim()];
  return a(v).filter((x): x is string => typeof x === "string" && !!x.trim());
}

function changeLines(payload: Obj | null, names: Record<string, string>): string[] {
  return a(payload?.changes)
    .map((c) => {
      const x = o(c);
      if (!x) return typeof c === "string" ? c : null;
      const id = s(x.player_id) ?? s(x.player);
      const who = s(x.name) ?? (id ? (names[id] ?? id) : "?");
      const from = s(x.from) ?? "?";
      const to = s(x.to) ?? "?";
      return `${who}: ${from} → ${to}`;
    })
    .filter((x): x is string => !!x);
}

function txnEvidence(t: TxnRow): string | null {
  const v = o(t.verification);
  const ev = o(v?.evidence);
  const bits: string[] = [];
  if (t.status === "verified") bits.push(`verified${s(v?.verified_at) ? ` ${s(v?.verified_at)}` : ""}${s(ev?.page) ? ` by re-reading ${s(ev?.page)}` : ""}`);
  if (t.error) bits.push(`error: ${t.error}`);
  return bits.length ? bits.join(" · ") : null;
}

function inWeek(dates: (string | null)[], start: string, end: string): boolean {
  return dates.some((d) => d != null && d >= start && d <= end);
}

/**
 * The week's decisions, newest first: Yahoo transactions (lineup changes with their verification evidence, IL moves,
 * add/drops, trades), approvals, recommendations (Moves items with rationale / why-now) and logged decisions.
 * An item belongs to the week when it happened in it (ET) or is FOR a date in it (txn payload.date, advice for_date/week).
 */
export function buildTimeline(
  input: { txns: TxnRow[]; approvals: ApprovalRow[]; advice: AdviceLite[]; decisions: DecisionRow[]; names?: Record<string, string> },
  week: { week: number; start: string; end: string },
): TimelineItem[] {
  const out: TimelineItem[] = [];
  const { start, end } = week;
  for (const t of input.txns) {
    const p = o(t.payload);
    const forDate = s(p?.date) ?? s(p?.for_date);
    if (!inWeek([forDate, etDate(t.created_at)], start, end)) continue;
    const kind = KIND_OF[t.kind] ?? (t.kind === "lineup" ? "lineup" : "other");
    const changes = changeLines(p, input.names ?? {});
    const il = changes.some((c) => c.endsWith("→ IL")) || a(p?.il_moves).length > 0;
    out.push({
      id: t.txn_id,
      at: t.updated_at ?? t.created_at,
      kind: kind === "lineup" && il ? "il" : kind,
      title: t.kind === "lineup" ? `Lineup ${forDate ?? ""}`.trim() + (changes.length ? ` · ${changes.length} move${changes.length > 1 ? "s" : ""}` : "") : (s(p?.summary) ?? `${t.kind} ${forDate ?? ""}`.trim()),
      status: t.status,
      detail: changes.slice(0, 12),
      rationale: texts(p?.rationale ?? p?.reason),
      whyNow: s(p?.why_now),
      evidence: txnEvidence(t),
      href: "/activity#runs",
    });
  }
  for (const r of input.approvals) {
    if (!inWeek([etDate(r.requested_at), etDate(r.decided_at)], start, end)) continue;
    const p = o(r.payload);
    out.push({
      id: r.approval_id,
      at: r.decided_at ?? r.requested_at,
      kind: "approval",
      title: `${r.action_type.replace(/_/g, "/")} approval: ${r.summary ?? r.approval_id}`,
      status: r.status,
      detail: r.decided_by ? [`decided by ${r.decided_by}`] : [],
      rationale: [...texts(p?.rationale), ...(r.note ? [r.note] : [])],
      whyNow: s(p?.why_now),
      evidence: null,
      href: "/activity",
    });
  }
  for (const x of input.advice) {
    if (!(inWeek([x.forDate, etDate(x.createdAt), etDate(x.firstCreatedAt)], start, end) || x.week === week.week)) continue;
    out.push({
      id: x.adviceId,
      at: x.createdAt,
      kind: KIND_OF[x.type] ?? "other",
      title: x.title,
      status: x.status,
      detail: x.headline ? [x.headline] : [],
      rationale: x.rationale,
      whyNow: x.whyNow,
      evidence: null,
      href: "/moves",
    });
  }
  for (const d of input.decisions) {
    if (!inWeek([etDate(d.created_at)], start, end)) continue;
    const c = o(d.choice);
    const choice = typeof d.choice === "string" ? d.choice : (s(c?.summary) ?? s(c?.choice) ?? (c ? JSON.stringify(c).slice(0, 160) : null));
    out.push({
      id: d.decision_id,
      at: d.created_at,
      kind: "decision",
      title: `Decision (${d.kind})${d.agrees_with_model === false ? " · overrides the model" : ""}`,
      status: d.agrees_with_model === false ? "override" : null,
      detail: choice ? [choice] : [],
      rationale: d.rationale ? [d.rationale] : [],
      whyNow: null,
      evidence: null,
      href: d.run_id ? `/activity?timeline=1#runs` : "/activity",
    });
  }
  return out.sort((x, y) => (y.at ?? "").localeCompare(x.at ?? ""));
}

// ------------------------------------------------------------------ week calendar

export interface LeagueWeek {
  week: number;
  start: string;
  end: string;
  opponent: number | null;
  opponent_name: string | null;
}

/** The week containing `today`, else the next one, else the last. */
export function pickWeek(weeks: LeagueWeek[], today: string, requested?: number | null): LeagueWeek | null {
  const sorted = [...weeks].sort((x, y) => x.week - y.week);
  if (requested != null) {
    const w = sorted.find((x) => x.week === requested);
    if (w) return w;
  }
  return sorted.find((w) => w.start <= today && today <= w.end) ?? sorted.find((w) => w.start > today) ?? sorted[sorted.length - 1] ?? null;
}
