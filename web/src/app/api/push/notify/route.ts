import { NextResponse } from "next/server";
import { bearerMatches } from "@/lib/agentPure";
import { notifyPending, vapid } from "@/lib/push";
import { serviceClient } from "@/lib/supabase-admin";

export const dynamic = "force-dynamic";

/**
 * Send Web Push for new pending approvals. Called by the Supabase Database Webhook on `approvals` INSERT with
 * `Authorization: Bearer $PUSH_NOTIFY_SECRET` (or by hand with curl). The request body is ignored: the route reads
 * the approvals table itself and notifies everything pending and not yet notified (idempotent; see lib/push.ts).
 */
export async function POST(req: Request) {
  const secret = process.env.PUSH_NOTIFY_SECRET;
  if (!secret) return NextResponse.json({ ok: false, error: "PUSH_NOTIFY_SECRET not configured" }, { status: 503 });
  if (!bearerMatches(req.headers.get("authorization"), secret)) {
    await new Promise((r) => setTimeout(r, 400));
    return NextResponse.json({ ok: false, error: "Unauthorized" }, { status: 401 });
  }
  if (!vapid()) return NextResponse.json({ ok: false, error: "VAPID_* env vars not configured" }, { status: 503 });
  const db = serviceClient();
  if (!db) return NextResponse.json({ ok: false, error: "SUPABASE_SERVICE_ROLE_KEY not configured" }, { status: 503 });
  const r = await notifyPending(db);
  return NextResponse.json({ ok: !r.error, ...r }, { status: r.error ? 500 : 200, headers: { "Cache-Control": "no-store" } });
}
