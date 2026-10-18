import { anonClient } from "@/lib/supabase";
import { activeDraft, fail, playerPool, reply, requireAgent } from "@/lib/agentApi";
import { normName, searchPlayers } from "@/lib/agentPure";
import type { DraftPick } from "@/lib/types";

export const dynamic = "force-dynamic";

/** GET /api/agent/players?q=&limit=&available=1 — player search with value/rank/adp and drafted status. */
export async function GET(req: Request) {
  const denied = await requireAgent(req);
  if (denied) return denied;
  const url = new URL(req.url);
  const q = (url.searchParams.get("q") ?? "").slice(0, 100);
  const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit")) || 25));
  const onlyAvailable = url.searchParams.get("available") === "1";
  const db = anonClient();
  try {
    const [{ pool, valuationRun }, { draft }] = await Promise.all([playerPool(db), activeDraft(db)]);
    let picks: DraftPick[] = [];
    if (draft) {
      const { data } = await db.from("draft_picks").select("*").eq("draft_id", draft.draft_id).order("overall_pick").limit(1000);
      picks = (data as DraftPick[]) ?? [];
    }
    const byId = new Map(picks.filter((p) => p.player_id).map((p) => [p.player_id!, p]));
    const byName = new Map(picks.filter((p) => p.player_name).map((p) => [normName(p.player_name), p]));
    const teamNames = (draft?.config?.team_names ?? {}) as Record<string, string>;
    let rows = searchPlayers(q, pool).map((p) => {
      const d = byId.get(p.player_id) ?? byName.get(normName(p.name)) ?? null;
      return {
        player_id: p.player_id,
        name: p.name,
        team: p.team,
        positions: p.positions,
        rank: p.rank,
        value: p.value == null ? null : Math.round(p.value * 100) / 100,
        adp: p.adp,
        status: p.status,
        injury_note: p.injury_note,
        drafted: d
          ? {
              overall: d.overall_pick,
              round: d.round,
              team_slot: d.team_slot,
              team_name: teamNames[String(d.team_slot)] ?? `Team ${d.team_slot}`,
              mine: draft ? d.team_slot === draft.my_slot : false,
            }
          : null,
      };
    });
    if (onlyAvailable) rows = rows.filter((r) => !r.drafted);
    return reply({
      query: q,
      draft_id: draft?.draft_id ?? null,
      valuation_run: valuationRun,
      total_matches: rows.length,
      players: rows.slice(0, limit),
      notes: "rank/value = our model's valuation (higher value = better; rank 1 = best). adp = average draft position.",
    });
  } catch (e) {
    return fail(`Read failed: ${String(e)}`, 500);
  }
}
