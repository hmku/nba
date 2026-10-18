import "server-only";
import { NextResponse } from "next/server";
import type { SupabaseClient } from "@supabase/supabase-js";
import { isAuthed } from "./auth";
import { serviceClient, writesConfigured } from "./supabase-admin";

/** Returns a service client if writes are configured and the caller is authed, else an error response. */
export async function requireWriter(): Promise<{ db: SupabaseClient } | { error: NextResponse }> {
  const cfg = writesConfigured();
  if (!cfg.ok) return { error: NextResponse.json({ error: `Writes disabled: ${cfg.reason}` }, { status: 503 }) };
  if (!(await isAuthed()))
    return { error: NextResponse.json({ error: "Not unlocked: enter the dashboard passphrase." }, { status: 401 }) };
  const db = serviceClient();
  if (!db) return { error: NextResponse.json({ error: "Service client unavailable" }, { status: 503 }) };
  return { db };
}
