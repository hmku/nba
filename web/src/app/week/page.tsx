import Link from "next/link";
import type { Metadata } from "next";
import { anonClient, cachedClient } from "@/lib/supabase";
import { loadSimPicks, parseSeasonSim, todayET, type SeasonSim } from "@/lib/sim";
import { latestAdvice, type AdviceRow } from "@/lib/moves";
import {
  actualForDay,
  buildTimeline,
  buildWeek,
  daysBetween,
  parseActual,
  parsePlan,
  pickWeek,
  ratioBenchFlag,
  ratioBenchIds,
  shortName,
  weekSummary,
  type AdviceLite,
  type RatioBench,
  type ApprovalRow,
  type DecisionRow,
  type LeagueWeek,
  type LineupActual,
  type LineupPlan,
  type RunRow,
  type TimelineItem,
  type TxnRow,
} from "@/lib/lineup";
import {
  gamesLeft,
  latestPlanInWeek,
  latestScore,
  mergePlanGames,
  remainingDays,
  simWeekOdds,
  splitActuals,
  teamGames,
  teamLineup,
  weekOdds,
  yahooMatchupUrl,
  type LeagueState,
  type MatchupScore,
  type RosterEntry,
  type WeekCat,
  type WeekOdds,
} from "@/lib/week";
import { weekOpponents, type H2HWeek } from "@/lib/postDraft";
import { CATS, CAT_LABEL, fmtCat, heatStyle } from "@/lib/categories";
import leagueWeeks from "@/content/league_weeks.json";
import leagueState from "@/content/league_state.json";
import nbaSchedule from "@/content/nba_schedule.json";
import { pct, shortTime } from "@/lib/format";
import { Badge, Disclosure, ErrorNote, statusTone } from "@/components/ui";
import { CatLabel, EmptyState, Panel, SectionTitle, probText } from "@/components/viz";
import type { DayView } from "./LineupGrid";
import { WeekLineups, type OppDay } from "./WeekLineups";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "This week" };

const RUN_COLS = "run_id,kind,created_at,summary";
const TXN_COLS = "txn_id,run_id,kind,status,payload,verification,error,approval_id,created_at,updated_at";
const APPROVAL_COLS = "approval_id,action_type,summary,payload,status,decided_by,note,requested_at,decided_at";
const DECISION_COLS = "decision_id,run_id,kind,choice,agrees_with_model,rationale,created_at";
const LINEUP_KINDS = ["lineup_plan", "lineup_actual"];

const WEEKS = (leagueWeeks.weeks ?? []) as LeagueWeek[];
const LEAGUE = leagueState as unknown as LeagueState;
const SCHEDULE = (nbaSchedule as { games: Record<string, string[]> }).games;

/** ISO midnight UTC of `day` shifted by `k` days (query windows; the timeline filters by ET date itself). */
function shift(day: string, k: number): string {
  const t = new Date(`${day}T00:00:00Z`);
  t.setUTCDate(t.getUTCDate() + k);
  return t.toISOString();
}

function dayLabel(d: string): string {
  return new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", day: "numeric", timeZone: "UTC" });
}

