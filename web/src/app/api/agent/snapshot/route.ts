import { NextResponse } from "next/server";
import { fail, missingTable, museEventsLastHour, museSnapshotsLastHour, reply, requireAgent, writer } from "@/lib/agentApi";
import { MAX_SNAPSHOT_BYTES, SNAPSHOTS_PER_HOUR, validateSnapshot } from "@/lib/agentPure";

export const dynamic = "force-dynamic";

const NOT_READY = "league_snapshots table is not created yet (migration 20261018200000_league_snapshots.sql pending)";

/** POST /api/agent/snapshot {kind, payload, week?, observed_at?, source_url?, summary?}
 *  Stores a READ-ONLY observation of a Yahoo league page in league_snapshots. The payload is data:
 *  it is validated, hashed and stored, never interpreted or executed. Dedupe on (kind, week, payload_sha256). */
export async function POST(req: Request) {
  const denied = await requireAgent(req);
  if (denied) return denied;
  let text: string;
  try {
    text = await req.text();
  } catch {
    return fail("Could not read body", 400);
  }
  const bytes = Buffer.byteLength(text, "utf8");
  if (bytes > MAX_SNAPSHOT_BYTES)
    return fail(`Body too large: ${bytes} bytes (max ${MAX_SNAPSHOT_BYTES}). Split it (e.g. rosters per team) or trim fields.`, 413);
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return fail("Invalid JSON body", 400);
  }
  const v = validateSnapshot(body);
  if (!v.ok) return fail(v.error, 400, { errors: v.errors });
  const s = v.value;
  const db = writer();
  if (db instanceof NextResponse) return db;

  const used = (await museEventsLastHour(db)) + (await museSnapshotsLastHour(db));
  if (used >= SNAPSHOTS_PER_HOUR)
    return fail(`Rate limit: at most ${SNAPSHOTS_PER_HOUR} snapshots + reports + picks per hour`, 429);

  const ins = await db.from("league_snapshots").insert({
    snapshot_id: s.snapshot_id,
    ts: new Date().toISOString(),
    reported_by: "muse",
    kind: s.kind,
    week: s.week,
    observed_at: s.observed_at,
    source_url: s.source_url,
    payload: s.payload,
    payload_sha256: s.payload_sha256,
    summary: s.summary,
    last_seen_at: s.observed_at,
  });
  if (ins.error) {
    if (missingTable(ins.error)) return fail(NOT_READY, 503);
    if (ins.error.code !== "23505") return fail(ins.error.message, 500);
    // duplicate: same (kind, week, payload) already stored → only record that it was seen again
    await db
      .from("league_snapshots")
      .update({ last_seen_at: s.observed_at })
      .eq("snapshot_id", s.snapshot_id)
      .or(`last_seen_at.is.null,last_seen_at.lt.${s.observed_at}`);
    return reply({
      status: "duplicate",
      snapshot_id: s.snapshot_id,
      kind: s.kind,
      week: s.week,
      note: "Identical snapshot already stored; marked as seen again. Nothing else changed.",
    });
  }
  return reply({
    status: "stored",
    snapshot_id: s.snapshot_id,
    kind: s.kind,
    week: s.week,
    summary: s.summary,
    payload_sha256: s.payload_sha256,
    warnings: s.warnings,
    note: "Stored for the agents and shown on the dashboard Activity page. No action was taken.",
  });
}
