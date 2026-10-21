// "This week" page: matchup_score parsing, odds fallback, games left, opponent lineup assembly, legacy route redirects,
// and the bundled league snapshot builder. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  canPlay,
  catLeader,
  gamesLeft,
  latestPlanInWeek,
  latestScore,
  mergePlanGames,
  orderRows,
  parseMatchupScore,
  remainingDays,
  simWeekOdds,
  splitActuals,
  teamCode,
  teamGames,
  teamLineup,
  weekOdds,
  yahooMatchupUrl,
} from "../src/lib/week.ts";
import { parseActual, parsePlan } from "../src/lib/lineup.ts";
import { LEGACY_REDIRECTS, legacyTarget, WEEK_PATH } from "../src/lib/routes.ts";
import { buildLeagueState, buildSchedule, capturedAt, parseCsv } from "../scripts/league-state.mjs";

// ------------------------------------------------------------------ fixtures (matchup_score/1 contract)

const totals = (over = {}) => ({ fgm: 120, fga: 250, ftm: 40, fta: 50, fg3m: 30, pts: 310, reb: 140, ast: 70, stl: 20, blk: 12, tov: 35, ...over });

const scoreRow = (over = {}, created = "2026-10-22T12:10:00Z") => ({
  run_id: `ms-1-${created}`,
  kind: "matchup_score",
  created_at: created,
  summary: {
    schema: "matchup_score/1",
    week: 1,
    date_et: "2026-10-22",
    observed_at: "2026-10-22T12:05:00Z",
    source: "yahoo_capture",
    me: { slot: 1, yahoo_team_id: 11, name: "Harrison's Best Team", totals: totals(), fg_pct: 0.48, ft_pct: 0.8, games_played: 20, games_remaining: 22 },
    opp: {
      slot: 12,
      yahoo_team_id: 5,
      name: "Joshua's Excellent Team",
      totals: totals({ fgm: 110, fga: 250, ftm: 45, fta: 50, fg3m: 30, pts: 300, reb: 150, ast: 60, stl: 25, blk: 10, tov: 30 }),
      fg_pct: 0.44,
      ft_pct: 0.9,
      games_played: null,
      games_remaining: null,
    },
    categories: [
      { cat: "fg_pct", me: 0.48, opp: 0.44, leader: "me" },
      { cat: "ft_pct", me: 0.8, opp: 0.9, leader: "opp" },
      { cat: "fg3m", me: 30, opp: 30, leader: "tie" },
      { cat: "pts", me: 310, opp: 300, leader: "me" },
      { cat: "reb", me: 140, opp: 150, leader: "opp" },
      { cat: "ast", me: 70, opp: 60, leader: "me" },
      { cat: "stl", me: 20, opp: 25, leader: "opp" },
      { cat: "blk", me: 12, opp: 10, leader: "me" },
      { cat: "tov", me: 35, opp: 30, leader: "opp" },
    ],
    score: { me: 4, opp: 4, ties: 1 },
    ...over,
  },
});

test("catLeader: more is better, turnovers fewer, ties and missing values", () => {
  assert.equal(catLeader("pts", 10, 9), "me");
  assert.equal(catLeader("pts", 9, 10), "opp");
  assert.equal(catLeader("tov", 9, 10), "me");
  assert.equal(catLeader("tov", 11, 10), "opp");
  assert.equal(catLeader("reb", 5, 5), "tie");
  assert.equal(catLeader("reb", null, 5), null);
});

test("parseMatchupScore reads the contract: sides, 9 categories in Yahoo order, percentages from makes/attempts, score", () => {
  const s = parseMatchupScore(scoreRow());
  assert.ok(s);
  assert.equal(s.week, 1);
  assert.equal(s.dateEt, "2026-10-22");
  assert.equal(s.observedAt, "2026-10-22T12:05:00Z");
  assert.equal(s.me.yahooTeamId, 11);
  assert.equal(s.opp.slot, 12);
  assert.equal(s.me.gamesRemaining, 22);
  assert.equal(s.opp.gamesRemaining, null);
  assert.deepEqual(
    s.categories.map((c) => c.cat),
    ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"],
  );
  // FG% = 120/250 = .48 recomputed from makes/attempts, not the rounded stored ratio
  assert.equal(s.categories[0].me, 120 / 250);
  assert.equal(s.categories[0].leader, "me");
  assert.deepEqual(s.categories.map((c) => c.leader), ["me", "opp", "tie", "me", "opp", "me", "opp", "me", "opp"]);
  assert.deepEqual(s.score, { me: 4, opp: 4, ties: 1 });
});

