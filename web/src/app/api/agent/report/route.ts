import { NextResponse } from "next/server";
import { ensureMuseRun, fail, museEventsLastHour, newEventId, readJson, reply, requireAgent, writer } from "@/lib/agentApi";
import { REPORTS_PER_HOUR, validateReport } from "@/lib/agentPure";

export const dynamic = "force-dynamic";

/** POST /api/agent/report {kind, message, details?, source?}
 *  Logs league intel as an agent_events 'observation' under today's agent='muse' run (created on first use). */
export async function POST(req: Request) {
  const denied = await requireAgent(req);
  if (denied) return denied;
  const body = await readJson(req);
  if (body instanceof NextResponse) return body;
  const v = validateReport(body);
  if (!v.ok) return fail(v.error, 400);
  const r = v.value;
  const db = writer();
  if (db instanceof NextResponse) return db;

  if ((await museEventsLastHour(db)) >= REPORTS_PER_HOUR)
    return fail(`Rate limit: at most ${REPORTS_PER_HOUR} reports/picks per hour`, 429);

  const { runId, error: rErr } = await ensureMuseRun(db);
  if (rErr) return fail(`Could not open the muse run: ${rErr}`, 500);
  const event = {
    event_id: newEventId(),
    run_id: runId,
    ts: new Date().toISOString(),
    kind: "observation",
    message: r.message,
    payload: {
      report_kind: r.kind,
      source: r.source,
      details: r.details,
      reported_by: "muse",
      ...(r.truncated ? { message_truncated: true } : {}),
    },
  };
  const { error } = await db.from("agent_events").insert(event);
  if (error) return fail(error.message, 500);
  return reply({
    status: "logged",
    event_id: event.event_id,
    run_id: runId,
    truncated: r.truncated,
    note: "Logged for the agents and shown on the dashboard Activity page. No action was taken.",
  });
}
