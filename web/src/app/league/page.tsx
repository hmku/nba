import Link from "next/link";
import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { defaultDraftId, loadDrafts, loadPlayers } from "@/lib/data";
import { latestSimRuns, parseSeasonSim, pickPreseasonRun, pickSeasonRun, type SeasonSim, type TeamRow } from "@/lib/sim";
import { CATS, CAT_LABEL, heatStyle } from "@/lib/categories";
import type { Draft, DraftPick, Player } from "@/lib/types";
import { fmt, pct, shortTime } from "@/lib/format";
import { ErrorNote } from "@/components/ui";
import { CatLabel, CatStrip, EmptyState, Explain, Panel, SectionTitle, ShareBar } from "@/components/viz";
import { explanationText } from "@/lib/sim";
import { ScenarioOutlook } from "@/components/Outlook";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "League" };

export default async function LeaguePage() {
  const db = anonClient();
  const [{ runs, error }, { drafts, error: dErr }] = await Promise.all([latestSimRuns(db, "season_sim", 60), loadDrafts(db)]);
  const pre = parseSeasonSim(pickPreseasonRun(runs));
  const post = parseSeasonSim(pickSeasonRun(runs));
  // Post-draft (real rosters) wins if it is newer than the pre-draft projection.
  const sim =
    post && (!pre || (post.run.created_at ?? "") >= (pre.run.created_at ?? "")) ? post : (pre ?? post);
  const draftId = defaultDraftId(drafts);
  // After the draft, follow the draft the simulation used (names + rosters); else the default draft.
  const draft =
    (sim && !sim.isPreseason ? drafts.find((d) => d.draft_id === sim.draftId) : undefined) ??
    drafts.find((d) => d.draft_id === draftId) ??
    null;
  const { data: pickData } = draft
    ? await db.from("draft_picks").select("*").eq("draft_id", draft.draft_id).order("overall_pick").limit(1000)
    : { data: [] };
  const picks = (pickData as DraftPick[]) ?? [];
  const needPlayers = picks.length > 0 || (sim && Object.keys(sim.rosters).length > 0);
  const players = needPlayers ? await loadPlayers(db) : {};
  const teamNames = (draft?.config?.team_names ?? {}) as Record<string, string>;
  // Names: drafts.config.team_names, else the post-draft simulation's own names (Yahoo team names)
  const simNames: Record<string, string> = {};
  if (sim && !sim.isPreseason) for (const r of sim.table) simNames[String(r.team)] = r.name;
  const nameOf = (team: number, fallback?: string) => teamNames[String(team)] ?? fallback ?? simNames[String(team)] ?? `Team ${team}`;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between px-1">
        <h1 className="text-xl font-semibold tracking-tight">League</h1>
        {sim && (
          <span className={`rounded-full px-2 py-0.5 text-[11px] ${sim.isPreseason ? "bg-warn/10 text-warn" : "bg-accent/10 text-accent"}`}>
            {sim.isPreseason ? "pre-draft projection" : sim.isPostDraft ? "post-draft · actual rosters" : "from rosters"}
          </span>
        )}
      </div>
      <ErrorNote error={error ?? dErr} />

      {!sim ? (
        <EmptyState icon="▦" title="No season simulation yet">
          Projected standings (P(playoffs), P(title), expected rank for all 12 teams) and each team&apos;s category strengths appear once{" "}
          <code className="num">fantasy sim preseason</code> (before the draft) or <code className="num">fantasy sim season</code> (after) has
          been published.
        </EmptyState>
      ) : (
        <>
          {sim.isPreseason && (
            <>
              <SectionTitle right="pre-draft · scenario range">My season outlook</SectionTitle>
              <ScenarioOutlook compact />
            </>
          )}
          <Standings sim={sim} nameOf={nameOf} />
          <Heatmap sim={sim} nameOf={nameOf} />
          {!sim.isPreseason && pre && (
            <p className="px-1 text-xs text-muted">
              The pre-draft projection (simulated drafts, before real rosters existed) stays under{" "}
              <Link href="/draft?view=model" className="text-accent">
                Draft › Model &amp; sims
              </Link>{" "}
              and is labelled pre-draft there.
            </p>
          )}
        </>
      )}

      <Rosters draft={draft} picks={picks} players={players} sim={sim} nameOf={nameOf} />
    </div>
  );
}

