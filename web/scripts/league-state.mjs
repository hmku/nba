// Build the "This week" page's bundled league snapshot from the repo's data files:
//   src/content/league_state.json  every team's roster with Yahoo slots + injury tags (data/manual/league_rosters_2026-27.json,
//                                  written by `fantasy league sync`), player names/NBA teams from the projections CSV
//   src/content/nba_schedule.json  the NBA regular-season schedule (data/raw/bbref_schedule/nba_2027_games.csv)
//   node scripts/league-state.mjs           # rewrite both (run from web/); `npm run build` runs it first (prebuild)
// Missing inputs (e.g. a build that only has web/) keep the committed files. The opponent's lineup on the page is
// "as of" this file's captured_at, so the daily routine re-runs this and commits web/ (which also triggers a deploy).
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const ROSTERS_PATH = "data/manual/league_rosters_2026-27.json";
export const PROJECTIONS_PATH = "data/processed/projections_2026_27.csv";
export const SCHEDULE_CSV_PATH = "data/raw/bbref_schedule/nba_2027_games.csv";

/** Minimal CSV reader (the inputs have no quoted commas in the columns used; quotes are stripped). */
export function parseCsv(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  const head = (lines.shift() ?? "").split(",");
  return lines.map((l) => {
    const cells = l.split(",").map((c) => c.replace(/^"|"$/g, ""));
    return Object.fromEntries(head.map((h, i) => [h, cells[i] ?? ""]));
  });
}

const slugToName = (id) =>
  id
    .split("-")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");

/** "… (captured 2026-10-20T04:43:21.234Z)" -> the ISO time, else null. */
export function capturedAt(source) {
  const m = /captured\s+(\d{4}-\d{2}-\d{2}T[\d:.]+Z)/.exec(source ?? "");
  return m ? m[1] : null;
}

/** {source, synced_at, captured_at, my_team, teams: {"<slot>": {name, yahoo_team_id, players: [{id, name, nba_team, positions, slot, status}]}}} */
export function buildLeagueState(rosters, projections) {
  const info = new Map(projections.map((r) => [r.player_id, r]));
  const teams = {};
  for (const [slot, t] of Object.entries(rosters.teams ?? {})) {
    const lineup = t.lineup ?? {};
    const status = t.status ?? {};
    teams[slot] = {
      name: t.name ?? `Team ${slot}`,
      yahoo_team_id: t.yahoo_team_id ?? null,
      players: (t.players ?? []).map((id) => {
        const p = info.get(id);
        return {
          id,
          name: p?.name || slugToName(id),
          nba_team: p?.team || null,
          positions: p?.positions ? p.positions.split("|") : [],
          slot: lineup[id] ?? null,
          status: status[id] ?? null,
        };
      }),
    };
  }
  return {
    source: ROSTERS_PATH,
    synced_at: rosters.synced_at ?? null,
    captured_at: capturedAt(rosters.source),
    my_team: rosters.my_team ?? null,
    teams,
  };
}

/** {source, games: {"YYYY-MM-DD": ["VIS@HOME", ...]}} */
export function buildSchedule(rows) {
  const games = {};
  for (const r of rows) if (r.date && r.visitor && r.home) (games[r.date] ??= []).push(`${r.visitor}@${r.home}`);
  return { source: SCHEDULE_CSV_PATH, games };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = new URL("../../", import.meta.url);
  const path = (p) => fileURLToPath(new URL(p, root));
  const out = (name, obj) => writeFileSync(new URL(`../src/content/${name}`, import.meta.url), JSON.stringify(obj) + "\n");
  if (existsSync(path(ROSTERS_PATH))) {
    const proj = existsSync(path(PROJECTIONS_PATH)) ? parseCsv(readFileSync(path(PROJECTIONS_PATH), "utf8")) : [];
    const state = buildLeagueState(JSON.parse(readFileSync(path(ROSTERS_PATH), "utf8")), proj);
    out("league_state.json", state);
    console.log(`wrote src/content/league_state.json: ${Object.keys(state.teams).length} teams, captured ${state.captured_at ?? "?"}`);
  } else console.log(`${ROSTERS_PATH} not found: keeping src/content/league_state.json`);
  if (existsSync(path(SCHEDULE_CSV_PATH))) {
    const sched = buildSchedule(parseCsv(readFileSync(path(SCHEDULE_CSV_PATH), "utf8")));
    out("nba_schedule.json", sched);
    console.log(`wrote src/content/nba_schedule.json: ${Object.keys(sched.games).length} game days`);
  } else console.log(`${SCHEDULE_CSV_PATH} not found: keeping src/content/nba_schedule.json`);
}
