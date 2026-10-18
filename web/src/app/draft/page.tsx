import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { defaultDraftId, loadDraftLive, loadDrafts, loadValues } from "@/lib/data";
import DraftClient from "./DraftClient";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Draft" };

export default async function DraftPage({ searchParams }: { searchParams: Promise<{ id?: string }> }) {
  const { id } = await searchParams;
  const db = anonClient();
  const { drafts, error } = await loadDrafts(db);
  const draftId = id && drafts.some((d) => d.draft_id === id) ? id : defaultDraftId(drafts);
  const [live, values] = await Promise.all([
    draftId ? loadDraftLive(db, draftId) : Promise.resolve(null),
    loadValues(db),
  ]);
  return (
    <DraftClient
      initialDrafts={drafts}
      initialDraftId={draftId}
      initialLive={live}
      initialValues={values}
      loadError={error ?? null}
    />
  );
}
