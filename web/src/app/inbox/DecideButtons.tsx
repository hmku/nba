"use client";

import { useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useWriteStatus } from "@/components/client";

/** Approve / Reject (+ an optional note behind "add note"). `children` (e.g. the expiry) sits at the end of the row. */
export function DecideButtons({ id, children }: { id: string; children?: ReactNode }) {
  const ws = useWriteStatus();
  const router = useRouter();
  const [noteOpen, setNoteOpen] = useState(false);
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
        <button
          onClick={() => decide("approved")}
          disabled={!can || busy}
          className="rounded-lg bg-good px-4 py-1.5 text-sm font-semibold text-bg disabled:opacity-40"
        >
          Approve
        </button>
        <button
          onClick={() => decide("rejected")}
          disabled={!can || busy}
          className="rounded-lg border border-bad px-4 py-1.5 text-sm font-semibold text-bad disabled:opacity-40"
        >
          Reject
        </button>
        {can && !noteOpen && (
          <button onClick={() => setNoteOpen(true)} className="text-xs text-accent">
            add note
          </button>
        )}
        <span className="ml-auto text-xs text-muted">{children}</span>
      </div>
      {noteOpen && (
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="note (optional)"
          aria-label="Approval note (optional)"
          disabled={busy}
          autoFocus
          className="mt-1.5 w-full rounded border border-line bg-panel-2 px-2 py-1.5 text-sm outline-none focus:border-accent disabled:opacity-50 sm:max-w-sm"
        />
      )}
      {ws.loaded && !ws.writesEnabled && <div className="mt-1 text-xs text-warn">Writes disabled: {ws.reason}</div>}
      {ws.loaded && ws.writesEnabled && !ws.authed && <div className="mt-1 text-xs text-muted">Unlock (top right) to decide.</div>}
      {err && <div className="mt-1 text-xs text-bad">{err}</div>}
    </div>
  );
}
