import { NextResponse } from "next/server";
import { fail, missingTable, reply, requireAgent, writer } from "@/lib/agentApi";
import { SNAPSHOT_KINDS, parseSince } from "@/lib/agentPure";

export const dynamic = "force-dynamic";

const META = "snapshot_id,ts,reported_by,kind,week,observed_at,source_url,payload_sha256,summary,last_seen_at";

/** GET /api/agent/snapshots?kind=&since=&limit=&payload=1 — what has already been sent (newest seen first). */
export async function GET(req: Request) {
  const denied = await requireAgent(req);
  if (denied) return denied;
  const url = new URL(req.url);
  const kind = url.searchParams.get("kind");
  if (kind && !(SNAPSHOT_KINDS as readonly string[]).includes(kind))
    return fail(`kind must be one of: ${SNAPSHOT_KINDS.join(", ")}`, 400);
  let since: string | null;
  try {
    since = parseSince(url.searchParams.get("since"));
  } catch (e) {
    return fail(String((e as Error).message), 400);
  }
  const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit")) || 20));
  const withPayload = url.searchParams.get("payload") === "1";
  // league_snapshots is private (no anon read since 20261019120000_private_league_intel.sql): service key
  const db = writer();
  if (db instanceof NextResponse) return db;
  let q = db
    .from("league_snapshots")
    .select(withPayload ? `${META},payload` : META)
    .order("last_seen_at", { ascending: false, nullsFirst: false })
    .order("observed_at", { ascending: false, nullsFirst: false })
    .limit(limit);
  if (kind) q = q.eq("kind", kind);
  if (since) q = q.or(`observed_at.gte.${since},last_seen_at.gte.${since}`);
  const { data, error } = await q;
  if (error) return missingTable(error) ? fail("league_snapshots table is not created yet", 503) : fail(error.message, 500);
  return reply({
    kind,
    since,
    count: data?.length ?? 0,
    snapshots: data ?? [],
    notes: "Newest first by last_seen_at (bumped when an identical snapshot is re-posted). Add payload=1 for the payloads.",
  });
}
