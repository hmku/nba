"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { anonClient } from "@/lib/supabase";
import { loadDraftLive, loadDrafts, loadValues, latestRun, slimValues, type DraftLive, type ValuesBundle } from "@/lib/data";
import type { Draft, DraftPick, RecItem, RecPayload, AgentEvent } from "@/lib/types";
import { customRoundsOf, orderForDraft, pickInfoForDraft, picksForDraft } from "@/lib/draftOrder";
import { CATS, CAT_LABEL, heatStyle } from "@/lib/categories";
import { fmt, pct, signed, asStringArray } from "@/lib/format";
import { matchesPos, normName, toRows, type PlayerRow } from "@/lib/players";
import { fallbackRanking, probAvailableFromAdp } from "@/lib/fallback";
import { Badge, Card, Empty, JsonView } from "@/components/ui";
import { Ago, WriteAccess, useNow, useWriteStatus } from "@/components/client";
import { KindIcon } from "@/components/KindIcon";

const POLL_MS = 4000;
const VALUES_CHECK_MS = 30000;

interface Props {
  initialDrafts: Draft[];
  initialDraftId: string | null;
  initialLive: DraftLive | null;
  initialValues: ValuesBundle;
  loadError: string | null;
}

export default function DraftClient({ initialDrafts, initialDraftId, initialLive, initialValues, loadError }: Props) {
  const router = useRouter();
  const [drafts, setDrafts] = useState(initialDrafts);
  const [draftId, setDraftId] = useState(initialDraftId);
  const [live, setLive] = useState<DraftLive | null>(initialLive);
  const [values, setValues] = useState<ValuesBundle>(initialValues);
  const [pollError, setPollError] = useState<string | null>(loadError);
  const [lastOk, setLastOk] = useState<string | null>(initialLive?.fetchedAt ?? null);
  const [selected, setSelected] = useState<PlayerRow | null>(null);
  const draftIdRef = useRef(draftId);
  draftIdRef.current = draftId;

  const refreshLive = useCallback(async () => {
    const id = draftIdRef.current;
    if (!id) return;
    try {
      const l = await loadDraftLive(anonClient(), id);
      if (draftIdRef.current !== id) return;
      setLive(l);
      setPollError(l.errors.length ? l.errors.join("; ") : null);
      if (!l.errors.length) setLastOk(l.fetchedAt);
    } catch (e) {
      setPollError(String(e));
    }
  }, []);

  // poll live state
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") refreshLive();
    }, POLL_MS);
    const onVis = () => document.visibilityState === "visible" && refreshLive();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [refreshLive]);

  // check for a new valuation run + new drafts occasionally
  useEffect(() => {
    const t = setInterval(async () => {
      if (document.visibilityState !== "visible") return;
      const db = anonClient();
      const run = await latestRun(db, "valuation").catch(() => null);
      // reload when a new valuation run appears (retry each check while it has no rows yet)
      if (run && run.run_id !== values.run?.run_id) {
        const v = await loadValues(db);
        if (v.values.length && v.run?.run_id !== values.run?.run_id) setValues(slimValues(v));
      }
      const d = await loadDrafts(db);
      if (!d.error) setDrafts(d.drafts);
    }, VALUES_CHECK_MS);
    return () => clearInterval(t);
  }, [values.run?.run_id]);

  const switchDraft = (id: string) => {
    setDraftId(id);
    setLive(null);
    draftIdRef.current = id;
    router.replace(`/draft?id=${encodeURIComponent(id)}`, { scroll: false });
    refreshLive();
  };

  const draft = drafts.find((d) => d.draft_id === draftId) ?? null;

  if (!draft) {
    return (
      <div className="space-y-3">
        <h1 className="text-lg font-semibold">Draft</h1>
        {pollError && <div className="text-sm text-bad">{pollError}</div>}
        <Card title="No drafts">
          <Empty>
            No rows in <code>drafts</code> yet. Create one with <code>fantasy draft init --draft-id …</code>.
          </Empty>
        </Card>
      </div>
    );
  }

  return (
    <DraftView
      draft={draft}
      drafts={drafts}
      live={live}
      values={values}
      pollError={pollError}
      lastOk={lastOk}
      onSwitch={switchDraft}
      onRefresh={refreshLive}
      selected={selected}
      setSelected={setSelected}
    />
  );
}

