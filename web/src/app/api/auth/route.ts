import { NextResponse } from "next/server";
import { SESSION_COOKIE, checkPassphrase, expectedToken, isAuthed } from "@/lib/auth";
import { writesConfigured } from "@/lib/supabase-admin";

export const dynamic = "force-dynamic";

export async function GET() {
  const cfg = writesConfigured();
  return NextResponse.json({ writesEnabled: cfg.ok, reason: cfg.reason ?? null, authed: await isAuthed() });
}

export async function POST(req: Request) {
  const cfg = writesConfigured();
  if (!cfg.ok) return NextResponse.json({ error: cfg.reason }, { status: 503 });
  let passphrase = "";
  try {
    const body = await req.json();
    passphrase = String(body?.passphrase ?? "");
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  if (!checkPassphrase(passphrase)) {
    await new Promise((r) => setTimeout(r, 600));
    return NextResponse.json({ error: "Wrong passphrase" }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, expectedToken()!, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
  return res;
}

export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
