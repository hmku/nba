import "server-only";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { SUPABASE_URL } from "./env";

/** Service-role client for writes. Returns null if SUPABASE_SERVICE_ROLE_KEY is not configured. */
export function serviceClient(): SupabaseClient | null {
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!key) return null;
  return createClient(SUPABASE_URL, key, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { fetch: (input, init) => fetch(input, { ...init, cache: "no-store" }) },
  });
}

export function writesConfigured(): { ok: boolean; reason?: string } {
  if (!process.env.SUPABASE_SERVICE_ROLE_KEY)
    return { ok: false, reason: "SUPABASE_SERVICE_ROLE_KEY is not set on the server (Vercel env)." };
  if (!process.env.DASHBOARD_PASSPHRASE)
    return { ok: false, reason: "DASHBOARD_PASSPHRASE is not set on the server (Vercel env)." };
  return { ok: true };
}