test("parseMatchupScore: makes/attempts override a stale stored ratio and leader; score recounted", () => {
  // stored fg_pct and leader disagree with the totals (me 100/250 = .40 < opp .44): the totals win
  const row = scoreRow({
    me: { slot: 1, yahoo_team_id: 11, name: "Me", totals: totals({ fgm: 100 }), fg_pct: 0.48, ft_pct: 0.8 },
    score: { me: 4, opp: 4, ties: 1 },
  });
  const s = parseMatchupScore(row);
  assert.equal(s.categories[0].me, 0.4);
  assert.equal(s.categories[0].leader, "opp");
  assert.deepEqual(s.score, { me: 3, opp: 5, ties: 1 });
});

test("parseMatchupScore: missing categories fall back to totals; junk rows are ignored", () => {
  const s = parseMatchupScore(scoreRow({ categories: [] }));
  assert.equal(s.categories.find((c) => c.cat === "pts").me, 310);
  assert.equal(s.categories.find((c) => c.cat === "tov").leader, "opp");
  assert.equal(parseMatchupScore({ run_id: "x", kind: "lineup_plan", created_at: null, summary: { week: 1 } }), null);
  assert.equal(parseMatchupScore({ run_id: "x", kind: "matchup_score", created_at: null, summary: { schema: "other/1", week: 1 } }), null);
  assert.equal(parseMatchupScore({ run_id: "x", kind: "matchup_score", created_at: null, summary: {} }), null);
  // a capture with no totals at all: values null, the stated score is kept
  const bare = parseMatchupScore({ run_id: "b", kind: "matchup_score", created_at: null, summary: { week: 2, me: {}, opp: {}, score: { me: 1, opp: 0 } } });
  assert.deepEqual(bare.score, { me: 1, opp: 0, ties: 0 });
  assert.equal(bare.categories[3].leader, null);
});

test("latestScore: newest row of the requested week", () => {
  const older = scoreRow({}, "2026-10-21T12:00:00Z");
  const newer = scoreRow({ score: { me: 9, opp: 0, ties: 0 } }, "2026-10-22T12:00:00Z");
  const other = scoreRow({ week: 2 }, "2026-10-23T12:00:00Z");
  assert.equal(latestScore([older, other, newer], 1).runId, newer.run_id);
  assert.equal(latestScore([older], 2), null);
});

// ------------------------------------------------------------------ odds

const plan = (date, expected, created = `${date}T13:00:00Z`) =>
  parsePlan({
    run_id: `lp-${date}-${created}`,
    kind: "lineup_plan",
    created_at: created,
    summary: {
      schema: "lineup_plan/1",
      date,
      week: 1,
      players: { a: { name: "Player A", positions: ["PG"], nba_team: "BOS", status: null }, b: { name: "Player B", positions: ["C"], nba_team: "PHX", status: "O" } },
      slots: [
        { slot: "PG", player_id: "a", has_game: true },
        { slot: "BN", player_id: "b", has_game: false },
      ],
      games: { a: { "2026-10-22": "@DET", "2026-10-24": "vs NYK" }, b: {} },
      expected,
    },
  });

const SIM = { pWin: 0.55, pTie: 0.05, catWin: { pts: 0.6, tov: 0.4 } };

