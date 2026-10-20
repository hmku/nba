// Lineup view: plan/actual parsing, grid assembly (plan vs actual merge, mismatch detection), summaries,
// decisions timeline, week calendar. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import {
  actualForDay,
  buildDay,
  buildTimeline,
  buildWeek,
  daysBetween,
  parseActual,
  parsePlan,
  pickWeek,
  planForDay,
  shortName,
  weekSummary,
} from "../src/lib/lineup.ts";
import { buildWeeks, ROSTERS_PATH, SCHEDULE_PATH } from "../scripts/league-weeks.mjs";

const STARTERS = ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util"];
const IDS = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m"];
const SLOTS13 = [...STARTERS, "BN", "BN", "BN"];

const planRow = (date, over = {}, created = `${date}T14:00:00Z`) => ({
  run_id: `lp-${date}-x`,
  kind: "lineup_plan",
  created_at: created,
  summary: {
    schema: "lineup_plan/1",
    date,
    week: 1,
    opponent: { team_id: 12, name: "Joshua" },
    players: Object.fromEntries(IDS.map((p) => [p, { name: `Player ${p.toUpperCase()}`, positions: ["PG"], nba_team: "BOS", status: p === "a" ? "Q" : null }])),
    slots: IDS.map((p, k) => ({ slot: SLOTS13[k], player_id: p, has_game: k !== 12, p_play: k === 0 ? 0.5 : 0.96, value: 1 })),
    week_plan: [
      { date, slots: IDS.map((p, k) => ({ slot: SLOTS13[k], player_id: p, has_game: k !== 12 })) },
      { date: "2026-10-22", slots: IDS.map((p, k) => ({ slot: SLOTS13[(k + 1) % 13], player_id: p, has_game: true })) },
    ],
    games: Object.fromEntries(IDS.map((p, k) => [p, k === 12 ? {} : { [date]: "@DET", "2026-10-22": "vs NYK" }])),
    decide_at_tipoff: [{ player_id: "a", name: "Player A", status: "Q", p_play: 0.5, slot: "PG", swap_with: "k", swap_with_name: "Player K", deadline: "before Player A's tip-off (@DET)", note: "starting" }],
    il_moves: [],
    bench_with_game: [{ player_id: "k", name: "Player K", game: "@DET" }],
    expected: {
      method: "analytic",
      availability_assumption: "rest_share 0.25 (PRIOR)",
      evidence: "simulated",
      optimized: { expected_cats: 5.1, p_win_matchup: 0.62 },
      monte_carlo: { n_sims: 2000, plans: { optimized: { expected_cats: 5.0, expected_cats_se: 0.03, p_win_matchup: 0.6, p_win_matchup_se: 0.01 } } },
    },
    warnings: ["w"],
    model_version: "lineup-0.2.0",
    ...over,
  },
});

const actualRow = (date, slots, over = {}, created = `${date}T15:00:00Z`) => ({
  run_id: `la-${date}-${created}`,
  kind: "lineup_actual",
  created_at: created,
  summary: { schema: "lineup_actual/1", date, source: "yahoo_verified", observed_at: created, how: "re-read", txn_id: "txn-1", complete: true, slots, ...over },
});

const asSlots = (map) => Object.entries(map).map(([player_id, slot]) => ({ slot, player_id, name: `Player ${player_id.toUpperCase()}` }));
const planSlotsMap = Object.fromEntries(IDS.map((p, k) => [p, SLOTS13[k]]));

test("parsePlan / parseActual read the saved shapes and ignore junk", () => {
  const p = parsePlan(planRow("2026-10-21"));
  assert.equal(p.date, "2026-10-21");
  assert.equal(p.days["2026-10-21"].length, 13);
  assert.equal(p.days["2026-10-22"].length, 13);
  assert.equal(p.tipoff[0].deadline, "before Player A's tip-off (@DET)");
  assert.deepEqual(p.expected.optimized, { cats: 5.1, pWin: 0.62 });
  assert.equal(p.expected.mc.pWinSe, 0.01);
  assert.equal(parsePlan({ run_id: "x", kind: "advice", created_at: null, summary: {} }), null);
  assert.equal(parsePlan({ run_id: "x", kind: "lineup_plan", created_at: null, summary: { slots: [] } }), null);
  const act = parseActual(actualRow("2026-10-21", [{ slot: "IL+", player_id: "a" }, { bogus: 1 }]));
  assert.equal(act.slots.length, 1);
  assert.equal(act.slots[0].slot, "IL");
  assert.equal(parseActual(actualRow("2026-10-21", [])), null);
});

