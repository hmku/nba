"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useWriteStatus } from "@/components/client";

export function DecideButtons({ id }: { id: string }) {
  const ws = useWriteStatus();
  const router = useRouter();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const can = ws.writesEnabled && ws.authed;

  const decide = async (decision: "approved" | "rejected") => {
    if (!window.confirm(`${decision === "approved" ? "Approve" : "Reject"} ${id}?`)) return;
    setBusy(true);
    setErr(null);
    const r = await fetch("/api/approvals", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ approval_id: id, decision, note: note || null }),
    });
    const j = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) setErr(j.error || `HTTP ${r.status}`);
    else router.refresh();
  };

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="note (optional)"
          aria-label="Approval note (optional)"
          disabled={!can || busy}
          className="min-w-0 flex-1 rounded border border-line bg-panel-2 px-2 py-1.5 text-sm outline-none focus:border-accent disabled:opacity-50 sm:max-w-xs"
        />
        <button
          onClick={() => decide("approved")}
          disabled={!can || busy}
          className="rounded-lg bg-good px-4 py-2 text-sm font-semibold text-bg disabled:opacity-40"
        >
          Approve
        </button>
        <button
          onClick={() => decide("rejected")}
          disabled={!can || busy}
          className="rounded-lg border border-bad px-4 py-2 text-sm font-semibold text-bad disabled:opacity-40"
        >
          Reject
        </button>
      </div>
      {ws.loaded && !ws.writesEnabled && <div className="mt-1 text-xs text-warn">Writes disabled: {ws.reason}</div>}
      {ws.loaded && ws.writesEnabled && !ws.authed && <div className="mt-1 text-xs text-muted">Tap “unlock” (top right) and enter the passphrase to decide.</div>}
      {err && <div className="mt-1 text-xs text-bad">{err}</div>}
    </div>
  );
}
