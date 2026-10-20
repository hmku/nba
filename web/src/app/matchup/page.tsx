import Link from "next/link";
import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { defaultDraftId, loadDrafts } from "@/lib/data";
import {
  arr,
  currentWeek,
  latestSimRuns,
  parseSeasonSim,
  pickSeasonRun,
  str,
  todayET,
  type SeasonSim,
  type WeekMatchup,
} from "@/lib/sim";
import { CATS, CAT_LABEL, fmtCat, heatStyle } from "@/lib/categories";
import { weekOpponents, type H2HWeek } from "@/lib/postDraft";
import type { CatKey } from "@/lib/types";
import { fmt, pct, shortTime } from "@/lib/format";
import { ErrorNote } from "@/components/ui";
import { CatLabel, EdgeBar, EmptyState, Hero, Panel, SectionTitle, probText } from "@/components/viz";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Matchup" };

function fmtDate(d: string | null): string {
  if (!d) return "?";
  const t = new Date(`${d}T12:00:00Z`);
  return Number.isNaN(t.getTime()) ? d : t.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export default async function MatchupPage({ searchParams }: { searchParams: Promise<{ week?: string; opp?: string }> }) {
  const { week: wq, opp: oq } = await searchParams;
  const db = anonClient();
  // 60: what-if `compare` / `draft-check` runs share kind season_sim and must not crowd out the season run
  const [{ runs, error }, { drafts }] = await Promise.all([latestSimRuns(db, "season_sim", 60), loadDrafts(db)]);
  const sim = parseSeasonSim(pickSeasonRun(runs));
  const draft = drafts.find((d) => d.draft_id === defaultDraftId(drafts)) ?? null;

  if (!sim || sim.weeks.length === 0)
    return (
      <div className="space-y-3">
        <h1 className="text-xl font-semibold tracking-tight">Matchup</h1>
        <ErrorNote error={error} />
        <EmptyState icon="⚔" title="Your weekly matchup appears after the draft">
          <p>
            Each week you face one opponent across 9 categories. Once the draft (Mon Oct 19, 10 pm ET
            {draft && draft.status !== "pending" ? `; now ${draft.status}` : ""}) is done and rosters are synced from Yahoo, this tab
            shows:
          </p>
          <ul className="mx-auto mt-2 max-w-xs space-y-1 text-left">
            <li>· this week&apos;s opponent, dates and P(win the matchup)</li>
            <li>· per-category win odds with both teams&apos; projected totals</li>
            <li>· games played this week for each side (schedule edge)</li>
            <li>· what to do: categories to chase or concede, streaming adds</li>
          </ul>
          <p className="mt-3">
            Week 1 is expected to run Tue Oct 20 – Sun Nov 1 (a long first week; derived until Yahoo&apos;s schedule is loaded).
          </p>
          <p className="mt-2">
            Source: <code className="num">fantasy sim season</code> on the real rosters (weekly matchups). Before the draft, see the{" "}
            <Link href="/draft?view=model" className="text-accent">
              pre-draft projection
            </Link>
            .
          </p>
        </EmptyState>
      </div>
    );

  const today = todayET();
  const sorted = [...sim.weeks].sort((a, b) => a.week - b.week);
  const req = wq ? sorted.find((w) => w.week === Number(wq)) : null;
  const cur = currentWeek(sorted, today);
  const base = req ?? cur ?? sorted[0];
  const mx = sim.h2h.find((x) => x.week === base.week) ?? null;
  // ?opp=N: the same week against another team (from the weekly H2H matrix) — e.g. Yahoo's real opponent
  const override = oq && mx ? withOpponent(base, mx, Number(oq)) : null;
  const w = override ?? base;
  const when = w.start && w.end ? (w.start <= today && today <= w.end ? "this week" : w.start > today ? (cur?.week === w.week ? "next up" : "upcoming") : "past") : "";
  // Team names: drafts.config.team_names of the draft the simulation used (else the sim's own names)
  const simDraft = drafts.find((d) => d.draft_id === sim.draftId) ?? draft;
  const names: Record<string, string> = { ...simNames(sim), ...((simDraft?.config?.team_names ?? {}) as Record<string, string>) };
  return (
    <div className="space-y-3">
      <WeekStrip weeks={sorted} selected={w.week} current={cur?.week ?? null} />
      {sim.schedule && sim.schedule.assumedWeeks.length > 0 && (
        <p className="rounded-lg bg-warn/10 px-3 py-2 text-[12.5px] leading-snug text-warn">
          <span className="font-semibold">Opponents are assumed.</span> Yahoo&apos;s matchup schedule isn&apos;t loaded yet, so weeks{" "}
          {sim.schedule.assumedWeeks[0]}–{sim.schedule.assumedWeeks[sim.schedule.assumedWeeks.length - 1]} use a round robin. Pick
          the real opponent under <span className="font-medium">vs every opponent</span> below to see that matchup.
        </p>
      )}
      <MatchupView sim={sim} w={w} when={when} names={names} picked={override != null} />
      {mx && <OpponentsTable sim={sim} mx={mx} selected={w.opponent} names={names} />}
    </div>
  );
}

function simNames(sim: SeasonSim): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of sim.table) out[String(r.team)] = r.name;
  for (const o of sim.vsOpponents) if (o.name) out[String(o.team)] = o.name;
  return out;
}

