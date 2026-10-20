/**
 * Recommendation (advice/1) readers for the Inbox decision cards and the This week timeline (pure, no runtime
 * imports: unit-tested by `npm test`). Grouping into Inbox sections, with approvals, lives in inbox.ts.
 *
 * Producer: `fantasy advise post|status` (src/fantasy/ops/advice.py). Each recommendation is an item
 * (`summary.advice_id`) with append-only versions, one `model_runs` row each (`kind = 'advice'`,
 * schema `advice/1`, docs/research/dashboard_data.md section 4). Readers show the newest version per item.
 */

type Obj = Record<string, unknown>;

const o = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);
const a = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const s = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export const ADVICE_TYPES = ["trade", "add_drop", "stream", "lineup", "il"] as const;
export const ADVICE_STATUSES = ["idea", "awaiting_yes", "approved", "done", "rejected", "expired"] as const;
export const EVIDENCE = ["fitted", "simulated", "prior", "literature"] as const;
export type AdviceType = (typeof ADVICE_TYPES)[number];
export type AdviceStatus = (typeof ADVICE_STATUSES)[number];
export type Evidence = (typeof EVIDENCE)[number];

export interface AdviceMetric {
  label: string;
  value: number | string;
  se: number | null;
  unit: string | null;
  evidence: Evidence | null;
  note: string | null;
  delta: boolean;
}

export type Cell = string | number | boolean | null;

export const CHANGE_KINDS = ["news", "model", "roster", "schedule", "league", "other"] as const;
export type ChangeKind = (typeof CHANGE_KINDS)[number];

/** One thing that changed since the earlier decision (advice-0.2.0 `why_now.changed[]`). */
export interface WhyChange {
  text: string;
  kind: ChangeKind | null;
}

/** advice-0.2.0 `why_now`: what we believed/decided before, and what changed since. */
export interface WhyNow {
  before: string;
  changed: WhyChange[];
  evidence: string[]; // metric labels backing the change
}

/** A rationale entry: a paragraph, or a bullet when the author wrote "- " / "• " in front. */
export interface RationaleItem {
  text: string;
  bullet: boolean;
}

export interface Advice {
  runId: string;
  adviceId: string;
  version: number;
  versions: number;
  createdAt: string | null; // this version
  firstCreatedAt: string | null; // first version of the item
  type: AdviceType | string;
  status: AdviceStatus | string;
  title: string;
  headline: string | null;
  verdict: string | null;
  forDate: string | null;
  week: number | null;
  requiresUserYes: boolean;
  metrics: AdviceMetric[];
  scenarioColumns: string[];
  scenarios: Record<string, Cell>[];
  risks: string[];
  links: { label: string; path: string }[];
  approvalId: string | null;
  rationale: RationaleItem[]; // [] on rows posted before advice-0.2.0
  whyNow: WhyNow | null;
}

export interface AdviceRow {
  run_id: string;
  kind?: string | null;
  created_at: string | null;
  summary: unknown;
}

function cell(v: unknown): Cell {
  return typeof v === "string" || typeof v === "boolean" || n(v) != null ? (v as Cell) : null;
}

/** `rationale`: a list of strings (or one string); blank entries dropped; "- " / "• " marks a bullet. */
export function parseRationale(v: unknown): RationaleItem[] {
  const xs = typeof v === "string" ? [v] : a(v);
  const out: RationaleItem[] = [];
  for (const x of xs) {
    const t = s(x)?.trim();
    if (!t) continue;
    const m = /^[-•*]\s+/.exec(t);
    out.push(m ? { text: t.slice(m[0].length), bullet: true } : { text: t, bullet: false });
  }
  return out;
}

/** `why_now` -> WhyNow, or null unless it has a `before` text or at least one change. */
export function parseWhyNow(v: unknown): WhyNow | null {
  const w = o(v);
  if (!w) return null;
  const changed: WhyChange[] = [];
  for (const c of a(w.changed)) {
    const text = s(c) ?? s(o(c)?.text);
    if (!text) continue;
    const k = s(o(c)?.kind);
    changed.push({ text: text.trim(), kind: k && (CHANGE_KINDS as readonly string[]).includes(k) ? (k as ChangeKind) : null });
  }
  const before = s(w.before)?.trim() ?? "";
  if (!before && !changed.length) return null;
  return { before, changed, evidence: a(w.evidence).filter((e): e is string => typeof e === "string" && !!e.trim()) };
}

/** Long explanations collapse on narrow screens: more than 2 rationale items, more than 3 changes, or > 420 chars. */
export function isLongWhy(rationale: RationaleItem[], whyNow: WhyNow | null): boolean {
  const chars = rationale.reduce((t, r) => t + r.text.length, 0) + (whyNow?.before.length ?? 0) +
    (whyNow?.changed.reduce((t, c) => t + c.text.length, 0) ?? 0);
  return rationale.length > 2 || (whyNow?.changed.length ?? 0) > 3 || chars > 420;
}

