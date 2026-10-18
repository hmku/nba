"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { anonClient } from "@/lib/supabase";
import { loadDraftLive, loadDrafts, loadValues, latestRun, slimValues, type DraftLive, type ValuesBundle } from "@/lib/data";
import type { Draft, DraftPick, RecItem, RecPayload, AgentEvent } from "@/lib/types";
import { pickInfo, picksForSlot } from "@/lib/draftOrder";
import { CATS, CAT_LABEL, POSITIONS, heatStyle } from "@/lib/categories";
import { fmt, pct, signed, asStringArray } from "@/lib/format";
import { matchesPos, normName, toRows, type PlayerRow } from "@/lib/players";
import { fallbackRanking, probAvailableFromAdp } from "@/lib/fallback";
import { Badge, Card, Empty, JsonView, statusTone } from "@/components/ui";
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
  const N = draft.num_teams;
  const R = draft.num_rounds;
  const total = N * R;
  const orderType = draft.order_type;
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
  const cur = complete ? null : pickInfo(currentPick, N, orderType);
  const myAll = picksForSlot(draft.my_slot, N, R, orderType);
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

  return (
    <div className="space-y-3">
      <DraftHeader
        draft={draft}
        drafts={drafts}
        onSwitch={onSwitch}
        currentPick={complete ? null : currentPick}
        cur={cur}
        teamName={teamName}
        total={total}
        picksMade={picks.length}
        nextMine={nextMine}
        picksUntil={picksUntil}
        myUpcoming={myUpcoming}
        lastOk={lastOk}
        pollError={pollError}
      />
      {gaps.length > 0 && (
        <div className="rounded-lg border border-warn/50 bg-warn/10 px-3 py-2 text-xs text-warn">
          Missing picks below #{maxPick}: {gaps.slice(0, 20).join(", ")}
          {gaps.length > 20 ? ` (+${gaps.length - 20} more)` : ""}. Re-sync from Yahoo or enter them manually (set Overall #).
        </div>
      )}
      {draft.status === "pending" && picks.length === 0 && (
        <div className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-xs text-muted">
          Draft not started (status <b>pending</b>). My first pick is #{nextMine ?? "—"}; the board starts updating as soon as picks are synced.
        </div>
      )}
      {onClock && (
        <div className="rounded-lg border border-accent bg-accent/15 px-4 py-3 text-center text-base font-semibold text-accent animate-pulse">
          YOU ARE ON THE CLOCK — pick #{currentPick}
        </div>
      )}

      <div className="flex flex-col gap-3 lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(320px,380px)] lg:items-start">
        <div className="flex flex-col gap-3 max-lg:contents">
          <div className="order-1">
            <RecommendationsCard
              rec={rec}
              fresh={recFresh}
              draftedIds={draftedIds}
              picksMade={picks.length}
              nextMine={nextMine}
              recCount={live?.recCount ?? 0}
            />
          </div>
          {!recFresh && (
            <div className="order-2">
              <FallbackCard
                available={available}
                myRoster={myRoster}
                picksUntil={picksUntil ?? 0}
                currentPick={currentPick}
                nextMine={nextMine}
                onSelect={setSelected}
              />
            </div>
          )}
          <div className="order-6">
            <AvailableTable
              rows={available}
              runId={values.run?.run_id ?? null}
              runAt={values.run?.created_at ?? null}
              picksUntil={picksUntil ?? 0}
              currentPick={currentPick}
              onSelect={setSelected}
              errors={values.errors}
            />
          </div>
        </div>
        <div className="flex flex-col gap-3 max-lg:contents">
          <div className="order-3">
            <AgentActionCard live={live} />
          </div>
          <div className="order-4">
            <ManualPickCard
              draft={draft}
              currentPick={complete ? null : currentPick}
              available={available}
              selected={selected}
              setSelected={setSelected}
              teamName={teamName}
              lastPick={picks.length ? picks.reduce((a, b) => (b.overall_pick > a.overall_pick ? b : a)) : null}
              onDone={onRefresh}
            />
          </div>
          <div className="order-5">
            <RosterCard myPicks={myPicks} rowById={rowById} rowByName={rowByName} myRoster={myRoster} payload={payload} />
          </div>
        </div>
      </div>

      <DraftBoard draft={draft} pickMap={pickMap} currentPick={complete ? null : currentPick} teamName={teamName} />
    </div>
  );
}

