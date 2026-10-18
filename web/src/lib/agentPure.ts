/** Pure helpers for the external-agent API (/api/agent/*).
 *  No imports besides node:crypto so `node --test` can load this file directly (type stripping). */
import { createHash, timingSafeEqual } from "node:crypto";

/* ------------------------------------------------------------------ auth */

/** Constant-time check of an `Authorization: Bearer <token>` header against the expected token.
 *  Both sides are hashed first so lengths never leak and timingSafeEqual always gets equal-size buffers. */
export function bearerMatches(header: string | null | undefined, expected: string): boolean {
  const m = /^Bearer\s+(.+)$/i.exec((header ?? "").trim());
  const got = m ? m[1].trim() : "";
  const a = createHash("sha256").update(`agent:${got}`).digest();
  const b = createHash("sha256").update(`agent:${expected}`).digest();
  return timingSafeEqual(a, b) && got.length > 0;
}

/* ------------------------------------------------------------------ names */

export function normName(s: string | null | undefined): string {
  return (s ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

export function slugify(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/[\s-]+/g, "-");
}

const SUFFIX = /(jr|sr|ii|iii|iv)$/;

function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let diag = prev[0];
    prev[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const tmp = prev[j];
      prev[j] = Math.min(prev[j] + 1, prev[j - 1] + 1, diag + (a[i - 1] === b[j - 1] ? 0 : 1));
      diag = tmp;
    }
  }
  return prev[b.length];
}

export function similarity(a: string, b: string): number {
  if (!a.length && !b.length) return 1;
  return 1 - levenshtein(a, b) / Math.max(a.length, b.length);
}

export interface NamedPlayer {
  player_id: string;
  name: string;
  team?: string | null;
  positions?: string[] | null;
  rank?: number | null;
}

export type Resolution<T extends NamedPlayer> =
  | { status: "ok"; player: T }
  | { status: "ambiguous"; candidates: T[] }
  | { status: "not_found"; candidates: T[] };

/** Resolve a player id or display name (mirrors fantasy/draft/names.py: id → slug → exact name →
 *  suffix-stripped → fuzzy ≥ cutoff). Ties are reported as ambiguous with candidates, never guessed. */
export function resolvePlayer<T extends NamedPlayer>(query: string, pool: T[], cutoff = 0.82): Resolution<T> {
  const q = query.trim();
  if (!q) return { status: "not_found", candidates: [] };
  const byId = pool.find((p) => p.player_id === q) ?? pool.find((p) => p.player_id === slugify(q));
  if (byId) return { status: "ok", player: byId };
  const qn = normName(q);
  const exact = pool.filter((p) => normName(p.name) === qn);
  if (exact.length === 1) return { status: "ok", player: exact[0] };
  if (exact.length > 1) return { status: "ambiguous", candidates: exact };
  const qb = qn.replace(SUFFIX, "");
  const base = pool.filter((p) => normName(p.name).replace(SUFFIX, "") === qb);
  if (base.length === 1) return { status: "ok", player: base[0] };
  if (base.length > 1) return { status: "ambiguous", candidates: base };
  // Yahoo-style abbreviations: "V. Wembanyama", "S. Gilgeous-Alexander"
  const ab = /^([a-z])\.?\s+(.+)$/i.exec(q);
  if (ab) {
    const ini = ab[1].toLowerCase();
    const rest = normName(ab[2]).replace(SUFFIX, "");
    const hits = pool.filter((p) => {
      const parts = p.name.trim().split(/\s+/);
      return normName(parts[0]).startsWith(ini) && normName(parts.slice(1).join(" ")).replace(SUFFIX, "") === rest;
    });
    if (hits.length === 1) return { status: "ok", player: hits[0] };
    if (hits.length > 1) return { status: "ambiguous", candidates: hits };
  }
  // a unique substring of ≥ 5 chars (e.g. a distinctive last name) is unambiguous
  if (qn.length >= 5) {
    const sub = pool.filter((p) => normName(p.name).includes(qn));
    if (sub.length === 1) return { status: "ok", player: sub[0] };
  }
  const scored = pool
    .map((p) => ({ p, s: similarity(qn, normName(p.name)) }))
    .sort((a, b) => b.s - a.s || (a.p.rank ?? 1e9) - (b.p.rank ?? 1e9));
  const close = scored.filter((x) => x.s >= cutoff).slice(0, 5);
  if (close.length === 1) return { status: "ok", player: close[0].p };
  if (close.length > 1) return { status: "ambiguous", candidates: close.map((x) => x.p) };
  // nothing close: suggest substring matches (e.g. a last name) or the nearest few
  const sub = pool.filter((p) => qn.length >= 3 && normName(p.name).includes(qn)).slice(0, 5);
  return { status: "not_found", candidates: sub.length ? sub : scored.slice(0, 3).map((x) => x.p) };
}

