import { useMemo, useState } from "react";
import type { DraftLive, ValuesBundle } from "@/lib/data";
import type { Draft } from "@/lib/types";
import { customRoundsOf, pickInfoForDraft, picksForDraft } from "@/lib/draftOrder";
import { pct, asStringArray } from "@/lib/format";
import { normName, toRows, type PlayerRow } from "@/lib/players";
import { fallbackRanking } from "@/lib/fallback";
import { Ago, useNow } from "@/components/client";
import { type Candidate, probTone, Pos, Expander } from "./parts";
import { WhyPanel, AgentLine } from "./WhyPanel";
import { ManualPickSheet } from "./ManualPickSheet";
import { RosterCard, AvailableTable, BoardPanel } from "./BoardPanels";

/* The live draft room for one draft: primary pick card, why/all options, manual pick, roster, available, board. */

export function DraftView({
  draft,
  drafts,
  live,
  values,
  pollError,
  lastOk,
  onSwitch,
  onRefresh,
  selected,
  setSelected,
}: {
  draft: Draft;
  drafts: Draft[];
  live: DraftLive | null;
  values: ValuesBundle;
  pollError: string | null;
  lastOk: string | null;
  onSwitch: (id: string) => void;
  onRefresh: () => void;
  selected: PlayerRow | null;
  setSelected: (p: PlayerRow | null) => void;
}) {
  const [sheetOpen, setSheetOpen] = useState(false);
  const [flash, setFlash] = useState<{ ok: boolean; text: string } | null>(null);
  const N = draft.num_teams;
  const R = draft.num_rounds;
  const total = N * R;
  const picks = useMemo(() => live?.picks ?? [], [live]);
  const pickMap = useMemo(() => new Map(picks.map((p) => [p.overall_pick, p])), [picks]);
  const teamNames = (draft.config?.team_names ?? {}) as Record<string, string>;
  const teamName = (slot: number) => teamNames[String(slot)] ?? `Team ${slot}`;

  // On the clock = one after the highest recorded pick (sync gaps must not rewind the clock).
  const maxPick = picks.reduce((m, p) => Math.max(m, p.overall_pick), 0);
  const currentPick = maxPick + 1;
  const gaps: number[] = [];
  for (let i = 1; i < maxPick; i++) if (!pickMap.has(i)) gaps.push(i);
  const complete = currentPick > total;
  const cur = complete ? null : pickInfoForDraft(draft, currentPick);
  const myAll = picksForDraft(draft, draft.my_slot);
  const myUpcoming = myAll.filter((p) => p >= currentPick);
  const nextMine = myUpcoming[0] ?? null;
  const picksUntil = nextMine != null ? nextMine - currentPick : null;
  const onClock = picksUntil === 0 && draft.status !== "pending" && draft.status !== "complete";

  const allRows = useMemo(() => toRows(values.values, values.players), [values]);
  const rowById = useMemo(() => new Map(allRows.map((r) => [r.player_id, r])), [allRows]);
  const rowByName = useMemo(() => new Map(allRows.map((r) => [normName(r.name), r])), [allRows]);
  const draftedIds = useMemo(() => {
    const s = new Set<string>();
    for (const p of picks) {
      if (p.player_id) s.add(p.player_id);
      const byName = rowByName.get(normName(p.player_name));
      if (byName) s.add(byName.player_id);
    }
    return s;
  }, [picks, rowByName]);
  const available = useMemo(() => allRows.filter((r) => !draftedIds.has(r.player_id)), [allRows, draftedIds]);
  // player_id -> overall pick where he was drafted (by id, or by name for rows recorded without one)
  const draftedAt = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of picks) {
      if (p.player_id) m.set(p.player_id, p.overall_pick);
      const byName = rowByName.get(normName(p.player_name));
      if (byName && !m.has(byName.player_id)) m.set(byName.player_id, p.overall_pick);
    }
    return m;
  }, [picks, rowByName]);

  const myPicks = picks.filter((p) => p.team_slot === draft.my_slot).sort((a, b) => a.overall_pick - b.overall_pick);
  const myRoster: PlayerRow[] = myPicks
    .map((p) => (p.player_id && rowById.get(p.player_id)) || rowByName.get(normName(p.player_name)))
    .filter((x): x is PlayerRow => !!x);

  const rec = live?.rec ?? null;
  const payload = rec?.payload ?? null;
  const recFresh = !!rec && rec.as_of_pick >= picks.length && (rec.for_pick == null || rec.for_pick >= currentPick);

  const fallback = useMemo(
    () => fallbackRanking(available, myRoster, picksUntil ?? 0, currentPick, 10),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [available, myRoster.map((r) => r.player_id).join(","), picksUntil, currentPick],
  );

  // model rec items -> candidates. prob_available_next refers to my pick after the one the rec is for.
  const recFor = rec?.for_pick ?? payload?.for_pick ?? null;
  const recNextAfter = payload?.my_next_picks?.find((p) => recFor != null && p > recFor) ?? null;
  const recCands: Candidate[] = (payload?.recommendations ?? []).slice(0, 10).map((r, i) => ({
    key: r.player_id ?? String(i),
    name: r.name ?? r.player_id,
    team: r.team ?? "",
    positions: asStringArray(r.positions),
    reason: r.reason ?? null,
    prob: r.prob_available_next ?? null,
    probAt: recNextAfter,
    taken: !!r.player_id && draftedIds.has(r.player_id),
    row: (r.player_id && rowById.get(r.player_id)) || null,
    rec: r,
  }));
  const fbCands: Candidate[] = fallback.items.map((it) => ({
    key: it.row.player_id,
    name: it.row.name,
    team: it.row.team,
    positions: it.row.positions,
    reason: it.reason,
    prob: picksUntil ? it.probAvail : null,
    probAt: picksUntil ? nextMine : null,
    taken: false,
    row: it.row,
  }));
  const usingModel = recFresh && recCands.some((c) => !c.taken);
  const cands = usingModel ? recCands.filter((c) => !c.taken) : fbCands;
  const top = cands[0] ?? null;
  const alts = cands.slice(1, 3);

  const openSheet = (p: PlayerRow | null) => {
    setSelected(p);
    setFlash(null);
    setSheetOpen(true);
  };
  const lastPick = picks.length ? picks.reduce((a, b) => (b.overall_pick > a.overall_pick ? b : a)) : null;

  return (
    <div className="space-y-3">
      <TopBar draft={draft} drafts={drafts} onSwitch={onSwitch} lastOk={lastOk} pollError={pollError} />

      {gaps.length > 0 && (
        <p className="px-1 text-xs text-warn">
          ⚠ Missing picks {gaps.slice(0, 12).join(", ")}
          {gaps.length > 12 ? ` (+${gaps.length - 12})` : ""} — re-sync from Yahoo or enter them manually.
        </p>
      )}

      {/* ---------------- primary card */}
      <section className={`overflow-hidden rounded-2xl border bg-panel ${onClock ? "border-accent shadow-[0_0_0_1px_var(--accent)]" : "border-line"}`}>
        <div className={`px-4 pt-4 pb-3 ${onClock ? "bg-accent/10" : ""}`}>
          {complete ? (
            <div className="text-xl font-semibold text-good">Draft complete</div>
          ) : onClock ? (
            <div className="text-xl font-semibold text-accent">You&apos;re on the clock · #{currentPick}</div>
          ) : draft.status === "pending" && picks.length === 0 ? (
            <>
              <div className="text-lg font-semibold">Draft not started</div>
              <div className="text-[15px] text-muted">
                Your first pick: <span className="font-medium text-fg">#{nextMine ?? "—"}</span>
              </div>
            </>
          ) : (
            <>
              <div className="text-lg font-semibold leading-snug">
                On the clock: #{currentPick} <span className="font-normal text-muted">({cur ? teamName(cur.slot) : "—"})</span>
              </div>
              <div className="text-[15px] text-muted">
                {nextMine ? (
                  <>
                    Your next pick <span className="font-semibold text-accent">#{nextMine}</span> in {picksUntil} pick
                    {picksUntil === 1 ? "" : "s"}
                  </>
                ) : (
                  "No picks left for you"
                )}
              </div>
            </>
          )}
        </div>

        {!complete && (
          <div className="border-t border-line px-4 pt-3 pb-4">
            <div className="text-[11px] font-medium tracking-wider text-muted uppercase">
              {usingModel ? "Take" : "Best available (fallback)"}
            </div>
            {!top ? (
              <div className="py-3 text-sm text-muted">No player values loaded yet.</div>
            ) : (
              <>
                <div className="mt-1 text-2xl leading-tight font-semibold">{top.name}</div>
                <div className="mt-0.5 text-[15px]">
                  <span className="text-muted">{top.team}</span> · <Pos positions={top.positions} />
                </div>
                {top.reason && <p className="mt-2 text-[15px] leading-snug">{top.reason}</p>}
                <p className="mt-2 text-sm">
                  {top.prob != null && top.probAt != null ? (
                    <>
                      <span className={`font-semibold ${probTone(top.prob)}`}>{pct(top.prob)}</span>
                      <span className="text-muted"> chance he&apos;s still there at your pick #{top.probAt}</span>
                    </>
                  ) : picksUntil === 0 ? (
                    <span className="text-muted">Your pick — take him now.</span>
                  ) : null}
                </p>
              </>
            )}
            {alts.length > 0 && (
              <ul className="mt-3 divide-y divide-line rounded-lg border border-line">
                {alts.map((c, i) => (
                  <li key={c.key} className="flex items-center gap-3 px-3 py-2.5">
                    <span className="num w-4 text-sm text-muted">{i + 2}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] font-medium">{c.name}</span>
                      <span className="block truncate text-xs text-muted">
                        {c.team} · {c.positions.join("/")}
                      </span>
                    </span>
                    {c.prob != null && c.probAt != null && (
                      <span className={`num shrink-0 text-sm ${probTone(c.prob)}`} title={`chance he's still there at #${c.probAt}`}>
                        {pct(c.prob)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-xs text-muted">
              {usingModel ? (
                <>
                  Model · as of pick {rec!.as_of_pick} · <Ago ts={payload?.generated_at ?? rec!.created_at} />
                </>
              ) : rec && recFresh ? (
                <span className="text-warn">All model picks are taken · showing client-side fallback</span>
              ) : rec ? (
                <span className="text-warn">
                  Model rec is stale (as of pick {rec.as_of_pick}, now {picks.length}) · showing client-side fallback
                </span>
              ) : (
                <span className="text-warn">No model rec yet · showing client-side fallback</span>
              )}
            </p>
          </div>
        )}
        {!complete && (
          <div className="border-t border-line px-4">
            <Expander summary="Why / all options" bare>
              <WhyPanel
                usingModel={usingModel}
                rec={rec}
                recCands={recCands}
                fbCands={fbCands}
                weakest={fallback.weakest}
                recCount={live?.recCount ?? 0}
                onSelect={openSheet}
              />
            </Expander>
          </div>
        )}
      </section>

      {/* ---------------- manual pick entry */}
      <div>
        <button
          type="button"
          onClick={() => openSheet(null)}
          className="w-full rounded-xl border border-line bg-panel px-4 py-3 text-[15px] font-medium hover:border-accent/50"
        >
          ✎ Enter a pick{complete ? "" : ` (#${currentPick})`}
        </button>
        {flash && <p className={`mt-1 px-1 text-xs ${flash.ok ? "text-good" : "text-bad"}`}>{flash.text}</p>}
      </div>

      <AgentLine live={live} />

      <RosterCard myPicks={myPicks} rowById={rowById} rowByName={rowByName} myRoster={myRoster} payload={payload} />

      <Expander
        summary="Draft board"
        right={lastPick ? `last: #${lastPick.overall_pick} ${lastPick.player_name ?? ""}` : `${picks.length}/${total}`}
      >
        <BoardPanel
          draft={draft}
          picks={picks}
          pickMap={pickMap}
          currentPick={complete ? null : currentPick}
          teamName={teamName}
          myUpcoming={myUpcoming}
          total={total}
        />
      </Expander>

      <Expander summary="Available players" right={`${available.length}`}>
        <AvailableTable
          rows={available}
          runId={values.run?.run_id ?? null}
          runAt={values.run?.created_at ?? null}
          picksUntil={picksUntil ?? 0}
          currentPick={currentPick}
          onSelect={openSheet}
          errors={values.errors}
        />
      </Expander>

      {sheetOpen && (
        <ManualPickSheet
          draft={draft}
          currentPick={complete ? null : currentPick}
          rows={allRows}
          draftedAt={draftedAt}
          picks={picks}
          selected={selected}
          setSelected={setSelected}
          teamName={teamName}
          lastPick={lastPick}
          onClose={() => setSheetOpen(false)}
          onDone={(m) => {
            setFlash(m);
            onRefresh();
          }}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ top bar */

function TopBar({
  draft,
  drafts,
  onSwitch,
  lastOk,
  pollError,
}: {
  draft: Draft;
  drafts: Draft[];
  onSwitch: (id: string) => void;
  lastOk: string | null;
  pollError: string | null;
}) {
  const now = useNow(2000);
  const lagging = !lastOk || now - Date.parse(lastOk) > 20000;
  const customOk = !!customRoundsOf(draft.config, draft.num_teams, draft.num_rounds);
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-xs text-muted">
        <select
          value={draft.draft_id}
          onChange={(e) => onSwitch(e.target.value)}
          aria-label="Draft"
          className="max-w-[55vw] truncate rounded-md border border-line bg-panel px-2 py-1 text-xs outline-none focus:border-accent"
        >
          {drafts.map((d) => (
            <option key={d.draft_id} value={d.draft_id}>
              {d.is_mock ? "[mock] " : ""}
              {d.draft_id} · {d.status}
            </option>
          ))}
        </select>
        {draft.is_mock && <span className="text-warn">mock</span>}
        {draft.order_type === "custom" && !customOk && (
          <span className="text-bad" title="order_type custom but config.custom_rounds missing/invalid; using snake">
            order invalid
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5" title={`${draft.num_teams} teams · ${draft.num_rounds} rounds · slot ${draft.my_slot} · ${draft.order_type}${customOk ? " (custom rounds)" : ""}`}>
          <span className={`inline-block h-2 w-2 rounded-full ${pollError || lagging ? "bg-bad" : "bg-good"}`} />
          {pollError ? "sync error" : "live"} · <Ago ts={lastOk} />
        </span>
      </div>
      {pollError && <p className="px-1 text-xs text-bad">{pollError}</p>}
    </div>
  );
}
