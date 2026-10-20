import "server-only";
import { NextResponse } from "next/server";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  advisorRunId,
  authorizeAgentRequest,
  museRunId,
  replyEventPayload,
  replyTarget,
  userRunId,
  type AgentIdentity,
  type ReplyInput,
} from "./agentPure";
import { serviceClient } from "./supabase-admin";
import { loadPlayers } from "./data";
import type { Draft, Player, PlayerValue } from "./types";
import { asStringArray } from "./format";

/** JSON response that always carries `ok` and is never cached. */
export function reply(body: Record<string, unknown>, status = 200): NextResponse {
  return NextResponse.json({ ok: status < 400, ...body }, { status, headers: { "Cache-Control": "no-store" } });
}

export function fail(error: string, status: number, extra: Record<string, unknown> = {}): NextResponse {
  return reply({ ...extra, ok: false, error }, status);
}

/** Bearer-token gate + scope check for /api/agent/*. Muse's AGENT_API_TOKEN may call every endpoint; the
 *  advisor's ADVISOR_API_TOKEN only the read endpoints and its own reports (ADVISOR_ALLOWED_ENDPOINTS, default deny →
 *  403). Returns the caller's identity (derived from which token matched) or an error response.
 *  The token itself is never logged or echoed. */
export async function authorizeAgent(req: Request): Promise<{ identity: AgentIdentity } | NextResponse> {
  const res = authorizeAgentRequest(
    req.headers.get("authorization"),
    { agent: process.env.AGENT_API_TOKEN, advisor: process.env.ADVISOR_API_TOKEN },
    req.method,
    new URL(req.url).pathname,
  );
  if (res.ok) return { identity: res.identity };
  if (res.status === 401) await new Promise((r) => setTimeout(r, 400));
  return fail(res.error, res.status);
}

/** Gate for routes that do not care who is calling (returns null when authorised). The advisor is still limited
 *  to ADVISOR_ALLOWED_ENDPOINTS, so Muse-only routes (snapshot, pick, ...) answer it with 403. */
export async function requireAgent(req: Request): Promise<NextResponse | null> {
  const a = await authorizeAgent(req);
  return a instanceof NextResponse ? a : null;
}

export function writer(): SupabaseClient | NextResponse {
  const db = serviceClient();
  if (!db) return fail("Writes disabled: SUPABASE_SERVICE_ROLE_KEY is not set on the server", 503);
  return db;
}

export async function readJson(req: Request): Promise<unknown | NextResponse> {
  try {
    return await req.json();
  } catch {
    return fail("Invalid JSON body", 400);
  }
}

/** The real draft: newest non-mock draft (mock drafts are rehearsals and never exposed here). */
export async function activeDraft(db: SupabaseClient): Promise<{ draft: Draft | null; error?: string }> {
  const { data, error } = await db
    .from("drafts")
    .select("*")
    .or("is_mock.is.null,is_mock.eq.false")
    .not("draft_id", "like", "mock-%")
    .order("created_at", { ascending: false })
    .limit(5);
  const drafts = (data as Draft[]) ?? [];
  return { draft: drafts.find((d) => d.status !== "complete") ?? drafts[0] ?? null, error: error?.message };
}

interface PoolPlayer {
  player_id: string;
  name: string;
  team: string | null;
  positions: string[];
  status: string | null;
  injury_note: string | null;
  rank: number | null;
  value: number | null;
  adp: number | null;
}

