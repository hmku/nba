import { NextResponse } from "next/server";
import { cachedClient } from "@/lib/supabase";

export const dynamic = "force-dynamic";

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;
const NO_STORE = { "Cache-Control": "no-store" };

/**
 * GET /api/history/run?id=<run_id> → {run_id, params, inputs, summary} of one model run (History page expanders load
 * it on open instead of shipping every run's JSON in the HTML). Read-only, anon key: `model_runs` is publicly
 * readable (RLS "public read model_runs"), the same rows History rendered before. A run row is immutable for its
 * id (sim.ts relies on the same), so it is cached (Next Data Cache + browser/CDN).
 */
export async function GET(req: Request) {
  const id = new URL(req.url).searchParams.get("id") ?? "";
  if (!ID_RE.test(id)) return NextResponse.json({ error: "invalid id" }, { status: 400, headers: NO_STORE });
  const { data, error } = await cachedClient(3600)
    .from("model_runs")
    .select("run_id,params,inputs,summary")
    .eq("run_id", id)
    .maybeSingle();
  if (error) return NextResponse.json({ error: error.message }, { status: 502, headers: NO_STORE });
  if (!data) return NextResponse.json({ error: "not found" }, { status: 404, headers: NO_STORE });
  return NextResponse.json(data, { headers: { "Cache-Control": "public, max-age=3600, s-maxage=86400" } });
}