/** Case/accent-insensitive search over name, id, team and positions. */
export function searchPlayers<T extends NamedPlayer>(q: string, pool: T[]): T[] {
  const terms = q
    .split(/\s+/)
    .map((t) => normName(t))
    .filter(Boolean);
  if (!terms.length) return pool;
  // a term matches a team/position exactly, or (≥ 2 chars) a substring of the name/id; so "okc c" = OKC centers
  return pool.filter((p) => {
    const team = normName(p.team ?? "");
    const pos = (p.positions ?? []).map(normName);
    const name = `${normName(p.name)}|${normName(p.player_id)}`;
    return terms.every((t) => t === team || pos.includes(t) || (t.length >= 2 && name.includes(t)));
  });
}

/* ------------------------------------------------------------------ draft board */

export interface BoardInfo {
  picks_made: number;
  max_recorded: number;
  on_the_clock: number | null;
  gaps: number[];
  complete: boolean;
}

/** On the clock = one after the highest recorded pick (same rule as the dashboard; gaps never rewind it). */
export function boardInfo(recorded: number[], total: number): BoardInfo {
  const set = new Set(recorded);
  const max = recorded.reduce((m, p) => Math.max(m, p), 0);
  const gaps: number[] = [];
  for (let i = 1; i < max; i++) if (!set.has(i)) gaps.push(i);
  const complete = max >= total && gaps.length === 0;
  return { picks_made: set.size, max_recorded: max, on_the_clock: max + 1 > total ? null : max + 1, gaps, complete };
}

/** Same freshness rule as the dashboard draft room. */
export function recIsFresh(asOfPick: number, forPick: number | null, picksMade: number, onClock: number | null): boolean {
  return asOfPick >= picksMade && (forPick == null || onClock == null || forPick >= onClock);
}

/** Default pick number when none is given: the lowest unrecorded pick (mirrors `fantasy draft pick`). */
export function defaultOverall(recorded: number[], total: number): number | null {
  const set = new Set(recorded);
  for (let i = 1; i <= total; i++) if (!set.has(i)) return i;
  return null;
}

/* ------------------------------------------------------------------ report validation */

export const REPORT_KINDS = ["draft_order", "trade", "rule", "injury_news", "schedule", "chat_note", "chat_digest", "other"] as const;
export type ReportKind = (typeof REPORT_KINDS)[number];
export const MAX_MESSAGE = 2000;
export const MAX_DETAILS_BYTES = 8000;
/** Per-kind override of MAX_DETAILS_BYTES: a chat digest carries message excerpts. */
export const MAX_DETAILS_BYTES_BY_KIND: Partial<Record<ReportKind, number>> = { chat_digest: 32000 };
export const REPORTS_PER_HOUR = 60;
/** chat_digest details shape (lenient): {period_start?, period_end?, excerpts: [{at?, author?, text}], topics?: [string]} */
export const CHAT_DIGEST_SHAPE = {
  "period_start?": "ISO timestamp of the first message covered",
  "period_end?": "ISO timestamp of the last message covered",
  excerpts: "[{at?: ISO timestamp, author?: display name, text: verbatim excerpt}] (may be empty)",
  "topics?": "[string] e.g. ['trade: Team A / Team B', 'draft time']",
  "message_count?": "number of chat messages in the period",
};

