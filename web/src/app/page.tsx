import { redirect } from "next/navigation";
import { anonClient } from "@/lib/supabase";
import { defaultDraftId, loadDrafts } from "@/lib/data";

export const dynamic = "force-dynamic";

/**
 * Landing: before/during the draft → Draft room; once the (real) draft is complete → Matchup.
 * (The old "Team" attention page moved: approvals → Activity tab badge, data freshness → More › History.)
 */
export default async function Home() {
  const db = anonClient();
  const { drafts } = await loadDrafts(db);
  const id = defaultDraftId(drafts);
  const draft = drafts.find((d) => d.draft_id === id);
  let complete = false;
  if (draft && !draft.is_mock) {
    if (draft.status === "complete") complete = true;
    else {
      const { count } = await db.from("draft_picks").select("overall_pick", { count: "exact", head: true }).eq("draft_id", draft.draft_id);
      complete = (count ?? 0) >= draft.num_teams * draft.num_rounds;
    }
  }
  redirect(complete ? "/matchup" : "/draft");
}