test("weekOdds: the plan's by-Sunday category odds when present, else the season sim (labelled)", () => {
  const withCats = plan("2026-10-22", {
    optimized: { expected_cats: 5, p_win_matchup: 0.6 },
    monte_carlo: { n_sims: 2000, plans: { optimized: { expected_cats: 5, p_win_matchup: 0.64, p_win_matchup_se: 0.01 } } },
    cat_win_prob: { pts: 0.9, tov: 0.2, bogus: 3, reb: 1.5 },
  });
  assert.deepEqual(withCats.expected.catWinProb, { pts: 0.9, tov: 0.2 });
  const o = weekOdds(withCats, SIM, "sim-1");
  assert.equal(o.source, "lineup_plan");
  assert.equal(o.pWin, 0.64);
  assert.equal(o.pWinSe, 0.01);
  assert.equal(o.method, "Monte Carlo");
  assert.deepEqual(o.catWin, { pts: 0.9, tov: 0.2 });
  // older plan without cat_win_prob -> season sim
  const old = plan("2026-10-22", { optimized: { expected_cats: 5, p_win_matchup: 0.6 } });
  assert.equal(old.expected.catWinProb, null);
  const f = weekOdds(old, SIM, "sim-1");
  assert.equal(f.source, "season_sim");
  assert.equal(f.runId, "sim-1");
  assert.equal(f.pWin, 0.55);
  assert.deepEqual(f.catWin, { pts: 0.6, tov: 0.4 });
  assert.equal(weekOdds(null, null, null).source, null);
});

test("simWeekOdds prefers the H2H matrix cell for the real opponent; latestPlanInWeek picks the newest plan in range", () => {
  const weeks = [{ week: 1, opponent: 3, pWin: 0.5, pTie: 0, catWin: { pts: 0.5 } }];
  const h2h = [{ week: 1, vs: { 12: { team: 12, pWin: 0.7, pTie: 0.02, catWin: { pts: 0.8 } } } }];
  assert.equal(simWeekOdds(weeks, h2h, 1, 12).pWin, 0.7);
  assert.equal(simWeekOdds(weeks, h2h, 1, 3).pWin, 0.5);
  assert.equal(simWeekOdds(weeks, [], 1, 9), null);
  const p1 = plan("2026-10-21", null, "2026-10-21T13:00:00Z");
  const p2 = plan("2026-10-22", null, "2026-10-22T09:00:00Z");
  const p3 = plan("2026-10-27", null, "2026-10-27T09:00:00Z");
  assert.equal(latestPlanInWeek([p1, p3, p2], "2026-10-20", "2026-10-25", "2026-10-22").runId, p2.runId);
  // a later day's plan (saved by a --week run) is never the week's odds: on Wed use Wed's plan, not Sunday's
  const p4 = plan("2026-10-25", null, "2026-10-21T02:44:00Z");
  assert.equal(latestPlanInWeek([p1, p2, p4], "2026-10-20", "2026-10-25", "2026-10-21").runId, p1.runId);
  // week not started yet: its first day's plan
  assert.equal(latestPlanInWeek([p2, p4], "2026-10-20", "2026-10-25", "2026-10-19").runId, p2.runId);
  assert.equal(latestPlanInWeek([p3], "2026-10-20", "2026-10-25", "2026-10-21"), null);
  // a plan whose banked totals predate its date (covers_week false) is skipped for an older complete one
  const stale = plan("2026-10-22", { covers_week: false }, "2026-10-22T11:00:00Z");
  assert.equal(stale.expected.coversWeek, false);
  assert.equal(latestPlanInWeek([p1, stale], "2026-10-20", "2026-10-25", "2026-10-22").runId, p1.runId);
});

// ------------------------------------------------------------------ games left

const SCHED = {
  "2026-10-22": ["BOS@DET", "PHO@LAL"],
  "2026-10-23": ["NYK@BOS"],
  "2026-10-24": ["BOS@NYK", "LAL@PHO"],
};

test("teamGames: per-team markers from the schedule ('@HOME' away, 'vs VIS' home)", () => {
  const g = teamGames(SCHED, ["2026-10-22", "2026-10-23"]);
  assert.deepEqual(g.BOS, { "2026-10-22": "@DET", "2026-10-23": "vs NYK" });
  assert.deepEqual(g.DET, { "2026-10-22": "vs BOS" });
  assert.equal(g.NYK["2026-10-24"], undefined); // outside the requested days
  assert.equal(teamCode("phx"), "PHO");
  assert.equal(teamCode("BKN"), "BRK");
  assert.equal(teamCode(null), null);
});