function fmtDate(d: string): string {
  return new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Weekly total as Yahoo shows it: percentages .xxx, counts as integers. */
function fmtTotal(c: WeekCat, v: number | null): string {
  if (v == null) return "—";
  if (c === "fg_pct" || c === "ft_pct") return fmtCat(c, v);
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

export default async function WeekPage({ searchParams }: { searchParams: Promise<{ week?: string; opp?: string }> }) {
  const { week: wq, opp: oq } = await searchParams;
  const today = todayET();
  const w = pickWeek(WEEKS, today, wq ? Number(wq) : null);
  if (!w)
    return (
      <div className="space-y-3">
        <h1 className="text-xl font-semibold tracking-tight">This week</h1>
        <EmptyState icon="⚔" title="No league schedule loaded">
          <p>Regenerate web/src/content/league_weeks.json with node scripts/league-weeks.mjs.</p>
        </EmptyState>
      </div>
    );
  const days = daysBetween(w.start, w.end);
  const pastDays = days.filter((d) => d < today);
  const liveDays = days.filter((d) => d >= today);
  const weekOver = w.end < today;
  const db = anonClient();
  const from = shift(w.start, -2);
  const to = shift(w.end, 2);
  const none = Promise.resolve({ data: [] as RunRow[], error: null });
  // one round: past days' rows (and a finished week's final score) are effectively immutable -> Data Cache (5 min)
  const [simP, pastR, liveR, scoreR, txnR, apprR, advR, decR] = await Promise.all([
    // 60: what-if `compare` / `draft-check` runs share kind season_sim and must not crowd out the season run
    loadSimPicks(db, cachedClient(3600), "season_sim", 60, { season: true }),
    pastDays.length
      ? cachedClient(300)
          .from("model_runs")
          .select(RUN_COLS)
          .in("kind", LINEUP_KINDS)
          .gte("summary->>date", pastDays[0])
          .lte("summary->>date", pastDays[pastDays.length - 1])
          .order("created_at", { ascending: false })
          .limit(300)
      : none,
    liveDays.length
      ? db
          .from("model_runs")
          .select(RUN_COLS)
          .in("kind", LINEUP_KINDS)
          .gte("summary->>date", liveDays[0])
          .lte("summary->>date", liveDays[liveDays.length - 1])
          .order("created_at", { ascending: false })
          .limit(300)
      : none,
    (weekOver ? cachedClient(300) : db)
      .from("model_runs")
      .select(RUN_COLS)
      .eq("kind", "matchup_score")
      .eq("summary->>week", String(w.week))
      .order("created_at", { ascending: false })
      .limit(3),
    db.from("transactions").select(TXN_COLS).gte("created_at", from).lte("created_at", to).order("created_at", { ascending: false }).limit(200),
    db.from("approvals").select(APPROVAL_COLS).gte("requested_at", from).lte("requested_at", to).order("requested_at", { ascending: false }).limit(100),
    db.from("model_runs").select(RUN_COLS).eq("kind", "advice").gte("created_at", shift(w.start, -7)).lte("created_at", to).order("created_at", { ascending: false }).limit(300),
    db.from("decisions").select(DECISION_COLS).gte("created_at", from).lte("created_at", to).order("created_at", { ascending: false }).limit(200),
  ]);
  const error = simP.error ?? [pastR, liveR, scoreR, txnR, apprR, advR, decR].map((r) => r.error?.message).find(Boolean) ?? null;
  const sim = parseSeasonSim(simP.season);

  // --- who plays whom (team ids = draft slots); ?opp=N is a what-if against another team
  const myTeam = LEAGUE.my_team ?? (leagueWeeks.my_team as number | null) ?? sim?.myTeamId ?? null;
  const ask = oq != null && Number.isFinite(Number(oq)) ? Number(oq) : null;
  const whatIf = ask != null && ask !== w.opponent && ask !== myTeam ? ask : null;
  const oppTeam = whatIf ?? w.opponent;
  const names = teamNames(sim);
  const myName = (myTeam != null ? names[String(myTeam)] : null) ?? "Me";
  const oppName = oppTeam != null ? (names[String(oppTeam)] ?? (oppTeam === w.opponent ? w.opponent_name : null) ?? `Team ${oppTeam}`) : null;

  // --- lineups
  const rows = [...((pastR.data as RunRow[]) ?? []), ...((liveR.data as RunRow[]) ?? [])];
  const plans = rows.map(parsePlan).filter((p): p is LineupPlan => p != null);
  const { mine, theirs } = splitActuals(
    rows.map(parseActual).filter((x): x is LineupActual => x != null),
    myTeam,
    oppTeam,
  );
  const grid = buildWeek(days, plans, mine);
  const ws = weekSummary(grid);
  const byTeam = teamGames(SCHEDULE, days);
  const oppState = oppTeam != null ? (LEAGUE.teams[String(oppTeam)] ?? null) : null;
  // my roster from the snapshot for days with neither a plan nor a Yahoo read (e.g. a future week)
  const myState = myTeam != null ? (LEAGUE.teams[String(myTeam)] ?? null) : null;
  const mySnapshot: OppDay[] = grid
    .filter((g) => !g.plan && !g.actual && myState)
    .map((g) => ({ date: g.date, rows: teamLineup(myState, g.date, byTeam), fromYahoo: false, observedAt: null }));
  const oppDays: OppDay[] | null =
    oppTeam != null
      ? days.map((d) => {
          const act = actualForDay(theirs, d);
          return { date: d, rows: teamLineup(oppState, d, byTeam, act), fromYahoo: act != null, observedAt: act?.observedAt ?? null };
        })
      : null;

  // --- games left (today included): my roster as Yahoo/plan shows it today, theirs from the snapshot / their read
  const left = remainingDays(days, today);
  const refDay = left[0] ?? days[days.length - 1];
  const { games: planGames, nbaTeam } = mergePlanGames(plans);
  const refGrid = grid.find((g) => g.date === refDay);
  let myRoster: RosterEntry[] = (refGrid?.rows ?? [])
    .filter((r) => r.shown?.playerId)
    .map((r) => ({ id: r.shown!.playerId!, nbaTeam: nbaTeam[r.shown!.playerId!] ?? null, slot: r.slot, status: r.shown!.status }));
  if (!myRoster.length && myTeam != null)
    myRoster = (LEAGUE.teams[String(myTeam)]?.players ?? []).map((p) => ({ id: p.id, nbaTeam: p.nba_team, slot: p.slot, status: p.status }));
  const myLeft = gamesLeft(myRoster, left, planGames, byTeam);
  const oppRef = oppDays?.find((x) => x.date === refDay);
  const oppLeft = oppRef ? gamesLeft(oppRef.rows.filter((r) => r.playerId).map((r) => ({ id: r.playerId!, nbaTeam: r.nbaTeam, slot: r.slot, status: r.status })), left, {}, byTeam) : null;

  // --- score + odds
  const score = whatIf == null ? latestScore((scoreR.data as RunRow[]) ?? [], w.week) : null;
  const simOdds = sim ? simWeekOdds(sim.weeks, sim.h2h, w.week, oppTeam) : null;
  const plan = whatIf == null ? latestPlanInWeek(plans, w.start, w.end, today) : null;
  const odds = weekOdds(plan, simOdds, sim?.run.run_id ?? null);
  const ratioBench = ratioBenchFlag(plan, today);
  const mx = sim?.h2h.find((x) => x.week === w.week) ?? null;

  // --- decisions
  const advice: AdviceLite[] = latestAdvice((advR.data as AdviceRow[]) ?? []).map((x) => ({
    adviceId: x.adviceId,
    type: x.type,
    title: x.title,
    status: x.status,
    headline: x.headline,
    createdAt: x.createdAt,
    firstCreatedAt: x.firstCreatedAt,
    forDate: x.forDate,
    week: x.week,
    rationale: x.rationale.map((r) => (r.bullet ? `• ${r.text}` : r.text)),
    whyNow: x.whyNow ? [x.whyNow.before, ...x.whyNow.changed.map((c) => `→ ${c.text}`)].join("\n") : null,
  }));
  const timeline = buildTimeline(
    {
      txns: (txnR.data as TxnRow[]) ?? [],
      approvals: (apprR.data as ApprovalRow[]) ?? [],
      advice,
      decisions: (decR.data as DecisionRow[]) ?? [],
      names: playerNames(plans, mine),
    },
    w,
  );

  const views: DayView[] = grid.map((g) => ({
    date: g.date,
    label: dayLabel(g.date),
    isToday: g.date === today,
    rows: g.rows,
    summary: g.summary,
    mismatches: g.mismatches,
    benchHints: ratioBenchIds(ratioBench, g.date),
    plan: g.plan
      ? {
          runId: g.plan.runId,
          date: g.plan.date,
          sameDay: g.planSameDay,
          createdAt: g.plan.createdAt,
          modelVersion: g.plan.modelVersion,
          tipoff: g.planSameDay ? g.plan.tipoff : [],
          ilMoves: g.planSameDay ? g.plan.ilMoves : [],
          benchWithGame: g.planSameDay ? g.plan.benchWithGame : [],
          expected: g.plan.expected,
          warnings: g.planSameDay ? g.plan.warnings : [],
        }
      : null,
    actual: g.actual
      ? { runId: g.actual.runId, source: g.actual.source, observedAt: g.actual.observedAt, how: g.actual.how, txnId: g.actual.txnId, complete: g.actual.complete }
      : null,
  }));
  const initial = Math.max(0, days.indexOf(today));
  const when = w.start <= today && today <= w.end ? "this week" : w.start > today ? "upcoming" : "final";
  const meYahoo = score?.me.yahooTeamId ?? (myTeam != null ? (LEAGUE.teams[String(myTeam)]?.yahoo_team_id ?? null) : null);
  const oppYahoo = score?.opp.yahooTeamId ?? oppState?.yahoo_team_id ?? null;
  const currentWeek = pickWeek(WEEKS, today)?.week ?? null;

  return (
    <div className="space-y-3">
      <WeekStrip sim={sim} selected={w.week} current={currentWeek} />
      <ErrorNote error={error} />
      {whatIf != null && (
        <p className="rounded-lg bg-accent/10 px-3 py-2 text-[12.5px] leading-snug">
          <span className="font-semibold">What-if:</span> week {w.week} against {oppName} (Yahoo&apos;s opponent is {w.opponent_name ?? `team ${w.opponent}`}).{" "}
          <Link href={`/week?week=${w.week}`} scroll={false} className="text-accent">
            Back to the real matchup →
          </Link>
        </p>
      )}
      <ScoreHeader
        week={w.week}
        dates={`${fmtDate(w.start)} – ${fmtDate(w.end)}`}
        when={when}
        myName={score?.me.name ?? myName}
        oppName={score?.opp.name ?? oppName ?? "Opponent"}
        score={score}
        odds={odds}
        yahooUrl={yahooMatchupUrl(w.week, meYahoo, oppYahoo)}
      />
      <CategoryStrip score={score} odds={odds} />
      {ratioBench && <RatioBenchFlag rb={ratioBench} />}

      <WeekLineups
        days={views}
        oppDays={oppDays}
        mySnapshot={mySnapshot}
        me={{ name: myName, gamesLeft: myLeft, yahooGamesLeft: score?.me.gamesRemaining ?? null }}
        opp={oppTeam != null ? { name: oppName ?? "Opponent", gamesLeft: oppLeft, yahooGamesLeft: score?.opp.gamesRemaining ?? null } : null}
        snapshotAt={LEAGUE.captured_at ?? LEAGUE.synced_at}
        initial={initial}
        today={today}
      />
      {(ws.unused > 0 || ws.mismatchDays > 0) && (
        <p className="px-1 text-[12px] text-muted">
          Week so far: <span className="num">{ws.started}</span>/<span className="num">{ws.available}</span> possible starts used
          {ws.unused > 0 && <span className="text-warn"> · {ws.unused} unused</span>}
          {ws.mismatchDays > 0 && <span className="text-warn"> · {ws.mismatchDays} day{ws.mismatchDays > 1 ? "s" : ""} differ from the plan</span>}
        </p>
      )}

      <Decisions items={timeline} />

      {mx && <OtherMatchups sim={sim!} mx={mx} selected={oppTeam} real={w.opponent} names={names} />}

      <HowMade
        sim={sim}
        score={score}
        odds={odds}
        plan={plan}
        snapshotAt={LEAGUE.captured_at ?? LEAGUE.synced_at}
        oppFromYahoo={oppDays?.some((d) => d.fromYahoo) ?? false}
        myTeam={myTeam}
      />
    </div>
  );
}

/** Team names: the league snapshot (Yahoo names) over the season sim's. */
function teamNames(sim: SeasonSim | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (sim) {
    for (const r of sim.table) out[String(r.team)] = r.name;
    for (const x of sim.vsOpponents) if (x.name) out[String(x.team)] = x.name;
  }
  for (const [k, t] of Object.entries(LEAGUE.teams)) if (t.name) out[k] = t.name;
  return out;
}

/** player_id -> name from the week's plans and Yahoo reads (txn `changes` often carry only ids). */
function playerNames(plans: LineupPlan[], actuals: LineupActual[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const x of actuals) for (const e of x.slots) if (e.playerId && e.name) out[e.playerId] = e.name;
  for (const p of plans) for (const [pid, info] of Object.entries(p.players)) out[pid] = info.name;
  return out;
}

function WeekStrip({ sim, selected, current }: { sim: SeasonSim | null; selected: number; current: number | null }) {
  return (
    <div className="scroll-thin -mx-4 overflow-x-auto px-4">
      <div className="flex gap-1.5 pb-1">
        {WEEKS.map((x) => {
          const p = sim ? (simWeekOdds(sim.weeks, sim.h2h, x.week, x.opponent)?.pWin ?? null) : null;
          return (
            <Link
              key={x.week}
              href={`/week?week=${x.week}`}
              scroll={false}
              title={`Week ${x.week}: ${fmtDate(x.start)} – ${fmtDate(x.end)}${x.opponent_name ? ` vs ${x.opponent_name}` : ""}${p != null ? ` · P(win) ${pct(p)} (season sim, pre-week)` : ""}`}
              className={`flex w-12 shrink-0 flex-col items-center rounded-lg border py-1 ${x.week === selected ? "border-accent bg-accent/10" : "border-line bg-panel"}`}
            >
              <span className={`text-[10.5px] ${x.week === current ? "font-semibold text-accent" : "text-muted"}`}>W{x.week}</span>
              <span className={`num text-[12px] ${p != null ? probText(p) : "text-muted"}`}>{p != null ? pct(p) : fmtDate(x.start).replace(/^\w+ /, "")}</span>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function oddsLabel(o: WeekOdds): string {
  if (o.source === "lineup_plan") return `simulated, lineup model${o.method ? ` (${o.method})` : ""}`;
  if (o.source === "season_sim") return "simulated, season sim (pre-week)";
  return "no simulation yet";
}

function ScoreHeader({
  week,
  dates,
  when,
  myName,
  oppName,
  score,
  odds,
  yahooUrl,
}: {
  week: number;
  dates: string;
  when: string;
  myName: string;
  oppName: string;
  score: MatchupScore | null;
  odds: WeekOdds;
  yahooUrl: string;
}) {
  const s = score?.score;
  const tone = s ? (s.me > s.opp ? "text-good" : s.me < s.opp ? "text-bad" : "text-fg") : probText(odds.pWin);
  return (
    <Panel className="px-4 pt-3 pb-3">
      <div className="flex items-baseline justify-between gap-2 text-xs text-muted">
        <span>
          Week {week} · {dates} · {when}
        </span>
        <a href={yahooUrl} target="_blank" rel="noreferrer" className="shrink-0 text-accent">
          Yahoo live ↗
        </a>
      </div>
      <div className="mt-1 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        <div className="min-w-0 line-clamp-2 text-[15px] leading-tight font-semibold break-words">{myName}</div>
        <div className={`num text-center text-3xl leading-none font-semibold ${tone}`}>
          {s ? (
            <>
              {s.me}–{s.opp}
              {s.ties > 0 && <span className="text-muted">–{s.ties}</span>}
            </>
          ) : (
            <span className="text-[13px] font-normal text-muted">vs</span>
          )}
        </div>
        <div className="min-w-0 line-clamp-2 text-right text-[15px] leading-tight font-semibold break-words">{oppName}</div>
      </div>
      {score && (gamesText(score.me) || gamesText(score.opp)) && (
        <div className="mt-1 grid grid-cols-[1fr_auto_1fr] items-baseline gap-2 text-[12px] text-muted" title="Player-games played so far / total this week (played + remaining), from Yahoo's matchup page at the time of the score capture">
          <span className="num">{gamesText(score.me) ?? "—"}</span>
          <span className="text-[11px]">games</span>
          <span className="num text-right">{gamesText(score.opp) ?? "—"}</span>
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-[12px]">
        <span className="text-muted">
          {score
            ? `categories won now${s && s.ties ? " (W–L–T)" : ""} · as of ${shortTime(score.observedAt ?? score.createdAt)} ET`
            : "no score captured yet — projected odds only"}
        </span>
        <span>
          P(win week){" "}
          <span className={`num text-[15px] font-semibold ${probText(odds.pWin)}`}>{pct(odds.pWin)}</span>
          {odds.pWinSe != null && <span className="num text-[11px] text-muted"> ±{(odds.pWinSe * 100).toFixed(1)}pp</span>}
          <span className="ml-1 text-[11px] text-muted">{oddsLabel(odds)}</span>
        </span>
      </div>
    </Panel>
  );
}

/** "6 / 38 played" from Yahoo's games strip (played + remaining = the week's total), else null. */
/** Ratio-bench suggestion (lineup-0.4.0): benching these player-days would protect a % category. Never applied. */
function RatioBenchFlag({ rb }: { rb: RatioBench }) {
  const label = (k: string) => CAT_LABEL[k as keyof typeof CAT_LABEL] ?? k.toUpperCase();
  const uniq = (xs: string[]) => [...new Set(xs)];
  const helped = uniq(rb.benches.flatMap((x) => x.helped.slice(0, 1))).map(label);
  const hurt = uniq(rb.benches.flatMap((x) => x.hurt.slice(0, 2))).map(label);
  const who = rb.benches.map((x) => `${shortName(x.name)} ${dayLabel(x.date).split(" ")[0]}`).join(", ");
  const sign = (v: number | null, d: number) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}`);
  return (
    <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-[12.5px] leading-snug" title={rb.rule ?? undefined}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <span>
          <span className="font-semibold">Sit {who}?</span> Protects {helped.join("/") || "a % category"}
          {hurt.length > 0 && <span className="text-muted"> · costs {hurt.join(", ")}</span>}
        </span>
        <span className="text-[11px] text-muted">suggestion · not applied</span>
      </div>
      <div className="text-[11.5px] text-muted">
        <span className="num text-fg">
          {sign(rb.deltaCats, 2)} ± {rb.deltaCatsSe?.toFixed(2) ?? "—"}
        </span>{" "}
        exp. cats · P(win) <span className="num">{sign(rb.deltaPWin == null ? null : rb.deltaPWin * 100, 1)} pts</span> · simulated
      </div>
    </div>
  );
}

function gamesText(t:{ gamesPlayed: number | null; gamesRemaining: number | null }): string | null {
  if (t.gamesPlayed == null || t.gamesRemaining == null) return null;
  return `${t.gamesPlayed} / ${t.gamesPlayed + t.gamesRemaining} played`;
}

function CategoryStrip({ score, odds }: { score: MatchupScore | null; odds: WeekOdds }) {
  const byCat = new Map((score?.categories ?? []).map((c) => [c.cat, c]));
  const swing = CATS.filter((c) => {
    const p = odds.catWin[c as WeekCat];
    return p != null && p >= 0.3 && p <= 0.7;
  });
  const has = score != null;
  const swingLine = (
    <div className="border-t border-line px-3 py-1.5 text-[11.5px] text-muted">
      {has ? "Leader shaded (TO: fewer wins). " : ""}
      {swing.length > 0 ? (
        <>
          Swing: <span className="text-fg">{swing.map((c) => CAT_LABEL[c]).join(" · ")}</span> — where lineup choices and streamers matter.
        </>
      ) : (
        "No category in the 30–70% swing range."
      )}
    </div>
  );
  if (!has)
    // before the first capture: projected odds only, compact (3 x 3 on phones, one row on wide screens)
    return (
      <Panel>
        <div className="grid grid-cols-3 gap-1 p-2 md:grid-cols-9">
          {CATS.map((c) => {
            const p = odds.catWin[c as WeekCat] ?? null;
            return (
              <div key={c} className="rounded-lg px-2 py-1.5 text-center" style={heatStyle(p == null ? null : p - 0.5, 0.5)} title={`P(win ${CAT_LABEL[c]}) ${pct(p)} · ${oddsLabel(odds)}`}>
                <CatLabel c={c} className="text-[12px]" />
                <div className="num text-[15px] font-semibold">{pct(p)}</div>
              </div>
            );
          })}
        </div>
        {swingLine}
      </Panel>
    );
  const cols = "grid-cols-[3.4rem_1fr_1fr_3.2rem]";
  return (
    <Panel>
      <div className={`grid ${cols} items-center gap-x-2 border-b border-line px-3 py-1.5 text-[10.5px] tracking-wider text-muted uppercase`}>
        <span />
        <span className="text-right">Me</span>
        <span className="text-right">Opp</span>
        <span className="text-right" title={odds.source === "lineup_plan" ? "chance to win the category by Sunday, given the stats already banked" : "pre-week odds from the season sim"}>
          {odds.source === "lineup_plan" ? "by Sun" : "odds"}
        </span>
      </div>
      <ul className="divide-y divide-line">
        {CATS.map((c) => {
          const k = c as WeekCat;
          const sc = byCat.get(k);
          const p = odds.catWin[k] ?? null;
          const lead = sc?.leader;
          const pctCat = c === "fg_pct" || c === "ft_pct";
          const ma = (side: "me" | "opp") => {
            const t = score?.[side].totals;
            if (!t || !pctCat) return null;
            const [m, at] = c === "fg_pct" ? [t.fgm, t.fga] : [t.ftm, t.fta];
            return m != null && at != null ? `${m}/${at}` : null;
          };
          return (
            <li key={c} className={`grid ${cols} items-center gap-x-2 px-3 py-1.5 text-[13.5px]`}>
              <CatLabel c={c} />
              <span className={`num min-w-[3.6rem] justify-self-end rounded px-1.5 text-right ${lead === "me" ? "bg-good/15 font-semibold" : ""}`} title={ma("me") ? `${CAT_LABEL[c]}: ${ma("me")} made/attempted` : undefined}>
                  {fmtTotal(k, sc?.me ?? null)}
                  {ma("me") && <span className="block text-[10px] font-normal text-muted">{ma("me")}</span>}
                </span>
              <span className={`num min-w-[3.6rem] justify-self-end rounded px-1.5 text-right ${lead === "opp" ? "bg-bad/15 font-semibold" : "text-muted"}`} title={ma("opp") ? `${CAT_LABEL[c]}: ${ma("opp")} made/attempted` : undefined}>
                  {fmtTotal(k, sc?.opp ?? null)}
                  {ma("opp") && <span className="block text-[10px] font-normal text-muted">{ma("opp")}</span>}
                </span>
              <span className={`num text-right ${probText(p)}`} title={`P(win ${CAT_LABEL[c]}) ${pct(p)} · ${oddsLabel(odds)}`}>
                {pct(p)}
              </span>
            </li>
          );
        })}
      </ul>
      {swingLine}
    </Panel>
  );
}

const KIND_LABEL: Record<string, string> = {
  lineup: "lineup",
  il: "IL",
  add_drop: "add/drop",
  trade: "trade",
  stream: "stream",
  approval: "approval",
  decision: "decision",
  other: "other",
};

function DecisionItem({ it }: { it: TimelineItem }) {
  return (
    <li className="px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="rounded bg-panel-2 px-1.5 text-[10.5px] tracking-wide text-muted uppercase">{KIND_LABEL[it.kind] ?? it.kind}</span>
        <span className="min-w-0 text-[13.5px] font-medium">{it.title}</span>
        {it.status && <Badge tone={statusTone(it.status)}>{it.status}</Badge>}
        <span className="ml-auto text-[11px] text-muted">{shortTime(it.at)}</span>
      </div>
      {it.detail.length > 0 && <div className="mt-0.5 line-clamp-2 text-[12.5px] text-muted">{it.detail.join(" · ")}</div>}
      {it.rationale.length > 0 && (
        <div className="mt-0.5 line-clamp-2 text-[12.5px]" title={it.rationale.join(" ")}>
          <span className="font-medium">Why: </span>
          {it.rationale.join(" ")}
        </div>
      )}
      {it.evidence && <div className="mt-0.5 text-[11.5px] text-good">{it.evidence}</div>}
      {it.href && (
        <Link href={it.href} className="text-[11px] text-accent">
          Inbox →
        </Link>
      )}
    </li>
  );
}

function Decisions({ items }: { items: TimelineItem[] }) {
  const head = items.slice(0, 3);
  const rest = items.slice(3);
  return (
    <>
      <span id="decisions" className="block scroll-mt-16" />
      <SectionTitle
        right={
          <Link href="/inbox" className="text-accent">
            inbox →
          </Link>
        }
      >
        Decisions
      </SectionTitle>
      <Panel>
        {items.length === 0 ? (
          <p className="px-4 py-3 text-sm text-muted">No lineup moves, approvals or recommendations this week yet.</p>
        ) : (
          <>
            <ul className="divide-y divide-line">
              {head.map((it) => (
                <DecisionItem key={`${it.kind}-${it.id}`} it={it} />
              ))}
            </ul>
            {rest.length > 0 && (
              <details className="group border-t border-line">
                <summary className="cursor-pointer list-none px-4 py-2 text-[12.5px] text-accent select-none [&::-webkit-details-marker]:hidden">
                  <span className="group-open:hidden">Show all {items.length} ▸</span>
                  <span className="hidden group-open:inline">Show fewer ▾</span>
                </summary>
                <ul className="divide-y divide-line border-t border-line">
                  {rest.map((it) => (
                    <DecisionItem key={`${it.kind}-${it.id}`} it={it} />
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </Panel>
    </>
  );
}

function OtherMatchups({ sim, mx, selected, real, names }: { sim: SeasonSim; mx: H2HWeek; selected: number | null; real: number | null; names: Record<string, string> }) {
  const rows = weekOpponents(mx);
  const season = new Map(sim.vsOpponents.map((o) => [o.team, o]));
  if (!rows.length) return null;
  return (
    <Disclosure summary="Other matchups" right={`week ${mx.week} vs every team`}>
      <div className="scroll-thin overflow-x-auto">
        <table className="w-full border-separate border-spacing-[2px] text-center text-[12px]">
          <thead>
            <tr className="text-[10.5px] text-muted">
              <th className="sticky left-0 z-10 bg-panel pl-2 text-left font-medium">Opponent</th>
              <th className="px-1 font-medium" title="P(win the matchup) in this week">Wk</th>
              <th className="px-1 font-medium" title="P(win) averaged over all regular-season weeks">Szn</th>
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
              return (
                <tr key={r.team} className={sel ? "bg-accent/10" : ""}>
                  <td className="sticky left-0 z-10 max-w-[7.5rem] truncate bg-panel py-1 pr-1 pl-2 text-left">
                    <Link href={r.team === real ? `/week?week=${mx.week}` : `/week?week=${mx.week}&opp=${r.team}`} scroll={false} className={sel ? "font-semibold text-accent" : "text-fg"}>
                      {names[String(r.team)] ?? `Team ${r.team}`}
                    </Link>
                    {r.team === real && <span className="ml-1 text-[10px] text-muted">(Yahoo)</span>}
                  </td>
                  <td className={`num px-1 font-medium ${probText(r.pWin)}`}>{pct(r.pWin)}</td>
                  <td className={`num px-1 ${probText(season.get(r.team)?.pWinAnyWeek ?? null)}`}>{pct(season.get(r.team)?.pWinAnyWeek ?? null)}</td>
                  {CATS.map((c) => {
                    const p = r.catWin[c] ?? null;
                    return (
                      <td key={c} className="num rounded px-1 py-1" style={heatStyle(p == null ? null : p - 0.5, 0.4)} title={`${CAT_LABEL[c]}: P(win) ${pct(p)}`}>
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
      <p className="px-4 py-2 text-xs text-muted">
        Season-sim odds if I played each team this week (Wk) and averaged over the regular season (Szn); cells = P(win the category) in %. Tap a team for a
        what-if view of that matchup.
      </p>
    </Disclosure>
  );
}

function HowMade({
  sim,
  score,
  odds,
  plan,
  snapshotAt,
  oppFromYahoo,
  myTeam,
}: {
  sim: SeasonSim | null;
  score: MatchupScore | null;
  odds: WeekOdds;
  plan: LineupPlan | null;
  snapshotAt: string | null;
  oppFromYahoo: boolean;
  myTeam: number | null;
}) {
  const ex = plan?.expected ?? null;
  const myRow = sim?.table.find((r) => r.team === (sim.myTeamId ?? myTeam));
  const warnings = [...(plan?.warnings ?? []), ...(sim?.warnings ?? [])];
  return (
    <Disclosure summary={<span className="text-[13px] text-muted">How these numbers are made</span>}>
      <dl className="space-y-2.5 px-4 py-3 text-[12.5px] leading-snug">
        <div>
          <dt className="font-semibold">Score</dt>
          <dd className="text-muted">
            {score ? (
              <>
                Yahoo&apos;s category totals read at {shortTime(score.observedAt ?? score.createdAt)} ET ({score.source ?? "capture"}; run <code className="num">{score.runId}</code>).
                Captured once in the morning, so it lags games played since — Yahoo&apos;s matchup page is live. FG%/FT% are recomputed from makes/attempts.
              </>
            ) : (
              <>No score capture for this week yet (model_runs kind matchup_score, written by the morning sync).</>
            )}
          </dd>
        </div>
        <div>
          <dt className="font-semibold">Odds</dt>
          <dd className="text-muted">
            {odds.source === "lineup_plan" ? (
              <>
                From the lineup plan for today (or the latest day before it) <code className="num">{odds.runId}</code> ({shortTime(odds.asOf)}): category odds are the chance to win each
                category by Sunday given the stats already banked; P(win week) is {odds.method ?? "the plan's estimate"}
                {ex?.mc?.nSims ? ` over ${ex.mc.nSims} simulated weeks` : ""}. Simulated, not fitted
                {ex?.availability ? `; availability: ${ex.availability}` : ""}.
              </>
            ) : odds.source === "season_sim" ? (
              <>
                Season sim <code className="num">{odds.runId}</code> ({sim?.nSims ?? "?"} sims, {shortTime(sim?.run.created_at)}): pre-week odds for this pairing
                from projected rosters and both teams&apos; scheduled games; they do not know the stats already banked this week.
              </>
            ) : (
              <>No season sim or lineup plan for this week yet.</>
            )}
          </dd>
        </div>
        <div>
          <dt className="font-semibold">Games left</dt>
          <dd className="text-muted">
            Remaining NBA games this week (today included) of rostered players not on IL and not tagged out (O/INJ/NA/SUSP). Mine use the lineup plan&apos;s
            game list, theirs the NBA schedule by team. A raw count: it ignores daily start limits and bench slots.
          </dd>
        </div>
        <div>
          <dt className="font-semibold">Their lineup</dt>
          <dd className="text-muted">
            {oppFromYahoo ? "Days marked ✓ come from a recorded Yahoo read of their team; others from " : "From "}
            the league snapshot of the last roster sync{snapshotAt ? ` (${shortTime(snapshotAt)} ET)` : ""}: future days repeat those slots with that
            day&apos;s games. Mine: ✓ on Yahoo (read that day) · ◌ planned only · ⚠ Yahoo differs from the plan.
          </dd>
        </div>
        {myRow && (
          <div>
            <dt className="font-semibold">Season</dt>
            <dd className="text-muted">
              P(playoffs) <span className="num text-fg">{pct(myRow.pPlayoffs)}</span> · P(title) <span className="num text-fg">{pct(myRow.pTitle)}</span> (season sim) ·{" "}
              <Link href="/league" className="text-accent">
                league →
              </Link>
            </dd>
          </div>
        )}
        {warnings.length > 0 && (
          <div>
            <dt className="font-semibold text-warn">Warnings</dt>
            <dd>
              <ul className="space-y-0.5 text-warn">
                {warnings.map((x, i) => (
                  <li key={i}>⚠ {x}</li>
                ))}
              </ul>
            </dd>
          </div>
        )}
      </dl>
    </Disclosure>
  );
}
