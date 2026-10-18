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

export const REPORT_KINDS = ["draft_order", "trade", "rule", "injury_news", "schedule", "chat_note", "other"] as const;
export type ReportKind = (typeof REPORT_KINDS)[number];
export const MAX_MESSAGE = 2000;
export const MAX_DETAILS_BYTES = 8000;
export const REPORTS_PER_HOUR = 60;

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
    if (JSON.stringify(b.details).length > MAX_DETAILS_BYTES) return { ok: false, error: `details too large (max ${MAX_DETAILS_BYTES} chars of JSON)` };
    details = b.details as Record<string, unknown>;
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