export interface ReportInput {
  kind: ReportKind;
  message: string;
  truncated: boolean;
  details: Record<string, unknown> | null;
  source: string | null;
}

export function validateReport(body: unknown): { ok: true; value: ReportInput } | { ok: false; error: string } {
  if (!body || typeof body !== "object" || Array.isArray(body)) return { ok: false, error: "Body must be a JSON object" };
  const b = body as Record<string, unknown>;
  const kind = String(b.kind ?? "");
  if (!(REPORT_KINDS as readonly string[]).includes(kind))
    return { ok: false, error: `kind must be one of: ${REPORT_KINDS.join(", ")}` };
  if (typeof b.message !== "string" || !b.message.trim()) return { ok: false, error: "message (non-empty string) is required" };
  const raw = b.message.trim();
  const message = raw.length > MAX_MESSAGE ? raw.slice(0, MAX_MESSAGE) : raw;
  let details: Record<string, unknown> | null = null;
  if (b.details != null) {
    if (typeof b.details !== "object" || Array.isArray(b.details)) return { ok: false, error: "details must be an object" };
    const maxDetails = MAX_DETAILS_BYTES_BY_KIND[kind as ReportKind] ?? MAX_DETAILS_BYTES;
    if (JSON.stringify(b.details).length > maxDetails) return { ok: false, error: `details too large (max ${maxDetails} chars of JSON)` };
    details = b.details as Record<string, unknown>;
  }
  if (kind === "chat_digest") {
    const ex = details?.excerpts;
    if (!Array.isArray(ex)) return { ok: false, error: "chat_digest needs details.excerpts: [{at?, author?, text}] (may be empty)" };
    const i = ex.findIndex((x) => !x || typeof x !== "object" || Array.isArray(x) || typeof (x as Record<string, unknown>).text !== "string");
    if (i >= 0) return { ok: false, error: `details.excerpts[${i}] must be an object with a text string` };
  }
  const source = b.source == null ? null : String(b.source).slice(0, 60);
  return { ok: true, value: { kind: kind as ReportKind, message, truncated: raw.length > MAX_MESSAGE, details, source } };
}

export interface PickInput {
  overall: number | null;
  player: string;
  note: string | null;
  allow_unknown: boolean;
}

export function validatePick(body: unknown): { ok: true; value: PickInput } | { ok: false; error: string } {
  if (!body || typeof body !== "object" || Array.isArray(body)) return { ok: false, error: "Body must be a JSON object" };
  const b = body as Record<string, unknown>;
  const player = typeof b.player === "string" ? b.player.trim() : "";
  if (!player || player.length > 100) return { ok: false, error: "player (name or player_id, ≤100 chars) is required" };
  let overall: number | null = null;
  if (b.overall != null && b.overall !== "") {
    overall = Number(b.overall);
    if (!Number.isInteger(overall) || overall < 1) return { ok: false, error: "overall must be a positive integer" };
  }
  const note = b.note == null ? null : String(b.note).slice(0, 500);
  return { ok: true, value: { overall, player, note, allow_unknown: b.allow_unknown === true } };
}

/** Muse's run id for a calendar day in US/Eastern (one agent_runs row per day). */
export function museRunId(now: Date = new Date()): string {
  const day = now.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  return `muse-${day}`;
}

/* ------------------------------------------------------------------ league snapshots (read-only Yahoo observations) */

export const SNAPSHOT_KINDS = [
  "my_team",
  "matchup",
  "scoreboard",
  "standings",
  "transactions",
  "rosters",
  "free_agents",
  "injuries",
  "settings",
  "other",
] as const;
export type SnapshotKind = (typeof SNAPSHOT_KINDS)[number];
export const MAX_SNAPSHOT_BYTES = 65536;
/** Combined budget per hour: snapshots + reports/picks (reports alone keep their own 60/hour cap). */
export const SNAPSHOTS_PER_HOUR = 120;
export const MAX_SUMMARY = 500;
export const PLAYER_STATUSES = ["healthy", "DTD", "GTD", "O", "INJ", "IL", "IL+", "NA", "SUSP"] as const;
export const CATEGORY_KEYS = ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"] as const;

