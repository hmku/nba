import { NextResponse } from "next/server";
import { requireWriter } from "@/lib/guard";
import { pickInfoForDraft } from "@/lib/draftOrder";
import { syncDraftStatus } from "@/lib/draftStatus";

export const dynamic = "force-dynamic";

/** Record a pick manually. Body: { draft_id, overall_pick, player_id, player_name, overwrite? } */
export async function POST(req: Request) {
  const w = await requireWriter();
  if ("error" in w) return w.error;
  const db = w.db;
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  const draftId = String(body.draft_id ?? "");
  const overall = Number(body.overall_pick);
  const playerId = body.player_id ? String(body.player_id) : null;
  const playerName = body.player_name ? String(body.player_name) : null;
  if (!draftId || !Number.isInteger(overall) || overall < 1 || (!playerId && !playerName))
    return NextResponse.json({ error: "draft_id, overall_pick and player are required" }, { status: 400 });

  const { data: draft, error: dErr } = await db.from("drafts").select("*").eq("draft_id", draftId).maybeSingle();
  if (dErr) return NextResponse.json({ error: dErr.message }, { status: 500 });
  if (!draft) return NextResponse.json({ error: "Draft not found" }, { status: 404 });
  if (overall > draft.num_teams * draft.num_rounds)
    return NextResponse.json({ error: "Pick number beyond end of draft" }, { status: 400 });

  const { data: existing } = await db
    .from("draft_picks")
    .select("overall_pick,player_id,player_name")
    .eq("draft_id", draftId)
    .or(`overall_pick.eq.${overall}${playerId ? `,player_id.eq.${playerId}` : ""}`);
  if (existing && existing.length && !body.overwrite) {
    const e = existing[0];
    return NextResponse.json(
      { error: `Conflict: pick ${e.overall_pick} already has ${e.player_name ?? e.player_id}` },
      { status: 409 },
    );
  }

  const { round, slot } = pickInfoForDraft(draft, overall);
  const row = {
    draft_id: draftId,
    overall_pick: overall,
    round,
    team_slot: slot,
    player_id: playerId,
    player_name: playerName,
    source: "manual",
    created_at: new Date().toISOString(),
  };
  const { error } = await db.from("draft_picks").upsert(row, { onConflict: "draft_id,overall_pick" });
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  await syncDraftStatus(db, draft); // pending -> live; complete when the board is full
  return NextResponse.json({ ok: true, pick: row });
}

/** Undo a manually entered pick. Query: ?draft_id=...&overall_pick=... (only source='manual' rows). */
export async function DELETE(req: Request) {
  const w = await requireWriter();
  if ("error" in w) return w.error;
  const url = new URL(req.url);
  const draftId = url.searchParams.get("draft_id");
  const overall = Number(url.searchParams.get("overall_pick"));
  if (!draftId || !Number.isInteger(overall))
    return NextResponse.json({ error: "draft_id and overall_pick required" }, { status: 400 });
  const { data, error } = await w.db
    .from("draft_picks")
    .delete()
    .eq("draft_id", draftId)
    .eq("overall_pick", overall)
    .eq("source", "manual")
    .select();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  if (!data?.length) return NextResponse.json({ error: "No manual pick at that number" }, { status: 404 });
  const { data: draft } = await w.db.from("drafts").select("*").eq("draft_id", draftId).maybeSingle();
  if (draft) await syncDraftStatus(w.db, draft); // complete -> live after an undo
  return NextResponse.json({ ok: true });
}