/** The selected week against team `opp`, from the H2H matrix (null if the matrix lacks that pairing). */
function withOpponent(base: WeekMatchup, mx: H2HWeek, opp: number): WeekMatchup | null {
  const c = mx.vs[String(opp)];
  if (!c) return null;
  const catWin: WeekMatchup["catWin"] = {};
  for (const k of CATS) if (c.catWin[k] != null) catWin[k] = c.catWin[k];
  return {
    ...base,
    opponent: opp,
    opponentName: null,
    pWin: c.pWin,
    pTie: c.pTie,
    pLoss: c.pWin != null && c.pTie != null ? Math.max(0, 1 - c.pWin - c.pTie) : null,
    expCatsWon: c.expCatsWon,
    myGames: mx.myGames ?? base.myGames,
    oppGames: c.oppGames,
    catWin,
    cats: {},
    opponentAssumed: false,
  };
}

function OpponentsTable({ sim, mx, selected, names }: { sim: SeasonSim; mx: H2HWeek; selected: number | null; names: Record<string, string> }) {
  const rows = weekOpponents(mx);
  const season = new Map(sim.vsOpponents.map((o) => [o.team, o]));
  if (!rows.length) return null;
  return (
    <>
      <SectionTitle right={`week ${mx.week} · toughest first`}>vs every opponent</SectionTitle>
      <Panel className="overflow-hidden">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full border-separate border-spacing-[2px] text-center text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-muted">
                <th className="sticky left-0 z-10 bg-panel pl-2 text-left font-medium">Opponent</th>
                <th className="px-1 font-medium" title="P(win the matchup) in this week">Wk</th>
                <th className="px-1 font-medium" title="P(win) averaged over all 17 regular-season weeks">Szn</th>
                {CATS.map((c) => (
                  <th key={c} className="px-0.5 font-medium">
                    <CatLabel c={c} className="flex-col !gap-0.5" />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const sel = r.team === selected;
                const sched = r.team === mx.scheduledOpponent;
                return (
                  <tr key={r.team} className={sel ? "bg-accent/10" : ""}>
                    <td className="sticky left-0 z-10 max-w-[7.5rem] truncate bg-panel py-1 pr-1 pl-2 text-left">
                      <Link href={`/matchup?week=${mx.week}&opp=${r.team}`} scroll={false} className={sel ? "font-semibold text-accent" : "text-fg"}>
                        {names[String(r.team)] ?? `Team ${r.team}`}
                      </Link>
                      {sched && <span className="ml-1 text-[10px] text-muted">{mx.opponentAssumed ? "(rr)" : "(sched)"}</span>}
                    </td>
                    <td className={`num px-1 font-medium ${probText(r.pWin)}`}>{pct(r.pWin)}</td>
                    <td className={`num px-1 ${probText(season.get(r.team)?.pWinAnyWeek ?? null)}`}>{pct(season.get(r.team)?.pWinAnyWeek ?? null)}</td>
                    {CATS.map((c) => {
                      const p = r.catWin[c] ?? null;
                      return (
                        <td
                          key={c}
                          className="num rounded px-1 py-1"
                          style={heatStyle(p == null ? null : p - 0.5, 0.4)}
                          title={`${CAT_LABEL[c]}: P(win) ${pct(p)}`}
                        >
                          {p == null ? "—" : Math.round(p * 100)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="border-t border-line px-4 py-2 text-xs text-muted">
          Simulated odds if I played each team in week {mx.week} (Wk; schedule-aware games for both sides) and averaged over all
          regular-season weeks (Szn). Category cells: P(win that category) in %, green = favoured, red = underdog (TO: winning = fewer).
          Tap a team to open that matchup. &quot;(rr)&quot; marks the round-robin placeholder opponent.
        </p>
      </Panel>
    </>
  );
}

function WeekStrip({ weeks, selected, current }: { weeks: WeekMatchup[]; selected: number; current: number | null }) {
  return (
    <div className="scroll-thin -mx-4 overflow-x-auto px-4">
      <div className="flex gap-1.5 pb-1">
        {weeks.map((w) => (
          <Link
            key={w.week}
            href={`/matchup?week=${w.week}`}
            scroll={false}
            className={`flex w-12 shrink-0 flex-col items-center rounded-lg border py-1 ${
              w.week === selected ? "border-accent bg-accent/10" : "border-line bg-panel"
            }`}
          >
            <span className={`text-[10.5px] ${w.week === current ? "font-semibold text-accent" : "text-muted"}`}>
              W{w.week}
            </span>
            <span className={`num text-[12.5px] ${probText(w.pWin)}`}>{w.pWin != null ? pct(w.pWin) : "—"}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function advice(w: WeekMatchup) {
  const chase: CatKey[] = [];
  const concede: CatKey[] = [];
  const safe: CatKey[] = [];
  for (const c of CATS) {
    const p = w.catWin[c];
    if (p == null) continue;
    if (p >= 0.8) safe.push(c);
    else if (p <= 0.2) concede.push(c);
    else if (p >= 0.3 && p <= 0.7) chase.push(c);
  }
  return { chase, concede, safe };
}

function MatchupView({ sim, w, when, names, picked }: { sim: SeasonSim; w: WeekMatchup; when: string; names: Record<string, string>; picked: boolean }) {
  const opp = w.opponent != null ? sim.profiles[String(w.opponent)] : undefined;
  const me = sim.myTeamId != null ? sim.profiles[String(sim.myTeamId)] : undefined;
  const hasWeekTotals = CATS.some((c) => w.cats[c]?.me != null);
  const myName = (sim.myTeamId != null ? names[String(sim.myTeamId)] : undefined) ?? "Me";
  const oppName =
    (w.opponent != null ? names[String(w.opponent)] : undefined) ?? w.opponentName ?? (w.opponent != null ? `Team ${w.opponent}` : "Opponent");
  const { chase, concede, safe } = advice(w);
  const recs = arr(w.raw.recommendations ?? w.raw.actions).map((x) => (typeof x === "string" ? x : str((x as Record<string, unknown>)?.text))).filter(Boolean) as string[];
  const myRow = sim.table.find((r) => r.team === sim.myTeamId);
  const pW = w.pWin ?? 0;
  const pT = w.pTie ?? 0;
  const pL = w.pLoss ?? Math.max(0, 1 - pW - pT);
  return (
    <>
      <Panel className="overflow-hidden">
        <div className="px-4 pt-3 pb-3">
          <div className="text-xs text-muted">
            Week {w.week} · {fmtDate(w.start)} – {fmtDate(w.end)}
            {when ? ` · ${when}` : ""}
          </div>
          <div className="mt-1 flex items-baseline gap-2 text-lg leading-tight font-semibold">
            <span className="truncate">{myName}</span>
            <span className="text-sm font-normal text-muted">vs</span>
            <span className="truncate">{oppName}</span>
          </div>
          {(w.opponentAssumed || picked) && (
            <div className="mt-1 text-[11.5px] text-muted">
              {picked ? "opponent picked from the table below (what-if for this week)" : "assumed opponent (round robin), not Yahoo's schedule"}
            </div>
          )}
          <div className="mt-3 grid grid-cols-3 gap-3">
            <Hero label="P(win)" value={pct(w.pWin)} tone={probText(w.pWin)} />
            <Hero label="Exp. cats" value={fmt(w.expCatsWon, 1)} sub="of 9" />
            <Hero
              label="Games"
              value={
                <>
                  {fmt(w.myGames, 0)}
                  <span className="text-[0.6em] font-normal text-muted"> vs {fmt(w.oppGames, 0)}</span>
                </>
              }
              sub="player-games"
            />
          </div>
          <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-panel-2" title={`win ${pct(pW)} · tie ${pct(pT)} · loss ${pct(pL)}`}>
            <div className="bg-good/70" style={{ width: `${pW * 100}%` }} />
            <div className="bg-muted/40" style={{ width: `${pT * 100}%` }} />
            <div className="bg-bad/60" style={{ width: `${pL * 100}%` }} />
          </div>
          <div className="mt-1 flex justify-between text-[11px] text-muted">
            <span>win {pct(pW)}</span>
            <span>tie {pct(pT)}</span>
            <span>loss {pct(pL)}</span>
          </div>
        </div>
      </Panel>

      <SectionTitle right={hasWeekTotals ? "projected this week" : me || opp ? "avg / week (season)" : undefined}>Categories</SectionTitle>
      <Panel>
        <div className="grid grid-cols-[3.6rem_1fr_1fr_4.2rem_2.4rem] items-center gap-x-2 border-b border-line px-3 py-1.5 text-[10.5px] tracking-wider text-muted uppercase">
          <span />
          <span className="text-right">Me</span>
          <span className="text-right">Opp</span>
          <span className="text-center">edge</span>
          <span className="text-right">win</span>
        </div>
        <ul className="divide-y divide-line">
          {CATS.map((c) => {
            const p = w.catWin[c] ?? null;
            const mine = w.cats[c]?.me ?? me?.weeklyMean[c] ?? null;
            const theirs = w.cats[c]?.opp ?? opp?.weeklyMean[c] ?? null;
            return (
              <li key={c} className="grid grid-cols-[3.6rem_1fr_1fr_4.2rem_2.4rem] items-center gap-x-2 px-3 py-2 text-[13.5px]">
                <CatLabel c={c} />
                <span className="num text-right">{fmtCat(c, mine)}</span>
                <span className="num text-right text-muted">{fmtCat(c, theirs)}</span>
                <EdgeBar p={p} title={`P(win ${c}) = ${pct(p)}`} />
                <span className={`num text-right ${probText(p)}`}>{pct(p)}</span>
              </li>
            );
          })}
        </ul>
        {!hasWeekTotals && (me || opp) && (
          <p className="border-t border-line px-4 py-2 text-xs text-muted">
            Totals are each team&apos;s simulated season-average week (TO: lower is better); win odds are for this specific week
            {w.start && w.end ? ` (${fmtDate(w.start)}–${fmtDate(w.end)})` : ""}.
          </p>
        )}
      </Panel>

      <SectionTitle>What to do</SectionTitle>
      <Panel className="space-y-2.5 px-4 py-3 text-[14px]">
        {recs.length > 0 && (
          <ul className="space-y-1">
            {recs.map((r, i) => (
              <li key={i}>• {r}</li>
            ))}
          </ul>
        )}
        <AdviceRow label="Chase" tone="text-accent" cats={chase} note="swing categories (30–70%): where streaming and lineup choices move the result" />
        <AdviceRow label="Safe" tone="text-good" cats={safe} note="≥80%: don't spend adds here" />
        <AdviceRow label="Concede" tone="text-bad" cats={concede} note="≤20%: unlikely to flip; ignore when picking streamers" />
        <p className="text-xs text-muted">
          Rule of thumb from the category win probabilities. Streaming-add suggestions arrive with the waiver optimizer (T-006) and Yahoo roster
          sync (T-004).
        </p>
      </Panel>

      <Panel className="px-4 py-3 text-xs text-muted">
        <div>
          Season: P(playoffs) <span className="num text-fg">{pct(myRow?.pPlayoffs ?? null)}</span> · P(title){" "}
          <span className="num text-fg">{pct(myRow?.pTitle ?? null)}</span> ·{" "}
          <Link href="/league" className="text-accent">
            league →
          </Link>
        </div>
        <div className="mt-1">
          From <code className="num">{sim.run.run_id}</code> · {sim.nSims ?? "?"} sims · {shortTime(sim.run.created_at)}
          {sim.draftId ? ` · rosters: ${sim.draftId}` : ""}
        </div>
        {sim.warnings.map((x, i) => (
          <p key={i} className="mt-1 text-warn">
            ⚠ {x}
          </p>
        ))}
      </Panel>
    </>
  );
}

function AdviceRow({ label, tone, cats, note }: { label: string; tone: string; cats: CatKey[]; note: string }) {
  return (
    <div className="flex gap-3">
      <span className={`w-16 shrink-0 text-[13px] font-semibold ${tone}`}>{label}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {cats.length ? cats.map((c) => <CatLabel key={c} c={c} />) : <span className="text-muted">—</span>}
        </div>
        <div className="mt-0.5 text-xs text-muted">{note}</div>
      </div>
    </div>
  );
}
