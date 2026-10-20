import { NextResponse } from "next/server";
import { requireWriter } from "@/lib/guard";
import { fail, postComment, readJson, userEventsLastHour } from "@/lib/agentApi";
import { USER_REPLIES_PER_HOUR, validateComment } from "@/lib/agentPure";

export const dynamic = "force-dynamic";

/** The user's comment on an Inbox decision card: starts a thread linked to the recommendation (or approval) by
 *  `payload.details.advice_id|approval_id`. Same passphrase-gated write path and hourly budget as replies; the author
 *  is always "user". Body: {advice_id | approval_id, stance, text}. Never decides anything (approvals do). */
export async function POST(req: Request) {
  const w = await requireWriter();
  if ("error" in w) return w.error;
  const body = await readJson(req);
  if (body instanceof NextResponse) return body;
  const v = validateComment(body);
  if (!v.ok) return fail("Invalid comment", 400, { errors: v.errors });
  if ((await userEventsLastHour(w.db)) >= USER_REPLIES_PER_HOUR)
    return fail(`Rate limit: at most ${USER_REPLIES_PER_HOUR} replies and comments per hour`, 429);
  return postComment(w.db, v.value);
}