/* ------------------------------------------------------------------ candidates */

/** One row in the "what should I pick" list: either a model recommendation or a client-side fallback item. */
interface Candidate {
  key: string;
  name: string;
  team: string;
  positions: string[];
  reason: string | null;
  prob: number | null;
  probAt: number | null; // the pick number prob refers to
  taken: boolean;
  row: PlayerRow | null;
  rec?: RecItem;
}

function CatStrip({ impact, scale = 0.6 }: { impact?: Record<string, number | undefined> | null; scale?: number }) {
  return (
    <div className="grid grid-cols-9 gap-px overflow-hidden rounded border border-line text-center">
      {CATS.map((c) => {
        const v = impact?.[c];
        return (
          <div key={c} className="min-w-0 overflow-hidden bg-panel-2 py-0.5" style={heatStyle(v, scale)}>
            <div className="text-[9px] leading-3 text-muted">{CAT_LABEL[c]}</div>
            <div className="num text-[9.5px] leading-4 tracking-tighter">{v == null ? "·" : signed(v, 1)}</div>
          </div>
        );
      })}
    </div>
  );
}

function probTone(p: number | null | undefined) {
  if (p == null) return "text-muted";
  return p >= 0.6 ? "text-good" : p >= 0.3 ? "text-warn" : "text-bad";
}

function Pos({ positions }: { positions: string[] }) {
  return <span className="text-muted">{positions.join(" / ")}</span>;
}

