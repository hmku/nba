import { NextResponse } from "next/server";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  advisorEventsLastHour,
  authorizeAgent,
  ensureAdvisorRun,
  ensureMuseRun,
  fail,
  museEventsLastHour,
  newEventId,
  postReply,
  readJson,
  reply,
  writer,
} from "@/lib/agentApi";
import {
  ADVISOR_REPORTS_PER_HOUR,
  advisorEventPayload,
  type AgentIdentity,
  isReplyBody,
  REPORTS_PER_HOUR,
  validateAdvisorSuggestion,
  validateReply,
  validateReport,
} from "@/lib/agentPure";

export const dynamic = "force-dynamic";

/** POST /api/agent/report
 *  - Muse (AGENT_API_TOKEN): {kind, message, details?, source?} → agent_events 'observation' under today's agent='muse' run.
 *  - Advisor (ADVISOR_API_TOKEN): {report_kind: "advisor_suggestion", category, title, summary, evidence, ...} →
 *    'observation' under today's agent='advisor' run, with source/reported_by='advisor' set by the server.
 *  - Either token: {report_kind: "reply", reply_to, stance, text, evidence?} → a thread reply ('note') under the
 *    caller's daily run. The author is the token's identity; reply_to must exist and must not be private chat content. */
export async function POST(req: Request) {
  const auth = await authorizeAgent(req);
  if (auth instanceof NextResponse) return auth;
  const body = await readJson(req);
  if (body instanceof NextResponse) return body;
  if (isReplyBody(body)) return replyReport(body, auth.identity);
  if (auth.identity === "advisor") return advisorReport(body);

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

async function replyReport(body: unknown, identity: AgentIdentity): Promise<NextResponse> {
  const v = validateReply(body);
  if (!v.ok) return fail("Invalid reply", 400, { errors: v.errors });
  const db = writer();
  if (db instanceof NextResponse) return db;
  if (identity === "advisor") {
    if ((await advisorEventsLastHour(db)) >= ADVISOR_REPORTS_PER_HOUR)
      return fail(`Rate limit: at most ${ADVISOR_REPORTS_PER_HOUR} advisor suggestions + replies per hour`, 429);
  } else if ((await museEventsLastHour(db)) >= REPORTS_PER_HOUR) {
    return fail(`Rate limit: at most ${REPORTS_PER_HOUR} reports/replies/picks per hour`, 429);
  }
  return postReply(db, identity, v.value);
}

async function advisorReport(body: unknown): Promise<NextResponse> {
  const v = validateAdvisorSuggestion(body);
  if (!v.ok) return fail("Invalid advisor_suggestion", 400, { errors: v.errors });
  const dbOrErr = writer();
  if (dbOrErr instanceof NextResponse) return dbOrErr;
  const db: SupabaseClient = dbOrErr;
  if ((await advisorEventsLastHour(db)) >= ADVISOR_REPORTS_PER_HOUR)
    return fail(`Rate limit: at most ${ADVISOR_REPORTS_PER_HOUR} advisor suggestions per hour`, 429);
  const { runId, error: rErr } = await ensureAdvisorRun(db);
  if (rErr) return fail(`Could not open the advisor run: ${rErr}`, 500);
  const { message, payload } = advisorEventPayload(v.value);
  const event = {
    event_id: `advisor-ev-${crypto.randomUUID().replace(/-/g, "")}`,
    run_id: runId,
    ts: new Date().toISOString(),
    kind: "observation",
    message,
    payload,
  };
  const { error } = await db.from("agent_events").insert(event);
  if (error) return fail(error.message, 500);
  return reply({
    status: "logged",
    event_id: event.event_id,
    run_id: runId,
    note: "Suggestion logged for the main Claude agent (fantasy intel latest → advisor_suggestions) and shown on the dashboard Activity page. Nothing was executed.",
  });
}