test("gamesLeft counts remaining games of rostered players not on IL / not out; explicit plan games win", () => {
  const days = remainingDays(["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"], "2026-10-22");
  assert.deepEqual(days, ["2026-10-22", "2026-10-23", "2026-10-24"]);
  const byTeam = teamGames(SCHED, days);
  const players = [
    { id: "a", nbaTeam: "BOS", slot: "PG", status: null }, // plan says 2 games (explicit)
    { id: "c", nbaTeam: "BOS", slot: "BN", status: "Q" }, // schedule: 3 games (Q still counts)
    { id: "d", nbaTeam: "LAL", slot: "IL", status: "O" }, // IL: excluded
    { id: "e", nbaTeam: "PHX", slot: "C", status: "O" }, // out: excluded
    { id: "f", nbaTeam: null, slot: "Util", status: null }, // unknown schedule
  ];
  const g = gamesLeft(players, days, { a: { "2026-10-22": "@DET", "2026-10-24": "@NYK", "2026-10-30": "x" } }, byTeam);
  assert.equal(g.total, 5);
  assert.deepEqual(g.perPlayer, { a: 2, c: 3 });
  assert.equal(g.counted, 3);
  assert.equal(g.unknown, 1);
  assert.equal(canPlay({ slot: "BN", status: "GTD" }), true);
  assert.equal(canPlay({ slot: "IL+", status: null }), false);
  assert.deepEqual(remainingDays(["2026-10-20"], "2026-10-22"), []);
});

test("mergePlanGames: newer plans win; players a plan knows without games get an empty map (= no game)", () => {
  const p = plan("2026-10-22", null);
  const { games, nbaTeam } = mergePlanGames([p]);
  assert.deepEqual(games.b, {});
  assert.equal(games.a["2026-10-24"], "vs NYK");
  assert.equal(nbaTeam.b, "PHX");
});

// ------------------------------------------------------------------ opponent lineup

const OPP = {
  name: "Joshua's Excellent Team",
  yahoo_team_id: 5,
  players: [
    { id: "x1", name: "Xavier One", nba_team: "BOS", slot: "C", status: null },
    { id: "x2", name: "Xavier Two", nba_team: "PHX", slot: "PG", status: "Q" },
    { id: "x3", name: "Xavier Three", nba_team: "LAL", slot: "BN", status: null },
    { id: "x4", name: "Xavier Four", nba_team: "NYK", slot: "IL", status: "O" },
    { id: "x5", name: "Xavier Five", nba_team: "DET", slot: "C", status: null },
    { id: "x6", name: "Xavier Six", nba_team: "DET", slot: null, status: null },
  ],
};

test("teamLineup from the snapshot: Yahoo slot order, empty starting slots kept, game markers for the day", () => {
  const byTeam = teamGames(SCHED, ["2026-10-22", "2026-10-23"]);
  const rows = teamLineup(OPP, "2026-10-22", byTeam);
  assert.deepEqual(
    rows.map((r) => r.slot),
    ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util", "BN", "IL", "?"],
  );
  assert.equal(rows[0].playerId, "x2");
  assert.equal(rows[0].game, "@LAL"); // PHO@LAL, via the PHX alias
  assert.equal(rows[0].status, "Q");
  assert.equal(rows[1].playerId, null); // empty SG kept
  assert.equal(rows[6].game, "@DET");
  assert.equal(rows[7].playerId, "x5");
  assert.equal(rows[7].game, "vs BOS");
  // a future day: same slots, that day's games
  const fri = teamLineup(OPP, "2026-10-23", byTeam);
  assert.equal(fri[6].game, "vs NYK");
  assert.equal(fri[0].hasGame, false);
  assert.deepEqual(teamLineup(null, "2026-10-22", byTeam), []);
});

