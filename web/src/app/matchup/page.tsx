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
import { CATS, fmtCat } from "@/lib/categories";
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

export default async function MatchupPage({ searchParams }: { searchParams: Promise<{ week?: string }> }) {
  const { week: wq } = await searchParams;
  const db = anonClient();
  const [{ runs, error }, { drafts }] = await Promise.all([latestSimRuns(db, "season_sim", 20), loadDrafts(db)]);
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
  const w = req ?? cur ?? sorted[0];
  return (
    <div className="space-y-3">
      <WeekStrip weeks={sorted} selected={w.week} current={cur?.week ?? null} />
      <MatchupView sim={sim} w={w} isCurrent={cur?.week === w.week} />
    </div>
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

function MatchupView({ sim, w, isCurrent }: { sim: SeasonSim; w: WeekMatchup; isCurrent: boolean }) {
  const opp = w.opponent != null ? sim.profiles[String(w.opponent)] : undefined;
  const me = sim.myTeamId != null ? sim.profiles[String(sim.myTeamId)] : undefined;
  const hasWeekTotals = CATS.some((c) => w.cats[c]?.me != null);
  const myName = str(sim.myTeam?.name) ?? "Me";
  const oppName = w.opponentName ?? (w.opponent != null ? `Team ${w.opponent}` : "Opponent");
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
            {isCurrent ? " · this week" : ""}
          </div>
          <div className="mt-1 flex items-baseline gap-2 text-lg leading-tight font-semibold">
            <span className="truncate">{myName}</span>
            <span className="text-sm font-normal text-muted">vs</span>
            <span className="truncate">{oppName}</span>
          </div>
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
