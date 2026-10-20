import { useEffect, useMemo, useState } from "react";
import type { Draft, DraftPick } from "@/lib/types";
import { pickInfoForDraft } from "@/lib/draftOrder";
import { normName, type PlayerRow } from "@/lib/players";
import { WriteAccess, useWriteStatus } from "@/components/client";

/* Manual pick entry sheet (writes via /api/draft/pick when unlocked). */

export function ManualPickSheet({
  draft,
  currentPick,
  rows,
  draftedAt,
  picks,
  selected,
  setSelected,
  teamName,
  lastPick,
  onClose,
  onDone,
}: {
  draft: Draft;
  currentPick: number | null;
  rows: PlayerRow[];
  draftedAt: Map<string, number>;
  picks: DraftPick[];
  selected: PlayerRow | null;
  setSelected: (p: PlayerRow | null) => void;
  teamName: (s: number) => string;
  lastPick: DraftPick | null;
  onClose: () => void;
  onDone: (msg: { ok: boolean; text: string }) => void;
}) {
  const ws = useWriteStatus();
  const [overall, setOverall] = useState<string>("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // the server could not resolve the typed name: its candidates (ambiguous) or suggestions (not found)
  const [cands, setCands] = useState<{ reason: string; list: { player_id: string; name: string }[] } | null>(null);
  const rowById = useMemo(() => new Map(rows.map((r) => [r.player_id, r])), [rows]);
  const effectiveOverall = overall === "" ? currentPick : Number(overall);
  const info = effectiveOverall ? pickInfoForDraft(draft, effectiveOverall) : null;
  const canWrite = ws.writesEnabled && ws.authed;

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  // search every player (drafted ones too, flagged "drafted #N") so a duplicate is visible before submitting
  const matches = useMemo(() => {
    const n = normName(q);
    if (!n) return [];
    const hits = rows.filter((r) => normName(r.name).includes(n) || r.player_id.includes(q.toLowerCase()));
    return [...hits.filter((r) => !draftedAt.has(r.player_id)), ...hits.filter((r) => draftedAt.has(r.player_id))].slice(0, 8);
  }, [q, rows, draftedAt]);
  // a typed name equal to an already recorded pick's name (also catches unlisted `unknown:` picks)
  const typedDup = useMemo(() => {
    const n = normName(q);
    return n ? (picks.find((p) => normName(p.player_name) === n) ?? null) : null;
  }, [q, picks]);

  const submit = async (overwrite = false, allowUnknown = false) => {
    if (!effectiveOverall) return;
    const player = selected;
    const name = player?.name ?? q.trim();
    if (!name) return;
    setBusy(true);
    setMsg(null);
    setCands(null);
    const r = await fetch("/api/draft/pick", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        draft_id: draft.draft_id,
        overall_pick: effectiveOverall,
        player_id: player?.player_id ?? null,
        player_name: name,
        overwrite,
        allow_unknown: allowUnknown,
      }),
    });
    const j = await r.json().catch(() => ({}));
    setBusy(false);
    if (r.ok) {
      setSelected(null);
      setQ("");
      setOverall("");
      onDone({ ok: true, text: `Recorded #${effectiveOverall}: ${name}` });
      onClose();
    } else if (r.status === 409 && j.reason === "pick_taken" && !overwrite) {
      // only a different player at this pick slot can be overwritten; a duplicate player never
      if (window.confirm(`${j.error}\n\nOverwrite?`)) submit(true, allowUnknown);
    } else {
      if (r.status === 400 && (j.reason === "ambiguous" || j.reason === "not_found"))
        setCands({ reason: j.reason, list: Array.isArray(j.candidates) ? j.candidates : [] });
      setMsg({ ok: false, text: j.error || `HTTP ${r.status}` });
    }
  };

  const undo = async () => {
    if (!lastPick || lastPick.source !== "manual") return;
    if (!window.confirm(`Undo manual pick #${lastPick.overall_pick} (${lastPick.player_name})?`)) return;
    setBusy(true);
    const r = await fetch(
      `/api/draft/pick?draft_id=${encodeURIComponent(draft.draft_id)}&overall_pick=${lastPick.overall_pick}`,
      { method: "DELETE" },
    );
    const j = await r.json().catch(() => ({}));
    setBusy(false);
    const m = r.ok ? { ok: true, text: `Removed #${lastPick.overall_pick}` } : { ok: false, text: j.error || `HTTP ${r.status}` };
    setMsg(m);
    onDone(m);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center md:items-center" role="dialog" aria-modal="true" aria-label="Enter a pick">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/50" />
      <div className="relative max-h-[88vh] w-full overflow-auto rounded-t-2xl border border-line bg-panel px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-2xl md:max-w-md md:rounded-2xl">
        <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-line md:hidden" />
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Enter a pick</h2>
          <div className="flex items-center gap-3">
            <WriteAccess compact />
            <button type="button" onClick={onClose} className="text-sm text-muted hover:text-fg">
              Close
            </button>
          </div>
        </div>
        {!ws.loaded ? (
          <div className="text-xs text-muted">Checking write access…</div>
        ) : !ws.writesEnabled ? (
          <div className="rounded-lg border border-warn/40 bg-warn/10 p-2 text-xs text-warn">
            Writes disabled: {ws.reason ?? "server not configured"} Picks can still be recorded from the CLI:{" "}
            <code>fantasy draft pick &lt;overall&gt; &lt;player&gt;</code>
          </div>
        ) : !ws.authed ? (
          <div className="text-xs text-muted">Tap “unlock” above and enter the passphrase to record picks.</div>
        ) : null}
        <fieldset disabled={!canWrite || busy} className={`mt-3 space-y-3 ${!canWrite ? "opacity-60" : ""}`}>
          <div className="flex items-end gap-3">
            <label className="w-24 shrink-0">
              <span className="block text-xs text-muted">Overall #</span>
              <input
                inputMode="numeric"
                value={overall}
                placeholder={currentPick ? String(currentPick) : ""}
                onChange={(e) => setOverall(e.target.value.replace(/\D/g, ""))}
                className="num w-full rounded-lg border border-line bg-panel-2 px-3 py-2.5 text-base outline-none focus:border-accent"
              />
            </label>
            <div className="pb-2.5 text-sm text-muted">
              {info ? (
                <>
                  R{info.round}.{info.pickInRound} · {teamName(info.slot)}
                  {info.slot === draft.my_slot && <span className="ml-1 text-accent">(me)</span>}
                </>
              ) : (
                "draft complete"
              )}
            </div>
          </div>
          <div className="relative">
            <span className="block text-xs text-muted">Player</span>
            {selected ? (
              <div className="flex items-center gap-2 rounded-lg border border-accent bg-accent/5 px-3 py-2.5">
                <span className="font-medium">{selected.name}</span>
                <span className="text-xs text-muted">
                  {selected.team} {selected.positions.join("/")}
                </span>
                <button type="button" onClick={() => setSelected(null)} className="ml-auto text-xs text-muted hover:text-fg">
                  change
                </button>
              </div>
            ) : (
              <>
                <input
                  value={q}
                  onChange={(e) => {
                    setQ(e.target.value);
                    setCands(null);
                  }}
                  placeholder="Search players…"
                  aria-label="Search players"
                  className="w-full rounded-lg border border-line bg-panel-2 px-3 py-2.5 text-base outline-none focus:border-accent"
                />
                {matches.length > 0 && (
                  <ul className="mt-1 max-h-64 overflow-auto rounded-lg border border-line bg-panel">
                    {matches.map((m) => (
                      <li key={m.player_id}>
                        <button
                          type="button"
                          disabled={draftedAt.has(m.player_id)}
                          onClick={() => {
                            setSelected(m);
                            setQ("");
                            setCands(null);
                          }}
                          className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm hover:bg-panel-2 disabled:opacity-60"
                        >
                          <span className="font-medium">{m.name}</span>
                          <span className="text-xs text-muted">
                            {m.team} {m.positions.join("/")}
                          </span>
                          {draftedAt.has(m.player_id) ? (
                            <span className="ml-auto text-xs text-bad">drafted #{draftedAt.get(m.player_id)}</span>
                          ) : (
                            <span className="num ml-auto text-xs text-muted">#{m.rank ?? "—"}</span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {typedDup && (
                  <div className="mt-1 text-[11px] text-bad">
                    {typedDup.player_name} already drafted at #{typedDup.overall_pick}
                  </div>
                )}
                {q && matches.length === 0 && !typedDup && (
                  <div className="mt-1 text-[11px] text-muted">
                    No name match here — the server will resolve it (aliases, spelling); an unlisted name needs confirmation.
                  </div>
                )}
                {cands && (
                  <div className="mt-2 rounded-lg border border-line p-2 text-xs">
                    <div className="mb-1 text-muted">{cands.reason === "ambiguous" ? "Which player?" : "Did you mean:"}</div>
                    <div className="flex flex-wrap gap-1">
                      {cands.list.map((c) => {
                        const row = rowById.get(c.player_id);
                        const at = draftedAt.get(c.player_id);
                        return (
                          <button
                            key={c.player_id}
                            type="button"
                            disabled={!row || at != null}
                            onClick={() => {
                              if (!row) return;
                              setSelected(row);
                              setQ("");
                              setCands(null);
                            }}
                            className="rounded border border-line px-2 py-1 hover:bg-panel-2 disabled:opacity-60"
                          >
                            {c.name}
                            {at != null && <span className="ml-1 text-bad">drafted #{at}</span>}
                          </button>
                        );
                      })}
                    </div>
                    {cands.reason === "not_found" && (
                      <button
                        type="button"
                        onClick={() => submit(false, true)}
                        className="mt-2 rounded border border-warn/50 px-2 py-1 text-warn hover:bg-warn/10"
                      >
                        Record “{q.trim()}” as an unlisted player
                      </button>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => submit(false)}
              disabled={!effectiveOverall || (!selected && !q.trim())}
              className="flex-1 rounded-lg bg-accent px-3 py-3 text-[15px] font-semibold text-accent-fg disabled:opacity-40"
            >
              {busy ? "Saving…" : `Record pick${effectiveOverall ? ` #${effectiveOverall}` : ""}`}
            </button>
            {lastPick?.source === "manual" && (
              <button type="button" onClick={undo} className="rounded-lg border border-line px-3 py-3 text-xs text-muted hover:text-bad">
                Undo #{lastPick.overall_pick}
              </button>
            )}
          </div>
        </fieldset>
        {msg && <div className={`mt-2 text-xs ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</div>}
      </div>
    </div>
  );
}