/* ------------------------------------------------------------------ header */

function DraftHeader(props: {
  draft: Draft;
  drafts: Draft[];
  onSwitch: (id: string) => void;
  currentPick: number | null;
  cur: { round: number; slot: number; pickInRound: number } | null;
  teamName: (s: number) => string;
  total: number;
  picksMade: number;
  nextMine: number | null;
  picksUntil: number | null;
  myUpcoming: number[];
  lastOk: string | null;
  pollError: string | null;
}) {
  const { draft, drafts, cur } = props;
  const now = useNow(2000);
  const lagging = !props.lastOk || now - Date.parse(props.lastOk) > 20000;
  return (
    <div className="rounded-lg border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <select
          value={draft.draft_id}
          onChange={(e) => props.onSwitch(e.target.value)}
          className="max-w-[60vw] rounded border border-line bg-panel-2 px-2 py-1 text-sm font-medium outline-none focus:border-accent"
        >
          {drafts.map((d) => (
            <option key={d.draft_id} value={d.draft_id}>
              {d.is_mock ? "[mock] " : ""}
              {d.draft_id} · {d.status}
            </option>
          ))}
        </select>
        <Badge tone={statusTone(draft.status)}>{draft.status}</Badge>
        {draft.is_mock && <Badge tone="warn">mock</Badge>}
        <Badge>{draft.order_type === "snake_3rr" ? "snake · 3RR" : draft.order_type}</Badge>
        <span className="text-xs text-muted">
          {draft.num_teams} teams · {draft.num_rounds} rds · my slot {draft.my_slot}
        </span>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted">
          <span className={`inline-block h-2 w-2 rounded-full ${props.pollError || lagging ? "bg-bad" : "bg-good"}`} />
          {props.pollError ? "sync error" : "live"} · <Ago ts={props.lastOk} />
        </span>
      </div>
      {props.pollError && <div className="border-b border-line px-3 py-1.5 text-xs text-bad">{props.pollError}</div>}
      <div className="grid grid-cols-2 gap-3 px-3 py-3 sm:grid-cols-4">
        <div>
          <div className="text-[10.5px] uppercase tracking-wider text-muted">On the clock</div>
          {props.currentPick && cur ? (
            <>
              <div className="num text-xl font-semibold">#{props.currentPick}</div>
              <div className="truncate text-xs text-muted">
                R{cur.round}.{cur.pickInRound} · {props.teamName(cur.slot)}
              </div>
            </>
          ) : (
            <div className="text-xl font-semibold text-good">Complete</div>
          )}
        </div>
        <div>
          <div className="text-[10.5px] uppercase tracking-wider text-muted">My next pick</div>
          <div className="num text-xl font-semibold text-accent">{props.nextMine ? `#${props.nextMine}` : "—"}</div>
          <div className="text-xs text-muted">
            {props.picksUntil == null ? "no picks left" : props.picksUntil === 0 ? "now!" : `in ${props.picksUntil} pick${props.picksUntil === 1 ? "" : "s"}`}
          </div>
        </div>
        <div>
          <div className="text-[10.5px] uppercase tracking-wider text-muted">Picks made</div>
          <div className="num text-xl font-semibold">
            {props.picksMade}
            <span className="text-sm text-muted">/{props.total}</span>
          </div>
          <div className="mt-1 h-1 rounded bg-panel-2">
            <div className="h-1 rounded bg-accent" style={{ width: `${(props.picksMade / props.total) * 100}%` }} />
          </div>
        </div>
        <div className="min-w-0">
          <div className="text-[10.5px] uppercase tracking-wider text-muted">My upcoming</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {props.myUpcoming.slice(0, 6).map((p, i) => (
              <span
                key={p}
                className={`num rounded border px-1.5 text-xs ${i === 0 ? "border-accent text-accent" : "border-line text-muted"}`}
              >
                {p}
              </span>
            ))}
            {props.myUpcoming.length > 6 && <span className="text-xs text-muted">+{props.myUpcoming.length - 6}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ recommendations */

function CatStrip({ impact, scale = 0.6 }: { impact?: Record<string, number | undefined> | null; scale?: number }) {
  return (
    <div className="grid grid-cols-9 gap-px overflow-hidden rounded border border-line text-center">
      {CATS.map((c) => {
        const v = impact?.[c];
        return (
          <div key={c} className="bg-panel-2 px-0.5 py-0.5" style={heatStyle(v, scale)}>
            <div className="text-[9px] leading-3 text-muted">{CAT_LABEL[c]}</div>
            <div className="num text-[10.5px] leading-4">{v == null ? "·" : signed(v, 1)}</div>
          </div>
        );
      })}
    </div>
  );
}

function ProbBar({ p, label }: { p: number | null | undefined; label?: string }) {
  if (p == null) return <span className="text-xs text-muted">—</span>;
  const tone = p >= 0.6 ? "bg-good" : p >= 0.3 ? "bg-warn" : "bg-bad";
  return (
    <div className="flex items-center gap-1.5" title={label}>
      <div className="h-1.5 w-12 rounded bg-panel-2">
        <div className={`h-1.5 rounded ${tone}`} style={{ width: `${Math.max(3, p * 100)}%` }} />
      </div>
      <span className="num text-xs">{pct(p)}</span>
    </div>
  );
}

function RecRow({ r, i, taken }: { r: RecItem; i: number; taken: boolean }) {
  const pos = asStringArray(r.positions);
  return (
    <li className={`border-t border-line px-3 py-2.5 first:border-t-0 ${taken ? "opacity-40" : ""} ${i === 0 && !taken ? "bg-accent/5" : ""}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="num w-5 text-sm text-muted">{i + 1}</span>
        <span className={`text-[15px] font-semibold ${taken ? "line-through" : ""}`}>{r.name ?? r.player_id}</span>
        <span className="text-xs text-muted">{r.team}</span>
        {pos.map((p) => (
          <Badge key={p}>{p}</Badge>
        ))}
        {taken && <Badge tone="bad">taken</Badge>}
        <span className="ml-auto flex items-center gap-3 text-xs">
          <span title="optimizer score">
            <span className="text-muted">score </span>
            <span className="num font-semibold">{fmt(r.score)}</span>
          </span>
          <span title="value">
            <span className="text-muted">val </span>
            <span className="num">{fmt(r.value)}</span>
          </span>
          <span title="marginal value to my roster">
            <span className="text-muted">mv </span>
            <span className="num">{fmt(r.marginal_value)}</span>
          </span>
          <span title="ADP">
            <span className="text-muted">adp </span>
            <span className="num">{fmt(r.adp, 1)}</span>
          </span>
        </span>
      </div>
      <div className="mt-1.5 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-center">
        <CatStrip impact={r.cat_impact} />
        <div className="flex items-center gap-1 text-xs text-muted">
          <span>avail@next</span>
          <ProbBar p={r.prob_available_next} label="probability still available at my next pick" />
        </div>
      </div>
      {r.reason && <p className="mt-1 text-xs leading-snug text-muted">{r.reason}</p>}
    </li>
  );
}

function RecommendationsCard({
  rec,
  fresh,
  draftedIds,
  picksMade,
  nextMine,
  recCount,
}: {
  rec: DraftLive["rec"];
  fresh: boolean;
  draftedIds: Set<string>;
  picksMade: number;
  nextMine: number | null;
  recCount: number;
}) {
  const p = rec?.payload;
  const items = p?.recommendations ?? [];
  return (
    <Card
      title="Model recommendations"
      bodyClassName="p-0"
      right={
        rec ? (
          <>
            {fresh ? <Badge tone="good">fresh</Badge> : <Badge tone="warn">stale</Badge>}
            <span className="num">
              as of {rec.as_of_pick} → for #{rec.for_pick ?? p?.for_pick ?? "?"}
            </span>
            <Ago ts={p?.generated_at ?? rec.created_at} />
          </>
        ) : null
      }
    >
      {!rec ? (
        <Empty>No recommendation rows for this draft yet. Fallback ranking below.</Empty>
      ) : (
        <>
          {!fresh && (
            <div className="border-b border-line bg-warn/10 px-3 py-1.5 text-xs text-warn">
              Recommendation is for an older board state ({rec.as_of_pick} picks made, now {picksMade}
              {nextMine ? `, my next #${nextMine}` : ""}). Use the client-side fallback below until the optimizer catches up.
            </div>
          )}
          {(p?.strategy?.name || p?.team_profile) && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3 py-2 text-xs">
              {p?.strategy?.name && (
                <span>
                  <span className="text-muted">strategy </span>
                  <span className="font-medium">{p.strategy.name}</span>
                </span>
              )}
              {p?.team_profile?.expected_category_wins != null && (
                <span>
                  <span className="text-muted">exp cat wins </span>
                  <span className="num font-medium">{fmt(p.team_profile.expected_category_wins, 1)}</span>
                </span>
              )}
              {!!p?.team_profile?.punts?.length && (
                <span>
                  <span className="text-muted">punts </span>
                  {p.team_profile.punts.map((x) => (
                    <Badge key={x} tone="warn">
                      {CAT_LABEL[x as keyof typeof CAT_LABEL] ?? x}
                    </Badge>
                  ))}
                </span>
              )}
              {p?.strategy?.notes && <span className="w-full text-muted">{p.strategy.notes}</span>}
            </div>
          )}
          {!!p?.warnings?.length && (
            <ul className="border-b border-line px-3 py-1.5 text-xs text-warn">
              {p.warnings.map((w, i) => (
                <li key={i}>⚠ {w}</li>
              ))}
            </ul>
          )}
          {items.length ? (
            <ol>
              {items.slice(0, 10).map((r, i) => (
                <RecRow key={r.player_id ?? i} r={r} i={i} taken={!!r.player_id && draftedIds.has(r.player_id)} />
              ))}
            </ol>
          ) : (
            <Empty>Payload has no recommendations.</Empty>
          )}
          <div className="flex items-center justify-between border-t border-line px-3 py-1.5 text-[11px] text-muted">
            <span>
              rec <code>{rec.rec_id}</code> · run <code>{rec.model_run_id ?? p?.model_run_id ?? "—"}</code>
            </span>
            <span>{recCount} recs for this draft</span>
          </div>
        </>
      )}
    </Card>
  );
}

