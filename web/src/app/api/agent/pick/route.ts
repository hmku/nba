import { NextResponse } from "next/server";
import {
  activeDraft,
  ensureMuseRun,
  fail,
  museEventsLastHour,
  newEventId,
  playerPool,
  readJson,
  reply,
  requireAgent,
  writer,
} from "@/lib/agentApi";
import { REPORTS_PER_HOUR, defaultOverall, resolvePlayer, slugify, validatePick } from "@/lib/agentPure";
import { customRoundsOf, pickInfo } from "@/lib/draftOrder";
import type { DraftPick } from "@/lib/types";

export const dynamic = "force-dynamic";

/** POST /api/agent/pick {overall?, player, note?, allow_unknown?}
 *  Records an OBSERVED pick on the active (non-mock) draft board with source='external_agent'.
 *  Semantics mirror `fantasy draft pick` / the manual pick route: same player at the same pick is a no-op,
 *  any other conflict is 409 (no overwrite from this API), ambiguous/unknown names are 422 with candidates.
 *  Never touches Yahoo. */
export async function POST(req: Request) {
  const denied = await requireAgent(req);
  if (denied) return denied;
  const body = await readJson(req);
  if (body instanceof NextResponse) return body;
  const v = validatePick(body);
  if (!v.ok) return fail(v.error, 400);
  const input = v.value;
  const db = writer();
  if (db instanceof NextResponse) return db;

  if ((await museEventsLastHour(db)) >= REPORTS_PER_HOUR)
    return fail(`Rate limit: at most ${REPORTS_PER_HOUR} reports/picks per hour`, 429);

  const { draft, error: dErr } = await activeDraft(db);
  if (dErr) return fail(dErr, 500);
  if (!draft) return fail("No active (non-mock) draft exists yet", 404);
  const total = draft.num_teams * draft.num_rounds;

  const { data: pData, error: pErr } = await db
    .from("draft_picks")
    .select("*")
    .eq("draft_id", draft.draft_id)
    .order("overall_pick")
    .limit(1000);
  if (pErr) return fail(pErr.message, 500);
  const picks = (pData as DraftPick[]) ?? [];
  const overall = input.overall ?? defaultOverall(picks.map((p) => p.overall_pick), total);
  if (overall == null) return fail("Draft board is full (all picks recorded)", 409);
  if (overall > total) return fail(`Pick number beyond end of draft (${total} picks)`, 400);

  // resolve the player
  const { pool } = await playerPool(db);
  const res = resolvePlayer(input.player, pool);
  let playerId: string;
  let playerName: string;
  if (res.status === "ok") {
    playerId = res.player.player_id;
    playerName = res.player.name;
  } else if (input.allow_unknown && res.status === "not_found") {
    playerId = `unknown:${slugify(input.player)}`;
    playerName = input.player;
  } else {
    return fail(
      res.status === "ambiguous"
        ? `Ambiguous player '${input.player}': resend with one of the candidates' player_id`
        : `Unknown player '${input.player}': resend with a candidate's player_id, or allow_unknown:true if he is truly not listed`,
      422,
      { candidates: res.candidates.map((c) => ({ player_id: c.player_id, name: c.name, team: c.team, positions: c.positions })) },
    );
  }

  // conflicts (no overwrite from this API)
  const atPick = picks.find((p) => p.overall_pick === overall);
  if (atPick) {
    if (atPick.player_id === playerId) return reply({ status: "unchanged", draft_id: draft.draft_id, pick: atPick });
    return fail(`Conflict: pick ${overall} already recorded as ${atPick.player_name ?? atPick.player_id} (source ${atPick.source})`, 409, {
      existing: atPick,
    });
  }
  const dup = picks.find((p) => p.player_id === playerId);
  if (dup) return fail(`Conflict: ${playerName} is already recorded at pick ${dup.overall_pick}`, 409, { existing: dup });

  const info = pickInfo(overall, draft.num_teams, draft.order_type, customRoundsOf(draft.config, draft.num_teams, draft.num_rounds), draft.num_rounds);
  const now = new Date().toISOString();
  const row = {
    draft_id: draft.draft_id,
    overall_pick: overall,
    round: info.round,
    team_slot: info.slot,
    player_id: playerId,
    player_name: playerName,
    source: "external_agent",
    created_at: now,
  };
  // plain insert: if another writer recorded this pick in the meantime the PK makes it fail → 409
  const { error } = await db.from("draft_picks").insert(row);
  if (error) {
    if (error.code === "23505") return fail(`Conflict: pick ${overall} was recorded concurrently; re-read state`, 409);
    return fail(error.message, 500);
  }
  await db
    .from("drafts")
    .update({ updated_at: now, ...(draft.status === "pending" ? { status: "live" } : {}) })
    .eq("draft_id", draft.draft_id);

  const { runId, error: rErr } = await ensureMuseRun(db);
  if (!rErr)
    await db.from("agent_events").insert({
      event_id: newEventId(),
      run_id: runId,
      ts: now,
      kind: "observation",
      message: `Observed pick #${overall}: ${playerName} (team slot ${info.slot})${input.note ? ` — ${input.note}` : ""}`,
      payload: { report_kind: "observed_pick", source: "external_agent", overall, player_id: playerId, player_name: playerName, query: input.player, note: input.note },
    });

  return reply({
    status: "recorded",
    draft_id: draft.draft_id,
    pick: row,
    note: "Recorded on the dashboard board only. Nothing was done on Yahoo.",
  });
}
