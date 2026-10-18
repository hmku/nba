// Public Supabase config. The publishable key is not secret (RLS = read-only for anon),
// so we fall back to the project's values if env vars are missing.
export const SUPABASE_URL =
  process.env.NEXT_PUBLIC_SUPABASE_URL || "https://bdzpkdkhlzjzddkptjex.supabase.co";
export const SUPABASE_ANON_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "sb_publishable_-v2T45D-2E361ksNm7OM1Q_BsgYRebd";
