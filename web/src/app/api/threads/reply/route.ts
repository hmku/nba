import { NextResponse } from "next/server";
import { requireWriter } from "@/lib/guard";
import { fail, postReply, readJson, userEventsLastHour } from "@/lib/agentApi";
import { USER_REPLIES_PER_HOUR, validateReply } from "@/lib/agentPure";

export const dynamic = "force-dynamic";

/** The user's reply to an agent update (Activity page). Same passphrase-gated write path as /api/approvals; the author
 *  is always "user" (never taken from the body). Body: {reply_to, stance, text}. */
export async function POST(req: Request) {
  const w = await requireWriter();
  if ("error" in w) return w.error;
  const body = await readJson(req);
  if (body instanceof NextResponse) return body;
  const v = validateReply(body);
  if (!v.ok) return fail("Invalid reply", 400, { errors: v.errors });
  if ((await userEventsLastHour(w.db)) >= USER_REPLIES_PER_HOUR)
    return fail(`Rate limit: at most ${USER_REPLIES_PER_HOUR} replies per hour`, 429);
  return postReply(w.db, "user", v.value);
}