/** One `model_runs` row -> Advice, or null when the summary is not an advice/1 object. */
export function parseAdvice(row: AdviceRow): Advice | null {
  const sm = o(row.summary);
  if (!sm) return null;
  const title = s(sm.title);
  if (!title) return null;
  const metrics: AdviceMetric[] = [];
  for (const m of a(sm.metrics)) {
    const mo = o(m);
    if (!mo || !s(mo.label)) continue;
    const value = n(mo.value) ?? s(mo.value);
    if (value == null) continue;
    const ev = s(mo.evidence);
    metrics.push({
      label: mo.label as string,
      value,
      se: n(mo.se),
      unit: s(mo.unit),
      evidence: ev && (EVIDENCE as readonly string[]).includes(ev) ? (ev as Evidence) : null,
      note: s(mo.note),
      delta: mo.delta === true,
    });
  }
  const scenarios = a(sm.scenarios)
    .map(o)
    .filter((x): x is Obj => !!x)
    .map((x) => Object.fromEntries(Object.entries(x).map(([k, v]) => [k, cell(v)])));
  // jsonb does not keep key order: prefer the explicit column list, then first-seen order with "name" first
  const cols: string[] = a(sm.scenario_columns).filter((c): c is string => typeof c === "string");
  if (!cols.length && scenarios.length) {
    const seen = new Set<string>(["name"]);
    cols.push("name");
    for (const r of scenarios)
      for (const k of Object.keys(r)) {
        if (seen.has(k)) continue;
        seen.add(k);
        cols.push(k);
      }
  }
  return {
    runId: row.run_id,
    adviceId: s(sm.advice_id) ?? row.run_id,
    version: n(sm.version) ?? 1,
    versions: 1,
    createdAt: row.created_at,
    firstCreatedAt: row.created_at,
    type: s(sm.type) ?? "other",
    status: s(sm.status) ?? "idea",
    title,
    headline: s(sm.headline),
    verdict: s(sm.verdict),
    forDate: s(sm.for_date),
    week: n(sm.week),
    requiresUserYes: sm.requires_user_yes === true,
    metrics,
    scenarioColumns: cols,
    scenarios,
    risks: a(sm.risks).filter((r): r is string => typeof r === "string" && !!r.trim()),
    links: a(sm.links)
      .map(o)
      .filter((l): l is Obj => !!l && !!s(l.label) && !!s(l.path))
      .map((l) => ({ label: l.label as string, path: l.path as string })),
    approvalId: s(sm.approval_id),
    rationale: parseRationale(sm.rationale),
    whyNow: parseWhyNow(sm.why_now),
  };
}

const order = (x: Advice) => [x.version, x.createdAt ?? ""] as const;
function newer(x: Advice, y: Advice): boolean {
  const [vx, tx] = order(x);
  const [vy, ty] = order(y);
  return vx !== vy ? vx > vy : tx > ty;
}

/** Newest version per advice_id (highest version, then created_at); items newest-updated first. */
export function latestAdvice(rows: AdviceRow[]): Advice[] {
  const by = new Map<string, { head: Advice; first: string | null; count: number }>();
  for (const r of rows) {
    const p = parseAdvice(r);
    if (!p) continue;
    const cur = by.get(p.adviceId);
    if (!cur) {
      by.set(p.adviceId, { head: p, first: p.createdAt, count: 1 });
      continue;
    }
    cur.count += 1;
    if (p.createdAt && (!cur.first || p.createdAt < cur.first)) cur.first = p.createdAt;
    if (newer(p, cur.head)) cur.head = p;
  }
  return [...by.values()]
    .map(({ head, first, count }) => ({ ...head, firstCreatedAt: first, versions: count }))
    .sort((x, y) => (y.createdAt ?? "").localeCompare(x.createdAt ?? ""));
}

/** "+8.6 ± 0.6 pp" / "2.31 per-game G units" / "about half". Minus sign is U+2212. */
export function formatMetric(m: Pick<AdviceMetric, "value" | "se" | "unit" | "delta">): string {
  let v: string;
  if (typeof m.value === "number") {
    const abs = String(Math.abs(m.value));
    v = m.value < 0 ? `−${abs}` : m.delta && m.value > 0 ? `+${abs}` : abs;
  } else v = m.value;
  const se = m.se != null ? ` ± ${m.se}` : "";
  return `${v}${se}${m.unit ? ` ${m.unit}` : ""}`;
}

export const TYPE_LABEL: Record<string, string> = {
  trade: "trade",
  add_drop: "add / drop",
  stream: "stream",
  lineup: "lineup",
  il: "IL move",
};

export const STATUS_LABEL: Record<string, string> = {
  idea: "idea",
  awaiting_yes: "needs your yes",
  approved: "approved",
  done: "done",
  rejected: "rejected",
  expired: "expired",
};

export const EVIDENCE_HELP: Record<Evidence, string> = {
  fitted: "Fitted to data (see the linked research note for method, sample and SE)",
  simulated: "Output of our simulator; ± is the Monte Carlo SE only, not model error",
  prior: "An assumption, not fitted to data",
  literature: "From a cited external source",
};

/** Repo-relative paths -> GitHub; dashboard routes stay internal; URLs pass through. A protocol-relative
 *  "//host" or "/\host" is not a dashboard route (browsers resolve it to another site): it goes to GitHub too. */
export function linkHref(path: string): { href: string; internal: boolean } {
  if (/^https?:\/\//.test(path)) return { href: path, internal: false };
  if (/^\/(?![/\\])/.test(path)) return { href: path, internal: true };
  return { href: `https://github.com/hmku/nba/blob/main/${path.replace(/^\.\//, "")}`, internal: false };
}
