import { NextResponse } from "next/server";
import { requireWriter } from "@/lib/guard";

export const dynamic = "force-dynamic";

/** Decide an approval. Body: { approval_id, decision: 'approved'|'rejected', note? } */
export async function POST(req: Request) {
  const w = await requireWriter();
  if ("error" in w) return w.error;
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  const id = String(body.approval_id ?? "");
  const decision = String(body.decision ?? "");
  if (!id || !["approved", "rejected"].includes(decision))
    return NextResponse.json({ error: "approval_id and decision (approved|rejected) required" }, { status: 400 });
  const { data, error } = await w.db
    .from("approvals")
    .update({
      status: decision,
      decided_by: "dashboard",
      note: body.note ? String(body.note) : null,
      decided_at: new Date().toISOString(),
    })
    .eq("approval_id", id)
    .eq("status", "pending")
    .select();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  if (!data?.length) return NextResponse.json({ error: "Approval not found or no longer pending" }, { status: 409 });
  return NextResponse.json({ ok: true, approval: data[0] });
}