test("teamLineup prefers a recorded Yahoo read of their team; splitActuals separates mine from theirs", () => {
  const act = (team, created) =>
    parseActual({
      run_id: `la-${team}-${created}`,
      kind: "lineup_actual",
      created_at: created,
      summary: {
        schema: "lineup_actual/1",
        date: "2026-10-22",
        ...(team != null ? { team } : {}),
        source: "yahoo_capture",
        observed_at: created,
        slots: [
          { slot: "PG", player_id: "x3", name: "Xavier Three" },
          { slot: "BN", player_id: "x2", name: "Xavier Two", status: "O" },
        ],
      },
    });
  const legacy = act(null, "2026-10-22T10:00:00Z");
  const mineRow = act(1, "2026-10-22T11:00:00Z");
  const theirs = act(12, "2026-10-22T12:00:00Z");
  const stranger = act(5, "2026-10-22T12:00:00Z");
  const split = splitActuals([legacy, mineRow, theirs, stranger], 1, 12);
  assert.deepEqual(split.mine.map((x) => x.runId), [legacy.runId, mineRow.runId]);
  assert.deepEqual(split.theirs.map((x) => x.runId), [theirs.runId]);
  const byTeam = teamGames(SCHED, ["2026-10-22"]);
  const rows = teamLineup(OPP, "2026-10-22", byTeam, theirs);
  assert.equal(rows[0].playerId, "x3");
  assert.equal(rows[0].game, "vs PHO"); // LAL hosts PHO; NBA team from the snapshot
  const bn = rows.find((r) => r.slot === "BN");
  assert.equal(bn.status, "O");
});

test("orderRows keeps unknown labels last and fills missing starters", () => {
  const rows = orderRows([{ slot: "Util", playerId: "u", name: "U", nbaTeam: null, status: null, game: null, hasGame: null }]);
  assert.equal(rows.length, 10);
  assert.equal(rows[8].playerId, "u");
});

// ------------------------------------------------------------------ links + routes

test("yahooMatchupUrl uses both Yahoo team ids when known", () => {
  assert.equal(yahooMatchupUrl(1, 11, 5), "https://basketball.fantasysports.yahoo.com/nba/44495/matchup?week=1&mid1=11&mid2=5");
  assert.equal(yahooMatchupUrl(3, null, 5), "https://basketball.fantasysports.yahoo.com/nba/44495/matchup?week=3");
});

test("legacy routes /matchup and /lineup redirect to /week keeping the query", () => {
  assert.equal(WEEK_PATH, "/week");
  assert.deepEqual(
    LEGACY_REDIRECTS.filter((r) => r.destination === WEEK_PATH).map((r) => [r.source, r.destination, r.permanent]),
    [
      ["/matchup", "/week", false],
      ["/lineup", "/week", false],
    ],
  );
  assert.equal(legacyTarget("/lineup", "?week=2"), "/week?week=2");
  assert.equal(legacyTarget("/matchup", "week=3&opp=7"), "/week?week=3&opp=7");
  assert.equal(legacyTarget("/matchup/"), "/week");
  assert.equal(legacyTarget("/league", "?week=2"), null);
});

// ------------------------------------------------------------------ bundled snapshot builder

test("league-state script: roster slots/status per team, names from projections, schedule by date", () => {
  const rosters = {
    source: "yahoo roster capture x.json (captured 2026-10-20T04:43:21.234Z)",
    my_team: 1,
    synced_at: "2026-10-20T04:44:57Z",
    teams: {
      12: { name: "Opp", yahoo_team_id: 5, players: ["a-b", "zz-unknown"], lineup: { "a-b": "PG" }, status: { "zz-unknown": "O" } },
    },
  };
  const proj = parseCsv("player_id,name,team,positions\na-b,A Bé,BOS,PG|SG\n");
  const st = buildLeagueState(rosters, proj);
  assert.equal(st.captured_at, "2026-10-20T04:43:21.234Z");
  assert.equal(st.my_team, 1);
  assert.deepEqual(st.teams["12"].players, [
    { id: "a-b", name: "A Bé", nba_team: "BOS", positions: ["PG", "SG"], slot: "PG", status: null },
    { id: "zz-unknown", name: "Zz Unknown", nba_team: null, positions: [], slot: null, status: "O" },
  ]);
  assert.equal(capturedAt("no time"), null);
  const sched = buildSchedule(parseCsv("date,visitor,home\n2026-10-20,BOS,DET\n2026-10-20,OKC,SAS\n"));
  assert.deepEqual(sched.games, { "2026-10-20": ["BOS@DET", "OKC@SAS"] });
});
