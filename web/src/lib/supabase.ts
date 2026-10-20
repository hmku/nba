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

/**
 * Server-only read client whose GETs go through Next's Data Cache for `revalidateSeconds`
 * (stale-while-revalidate, shared across Vercel instances). Use it only for reads whose result is
 * immutable for a given URL (e.g. model_runs rows fetched by run_id) or harmless when a few minutes
 * stale (player names / teams / positions). Anything that decides "what is latest" (which run, approvals,
 * activity, the live draft) must use `anonClient()`.
 */
export function cachedClient(revalidateSeconds: number): SupabaseClient {
  return createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: {
      fetch: (input, init) => fetch(input, { ...init, next: { revalidate: revalidateSeconds, tags: ["supabase"] } }),
    },
  });
}
