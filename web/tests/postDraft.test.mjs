// Post-draft season-sim readers (shape: fantasy sim season --scenario "post-draft: actual rosters"). Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isSeasonScenario,
  parseH2HMatrix,
  parseScheduleInfo,
  parseVsOpponents,
  weekOpponents,
} from "../src/lib/postDraft.ts";

const myTeam = {
  team: 1,
  weekly_h2h_matrix: [
    {
      week: 2, start: "2026-11-02", end: "2026-11-08", scheduled_opponent: 3, opponent_assumed: true, my_player_games: 40.2,
      vs: { "2": { p_win: 0.72, p_tie: 0.05, exp_cats_won: 5.6, opp_player_games: 36, cat_win_probs: { pts: 0.6, tov: 0.3 } },
            "3": { p_win: 0.56, p_tie: 0.06, exp_cats_won: 5.1, opp_player_games: 37, cat_win_probs: { pts: 0.5 } } },
    },
    { week: 1, start: "2026-10-20", end: "2026-11-01", scheduled_opponent: 2, opponent_assumed: false, vs: {} },
    { nope: true },
  ],
  vs_opponents_all_play: {
    "2": { name: "B", p_win_matchup_any_week: 0.72, exp_cats_won: 5.5, cat_win_rate: { pts: 0.6, bad: "x" } },
    "4": { name: "D", p_win_matchup_any_week: 0.63, exp_cats_won: 5.0, cat_win_rate: {} },
  },
};

test("scenario filter keeps real-roster season runs and drops the pre-draft projection", () => {
  assert.equal(isSeasonScenario(null), true);
  assert.equal(isSeasonScenario(undefined), true);
  assert.equal(isSeasonScenario("post-draft: actual rosters"), true);
  assert.equal(isSeasonScenario("pre-draft projection"), false);
});

test("weekly H2H matrix parses, sorts weeks and ranks opponents toughest first", () => {
  const m = parseH2HMatrix(myTeam);
  assert.deepEqual(m.map((w) => w.week), [1, 2]);
  assert.equal(m[1].opponentAssumed, true);
  assert.equal(m[1].scheduledOpponent, 3);
  assert.deepEqual(m[1].vs["2"].catWin, { pts: 0.6, tov: 0.3 });
  assert.deepEqual(weekOpponents(m[1]).map((c) => c.team), [3, 2]);
  assert.deepEqual(parseH2HMatrix(null), []);
});

test("all-play summary drops non-numeric categories and sorts toughest first", () => {
  const v = parseVsOpponents(myTeam);
  assert.deepEqual(v.map((x) => x.team), [4, 2]);
  assert.deepEqual(v[1].catWinRate, { pts: 0.6 });
});

test("schedule info: assumed round robin is reported", () => {
  const si = parseScheduleInfo({ matchup_schedule: { source: "round_robin(12 teams, 17 weeks)", assumed_weeks: [1, 2], all_assumed: true, note: "ASSUMPTION" } });
  assert.deepEqual(si, { source: "round_robin(12 teams, 17 weeks)", assumedWeeks: [1, 2], allAssumed: true, note: "ASSUMPTION" });
  assert.equal(parseScheduleInfo({}), null);
});
