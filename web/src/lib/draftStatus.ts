import type { SupabaseClient } from "@supabase/supabase-js";

/** Draft lifecycle rule — mirrors `fantasy.draft.store.status_for` (tests on both sides):
 *  `complete` once every pick is on the board, `live` while some are (or after an undo from complete),
 *  otherwise unchanged (`pending`). */
export function draftStatusFor(current: string | null | undefined, nPicks: number, nTotal: number): string {
  if (nTotal > 0 && nPicks >= nTotal) return "complete";
  if (nPicks > 0 || current === "complete") return "live";
  return current || "pending";
}

/** Recount the board and write `drafts.status` (+ updated_at) — idempotent. Returns the status, or null
 *  when the count or the update failed (the pick itself is already stored; the dashboard also falls back
 *  to counting picks). */
export async function syncDraftStatus(
  db: SupabaseClient,
  draft: { draft_id: string; status: string | null; num_teams: number; num_rounds: number },
  now: string = new Date().toISOString(),
): Promise<string | null> {
  const { count, error } = await db
    .from("draft_picks")
    .select("overall_pick", { count: "exact", head: true })
    .eq("draft_id", draft.draft_id);
  if (error || count == null) return null;
  const status = draftStatusFor(draft.status, count, draft.num_teams * draft.num_rounds);
  const { error: uErr } = await db.from("drafts").update({ updated_at: now, status }).eq("draft_id", draft.draft_id);
  return uErr ? null : status;
}