function Standings({ sim, nameOf }: { sim: SeasonSim; nameOf: (t: number, f?: string) => string }) {
  const rows = [...sim.table].sort((a, b) => (a.expRank ?? 99) - (b.expRank ?? 99));
  const isMe = (r: TeamRow) => r.isMe || r.team === sim.myTeamId;
  const ex = sim.explanations;
  return (
    <>
      <SectionTitle right={`${sim.nSims ?? "?"} sims${sim.nLeagues ? ` · ${sim.nLeagues} drafts` : ""} · ${shortTime(sim.run.created_at)}`}>
        {sim.isPreseason ? "Standings · status quo" : "Projected standings"}
      </SectionTitle>
      <Panel className="overflow-hidden">
        {sim.isPreseason && (
          <p className="border-b border-line bg-warn/10 px-4 py-2 text-[12.5px] leading-snug text-warn">
            <span className="font-semibold">Status-quo scenario (optimistic):</span> other teams draft by noisy ADP and
            everything is graded with our projections. My realistic range is above.
          </p>
        )}
        <table className="w-full text-[13.5px]">
          <thead>
            <tr className="text-[10.5px] tracking-wider text-muted uppercase">
              <th className="py-2 pl-3 text-left font-medium">Team</th>
              <th className="px-1 text-right font-medium">Rank</th>
              <th className="px-1 text-right font-medium">Playoffs</th>
              <th className="px-1 text-right font-medium" title="Top-2 seed: first-round bye">Bye</th>
              <th className="pr-3 pl-1 text-right font-medium">Title</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.team} className={`border-t border-line ${isMe(r) ? "bg-mine" : ""}`}>
                <td className="max-w-[9rem] py-2 pl-3">
                  <div className={`truncate ${isMe(r) ? "font-semibold" : ""}`}>
                    {sim.isPreseason ? (isMe(r) ? "Me" : `Slot ${r.team}`) : nameOf(r.team, r.name)}
                  </div>
                  <div className="mt-1 w-full max-w-[7rem]">
                    <ShareBar v={r.pPlayoffs} tone={isMe(r) ? "accent" : "muted"} title={`P(playoffs) ${pct(r.pPlayoffs, 1)}`} />
                  </div>
                </td>
                <td className="num px-1 text-right">{fmt(r.expRank, 1)}</td>
                <td className="num px-1 text-right" title={r.pPlayoffsSe != null ? `± ${pct(r.pPlayoffsSe, 1)}` : undefined}>
                  {pct(r.pPlayoffs)}
                </td>
                <td className="num px-1 text-right text-muted">{pct(r.pBye)}</td>
                <td className="num pr-3 pl-1 text-right font-medium" title={r.pTitleSe != null ? `± ${pct(r.pTitleSe, 1)}` : undefined}>
                  {pct(r.pTitle)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="space-y-1 border-t border-line px-4 py-2.5 text-xs text-muted">
          {sim.isPreseason ? (
            <p>
              Before the draft: teams are draft slots (I am slot 1); other teams are drafted by noisy ADP, pooled over simulated drafts.
              Real team names and rosters replace this after the draft.
            </p>
          ) : (
            <>
              <p>
                Sorted by expected final regular-season rank. Top 6 make the playoffs (top 2 get a first-round bye); title = wins the
                3-week playoff bracket. Simulated from the drafted rosters with our projections (season-sim, fitted priors); rosters frozen
                apart from streaming, so the spread is wider in reality.
              </p>
              {sim.schedule && sim.schedule.assumedWeeks.length > 0 && (
                <p className="text-warn">
                  Weekly pairings are an assumed round robin (Yahoo&apos;s league schedule not loaded yet). Who plays whom matters: two
                  other random round robins moved mid-table teams&apos; P(playoffs) by up to ~10 pp (mine ~1 pp; P(title) ≤ 1.3 pp), so
                  treat the middle of the table as ± 10 pp until the real schedule is loaded.
                </p>
              )}
            </>
          )}
          <Explain text={explanationText(ex, "league_table")} />
        </div>
      </Panel>
    </>
  );
}