test("grid: actual equal to the plan -> every row verified, 16 rows in Yahoo order", () => {
  const plan = parsePlan(planRow("2026-10-21"));
  const act = parseActual(actualRow("2026-10-21", asSlots(planSlotsMap)));
  const g = buildDay("2026-10-21", plan.days["2026-10-21"], act, plan.games, plan.players);
  assert.deepEqual(
    g.rows.map((r) => r.slot),
    ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util", "BN", "BN", "BN", "IL", "IL", "IL"],
  );
  assert.equal(g.mismatches, 0);
  assert.deepEqual([...new Set(g.rows.map((r) => r.state))].sort(), ["empty", "verified"]);
  assert.equal(g.rows[0].shown.short, "P. A");
  assert.equal(g.rows[0].shown.status, "Q");
  assert.equal(g.rows[0].shown.game, "@DET");
  // m (BN, no game) -> hasGame false; summary: 10 starters with games of 12 players with games
  assert.equal(g.rows.find((r) => r.shown?.playerId === "m").shown.hasGame, false);
  assert.deepEqual(g.summary, { started: 10, available: 10, unused: 0, known: true });
});

test("grid: duplicate slots align by player, mismatches flagged in both rows", () => {
  const plan = parsePlan(planRow("2026-10-21"));
  // Yahoo: the two C swapped order (no mismatch), and k (BN) started at Util instead of j
  const yahoo = { ...planSlotsMap, g: "C", h: "C", j: "BN", k: "Util" };
  const act = parseActual(actualRow("2026-10-21", asSlots(yahoo).reverse()));
  const g = buildDay("2026-10-21", plan.days["2026-10-21"], act, plan.games, plan.players);
  const cs = g.rows.filter((r) => r.slot === "C");
  assert.deepEqual(cs.map((r) => r.state), ["verified", "verified"]);
  const util = g.rows.filter((r) => r.slot === "Util");
  assert.deepEqual(util.map((r) => [r.state, r.planned?.playerId, r.actual?.playerId]), [
    ["verified", "i", "i"],
    ["mismatch", "j", "k"],
  ]);
  const bn = g.rows.filter((r) => r.slot === "BN");
  assert.deepEqual(bn.map((r) => [r.state, r.planned?.playerId, r.actual?.playerId]), [
    ["verified", "l", "l"],
    ["verified", "m", "m"],
    ["mismatch", "k", "j"],
  ]);
  assert.equal(g.mismatches, 2);
});

test("grid: no Yahoo read -> planned; empty starting slot on Yahoo -> mismatch; no plan -> verified", () => {
  const plan = parsePlan(planRow("2026-10-21"));
  const g = buildDay("2026-10-21", plan.days["2026-10-21"], null, plan.games, plan.players);
  assert.ok(g.rows.filter((r) => r.shown).every((r) => r.state === "planned"));
  const yahoo = { ...planSlotsMap };
  delete yahoo.a; // PG left empty on Yahoo (player dropped / moved off)
  const act = parseActual(actualRow("2026-10-21", asSlots(yahoo)));
  const g2 = buildDay("2026-10-21", plan.days["2026-10-21"], act, plan.games, plan.players);
  const pg = g2.rows.find((r) => r.slot === "PG");
  assert.equal(pg.state, "mismatch");
  assert.equal(pg.actual, null);
  assert.equal(pg.shown, null);
  const g3 = buildDay("2026-10-21", null, act, plan.games, plan.players);
  assert.ok(g3.rows.filter((r) => r.shown).every((r) => r.state === "verified"));
  assert.equal(g3.mismatches, 0);
});

test("grid: a partial read keeps unread players as planned and checks only what was read", () => {
  const plan = parsePlan(planRow("2026-10-21"));
  const act = parseActual(actualRow("2026-10-21", asSlots({ a: "PG", b: "BN" }), { complete: false }));
  const g = buildDay("2026-10-21", plan.days["2026-10-21"], act, plan.games, plan.players);
  assert.equal(g.rows.find((r) => r.slot === "PG").state, "verified");
  const sg = g.rows.find((r) => r.slot === "SG");
  assert.equal(sg.state, "mismatch"); // plan b at SG, Yahoo shows nobody read there... b is on BN
  const bnB = g.rows.find((r) => r.slot === "BN" && r.actual?.playerId === "b");
  assert.equal(bnB.state, "mismatch");
  assert.equal(g.rows.find((r) => r.slot === "SF").state, "planned");
});