/** Documented JSON shapes per kind (lenient: required core fields; extra fields are kept). `?` = optional. */
export const SNAPSHOT_SHAPES: Record<SnapshotKind, Record<string, string>> = {
  my_team: {
    team_name: "string",
    players: `[{name, positions?: string[], status?: ${PLAYER_STATUSES.join("|")}, slot?: "PG|SG|G|SF|PF|F|C|Util|BN|IL|IL+", injury_note?, games_today?}] (≥1)`,
    "adds_used_this_week?": "number",
  },
  matchup: {
    week: "integer (top-level week also accepted)",
    opponent: "string (opponent team name)",
    categories: `{${CATEGORY_KEYS.join("|")}: {me, opp}} — numbers, or strings as Yahoo shows them (e.g. ".481")`,
    "games_remaining?": "{me, opp}",
    "score?": "{me, opp, ties?} category wins so far",
  },
  scoreboard: {
    week: "integer",
    matchups: "[{team_a, team_b, score_a?, score_b?}] (≥1); score = category wins, e.g. 5",
  },
  standings: { teams: "[{team_name, rank?, record? 'W-L-T', pct?, games_back?, moves?}] (≥1)" },
  transactions: { items: "[{type: add|drop|add_drop|trade|waiver|other, team?, players: [string], ts?, note?}] (may be empty)" },
  rosters: { teams: "[{team_name, players: [{name, positions?, status?, slot?}]}] (≥1)" },
  free_agents: { "filter?": "string, e.g. 'All positions, sorted by rank (last 7 days)'", players: "[{name, team?, positions?, status?, percent_rostered?, rank?}] (≥1)" },
  injuries: { players: "[{name, status, team?, fantasy_team?, note?}] (may be empty)" },
  settings: { "(any)": "non-empty object of the settings as shown, e.g. {max_weekly_adds: 6, waiver_days: 2}" },
  other: { "(any)": "non-empty object; a top-level `summary` is required for this kind" },
};

export interface SnapshotInput {
  kind: SnapshotKind;
  week: number | null;
  observed_at: string;
  source_url: string | null;
  summary: string;
  payload: Record<string, unknown>;
  payload_sha256: string;
  snapshot_id: string;
  warnings: string[];
}

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
function isStr(v: unknown): v is string {
  return typeof v === "string" && v.trim().length > 0;
}
/** Number, or a numeric string as Yahoo prints it (".481", "48.1%", "1,234"). */
export function numLike(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v !== "string") return null;
  const s = v.trim().replace(/,/g, "").replace(/%$/, "");
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(s)) return null;
  return Number(s);
}

/** Sorted-key, whitespace-free JSON (same bytes as Python json.dumps(sort_keys=True, separators=(",",":"),
 *  ensure_ascii=False) for JSON-native values) — basis of payload_sha256 and the dedupe key. */
export function canonicalJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map((x) => canonicalJson(x === undefined ? null : x)).join(",")}]`;
  if (isObj(v))
    return `{${Object.keys(v)
      .filter((k) => v[k] !== undefined)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonicalJson(v[k])}`)
      .join(",")}}`;
  return JSON.stringify(v ?? null);
}

export function sha256Hex(s: string): string {
  return createHash("sha256").update(s, "utf8").digest("hex");
}

/** Deterministic id = dedupe key (kind, week, payload_sha256); mirrors fantasy.ops.intel.snapshot_id. */
export function snapshotId(kind: string, week: number | null, payloadSha: string): string {
  return "snap_" + sha256Hex(`${kind}|${week == null ? "" : week}|${payloadSha}`).slice(0, 32);
}

function arr(p: Record<string, unknown>, key: string, errors: string[], min = 1): Record<string, unknown>[] {
  const v = p[key];
  if (!Array.isArray(v)) {
    errors.push(`payload.${key} must be an array`);
    return [];
  }
  if (v.length < min) errors.push(`payload.${key} must have at least ${min} item(s)`);
  const bad = v.findIndex((x) => !isObj(x));
  if (bad >= 0) errors.push(`payload.${key}[${bad}] must be an object`);
  return v.filter(isObj);
}

