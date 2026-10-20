import type { DraftLive } from "@/lib/data";
import type { AgentEvent } from "@/lib/types";
import { CAT_LABEL } from "@/lib/categories";
import { fmt, pct } from "@/lib/format";
import type { PlayerRow } from "@/lib/players";
import { Badge, JsonView } from "@/components/ui";
import { Ago } from "@/components/client";
import { KindIcon } from "@/components/KindIcon";
import { type Candidate, ImpactStrip, probTone, Expander } from "./parts";

/* "Why / all options" expander and the agent activity line of the draft room. */

export function WhyPanel({
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
              <ImpactStrip impact={c.rec?.cat_impact} />
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
                <ImpactStrip impact={c.row?.cat_values} scale={1.5} />
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

export function AgentLine({ live }: { live: DraftLive | null }) {
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