function FallbackCard({
  available,
  myRoster,
  picksUntil,
  currentPick,
  nextMine,
  onSelect,
}: {
  available: PlayerRow[];
  myRoster: PlayerRow[];
  picksUntil: number;
  currentPick: number;
  nextMine: number | null;
  onSelect: (p: PlayerRow) => void;
}) {
  const { items, weakest } = useMemo(
    () => fallbackRanking(available, myRoster, picksUntil, currentPick, 8),
    [available, myRoster, picksUntil, currentPick],
  );
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Fallback ranking <Badge tone="warn">fallback (client-side)</Badge>
        </span>
      }
      bodyClassName="p-0"
      right={nextMine ? <span className="num">for #{nextMine}</span> : null}
    >
      <p className="border-b border-line px-3 py-1.5 text-[11px] text-muted">
        Best available by player_values.value + 0.25× value in my weakest categories
        {weakest.length ? ` (${weakest.map((c) => CAT_LABEL[c as keyof typeof CAT_LABEL]).join(", ")})` : ""} + 0.4 if it fills an empty
        G/F/C group. P(avail) is a rough ADP estimate. Not the optimizer.
      </p>
      {items.length === 0 ? (
        <Empty>No player values loaded.</Empty>
      ) : (
        <ol>
          {items.map((it, i) => (
            <li key={it.row.player_id} className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line px-3 py-2 first:border-t-0">
              <span className="num w-5 text-sm text-muted">{i + 1}</span>
              <span className="font-semibold">{it.row.name}</span>
              <span className="text-xs text-muted">{it.row.team}</span>
              {it.row.positions.map((p) => (
                <Badge key={p}>{p}</Badge>
              ))}
              <span className="ml-auto flex items-center gap-3 text-xs">
                <span>
                  <span className="text-muted">adj </span>
                  <span className="num font-semibold">{fmt(it.score)}</span>
                </span>
                <span>
                  <span className="text-muted">val </span>
                  <span className="num">{fmt(it.row.value)}</span>
                </span>
                <ProbBar p={it.probAvail} label="ADP-based estimate" />
                <button onClick={() => onSelect(it.row)} className="rounded border border-line px-1.5 text-[11px] text-muted hover:border-accent hover:text-accent">
                  select
                </button>
              </span>
              <span className="w-full pl-7 text-[11px] text-muted">{it.reason}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ agent */

function AgentActionCard({ live }: { live: DraftLive | null }) {
  const runs = live?.agentRuns ?? [];
  const events = live?.agentEvents ?? [];
  const run = runs[0];
  const lastDecision = events.find((e) => e.kind === "decision");
  const dec = live?.decisions?.[0];
  return (
    <Card
      title="Agent next action"
      right={run ? <Badge tone={statusTone(run.status)}>{run.status}</Badge> : null}
    >
      {!run ? (
        <Empty>No agent run references this draft (agent_runs.task).</Empty>
      ) : (
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
            <span className="font-medium text-fg">{run.agent}</span>
            <Badge tone={run.mode === "live" ? "bad" : "neutral"}>{run.mode ?? "?"}</Badge>
            <span>
              heartbeat <Ago ts={run.heartbeat_at} staleAfter={run.status === "running" ? 120 : undefined} />
            </span>
          </div>
          {lastDecision ? (
            <div className="rounded border border-accent/40 bg-accent/5 p-2">
              <div className="flex items-center gap-1.5 text-[11px] text-muted">
                <KindIcon kind="decision" /> latest decision · <Ago ts={lastDecision.ts} />
              </div>
              <div className="mt-0.5 font-medium leading-snug">{lastDecision.message}</div>
              {lastDecision.payload != null && <JsonView value={lastDecision.payload} label="payload" />}
            </div>
          ) : (
            <div className="text-xs text-muted">No decision events yet.</div>
          )}
          {dec && (
            <div className={`rounded border p-2 text-xs ${dec.agrees_with_model === false ? "border-warn/50 bg-warn/10" : "border-line"}`}>
              <div className="flex items-center gap-2">
                <span className="text-muted">decision row</span>
                <Badge>{dec.kind}</Badge>
                {dec.agrees_with_model === false ? <Badge tone="warn">disagrees with model</Badge> : dec.agrees_with_model ? <Badge tone="good">agrees</Badge> : null}
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
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ manual */

function ManualPickCard({
  draft,
  currentPick,
  available,
  selected,
  setSelected,
  teamName,
  lastPick,
  onDone,
}: {
  draft: Draft;
  currentPick: number | null;
  available: PlayerRow[];
  selected: PlayerRow | null;
  setSelected: (p: PlayerRow | null) => void;
  teamName: (s: number) => string;
  lastPick: DraftPick | null;
  onDone: () => void;
}) {
  const ws = useWriteStatus();
  const [overall, setOverall] = useState<string>("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const effectiveOverall = overall === "" ? currentPick : Number(overall);
  const info = effectiveOverall ? pickInfo(effectiveOverall, draft.num_teams, draft.order_type) : null;
  const canWrite = ws.writesEnabled && ws.authed;

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
      setMsg({ ok: true, text: `Recorded #${effectiveOverall}: ${name}` });
      setSelected(null);
      setQ("");
      setOverall("");
      onDone();
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
    setMsg(r.ok ? { ok: true, text: `Removed #${lastPick.overall_pick}` } : { ok: false, text: j.error || `HTTP ${r.status}` });
    onDone();
  };

  return (
    <Card title="Manual pick entry" right={<WriteAccess compact />}>
      {!ws.loaded ? (
        <div className="text-xs text-muted">Checking write access…</div>
      ) : !ws.writesEnabled ? (
        <div className="rounded border border-warn/40 bg-warn/10 p-2 text-xs text-warn">
          Writes disabled: {ws.reason ?? "server not configured"} Picks can still be recorded from the CLI:{" "}
          <code>fantasy draft pick &lt;overall&gt; &lt;player&gt;</code>
        </div>
      ) : null}
      <fieldset disabled={!canWrite || busy} className={`mt-2 space-y-2 ${!canWrite ? "opacity-60" : ""}`}>
        <div className="flex items-end gap-2">
          <label className="w-24 shrink-0">
            <span className="block text-[10.5px] uppercase tracking-wider text-muted">Overall #</span>
            <input
              inputMode="numeric"
              value={overall}
              placeholder={currentPick ? String(currentPick) : ""}
              onChange={(e) => setOverall(e.target.value.replace(/\D/g, ""))}
              className="num w-full rounded border border-line bg-panel-2 px-2 py-2 text-base outline-none focus:border-accent"
            />
          </label>
          <div className="pb-2 text-xs text-muted">
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
          <span className="block text-[10.5px] uppercase tracking-wider text-muted">Player</span>
          {selected ? (
            <div className="flex items-center gap-2 rounded border border-accent bg-accent/5 px-2 py-2">
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
                className="w-full rounded border border-line bg-panel-2 px-2 py-2 text-base outline-none focus:border-accent"
              />
              {matches.length > 0 && (
                <ul className="absolute z-30 mt-1 max-h-72 w-full overflow-auto rounded border border-line bg-panel shadow-xl">
                  {matches.map((m) => (
                    <li key={m.player_id}>
                      <button
                        type="button"
                        onClick={() => {
                          setSelected(m);
                          setQ("");
                        }}
                        className="flex w-full items-center gap-2 px-2 py-2 text-left text-sm hover:bg-panel-2"
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
            className="flex-1 rounded bg-accent px-3 py-2 text-sm font-semibold text-accent-fg disabled:opacity-40"
          >
            {busy ? "Saving…" : `Record pick${effectiveOverall ? ` #${effectiveOverall}` : ""}`}
          </button>
          {lastPick?.source === "manual" && (
            <button type="button" onClick={undo} className="rounded border border-line px-3 py-2 text-xs text-muted hover:text-bad">
              Undo #{lastPick.overall_pick}
            </button>
          )}
        </div>
      </fieldset>
      {msg && <div className={`mt-2 text-xs ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</div>}
    </Card>
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
  const maxAbs = Math.max(1, ...totals.map((t) => Math.abs(t.v)));
  const sorted = [...totals].sort((a, b) => b.v - a.v);
  const strengths = myRoster.length ? sorted.slice(0, 3) : [];
  const weaknesses = myRoster.length ? sorted.slice(-3).reverse() : [];
  const profile = payload?.team_profile?.categories ?? {};
  const posCount: Record<string, number> = {};
  for (const r of myRoster) for (const p of r.positions) posCount[p] = (posCount[p] ?? 0) + 1;

  return (
    <Card title="My roster" right={<span className="num">{myPicks.length} players</span>}>
      {myPicks.length === 0 ? (
        <Empty>No picks yet.</Empty>
      ) : (
        <ul className="mb-3 space-y-1">
          {myPicks.map((p) => {
            const row = (p.player_id && rowById.get(p.player_id)) || rowByName.get(normName(p.player_name));
            return (
              <li key={p.overall_pick} className="flex items-center gap-2 text-sm">
                <span className="num w-10 text-xs text-muted">
                  R{p.round}·{p.overall_pick}
                </span>
                <span className="truncate font-medium">{p.player_name ?? row?.name ?? p.player_id}</span>
                <span className="text-xs text-muted">{row?.positions.join("/")}</span>
                <span className="num ml-auto text-xs">{fmt(row?.value)}</span>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mb-1 flex items-center justify-between text-[10.5px] uppercase tracking-wider text-muted">
        <span>Category profile (Σ cat value)</span>
        {Object.keys(profile).length > 0 && <span>win%</span>}
      </div>
      <div className="space-y-1">
        {totals.map(({ c, v }) => {
          const w = (Math.abs(v) / maxAbs) * 50;
          const wp = profile[c]?.win_prob;
          return (
            <div key={c} className="flex items-center gap-2 text-xs">
              <span className="w-8 text-muted">{CAT_LABEL[c]}</span>
              <div className="relative h-3 flex-1 rounded bg-panel-2">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line" />
                <div
                  className={`absolute inset-y-0 rounded ${v >= 0 ? "bg-good/70" : "bg-bad/70"}`}
                  style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }}
                />
              </div>
              <span className="num w-10 text-right">{signed(v, 1)}</span>
              {Object.keys(profile).length > 0 && <span className="num w-9 text-right text-muted">{wp != null ? pct(wp) : "—"}</span>}
            </div>
          );
        })}
      </div>
      {myRoster.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1 text-xs">
          {strengths.map((s) => (
            <Badge key={s.c} tone="good">
              ▲ {CAT_LABEL[s.c]}
            </Badge>
          ))}
          {weaknesses.map((s) => (
            <Badge key={s.c} tone="bad">
              ▼ {CAT_LABEL[s.c]}
            </Badge>
          ))}
          <span className="ml-auto flex gap-1">
            {POSITIONS.map((p) => (
              <span key={p} className="num text-muted">
                {p}:{posCount[p] ?? 0}
              </span>
            ))}
          </span>
        </div>
      )}
    </Card>
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
  const [limit, setLimit] = useState(60);

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
    <Card
      title="Available players"
      bodyClassName="p-0"
      right={
        <span className="truncate">
          {rows.length} avail · run <code>{runId ?? "none"}</code> {runAt && <Ago ts={runAt} />}
        </span>
      }
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name / team"
          className="min-w-0 flex-1 rounded border border-line bg-panel-2 px-2 py-1.5 text-sm outline-none focus:border-accent sm:max-w-56"
        />
        <div className="flex flex-wrap gap-1">
          {["ALL", "PG", "SG", "G", "SF", "PF", "F", "C"].map((p) => (
            <button
              key={p}
              onClick={() => setPos(p)}
              className={`rounded border px-2 py-1 text-xs ${pos === p ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-fg"}`}
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
                  <td className="sticky left-0 bg-panel max-w-[44vw] sm:max-w-none">
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
                      title="Select in manual pick panel"
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
            <button onClick={() => setLimit((l) => l + 100)} className="w-full border-t border-line py-2 text-xs text-accent">
              Show more ({filtered.length - limit} more)
            </button>
          )}
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ board */

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
  const ref = useRef<HTMLTableCellElement | null>(null);
  return (
    <Card title="Draft board" bodyClassName="p-0" right={<span>rounds × slots · my column highlighted</span>}>
      <div className="scroll-thin overflow-x-auto">
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
              const rev = pickInfo((r - 1) * N + 1, N, draft.order_type).slot !== 1;
              return (
                <tr key={r} className="border-t border-line">
                  <td className="sticky left-0 z-10 bg-panel px-1 py-1 text-center text-[10.5px] text-muted">
                    <span className="num">{r}</span>
                    <span className="ml-0.5">{rev ? "←" : "→"}</span>
                  </td>
                  {slots.map((s) => {
                    const overall = rev ? (r - 1) * N + (N - s) + 1 : (r - 1) * N + s;
                    const p = pickMap.get(overall);
                    const isCur = overall === currentPick;
                    const mine = s === draft.my_slot;
                    return (
                      <td
                        key={s}
                        ref={isCur ? ref : undefined}
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
    </Card>
  );
}