function needStr(items: Record<string, unknown>[], key: string, path: string, errors: string[]) {
  const i = items.findIndex((x) => !isStr(x[key]));
  if (i >= 0) errors.push(`${path}[${i}].${key} (non-empty string) is required`);
}

function checkStatuses(items: Record<string, unknown>[], path: string, warnings: string[]) {
  const known = new Set<string>(PLAYER_STATUSES);
  const odd = [...new Set(items.map((x) => x.status).filter((s) => s != null && !known.has(String(s))).map(String))];
  if (odd.length) warnings.push(`${path}: unrecognised status ${odd.join(", ")} (known: ${PLAYER_STATUSES.join(", ")}); stored as given`);
}

const KIND_LABEL: Record<SnapshotKind, string> = {
  my_team: "My team",
  matchup: "Matchup",
  scoreboard: "Scoreboard",
  standings: "Standings",
  transactions: "Transactions",
  rosters: "Rosters",
  free_agents: "Free agents",
  injuries: "Injuries",
  settings: "Settings",
  other: "Other",
};
export function snapshotLabel(kind: string): string {
  return KIND_LABEL[kind as SnapshotKind] ?? kind;
}

/** Lower is better only for turnovers. Returns "me" | "opp" | "tie" | null (not comparable). */
export function catLeader(cat: string, me: unknown, opp: unknown): "me" | "opp" | "tie" | null {
  const a = numLike(me);
  const b = numLike(opp);
  if (a == null || b == null) return null;
  if (a === b) return "tie";
  const lowerBetter = cat === "tov" || cat === "to";
  return (a > b) !== lowerBetter ? "me" : "opp";
}

/** One-line human summary when the reporter gives none. */
export function summarizeSnapshot(kind: string, p: Record<string, unknown>, week: number | null): string {
  const list = (k: string) => (Array.isArray(p[k]) ? (p[k] as unknown[]).filter(isObj) : []);
  const wk = week != null ? `week ${week}: ` : "";
  switch (kind) {
    case "my_team": {
      const ps = list("players");
      const hurt = ps.filter((x) => x.status != null && String(x.status) !== "healthy");
      const adds = numLike(p.adds_used_this_week);
      return (
        `${wk}${String(p.team_name ?? "my team")}: ${ps.length} players` +
        (hurt.length ? `; ${hurt.map((x) => `${x.name} ${x.status}`).join(", ")}` : "; all healthy") +
        (adds != null ? `; adds used ${adds}` : "")
      );
    }
    case "matchup": {
      const cats = isObj(p.categories) ? p.categories : {};
      let me = 0,
        opp = 0,
        tie = 0;
      for (const [c, v] of Object.entries(cats)) {
        if (!isObj(v)) continue;
        const l = catLeader(c, v.me, v.opp);
        if (l === "me") me++;
        else if (l === "opp") opp++;
        else if (l === "tie") tie++;
      }
      const gr = isObj(p.games_remaining) ? `; games left ${p.games_remaining.me ?? "?"}-${p.games_remaining.opp ?? "?"}` : "";
      return `${wk}vs ${String(p.opponent ?? "?")}: leading ${me}-${opp}${tie ? `-${tie}` : ""} in categories${gr}`;
    }
    case "scoreboard":
      return `${wk}${list("matchups").length} matchups`;
    case "standings": {
      const t = list("teams");
      const top = t.find((x) => numLike(x.rank) === 1) ?? t[0];
      return `${t.length} teams; 1st ${top ? String(top.team_name) : "?"}${top?.record ? ` (${top.record})` : ""}`;
    }
    case "transactions": {
      const it = list("items");
      return `${wk}${it.length} transaction(s)` + (it.length ? `; latest: ${String(it[0].type ?? "?")} ${String(it[0].team ?? "")}`.trimEnd() : "");
    }
    case "rosters":
      return `${list("teams").length} rosters`;
    case "free_agents":
      return `${list("players").length} free agents${p.filter ? ` (${String(p.filter)})` : ""}`;
    case "injuries":
      return `${list("players").length} injured players`;
    default:
      return `${wk}${Object.keys(p).length} fields`;
  }
}

