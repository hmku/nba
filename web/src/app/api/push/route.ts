import { NextResponse } from "next/server";
import { requireWriter } from "@/lib/guard";
import { vapid } from "@/lib/push";
import { parseSubscription } from "@/lib/pushPure";

export const dynamic = "force-dynamic";

/** Push config for the "Enable notifications" control: whether sending is configured, and the VAPID public key. */
export async function GET() {
  const v = vapid();
  return NextResponse.json({ configured: !!v, publicKey: v?.publicKey ?? null }, { headers: { "Cache-Control": "no-store" } });
}

async function body(req: Request): Promise<Record<string, unknown> | null> {
  try {
    const b = await req.json();
    return b && typeof b === "object" ? (b as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** Store this device's subscription. Body: { subscription: PushSubscription.toJSON() }. Passphrase-gated. */
export async function POST(req: Request) {
  const w = await requireWriter();
  if ("error" in w) return w.error;
  if (!vapid()) return NextResponse.json({ error: "Push not configured: VAPID_* env vars are not set" }, { status: 503 });
  const sub = parseSubscription((await body(req))?.subscription);
  if (!sub) return NextResponse.json({ error: "Invalid push subscription" }, { status: 400 });
  const { error } = await w.db.from("push_subscriptions").upsert(
    {
      ...sub,
      user_agent: (req.headers.get("user-agent") ?? "").slice(0, 300) || null,
      created_at: new Date().toISOString(),
      last_error: null,
    },
    { onConflict: "endpoint" },
  );
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ ok: true });
}

/** Forget this device. Body: { endpoint }. Passphrase-gated. */
export async function DELETE(req: Request) {
  const w = await requireWriter();
  if ("error" in w) return w.error;
  const endpoint = String((await body(req))?.endpoint ?? "");
  if (!endpoint) return NextResponse.json({ error: "endpoint required" }, { status: 400 });
  const { error } = await w.db.from("push_subscriptions").delete().eq("endpoint", endpoint);
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ ok: true });
}
