import type { SupabaseClient } from "@supabase/supabase-js";
import type {
  AgentEvent,
  AgentRun,
  Decision,
  Draft,
  DraftPick,
  DraftRecommendation,
  ModelRun,
  Player,
  PlayerValue,
  Projection,
} from "./types";

export interface DraftLive {
  picks: DraftPick[];
  rec: DraftRecommendation | null;
  recCount: number;
  agentRuns: AgentRun[];
  agentEvents: AgentEvent[];
  decisions: Decision[];
  fetchedAt: string;
  errors: string[];
}

export interface ValuesBundle {
  run: ModelRun | null;
  values: PlayerValue[];
  players: Record<string, Player>;
  errors: string[];
}

export async function loadDrafts(db: SupabaseClient): Promise<{ drafts: Draft[]; error?: string }> {
  const { data, error } = await db.from("drafts").select("*").order("created_at", { ascending: false }).limit(100);
  return { drafts: (data as Draft[]) ?? [], error: error?.message };
}

export function defaultDraftId(drafts: Draft[]): string | null {
  const real = drafts.find((d) => !d.is_mock);
  return (real ?? drafts[0])?.draft_id ?? null;
}

export async function loadDraftLive(db: SupabaseClient, draftId: string): Promise<DraftLive> {
  const errors: string[] = [];
  const [picksR, recR, runsR] = await Promise.all([
    db.from("draft_picks").select("*").eq("draft_id", draftId).order("overall_pick").limit(1000),
    db
      .from("draft_recommendations")
      .select("*", { count: "exact" })
      .eq("draft_id", draftId)
      .order("as_of_pick", { ascending: false })
      .order("created_at", { ascending: false })
      .limit(1),
    db
      .from("agent_runs")
      .select("*")
      .ilike("task", `%${draftId}%`)
      .order("started_at", { ascending: false })
      .limit(5),
  ]);
  for (const r of [picksR, recR, runsR]) if (r.error) errors.push(r.error.message);
  const agentRuns = (runsR.data as AgentRun[]) ?? [];
  let agentEvents: AgentEvent[] = [];
  let decisions: Decision[] = [];
  if (agentRuns.length) {
    const ids = agentRuns.map((r) => r.run_id);
    const [evR, decR] = await Promise.all([
      db.from("agent_events").select("*").in("run_id", ids).order("ts", { ascending: false }).limit(25),
      db.from("decisions").select("*").in("run_id", ids).order("created_at", { ascending: false }).limit(10),
    ]);
    if (evR.error) errors.push(evR.error.message);
    if (decR.error) errors.push(decR.error.message);
    agentEvents = (evR.data as AgentEvent[]) ?? [];
    decisions = (decR.data as Decision[]) ?? [];
  }
  return {
    picks: (picksR.data as DraftPick[]) ?? [],
    rec: ((recR.data as DraftRecommendation[]) ?? [])[0] ?? null,
    recCount: recR.count ?? 0,
    agentRuns,
    agentEvents,
    decisions,
    fetchedAt: new Date().toISOString(),
    errors,
  };
}

export async function latestRun(db: SupabaseClient, kind: string): Promise<ModelRun | null> {
  const { data } = await db
    .from("model_runs")
    .select("*")
    .eq("kind", kind)
    .order("created_at", { ascending: false })
    .limit(1);
  return ((data as ModelRun[]) ?? [])[0] ?? null;
}

export async function loadPlayers(db: SupabaseClient): Promise<Record<string, Player>> {
  const out: Record<string, Player> = {};
  for (let from = 0; from < 5000; from += 1000) {
    const { data, error } = await db
      .from("players")
      .select("player_id,name,team,positions,status,injury_note,age")
      .range(from, from + 999);
    if (error || !data) break;
    for (const p of data as Player[]) out[p.player_id] = p;
    if (data.length < 1000) break;
  }
  return out;
}

async function pagedByRun<T>(db: SupabaseClient, table: string, runId: string, order: string): Promise<T[]> {
  const rows: T[] = [];
  for (let from = 0; from < 5000; from += 1000) {
    const { data, error } = await db
      .from(table)
      .select("*")
      .eq("run_id", runId)
      .order(order, { ascending: true, nullsFirst: false })
      .range(from, from + 999);
    if (error) throw new Error(error.message);
    rows.push(...((data as T[]) ?? []));
    if (!data || data.length < 1000) break;
  }
  return rows;
}

export async function loadValues(db: SupabaseClient, runId?: string): Promise<ValuesBundle> {
  const errors: string[] = [];
  let run: ModelRun | null = null;
  if (runId) {
    const { data } = await db.from("model_runs").select("*").eq("run_id", runId).maybeSingle();
    run = (data as ModelRun) ?? null;
  }
  let values: PlayerValue[] = [];
  // Candidate runs: the requested one, else the latest few valuation runs; use the first that has rows
  // (a run row can exist before its player_values finish loading).
  let candidates: ModelRun[] = run ? [run] : [];
  if (!run) {
    const { data } = await db
      .from("model_runs")
      .select("*")
      .eq("kind", "valuation")
      .order("created_at", { ascending: false })
      .limit(5);
    candidates = (data as ModelRun[]) ?? [];
  }
  run = candidates[0] ?? null;
  for (const c of candidates) {
    try {
      const v = await pagedByRun<PlayerValue>(db, "player_values", c.run_id, "rank");
      if (v.length) {
        run = c;
        values = v;
        break;
      }
    } catch (e) {
      errors.push(String(e));
      break;
    }
  }
  if (!run) return { run: null, values: [], players: {}, errors };
  const players = await loadPlayers(db);
  return { run, values, players, errors };
}

export async function loadProjections(db: SupabaseClient): Promise<{ run: ModelRun | null; rows: Projection[] }> {
  const run = await latestRun(db, "projections");
  if (!run) return { run: null, rows: [] };
  const rows = await pagedByRun<Projection>(db, "projections", run.run_id, "player_id");
  return { run, rows };
}

/** Shrink a values bundle for serialization to the client (only referenced players, minimal extra). */
export function slimValues(v: ValuesBundle): ValuesBundle {
  const players: Record<string, Player> = {};
  const values = v.values.map((pv) => {
    const p = v.players[pv.player_id];
    if (p) players[pv.player_id] = { ...p, injury_note: null };
    const ex = (pv.extra ?? {}) as Record<string, unknown>;
    return { ...pv, extra: p ? null : { name: ex.name ?? null, team: ex.team ?? null } };
  });
  return { ...v, values, players };
}