/** Validate a POST /api/agent/snapshot body. `bytes` = raw body size (checked against MAX_SNAPSHOT_BYTES).
 *  Lenient: required core fields per kind; unknown extra fields are kept; soft problems become warnings. */
export function validateSnapshot(
  body: unknown,
  now: Date = new Date(),
): { ok: true; value: SnapshotInput } | { ok: false; error: string; errors: string[] } {
  const bad = (errors: string[]) => ({ ok: false as const, error: errors.join("; "), errors });
  if (!isObj(body)) return bad(["Body must be a JSON object"]);
  const kind = String(body.kind ?? "");
  if (!(SNAPSHOT_KINDS as readonly string[]).includes(kind)) return bad([`kind must be one of: ${SNAPSHOT_KINDS.join(", ")}`]);
  const p = body.payload;
  if (!isObj(p) || Object.keys(p).length === 0) return bad(["payload (non-empty JSON object) is required"]);
  const errors: string[] = [];
  const warnings: string[] = [];

  // week: top-level wins, else payload.week
  let week: number | null = null;
  const wRaw = body.week ?? p.week;
  if (wRaw != null && wRaw !== "") {
    const w = Number(wRaw);
    if (!Number.isInteger(w) || w < 0 || w > 30) errors.push("week must be an integer 0-30");
    else week = w;
  }
  if (body.week != null && p.week != null && Number(body.week) !== Number(p.week))
    errors.push("week and payload.week disagree");

  // observed_at: when the page was looked at (default now); not in the future
  let observed = now;
  if (body.observed_at != null) {
    const t = typeof body.observed_at === "string" ? Date.parse(body.observed_at) : NaN;
    if (Number.isNaN(t)) errors.push("observed_at must be an ISO 8601 timestamp, e.g. 2026-11-02T08:15:00-05:00");
    else if (t > now.getTime() + 10 * 60_000) errors.push("observed_at is in the future");
    else {
      observed = new Date(t);
      if (now.getTime() - t > 14 * 86400_000) warnings.push("observed_at is more than 14 days old");
    }
  }
  let source_url: string | null = null;
  if (body.source_url != null) {
    if (typeof body.source_url !== "string" || !/^https?:\/\/\S+$/.test(body.source_url) || body.source_url.length > 500)
      errors.push("source_url must be an http(s) URL (≤500 chars)");
    else source_url = body.source_url;
  }
  if (body.summary != null && typeof body.summary !== "string") errors.push("summary must be a string");

  switch (kind as SnapshotKind) {
    case "my_team": {
      if (!isStr(p.team_name)) errors.push("payload.team_name (string) is required");
      const ps = arr(p, "players", errors);
      needStr(ps, "name", "payload.players", errors);
      checkStatuses(ps, "payload.players", warnings);
      if (p.adds_used_this_week != null && numLike(p.adds_used_this_week) == null)
        errors.push("payload.adds_used_this_week must be a number");
      break;
    }
    case "matchup": {
      if (week == null) errors.push("week (integer) is required for matchup");
      if (!isStr(p.opponent)) errors.push("payload.opponent (string) is required");
      if (!isObj(p.categories) || !Object.keys(p.categories).length) errors.push("payload.categories (object of {me, opp}) is required");
      else {
        const known = new Set<string>(CATEGORY_KEYS);
        for (const [c, v] of Object.entries(p.categories)) {
          if (!isObj(v) || !("me" in v) || !("opp" in v)) errors.push(`payload.categories.${c} must be {me, opp}`);
          else if (numLike(v.me) == null || numLike(v.opp) == null) warnings.push(`payload.categories.${c}: non-numeric value kept as given`);
        }
        const odd = Object.keys(p.categories).filter((c) => !known.has(c));
        if (odd.length) warnings.push(`unrecognised category keys ${odd.join(", ")} (expected ${CATEGORY_KEYS.join(", ")})`);
      }
      if (p.games_remaining != null && !isObj(p.games_remaining)) errors.push("payload.games_remaining must be {me, opp}");
      break;
    }
    case "scoreboard": {
      if (week == null) errors.push("week (integer) is required for scoreboard");
      const ms = arr(p, "matchups", errors);
      needStr(ms, "team_a", "payload.matchups", errors);
      needStr(ms, "team_b", "payload.matchups", errors);
      break;
    }
    case "standings": {
      const ts = arr(p, "teams", errors);
      needStr(ts, "team_name", "payload.teams", errors);
      break;
    }
    case "transactions": {
      const it = arr(p, "items", errors, 0);
      needStr(it, "type", "payload.items", errors);
      if (it.some((x) => !Array.isArray(x.players))) warnings.push("payload.items: some items have no players array");
      break;
    }
    case "rosters": {
      const ts = arr(p, "teams", errors);
      needStr(ts, "team_name", "payload.teams", errors);
      ts.forEach((t, i) => {
        if (!Array.isArray(t.players)) errors.push(`payload.teams[${i}].players must be an array`);
      });
      break;
    }
    case "free_agents": {
      const ps = arr(p, "players", errors);
      needStr(ps, "name", "payload.players", errors);
      break;
    }
    case "injuries": {
      const ps = arr(p, "players", errors, 0);
      needStr(ps, "name", "payload.players", errors);
      needStr(ps, "status", "payload.players", errors);
      break;
    }
    case "settings":
      break;
    case "other":
      if (!isStr(body.summary)) errors.push("summary (string) is required for kind 'other'");
      break;
  }
  if (errors.length) return bad(errors);

  const payload_sha256 = sha256Hex(canonicalJson(p));
  const summary = (isStr(body.summary) ? body.summary.trim() : summarizeSnapshot(kind, p, week)).slice(0, MAX_SUMMARY);
  return {
    ok: true,
    value: {
      kind: kind as SnapshotKind,
      week,
      observed_at: observed.toISOString(),
      source_url,
      summary,
      payload: p,
      payload_sha256,
      snapshot_id: snapshotId(kind, week, payload_sha256),
      warnings,
    },
  };
}