test("planForDay falls back to the newest earlier plan's week plan; actualForDay takes the newest read", () => {
  const p21 = parsePlan(planRow("2026-10-21"));
  const p20 = parsePlan(planRow("2026-10-20", { week_plan: [{ date: "2026-10-21", slots: [{ slot: "PG", player_id: "z" }] }] }));
  assert.equal(planForDay([p20, p21], "2026-10-21").plan.date, "2026-10-21");
  assert.equal(planForDay([p20, p21], "2026-10-22").sameDay, false);
  assert.equal(planForDay([p20], "2026-10-21").slots[0].playerId, "z");
  assert.equal(planForDay([p21], "2026-10-23"), null);
  const early = parseActual(actualRow("2026-10-21", asSlots({ a: "BN" }), { source: "yahoo_capture" }, "2026-10-21T05:00:00Z"));
  const late = parseActual(actualRow("2026-10-21", asSlots({ a: "PG" }), {}, "2026-10-21T15:00:00Z"));
  assert.equal(actualForDay([early, late], "2026-10-21").runId, late.runId);
  assert.equal(actualForDay([early, late], "2026-10-22"), null);
});

test("buildWeek + weekSummary: unused starts, mismatch days, game markers from plans", () => {
  const plan = parsePlan(planRow("2026-10-21"));
  const yahoo22 = { ...Object.fromEntries(IDS.map((p, k) => [p, SLOTS13[(k + 1) % 13]])) };
  const act22 = parseActual(actualRow("2026-10-22", asSlots(yahoo22)));
  const days = daysBetween("2026-10-20", "2026-10-25");
  assert.equal(days.length, 6);
  const grid = buildWeek(days, [plan], [act22]);
  assert.equal(grid[0].plan, null);
  assert.equal(grid[1].planSameDay, true);
  assert.equal(grid[2].planSameDay, false);
  assert.equal(grid[2].mismatches, 0);
  assert.equal(grid[2].rows[1].shown.game, "vs NYK"); // a at SG: marker from the 10-21 plan's games map
  // m starts at PG on 10-22 but has no game there (plan's schedule): one start lost, one healthy player benched
  assert.equal(grid[2].rows[0].shown.playerId, "m");
  assert.equal(grid[2].rows[0].shown.hasGame, false);
  assert.deepEqual(grid[2].summary, { started: 9, available: 10, unused: 1, known: true });
  const ws = weekSummary(grid);
  assert.equal(ws.daysWithData, 2);
  assert.equal(ws.started, 19);
  assert.equal(ws.unused, 1);
  assert.equal(ws.mismatchDays, 0);
});

test("buildWeek: a Yahoo read on a day no plan covers has unknown games (not 'no game') unless it carries markers", () => {
  const plan = parsePlan(planRow("2026-10-21"));
  const a20 = parseActual(actualRow("2026-10-20", asSlots(planSlotsMap)));
  const grid = buildWeek(["2026-10-20"], [plan], [a20]);
  assert.equal(grid[0].rows[0].shown.hasGame, null);
  assert.equal(grid[0].summary.known, false);
  const withMarkers = parseActual(
    actualRow("2026-10-20", [{ slot: "PG", player_id: "a", name: "A", game: "vs PHI" }, { slot: "SG", player_id: "b", name: "B" }], { games_known: true }),
  );
  const g2 = buildWeek(["2026-10-20"], [plan], [withMarkers]);
  assert.deepEqual(g2[0].rows.slice(0, 2).map((r) => r.shown.hasGame), [true, false]);
});