/** Players table joined with the latest valuation run that has rows (rank / value / adp). */
export async function playerPool(db: SupabaseClient): Promise<{ pool: PoolPlayer[]; valuationRun: string | null }> {
  const players: Record<string, Player> = await loadPlayers(db);
  const { data: runs, error: runsErr } = await db
    .from("model_runs")
    .select("run_id")
    .eq("kind", "valuation")
    .order("created_at", { ascending: false })
    .limit(3);
  if (runsErr) throw new Error(runsErr.message);
  let values: PlayerValue[] = [];
  let runId: string | null = null;
  for (const r of (runs as { run_id: string }[]) ?? []) {
    const rows: PlayerValue[] = [];
    for (let from = 0; from < 5000; from += 1000) {
      const { data, error } = await db
        .from("player_values")
        .select("run_id,player_id,rank,value,adp,positions,extra")
        .eq("run_id", r.run_id)
        .order("rank", { ascending: true, nullsFirst: false })
        .range(from, from + 999);
      if (error || !data) break;
      rows.push(...(data as PlayerValue[]));
      if (data.length < 1000) break;
    }
    if (rows.length) {
      values = rows;
      runId = r.run_id;
      break;
    }
  }
  const byId = new Map<string, PoolPlayer>();
  for (const v of values) {
    const p = players[v.player_id];
    const ex = (v.extra ?? {}) as Record<string, unknown>;
    byId.set(v.player_id, {
      player_id: v.player_id,
      name: p?.name ?? (typeof ex.name === "string" ? ex.name : v.player_id),
      team: p?.team ?? (typeof ex.team === "string" ? ex.team : null),
      positions: asStringArray(v.positions ?? p?.positions),
      status: p?.status ?? null,
      injury_note: p?.injury_note ?? null,
      rank: v.rank,
      value: v.value,
      adp: v.adp,
    });
  }
  for (const p of Object.values(players))
    if (!byId.has(p.player_id))
      byId.set(p.player_id, {
        player_id: p.player_id,
        name: p.name,
        team: p.team,
        positions: asStringArray(p.positions),
        status: p.status,
        injury_note: p.injury_note,
        rank: null,
        value: null,
        adp: null,
      });
  const pool = [...byId.values()].sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9) || a.name.localeCompare(b.name));
  return { pool, valuationRun: runId };
}

const MUSE_TASK = "league intel from the user's assistant Meta Muse (Messenger group chat, observed picks)";
const ADVISOR_TASK = "suggestions from the independent read-only advisor (ADVISOR_API_TOKEN)";

/** Ensure today's run for `agent` exists (dry_run: these agents never act on Yahoo) and refresh its heartbeat. */
async function ensureDailyRun(
  db: SupabaseClient,
  agent: AgentIdentity,
  runId: string,
  task: string,
): Promise<{ runId: string; error?: string }> {
  const now = new Date().toISOString();
  const ins = await db.from("agent_runs").upsert(
    { run_id: runId, agent, task: `${task} · ${runId.slice(agent.length + 1)}`, status: "running", mode: "dry_run", started_at: now, heartbeat_at: now },
    { onConflict: "run_id", ignoreDuplicates: true },
  );
  if (ins.error) return { runId, error: ins.error.message };
  const upd = await db.from("agent_runs").update({ status: "running", heartbeat_at: now, ended_at: null }).eq("run_id", runId);
  return { runId, error: upd.error?.message };
}

/** Ensure today's muse run exists (agent="muse", dry_run) and refresh its heartbeat; returns its run_id. */
export function ensureMuseRun(db: SupabaseClient): Promise<{ runId: string; error?: string }> {
  return ensureDailyRun(db, "muse", museRunId(), MUSE_TASK);
}

/** Ensure today's advisor run exists (agent="advisor", dry_run: it never acts) and refresh its heartbeat. */
export function ensureAdvisorRun(db: SupabaseClient): Promise<{ runId: string; error?: string }> {
  return ensureDailyRun(db, "advisor", advisorRunId(), ADVISOR_TASK);
}

/** The user's daily replies run (agent 'user'). Inserted as "succeeded": it is a log container, never a live run,
 *  so it never shows as stuck and the stale-run reaper ignores it. */