/** `24h`, `90m`, `7d`, `2w` (relative to now) or an ISO date/datetime → ISO string; null if absent; throws on junk. */
export function parseSince(v: string | null | undefined, now: Date = new Date()): string | null {
  if (v == null || !v.trim()) return null;
  const m = /^(\d+(?:\.\d+)?)\s*([mhdw])$/i.exec(v.trim());
  if (m) {
    const unit = { m: 60_000, h: 3600_000, d: 86400_000, w: 7 * 86400_000 }[m[2].toLowerCase() as "m" | "h" | "d" | "w"];
    return new Date(now.getTime() - Number(m[1]) * unit).toISOString();
  }
  const t = Date.parse(v);
  if (Number.isNaN(t)) throw new Error(`invalid since ${JSON.stringify(v)}: use 24h, 7d, 2w or an ISO date`);
  return new Date(t).toISOString();
}

/** Newest row per kind by last_seen_at (else observed_at, else ts), in SNAPSHOT_KINDS order. */
export function latestPerKind<T extends { kind: string; last_seen_at?: string | null; observed_at?: string | null; ts?: string | null }>(
  rows: T[],
): T[] {
  const seen = (r: T) => Date.parse(r.last_seen_at ?? r.observed_at ?? r.ts ?? "") || 0;
  const best = new Map<string, T>();
  for (const r of rows) {
    const cur = best.get(r.kind);
    if (!cur || seen(r) > seen(cur)) best.set(r.kind, r);
  }
  const order = (k: string) => {
    const i = (SNAPSHOT_KINDS as readonly string[]).indexOf(k);
    return i < 0 ? 99 : i;
  };
  return [...best.values()].sort((a, b) => order(a.kind) - order(b.kind));
}
