// Build web/src/content/league_weeks.json (the Lineup tab's week picker) from the league schedule
// data/manual/league_schedule.json (Yahoo capture) and my team id from data/manual/league_rosters_2026-27.json.
//   node scripts/league-weeks.mjs        # rewrite the JSON (run from web/)
// tests/lineup.test.mjs checks the committed JSON still matches data/manual.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const SCHEDULE_PATH = "data/manual/league_schedule.json";
export const ROSTERS_PATH = "data/manual/league_rosters_2026-27.json";

/** {my_team, weeks: [{week, start, end, opponent, opponent_name}]} — opponent null when the pairing is unknown. */
export function buildWeeks(schedule, myTeam) {
  const names = schedule.team_names ?? {};
  const weeks = (schedule.weeks ?? []).map((w) => {
    const pair = (w.matchups ?? []).find((m) => m.includes(myTeam));
    const opp = pair ? pair.find((t) => t !== myTeam) ?? null : null;
    return { week: w.week, start: w.start, end: w.end, opponent: opp, opponent_name: opp != null ? (names[String(opp)] ?? null) : null };
  });
  return { source: SCHEDULE_PATH, my_team: myTeam, my_team_name: names[String(myTeam)] ?? null, weeks };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = new URL("../../", import.meta.url);
  const schedule = JSON.parse(readFileSync(new URL(SCHEDULE_PATH, root), "utf8"));
  const myTeam = Number(JSON.parse(readFileSync(new URL(ROSTERS_PATH, root), "utf8")).my_team ?? 1);
  const out = buildWeeks(schedule, myTeam);
  writeFileSync(new URL("../src/content/league_weeks.json", import.meta.url), JSON.stringify(out, null, 1) + "\n");
  console.log(`wrote src/content/league_weeks.json: ${out.weeks.length} weeks, my team ${myTeam}`);
}
