import { NextResponse } from "next/server";
import { requireWriter } from "@/lib/guard";
import { sendTo, vapid } from "@/lib/push";

export const dynamic = "force-dynamic";

/** Send a test notification to this device. Body: { endpoint } (must be a stored subscription). Passphrase-gated. */
export async function POST(req: Request) {
  const w = await requireWriter();
  if ("error" in w) return w.error;
  if (!vapid()) return NextResponse.json({ error: "Push not configured: VAPID_* env vars are not set" }, { status: 503 });
  let endpoint = "";
  try {
    endpoint = String((await req.json())?.endpoint ?? "");
  } catch {
    /* empty endpoint → 404 below */
  }
  const { data, error } = await w.db.from("push_subscriptions").select("endpoint,p256dh,auth").eq("endpoint", endpoint);
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  if (!data?.length) return NextResponse.json({ error: "This device is not subscribed" }, { status: 404 });
  const r = await sendTo(w.db, data, {
    title: "Fantasy Desk",
    body: "Test: new approvals will look like this.",
    url: "/inbox",
    tag: "test",
  });
  return NextResponse.json({ ok: r.sent > 0, ...r }, { status: r.sent > 0 ? 200 : 502 });
}