/** Lazily rendered expander (children mount only once opened, keeps the phone DOM light). */
function Expander({
  summary,
  right,
  children,
  bare = false,
}: {
  summary: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  bare?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className={bare ? "" : "rounded-xl border border-line bg-panel"}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={`flex w-full items-center gap-2 text-left ${bare ? "py-2 text-sm text-accent" : "px-4 py-3"}`}
      >
        <span className={`text-[10px] text-muted transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
        <span className={`min-w-0 flex-1 ${bare ? "" : "text-[15px] font-medium"}`}>{summary}</span>
        {right != null && <span className="shrink-0 text-xs text-muted">{right}</span>}
      </button>
      {open && <div className={bare ? "" : "border-t border-line"}>{children}</div>}
    </section>
  );
}

/* ------------------------------------------------------------------ view */

function DraftView({
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
          available={available}
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

/* ------------------------------------------------------------------ why / all options */

function WhyPanel({
  usingModel,
  rec,
  recCands,
  fbCands,
  weakest,
  recCount,
  onSelect,
}: {
  usingModel: boolean;
  rec: DraftLive["rec"];
  recCands: Candidate[];
  fbCands: Candidate[];
  weakest: string[];
  recCount: number;
  onSelect: (p: PlayerRow) => void;
}) {
  const p = rec?.payload;
  const modelList = (
    <>
      {(p?.strategy?.name || p?.team_profile) && (
        <div className="space-y-1 pb-2 text-xs">
          {p?.strategy?.name && (
            <div>
              <span className="text-muted">Strategy </span>
              <span className="font-medium">{p.strategy.name}</span>
              {p.strategy.notes && <span className="text-muted"> — {p.strategy.notes}</span>}
            </div>
          )}
          {p?.team_profile?.expected_category_wins != null && (
            <div>
              <span className="text-muted">Expected category wins / week </span>
              <span className="num font-medium">{fmt(p.team_profile.expected_category_wins, 1)} of 9</span>
              {!!p.team_profile.punts?.length && (
                <span className="text-muted"> · punting {p.team_profile.punts.map((x) => CAT_LABEL[x as keyof typeof CAT_LABEL] ?? x).join(", ")}</span>
              )}
            </div>
          )}
        </div>
      )}
      {!!p?.warnings?.length && (
        <ul className="pb-2 text-xs text-warn">
          {p.warnings.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
      )}
      <ol className="divide-y divide-line">
        {recCands.map((c, i) => (
          <li key={c.key} className={`py-2.5 ${c.taken ? "opacity-40" : ""}`}>
            <div className="flex items-baseline gap-2">
              <span className="num w-5 text-xs text-muted">{i + 1}</span>
              <span className={`min-w-0 flex-1 truncate font-medium ${c.taken ? "line-through" : ""}`}>{c.name}</span>
              <span className="shrink-0 text-xs text-muted">{c.positions.join("/")}</span>
              {c.prob != null && <span className={`num shrink-0 text-xs ${probTone(c.prob)}`}>{pct(c.prob)}</span>}
            </div>
            <div className="mt-1">
              <CatStrip impact={c.rec?.cat_impact} />
              <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-muted">
                <span>
                  score <span className="num text-fg">{fmt(c.rec?.score)}</span>
                </span>
                <span>
                  value <span className="num">{fmt(c.rec?.value)}</span>
                </span>
                <span>
                  marginal <span className="num">{fmt(c.rec?.marginal_value)}</span>
                </span>
                <span>
                  ADP <span className="num">{fmt(c.rec?.adp, 1)}</span>
                </span>
              </div>
              {c.reason && <p className="mt-0.5 text-xs text-muted">{c.reason}</p>}
            </div>
          </li>
        ))}
      </ol>
      {rec && (
        <p className="pt-2 text-[11px] text-muted">
          rec <code>{rec.rec_id}</code> · run <code>{rec.model_run_id ?? p?.model_run_id ?? "—"}</code> · as of {rec.as_of_pick} → for #
          {rec.for_pick ?? p?.for_pick ?? "?"} · {recCount} recs for this draft. Category cells = change in my weekly category z-score; % =
          chance he&apos;s still there at my following pick.
        </p>
      )}
    </>
  );
  const fallbackList = (
    <>
      <p className="pb-2 text-xs text-muted">
        Client-side fallback (not the optimizer): value + 0.25× value in my weakest categories
        {weakest.length ? ` (${weakest.map((c) => CAT_LABEL[c as keyof typeof CAT_LABEL]).join(", ")})` : ""} + 0.4 if he fills an empty G/F/C
        group. % = rough ADP-based chance he lasts to my next pick.
      </p>
      {fbCands.length === 0 ? (
        <p className="py-2 text-sm text-muted">No player values loaded.</p>
      ) : (
        <ol className="divide-y divide-line">
          {fbCands.map((c, i) => (
            <li key={c.key} className="py-2.5">
              <div className="flex items-baseline gap-2">
                <span className="num w-5 text-xs text-muted">{i + 1}</span>
                <span className="min-w-0 flex-1 truncate font-medium">{c.name}</span>
                <span className="shrink-0 text-xs text-muted">{c.positions.join("/")}</span>
                {c.prob != null && <span className={`num shrink-0 text-xs ${probTone(c.prob)}`}>{pct(c.prob)}</span>}
                {c.row && (
                  <button
                    type="button"
                    onClick={() => onSelect(c.row!)}
                    className="shrink-0 rounded border border-line px-1.5 text-[11px] text-muted hover:border-accent hover:text-accent"
                  >
                    pick
                  </button>
                )}
              </div>
              <div className="mt-1">
                <CatStrip impact={c.row?.cat_values} scale={1.5} />
                <p className="mt-0.5 text-[11px] text-muted">
                  value <span className="num">{fmt(c.row?.value)}</span> · {c.reason}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </>
  );
  return (
    <div className="pb-3">
      {usingModel ? (
        modelList
      ) : (
        <>
          {fallbackList}
          {rec && recCands.length > 0 && (
            <div className="mt-3 border-t border-line pt-3">
              <div className="pb-2 text-xs font-medium text-warn">Last model recommendation (stale, as of pick {rec.as_of_pick})</div>
              {modelList}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ agent */

function AgentLine({ live }: { live: DraftLive | null }) {
  const runs = live?.agentRuns ?? [];
  const events = live?.agentEvents ?? [];
  const run = runs[0];
  if (!run) return null;
  const lastDecision = events.find((e) => e.kind === "decision");
  const dec = live?.decisions?.[0];
  const disagree = dec?.agrees_with_model === false;
  return (
    <div className={`rounded-xl border bg-panel px-4 ${disagree ? "border-warn/60" : "border-line"}`}>
      <Expander
        bare
        summary={
          <span className="block truncate text-fg">
            <span className="text-muted">Agent </span>
            <span className="font-medium">{run.agent}</span>
            <span className="text-muted"> · {run.status}</span>
            {disagree && <span className="text-warn"> · ≠ model</span>}
            {lastDecision?.message && <span className="text-muted"> · {lastDecision.message}</span>}
          </span>
        }
      >
        <div className="space-y-2 pb-3 text-sm">
          <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
            <Badge tone={run.mode === "live" ? "bad" : "neutral"}>{run.mode ?? "?"}</Badge>
            <span>
              heartbeat <Ago ts={run.heartbeat_at} staleAfter={run.status === "running" ? 120 : undefined} />
            </span>
          </div>
          {lastDecision && (
            <div className="rounded-lg border border-accent/40 bg-accent/5 p-2">
              <div className="flex items-center gap-1.5 text-[11px] text-muted">
                <KindIcon kind="decision" /> latest decision · <Ago ts={lastDecision.ts} />
              </div>
              <div className="mt-0.5 font-medium leading-snug">{lastDecision.message}</div>
              {lastDecision.payload != null && <JsonView value={lastDecision.payload} label="payload" />}
            </div>
          )}
          {dec && (
            <div className={`rounded-lg border p-2 text-xs ${disagree ? "border-warn/50 bg-warn/10" : "border-line"}`}>
              <div className="flex items-center gap-2">
                <Badge>{dec.kind}</Badge>
                {disagree ? <Badge tone="warn">disagrees with model</Badge> : dec.agrees_with_model ? <Badge tone="good">agrees</Badge> : null}
              </div>
              {dec.rationale && <div className="mt-1">{dec.rationale}</div>}
            </div>
          )}
          <ul className="space-y-1">
            {events.slice(0, 6).map((e: AgentEvent) => (
              <li key={e.event_id} className="flex gap-2 text-xs">
                <KindIcon kind={e.kind} />
                <span className="min-w-0 flex-1 truncate" title={e.message ?? ""}>
                  {e.message}
                </span>
                <Ago ts={e.ts} className="shrink-0 text-muted" />
              </li>
            ))}
          </ul>
        </div>
      </Expander>
    </div>
  );
}

/* ------------------------------------------------------------------ manual */

function ManualPickSheet({
  draft,
  currentPick,
  available,
  selected,
  setSelected,
  teamName,
  lastPick,
  onClose,
  onDone,
}: {
  draft: Draft;
  currentPick: number | null;
  available: PlayerRow[];
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
  const effectiveOverall = overall === "" ? currentPick : Number(overall);
  const info = effectiveOverall ? pickInfoForDraft(draft, effectiveOverall) : null;
  const canWrite = ws.writesEnabled && ws.authed;

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const matches = useMemo(() => {
    const n = normName(q);
    if (!n) return [];
    return available.filter((r) => normName(r.name).includes(n) || r.player_id.includes(q.toLowerCase())).slice(0, 8);
  }, [q, available]);

  const submit = async (overwrite = false) => {
    if (!effectiveOverall) return;
    const player = selected;
    const name = player?.name ?? q.trim();
    if (!name) return;
    setBusy(true);
    setMsg(null);
    const r = await fetch("/api/draft/pick", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        draft_id: draft.draft_id,
        overall_pick: effectiveOverall,
        player_id: player?.player_id ?? null,
        player_name: name,
        overwrite,
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
    } else if (r.status === 409 && !overwrite) {
      if (window.confirm(`${j.error}\n\nOverwrite?`)) submit(true);
    } else {
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
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search available players…"
                  className="w-full rounded-lg border border-line bg-panel-2 px-3 py-2.5 text-base outline-none focus:border-accent"
                />
                {matches.length > 0 && (
                  <ul className="mt-1 max-h-64 overflow-auto rounded-lg border border-line bg-panel">
                    {matches.map((m) => (
                      <li key={m.player_id}>
                        <button
                          type="button"
                          onClick={() => {
                            setSelected(m);
                            setQ("");
                          }}
                          className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm hover:bg-panel-2"
                        >
                          <span className="font-medium">{m.name}</span>
                          <span className="text-xs text-muted">
                            {m.team} {m.positions.join("/")}
                          </span>
                          <span className="num ml-auto text-xs text-muted">#{m.rank ?? "—"}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {q && matches.length === 0 && (
                  <div className="mt-1 text-[11px] text-muted">No match in available players — will record the free-text name (no player_id).</div>
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

/* ------------------------------------------------------------------ roster */

function RosterCard({
  myPicks,
  rowById,
  rowByName,
  myRoster,
  payload,
}: {
  myPicks: DraftPick[];
  rowById: Map<string, PlayerRow>;
  rowByName: Map<string, PlayerRow>;
  myRoster: PlayerRow[];
  payload: RecPayload | null;
}) {
  const totals = CATS.map((c) => ({ c, v: myRoster.reduce((s, p) => s + (p.cat_values[c] ?? 0), 0) }));
  const ranked = [...totals].sort((a, b) => b.v - a.v).map((t) => t.c);
  const profile = payload?.team_profile?.categories ?? {};
  const hasProfile = Object.keys(profile).length > 0;
  // strong / weak: model win probability when present, else top/bottom 3 of my summed category values
  const level = (c: (typeof CATS)[number]): "strong" | "weak" | "mid" | "none" => {
    if (!myRoster.length) return "none";
    const wp = profile[c]?.win_prob;
    if (hasProfile && wp != null) return wp >= 0.6 ? "strong" : wp <= 0.4 ? "weak" : "mid";
    const i = ranked.indexOf(c);
    return i < 3 ? "strong" : i >= ranked.length - 3 ? "weak" : "mid";
  };
  const STYLE = {
    strong: "bg-good/20 text-good border-good/40",
    weak: "bg-bad/15 text-bad border-bad/40",
    mid: "bg-panel-2 text-muted border-line",
    none: "bg-panel-2 text-muted border-line",
  };

  return (
    <section className="rounded-xl border border-line bg-panel px-4 py-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-medium">Your roster</h2>
        <span className="text-xs text-muted">{myPicks.length} players</span>
      </div>
      {myPicks.length === 0 ? (
        <p className="py-2 text-sm text-muted">No picks yet.</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {myPicks.map((p) => {
            const row = (p.player_id && rowById.get(p.player_id)) || rowByName.get(normName(p.player_name));
            return (
              <li key={p.overall_pick} className="flex items-baseline gap-2 text-[15px]">
                <span className="num w-8 shrink-0 text-xs text-muted">R{p.round}</span>
                <span className="min-w-0 truncate">{p.player_name ?? row?.name ?? p.player_id}</span>
                <span className="shrink-0 text-xs text-muted">{row?.positions.join("/")}</span>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-3 grid grid-cols-9 gap-1">
        {totals.map(({ c, v }) => {
          const wp = profile[c]?.win_prob;
          return (
            <div
              key={c}
              title={`${CAT_LABEL[c]}: Σ value ${signed(v, 1)}${wp != null ? ` · win ${pct(wp)}` : ""}`}
              className={`rounded border py-1 text-center text-[10px] leading-none font-medium ${STYLE[level(c)]}`}
            >
              {CAT_LABEL[c]}
            </div>
          );
        })}
      </div>
      {myRoster.length > 0 && (
        <p className="mt-1.5 text-[11px] text-muted">
          green = strength, red = weakness ({hasProfile ? "model weekly win %" : "sum of category values"})
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ available */

type SortKey = "value" | "adp" | "rank" | (typeof CATS)[number];

function AvailableTable({
  rows,
  runId,
  runAt,
  picksUntil,
  currentPick,
  onSelect,
  errors,
}: {
  rows: PlayerRow[];
  runId: string | null;
  runAt: string | null;
  picksUntil: number;
  currentPick: number;
  onSelect: (p: PlayerRow) => void;
  errors: string[];
}) {
  const [q, setQ] = useState("");
  const [pos, setPos] = useState("ALL");
  const [sort, setSort] = useState<SortKey>("value");
  const [limit, setLimit] = useState(40);

  const filtered = useMemo(() => {
    const n = normName(q);
    const out = rows.filter((r) => matchesPos(r.positions, pos) && (!n || normName(r.name).includes(n) || normName(r.team).includes(n)));
    const key = (r: PlayerRow): number | null =>
      sort === "value" ? r.value : sort === "adp" ? r.adp : sort === "rank" ? r.rank : r.cat_values[sort] ?? null;
    const asc = sort === "adp" || sort === "rank";
    return out.sort((a, b) => {
      const ka = key(a);
      const kb = key(b);
      if (ka == null && kb == null) return 0;
      if (ka == null) return 1;
      if (kb == null) return -1;
      return asc ? ka - kb : kb - ka;
    });
  }, [rows, q, pos, sort]);

  const th = (k: SortKey, label: string, cls = "") => (
    <th key={k} className={`cursor-pointer select-none ${cls} ${sort === k ? "text-accent!" : ""}`} onClick={() => setSort(k)}>
      {label}
      {sort === k ? (k === "adp" || k === "rank" ? " ↑" : " ↓") : ""}
    </th>
  );

  return (
    <div>
      <div className="space-y-2 border-b border-line px-3 py-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name / team"
          className="w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-base outline-none focus:border-accent"
        />
        <div className="flex flex-wrap gap-1">
          {["ALL", "PG", "SG", "G", "SF", "PF", "F", "C"].map((p) => (
            <button
              key={p}
              onClick={() => setPos(p)}
              className={`rounded-md border px-2 py-1 text-xs ${pos === p ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-fg"}`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
      {errors.length > 0 && <div className="px-3 py-1 text-xs text-bad">{errors.join("; ")}</div>}
      {rows.length === 0 ? (
        <Empty>No player_values for the latest valuation run.</Empty>
      ) : (
        <div className="scroll-thin max-h-[70vh] overflow-auto">
          <table className="dense w-full text-sm">
            <thead className="sticky top-0 z-10 bg-panel">
              <tr className="text-left">
                {th("rank", "Rk", "w-8")}
                <th className="sticky left-0 z-10 bg-panel">Player</th>
                {th("value", "Val", "text-right")}
                {CATS.map((c) => th(c, CAT_LABEL[c], "text-center"))}
                {th("adp", "ADP", "text-right")}
                <th className="text-right" title="Rough ADP-based probability the player lasts to my next pick">
                  P@next
                </th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, limit).map((r) => (
                <tr key={r.player_id} className="hover:bg-panel-2">
                  <td className="num text-muted">{r.rank ?? "—"}</td>
                  <td className="sticky left-0 max-w-[44vw] bg-panel sm:max-w-none">
                    <div className="truncate font-medium">{r.name}</div>
                    <div className="truncate text-[11px] text-muted">
                      {r.team} · {r.positions.join("/")}
                      {r.status && r.status !== "healthy" && <span className="ml-1 text-bad">{r.status}</span>}
                    </div>
                  </td>
                  <td className="num text-right font-semibold">{fmt(r.value)}</td>
                  {CATS.map((c) => (
                    <td key={c} className="num text-center text-xs" style={heatStyle(r.cat_values[c], 1.5)}>
                      {fmt(r.cat_values[c], 1)}
                    </td>
                  ))}
                  <td className="num text-right text-muted">{fmt(r.adp, 1)}</td>
                  <td className="num text-right text-xs text-muted">{pct(probAvailableFromAdp(r.adp, picksUntil, currentPick))}</td>
                  <td className="text-right">
                    <button
                      onClick={() => onSelect(r)}
                      title="Enter as a pick"
                      className="rounded border border-line px-1.5 text-xs text-muted hover:border-accent hover:text-accent"
                    >
                      +
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > limit && (
            <button onClick={() => setLimit((l) => l + 100)} className="w-full border-t border-line py-2.5 text-sm text-accent">
              Show more ({filtered.length - limit} more)
            </button>
          )}
        </div>
      )}
      <p className="border-t border-line px-3 py-1.5 text-[11px] text-muted">
        run <code>{runId ?? "none"}</code> {runAt && <Ago ts={runAt} />} · tap a column to sort · + enters a pick
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ board */

function BoardPanel({
  draft,
  picks,
  pickMap,
  currentPick,
  teamName,
  myUpcoming,
  total,
}: {
  draft: Draft;
  picks: DraftPick[];
  pickMap: Map<number, DraftPick>;
  currentPick: number | null;
  teamName: (s: number) => string;
  myUpcoming: number[];
  total: number;
}) {
  const recent = [...picks].sort((a, b) => b.overall_pick - a.overall_pick).slice(0, 8);
  return (
    <div>
      <div className="px-4 py-2 text-xs text-muted">
        {picks.length}/{total} picks · your upcoming:{" "}
        <span className="num text-fg">{myUpcoming.slice(0, 8).join(", ") || "—"}</span>
        {myUpcoming.length > 8 ? ` +${myUpcoming.length - 8}` : ""}
      </div>
      {recent.length === 0 ? (
        <p className="px-4 pb-3 text-sm text-muted">No picks yet.</p>
      ) : (
        <ul className="border-t border-line">
          {recent.map((p) => (
            <li
              key={p.overall_pick}
              className={`flex items-baseline gap-3 border-b border-line px-4 py-2 text-sm last:border-b-0 ${p.team_slot === draft.my_slot ? "bg-mine" : ""}`}
            >
              <span className="num w-8 shrink-0 text-xs text-muted">#{p.overall_pick}</span>
              <span className="min-w-0 flex-1 truncate font-medium">{p.player_name ?? p.player_id}</span>
              <span className="shrink-0 truncate text-xs text-muted">
                {teamName(p.team_slot)}
                {p.source && p.source !== "yahoo_sync" && <span className="ml-1 text-warn">{p.source}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="border-t border-line px-4">
        <Expander bare summary="Full board grid">
          <div className="-mx-4">
            <DraftBoard draft={draft} pickMap={pickMap} currentPick={currentPick} teamName={teamName} />
          </div>
        </Expander>
      </div>
    </div>
  );
}

function DraftBoard({
  draft,
  pickMap,
  currentPick,
  teamName,
}: {
  draft: Draft;
  pickMap: Map<number, DraftPick>;
  currentPick: number | null;
  teamName: (s: number) => string;
}) {
  const N = draft.num_teams;
  const slots = Array.from({ length: N }, (_, i) => i + 1);
  const rounds = Array.from({ length: draft.num_rounds }, (_, i) => i + 1);
  const custom = customRoundsOf(draft.config, N, draft.num_rounds);
  const order = orderForDraft(draft);
  return (
    <div className="scroll-thin overflow-x-auto border-t border-line">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            <th className="sticky left-0 z-10 w-10 bg-panel px-1 py-1.5 text-[10px] font-medium text-muted">Rd</th>
            {slots.map((s) => (
              <th
                key={s}
                className={`min-w-[88px] border-l border-line px-1 py-1.5 text-left text-[10.5px] font-medium ${s === draft.my_slot ? "bg-mine text-accent" : "text-muted"}`}
              >
                <span className="num">{s}</span> <span className="truncate">{teamName(s)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rounds.map((r) => {
            const inRound = order.filter((p) => p.round === r);
            const bySlot = new Map(inRound.map((p) => [p.slot, p.overall]));
            const rev = custom ? false : inRound[0]?.slot !== 1;
            return (
              <tr key={r} className="border-t border-line">
                <td className="sticky left-0 z-10 bg-panel px-1 py-1 text-center text-[10.5px] text-muted">
                  <span className="num">{r}</span>
                  <span className="ml-0.5">{custom ? "*" : rev ? "←" : "→"}</span>
                </td>
                {slots.map((s) => {
                  const overall = bySlot.get(s) ?? 0;
                  const p = pickMap.get(overall);
                  const isCur = overall === currentPick;
                  const mine = s === draft.my_slot;
                  return (
                    <td
                      key={s}
                      className={`h-11 border-l border-line px-1 py-0.5 align-top ${mine ? "bg-mine" : ""} ${isCur ? "outline-2 -outline-offset-2 outline-accent" : ""}`}
                    >
                      <div className="num text-[9.5px] text-muted">
                        {overall}
                        {p?.source && p.source !== "yahoo_sync" && <span className="ml-1 text-warn">{p.source}</span>}
                      </div>
                      <div className={`truncate leading-tight ${p ? "font-medium" : "text-muted"}`} title={p?.player_name ?? ""}>
                        {p ? p.player_name ?? p.player_id : isCur ? "on clock" : ""}
                      </div>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