function Heatmap({ sim, nameOf }: { sim: SeasonSim; nameOf: (t: number, f?: string) => string }) {
  const profiles = Object.values(sim.profiles).sort((a, b) => a.team - b.team);
  if (profiles.length === 0) {
    const cp = sim.categoryProfile;
    if (!Object.keys(cp).length) return null;
    return (
      <>
        <SectionTitle right="status-quo scenario">My category strengths</SectionTitle>
        <Panel className="px-3 py-3">
          <CatStrip cells={Object.fromEntries(CATS.map((c) => [c, { value: cp[c]?.rank ?? null, z: cp[c]?.z ?? null }]))} />
          <p className="mt-2 text-xs text-muted">
            Average rank of my team in each category among 12 (1 = best); colour = z-score. All teams&apos; strengths appear after the draft.
          </p>
        </Panel>
      </>
    );
  }
  return (
    <>
      <SectionTitle right="rank 1–12 · colour = z">Category strengths</SectionTitle>
      <Panel className="overflow-hidden">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full border-separate border-spacing-[2px] text-center text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-muted">
                <th className="sticky left-0 z-10 bg-panel pl-2 text-left font-medium">Team</th>
                {CATS.map((c) => (
                  <th key={c} className="px-0.5 font-medium">
                    <CatLabel c={c} className="flex-col !gap-0.5" />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => {
                const me = p.team === sim.myTeamId;
                return (
                  <tr key={p.team}>
                    <td className={`sticky left-0 z-10 max-w-[6.5rem] truncate bg-panel py-1 pr-1 pl-2 text-left ${me ? "font-semibold text-accent" : ""}`}>
                      {nameOf(p.team, p.name)}
                    </td>
                    {CATS.map((c) => (
                      <td
                        key={c}
                        className="num rounded px-1 py-1"
                        style={heatStyle(p.z[c] ?? null, 2)}
                        title={`${CAT_LABEL[c]}: z ${fmt(p.z[c] ?? null, 2)}, rank ${p.rank[c] ?? "—"}`}
                      >
                        {p.rank[c] ?? "—"}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="border-t border-line px-4 py-2 text-xs text-muted">
          Each team&apos;s simulated weekly average, ranked across the league (1 = best; TO: fewest is best). Green = strength, red = weakness.
        </p>
      </Panel>
    </>
  );
}

function Rosters({
  draft,
  picks,
  players,
  sim,
  nameOf,
}: {
  draft: Draft | null;
  picks: DraftPick[];
  players: Record<string, Player>;
  sim: SeasonSim | null;
  nameOf: (t: number, f?: string) => string;
}) {
  const pname = (id: string) => players[id]?.name ?? id.replace(/-/g, " ");
  // 1) real draft picks
  if (draft && picks.length > 0) {
    const bySlot = new Map<number, DraftPick[]>();
    for (const p of picks) bySlot.set(p.team_slot, [...(bySlot.get(p.team_slot) ?? []), p]);
    const slots = Array.from({ length: draft.num_teams }, (_, i) => i + 1);
    return (
      <>
        <SectionTitle right={`${draft.is_mock ? "mock · " : ""}${picks.length}/${draft.num_teams * draft.num_rounds} picks`}>Rosters</SectionTitle>
        <div className="space-y-2">
          {slots.map((s) => {
            const list = bySlot.get(s) ?? [];
            const mine = s === draft.my_slot;
            return (
              <details key={s} open={mine} className="group rounded-xl border border-line bg-panel">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 select-none [&::-webkit-details-marker]:hidden">
                  <span className="text-[10px] text-muted transition-transform group-open:rotate-90">▶</span>
                  <span className={`min-w-0 flex-1 truncate text-[15px] ${mine ? "font-semibold text-accent" : "font-medium"}`}>{nameOf(s)}</span>
                  <span className="num text-xs text-muted">{list.length}</span>
                </summary>
                <ol className="divide-y divide-line border-t border-line text-sm">
                  {list.length === 0 && <li className="px-4 py-2 text-muted">No picks yet</li>}
                  {list.map((p) => {
                    const pl = p.player_id ? players[p.player_id] : undefined;
                    return (
                      <li key={p.overall_pick} className="flex items-baseline gap-2 px-4 py-1.5">
                        <span className="num w-9 shrink-0 text-xs text-muted">#{p.overall_pick}</span>
                        <span className="min-w-0 flex-1 truncate">{pl?.name ?? p.player_name ?? p.player_id ?? "?"}</span>
                        <span className="shrink-0 text-xs text-muted">
                          {pl?.team ?? ""} {pl?.positions?.join("/") ?? ""}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              </details>
            );
          })}
        </div>
      </>
    );
  }
  // 2) simulated rosters from a season run
  const simRosters = sim ? Object.entries(sim.rosters) : [];
  if (sim && !sim.isPreseason && simRosters.length) {
    return (
      <>
        <SectionTitle right="from the simulation">Rosters</SectionTitle>
        <div className="space-y-2">
          {simRosters.map(([t, r]) => (
            <details key={t} open={Number(t) === sim.myTeamId} className="group rounded-xl border border-line bg-panel">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 select-none [&::-webkit-details-marker]:hidden">
                <span className="text-[10px] text-muted transition-transform group-open:rotate-90">▶</span>
                <span className="min-w-0 flex-1 truncate text-[15px] font-medium">{nameOf(Number(t), sim.profiles[t]?.name)}</span>
                <span className="num text-xs text-muted">{r.players.length}</span>
              </summary>
              <ul className="divide-y divide-line border-t border-line text-sm">
                {r.players.map((id) => (
                  <li key={id} className="flex items-baseline gap-2 px-4 py-1.5">
                    <span className="min-w-0 flex-1 truncate">{pname(id)}</span>
                    {r.projected.includes(id) && <span className="text-[11px] text-warn">projected</span>}
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      </>
    );
  }
  // 3) pre-draft: nothing real yet
  const ex = sim?.leagues.find((l) => l.roster.length > 0);
  return (
    <>
      <SectionTitle>Rosters</SectionTitle>
      <Panel className="px-4 py-3 text-[13.5px] text-muted">
        <p>
          Rosters fill in here pick by pick during the draft{draft ? ` (${draft.draft_id}, ${draft.status})` : ""}, from{" "}
          <code className="num">draft_picks</code>.
        </p>
        {ex && (
          <div className="mt-3">
            <div className="text-[11px] tracking-wider uppercase">A simulated roster for me (draft {ex.league + 1})</div>
            <p className="mt-1 leading-relaxed text-fg">{ex.roster.map((p) => p.name).join(" · ")}</p>
            <Link href="/draft?view=model" className="mt-1 inline-block text-accent">
              More in Model & sims →
            </Link>
          </div>
        )}
      </Panel>
    </>
  );
}