async function ensureUserRun(db: SupabaseClient): Promise<{ runId: string; error?: string }> {
  const runId = userRunId();
  const now = new Date().toISOString();
  const ins = await db.from("agent_runs").upsert(
    {
      run_id: runId,
      agent: "user",
      task: `the user's replies on the dashboard · ${runId.slice(5)}`,
      status: "succeeded",
      mode: "dry_run",
      started_at: now,
      heartbeat_at: now,
      ended_at: now,
    },
    { onConflict: "run_id", ignoreDuplicates: true },
  );
  return { runId, error: ins.error?.message };
}

/** Insert a thread reply by `author` (identity known to the server: bearer token or passphrase cookie).
 *  Checks that reply_to exists and is not private, and files the reply under the author's daily run. */
export async function postReply(db: SupabaseClient, author: AgentIdentity | "user", r: ReplyInput): Promise<NextResponse> {
  const { data: rows, error: tErr } = await db.from("agent_events").select("event_id,run_id,payload").eq("event_id", r.reply_to).limit(1);
  if (tErr) return fail(tErr.message, 500);
  const target = replyTarget(((rows as { event_id: string; payload: unknown }[]) ?? [])[0] ?? null);
  if (!target.ok) return fail(target.error, target.status);
  const run = author === "muse" ? await ensureMuseRun(db) : author === "advisor" ? await ensureAdvisorRun(db) : await ensureUserRun(db);
  if (run.error) return fail(`Could not open the ${author} run: ${run.error}`, 500);
  const { message, payload } = replyEventPayload(r, author, target.threadRoot);
  const event = {
    event_id: `${author}-ev-${crypto.randomUUID().replace(/-/g, "")}`,
    run_id: run.runId,
    ts: new Date().toISOString(),
    kind: "note",
    message,
    payload,
  };
  const { error } = await db.from("agent_events").insert(event);
  if (error) return fail(error.message, 500);
  return reply({
    status: "logged",
    event_id: event.event_id,
    run_id: run.runId,
    thread_root: target.threadRoot,
    note: "Reply added to the thread (dashboard Activity page; `fantasy intel thread` for the agents). Nothing was executed.",
  });
}

/** Events in the last hour under runs named `<agent>-...` (each agent has its own budget). */
async function eventsLastHour(db: SupabaseClient, agent: AgentIdentity | "user"): Promise<number> {
  const since = new Date(Date.now() - 3600_000).toISOString();
  const { count } = await db
    .from("agent_events")
    .select("event_id", { count: "exact", head: true })
    .like("run_id", `${agent}-%`)
    .gte("ts", since);
  return count ?? 0;
}

/** Advisor events in the last hour (its own budget, separate from Muse's). */
export function advisorEventsLastHour(db: SupabaseClient): Promise<number> {
  return eventsLastHour(db, "advisor");
}

/** The user's dashboard replies in the last hour. */
export function userEventsLastHour(db: SupabaseClient): Promise<number> {
  return eventsLastHour(db, "user");
}

/** Number of muse events in the last hour (shared budget for reports and observed picks). */
export function museEventsLastHour(db: SupabaseClient): Promise<number> {
  return eventsLastHour(db, "muse");
}

export function newEventId(): string {
  return `muse-ev-${crypto.randomUUID().replace(/-/g, "")}`;
}

/** Snapshots stored in the last hour (combined with museEventsLastHour for the snapshot budget). */
export async function museSnapshotsLastHour(db: SupabaseClient): Promise<number> {
  const since = new Date(Date.now() - 3600_000).toISOString();
  const { count } = await db
    .from("league_snapshots")
    .select("snapshot_id", { count: "exact", head: true })
    .gte("ts", since);
  return count ?? 0;
}

/** Postgres "relation does not exist" (migration not applied yet) → a clear 503 instead of a 500. */
export function missingTable(err: { code?: string; message?: string } | null | undefined): boolean {
  const msg = err?.message ?? "";
  return !!err && (err.code === "42P01" || err.code === "PGRST205" || (/league_snapshots/.test(msg) && /does not exist|schema cache/.test(msg)));
}
