import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { SUPABASE_ANON_KEY, SUPABASE_URL } from "./env";

let browserClient: SupabaseClient | null = null;

/** Read-only client (publishable/anon key). Safe on server and in the browser. */
export function anonClient(): SupabaseClient {
  if (typeof window !== "undefined") {
    if (!browserClient) {
      browserClient = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
        auth: { persistSession: false, autoRefreshToken: false },
      });
    }
    return browserClient;
  }
  return createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { fetch: (input, init) => fetch(input, { ...init, cache: "no-store" }) },
  });
}
