"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useWriteStatus } from "@/components/client";

const STANCES = ["agree", "disagree", "comment", "question"] as const;

/** The user's reply to an update. Only rendered once the dashboard is unlocked (same passphrase-gated write path as
 *  approvals); posts to /api/threads/reply, where the server sets the author to "user". */
export function ReplyBox({ replyTo }: { replyTo: string }) {
  const ws = useWriteStatus();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [stance, setStance] = useState<(typeof STANCES)[number]>("comment");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!ws.loaded || !ws.writesEnabled || !ws.authed) return null;

  if (!open)
    return (
      <button onClick={() => setOpen(true)} className="text-xs text-accent hover:underline">
        Reply
      </button>
    );

  const send = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setErr(null);
    const r = await fetch("/api/threads/reply", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reply_to: replyTo, stance, text }),
    });
    const j = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) setErr(j.errors?.join("; ") || j.error || `HTTP ${r.status}`);
    else {
      setText("");
      setOpen(false);
      router.refresh();
    }
  };

  return (
    <div className="mt-2 w-full space-y-1.5">
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Stance">
        {STANCES.map((s) => (
          <button
            key={s}
            role="radio"
            aria-checked={stance === s}
            onClick={() => setStance(s)}
            className={`rounded-full border px-2.5 py-0.5 text-xs ${stance === s ? "border-accent bg-accent/10 text-accent" : "border-line text-muted"}`}
          >
            {s}
          </button>
        ))}
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={2000}
        rows={3}
        placeholder="Your reply (public on the dashboard: no private chat content)"
        aria-label="Reply text"
        disabled={busy}
        className="w-full rounded border border-line bg-panel-2 px-2 py-1.5 text-sm outline-none focus:border-accent disabled:opacity-50"
      />
      <div className="flex items-center gap-2">
        <button
          onClick={send}
          disabled={busy || !text.trim()}
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-semibold text-bg disabled:opacity-40"
        >
          Post reply
        </button>
        <button onClick={() => setOpen(false)} disabled={busy} className="text-xs text-muted">
          Cancel
        </button>
        {err && <span className="text-xs text-bad">{err}</span>}
      </div>
    </div>
  );
}