test("buildTimeline: txns with evidence, approvals, advice and decisions in the week, newest first", () => {
  const items = buildTimeline(
    {
      txns: [
        {
          txn_id: "txn-1",
          run_id: "r",
          kind: "lineup",
          status: "verified",
          payload: { date: "2026-10-22", changes: [{ name: "Jokic", from: "BN", to: "C" }, { name: "Clingan", from: "C", to: "BN" }] },
          verification: { verified_at: "2026-10-20T05:00:00Z", evidence: { page: "/nba/44495/11" } },
          error: null,
          approval_id: null,
          created_at: "2026-10-20T04:55:00Z",
          updated_at: "2026-10-20T05:00:00Z",
        },
        { txn_id: "txn-old", run_id: null, kind: "lineup", status: "verified", payload: { date: "2026-10-10" }, verification: null, error: null, approval_id: null, created_at: "2026-10-10T04:55:00Z", updated_at: null },
        { txn_id: "txn-il", run_id: null, kind: "lineup", status: "failed", payload: { date: "2026-10-23", changes: [{ name: "X", from: "BN", to: "IL" }] }, verification: null, error: "not IL eligible", approval_id: null, created_at: "2026-10-23T14:00:00Z", updated_at: "2026-10-23T14:05:00Z" },
      ],
      approvals: [{ approval_id: "apr-1", action_type: "add_drop", summary: "Add Champagnie, drop Bailey", payload: { rationale: "3-and-D" }, status: "pending", decided_by: null, note: null, requested_at: "2026-10-21T15:00:00Z", decided_at: null }],
      advice: [{ adviceId: "adv-a", type: "trade", title: "Murray trade", status: "idea", headline: "2-for-1", createdAt: "2026-10-19T12:00:00Z", firstCreatedAt: "2026-10-19T12:00:00Z", forDate: null, week: 1, rationale: ["why"], whyNow: "before\n→ changed" }],
      decisions: [{ decision_id: "dec-1", run_id: "r", kind: "lineup", choice: { summary: "set Jokic C" }, agrees_with_model: true, rationale: "model plan", created_at: "2026-10-20T04:50:00Z" }],
    },
    { week: 1, start: "2026-10-20", end: "2026-10-25" },
  );
  assert.deepEqual(items.map((x) => x.id), ["txn-il", "apr-1", "txn-1", "dec-1", "adv-a"]);
  const t1 = items.find((x) => x.id === "txn-1");
  assert.equal(t1.title, "Lineup 2026-10-22 · 2 moves");
  assert.deepEqual(t1.detail, ["Jokic: BN → C", "Clingan: C → BN"]);
  assert.match(t1.evidence, /verified 2026-10-20T05:00:00Z by re-reading \/nba\/44495\/11/);
  assert.equal(items[0].kind, "il");
  assert.match(items[0].evidence, /not IL eligible/);
  assert.deepEqual(items.find((x) => x.id === "apr-1").rationale, ["3-and-D"]);
  assert.equal(items.find((x) => x.id === "adv-a").whyNow, "before\n→ changed");
});

test("shortName, daysBetween, pickWeek", () => {
  assert.equal(shortName("Nikola Jokić"), "N. Jokić");
  assert.equal(shortName("Jaren Jackson Jr."), "J. Jackson Jr.");
  assert.equal(shortName("Nenê"), "Nenê");
  assert.deepEqual(daysBetween("2026-10-31", "2026-11-01"), ["2026-10-31", "2026-11-01"]);
  const weeks = [
    { week: 1, start: "2026-10-20", end: "2026-10-25", opponent: 12, opponent_name: "J" },
    { week: 2, start: "2026-10-26", end: "2026-11-01", opponent: 7, opponent_name: "A" },
  ];
  assert.equal(pickWeek(weeks, "2026-10-21").week, 1);
  assert.equal(pickWeek(weeks, "2026-10-01").week, 1);
  assert.equal(pickWeek(weeks, "2027-01-01").week, 2);
  assert.equal(pickWeek(weeks, "2026-10-21", 2).week, 2);
  assert.equal(pickWeek(weeks, "2026-10-21", 99).week, 1);
});

test("league_weeks.json matches data/manual (regenerate: node scripts/league-weeks.mjs)", { skip: !existsSync(new URL(`../../${SCHEDULE_PATH}`, import.meta.url)) }, () => {
  const sched = JSON.parse(readFileSync(new URL(`../../${SCHEDULE_PATH}`, import.meta.url), "utf8"));
  const my = Number(JSON.parse(readFileSync(new URL(`../../${ROSTERS_PATH}`, import.meta.url), "utf8")).my_team ?? 1);
  const json = JSON.parse(readFileSync(new URL("../src/content/league_weeks.json", import.meta.url), "utf8"));
  assert.deepEqual(json, buildWeeks(sched, my));
  assert.ok(json.weeks.length >= 17);
});
