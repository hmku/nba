// Unit tests for the external-agent API helpers. Run: `npm test` (node --test, Node ≥ 22.18 strips TS types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ALIASES,
  TRANSLIT,
  bearerMatches,
  canonicalId,
  boardInfo,
  defaultOverall,
  museRunId,
  recIsFresh,
  resolvePlayer,
  searchPlayers,
  slugify,
  validatePick,
  validateReport,
} from "../src/lib/agentPure.ts";
import { picksForSlot } from "../src/lib/draftOrder.ts";

const pool = [
  { player_id: "nikola-jokic", name: "Nikola Jokić", team: "DEN", positions: ["C"], rank: 1 },
  { player_id: "jalen-williams", name: "Jalen Williams", team: "OKC", positions: ["SG", "SF"], rank: 20 },
  { player_id: "jaylin-williams", name: "Jaylin Williams", team: "OKC", positions: ["PF", "C"], rank: 300 },
  { player_id: "jaren-jackson-jr", name: "Jaren Jackson Jr.", team: "MEM", positions: ["PF", "C"], rank: 15 },
  { player_id: "victor-wembanyama", name: "Victor Wembanyama", team: "SAS", positions: ["C"], rank: 2 },
];

test("bearer token: exact match only, case-insensitive scheme", () => {
  assert.equal(bearerMatches("Bearer s3cret", "s3cret"), true);
  assert.equal(bearerMatches("bearer s3cret", "s3cret"), true);
  assert.equal(bearerMatches("Bearer s3cre", "s3cret"), false);
  assert.equal(bearerMatches("Bearer s3cretx", "s3cret"), false);
  assert.equal(bearerMatches("s3cret", "s3cret"), false);
  assert.equal(bearerMatches(null, "s3cret"), false);
  assert.equal(bearerMatches("Bearer ", ""), false);
});

test("resolvePlayer: id, accents, suffix, fuzzy, ambiguity", () => {
  assert.equal(resolvePlayer("nikola-jokic", pool).player.player_id, "nikola-jokic");
  assert.equal(resolvePlayer("Nikola Jokic", pool).player.player_id, "nikola-jokic");
  assert.equal(resolvePlayer("Jaren Jackson", pool).player.player_id, "jaren-jackson-jr");
  assert.equal(resolvePlayer("Victor Wembanyamma", pool).player.player_id, "victor-wembanyama");
  assert.equal(resolvePlayer("Jalen Wiliams", pool).player.player_id, "jalen-williams");
  const amb = resolvePlayer("Jaxlin Williams", pool);
  // close to both Jalen and Jaylin Williams → must not guess
  assert.equal(amb.status, "ambiguous");
  assert.ok(amb.candidates.some((c) => c.player_id === "jalen-williams"));
  const nf = resolvePlayer("Williams", pool);
  assert.equal(nf.status, "not_found");
  assert.equal(nf.candidates.length, 2);
  assert.equal(resolvePlayer("Totally Unknown", pool).status, "not_found");
  assert.equal(resolvePlayer("V. Wembanyama", pool).player.player_id, "victor-wembanyama");
  assert.equal(resolvePlayer("J. Jackson Jr.", pool).player.player_id, "jaren-jackson-jr");
  assert.equal(resolvePlayer("J. Williams", pool).status, "ambiguous");
  assert.equal(resolvePlayer("Wembanyama", pool).player.player_id, "victor-wembanyama");
});

test("searchPlayers matches name/team/position terms", () => {
  assert.deepEqual(searchPlayers("okc c", pool).map((p) => p.player_id), ["jaylin-williams"]);
  assert.equal(searchPlayers("jokic", pool).length, 1);
  assert.equal(searchPlayers("", pool).length, pool.length);
});

test("boardInfo: clock after highest pick, gaps, completeness", () => {
  assert.deepEqual(boardInfo([], 156), { picks_made: 0, max_recorded: 0, on_the_clock: 1, gaps: [], complete: false });
  const b = boardInfo([1, 2, 4, 5], 156);
  assert.equal(b.on_the_clock, 6);
  assert.deepEqual(b.gaps, [3]);
  assert.equal(defaultOverall([1, 2, 4, 5], 156), 3);
  const full = Array.from({ length: 24 }, (_, i) => i + 1);
  assert.equal(boardInfo(full, 24).complete, true);
  assert.equal(boardInfo(full, 24).on_the_clock, null);
  assert.equal(defaultOverall(full, 24), null);
});

test("recIsFresh mirrors the dashboard rule", () => {
  assert.equal(recIsFresh(5, 6, 5, 6), true);
  assert.equal(recIsFresh(4, 6, 5, 6), false);
  assert.equal(recIsFresh(5, 6, 5, 7), false);
});

test("3RR order gives slot 1 picks 1, 24, 36, 37, 60, 61", () => {
  assert.deepEqual(picksForSlot(1, 12, 13, "snake_3rr").slice(0, 6), [1, 24, 36, 37, 60, 61]);
});

test("validateReport", () => {
  assert.equal(validateReport({ kind: "bogus", message: "x" }).ok, false);
  assert.equal(validateReport({ kind: "trade" }).ok, false);
  assert.equal(validateReport({ kind: "trade", message: "x", details: [1] }).ok, false);
  assert.equal(validateReport([]).ok, false);
  const r = validateReport({ kind: "trade", message: "a".repeat(2500), details: { a: 1 }, source: "messenger" });
  assert.equal(r.ok, true);
  assert.equal(r.value.message.length, 2000);
  assert.equal(r.value.truncated, true);
  assert.equal(r.value.source, "messenger");
});

test("validatePick", () => {
  assert.equal(validatePick({}).ok, false);
  assert.equal(validatePick({ player: "X", overall: 0 }).ok, false);
  assert.equal(validatePick({ player: "X", overall: 2.5 }).ok, false);
  const ok = validatePick({ player: " Nikola Jokic ", overall: "14" });
  assert.equal(ok.ok, true);
  assert.equal(ok.value.overall, 14);
  assert.equal(ok.value.player, "Nikola Jokic");
  assert.equal(validatePick({ player: "X" }).value.overall, null);
});

test("museRunId is per US/Eastern day", () => {
  assert.equal(museRunId(new Date("2026-10-20T03:30:00Z")), "muse-2026-10-19");
  assert.equal(museRunId(new Date("2026-10-20T05:30:00Z")), "muse-2026-10-20");
});

test("docs/integrations/muse.md is identical to the /agent page source", () => {
  const a = readFileSync(new URL("../src/content/muse.md", import.meta.url), "utf8");
  const b = readFileSync(new URL("../../docs/integrations/muse.md", import.meta.url), "utf8");
  assert.equal(a, b);
});

// Player ids: parity with Python fantasy.data.ids (fixture generated by tests/test_player_id_parity.py).
const idFx = JSON.parse(readFileSync(new URL("../../tests/fixtures/player_id_parity.json", import.meta.url), "utf8"));

test("player ids: alias and transliteration tables equal Python's", () => {
  assert.deepEqual(ALIASES, idFx.aliases);
  assert.deepEqual(TRANSLIT, idFx.translit);
});

test("player ids: slugify/canonicalId equal Python for every projected player + edge cases", () => {
  for (const c of idFx.cases) {
    assert.equal(slugify(c.name), c.slug, c.name);
    assert.equal(canonicalId(c.name), c.id, c.name);
  }
});

test("resolvePlayer: Python alias spellings resolve by id", () => {
  const p = [{ player_id: "alexandre-sarr", name: "Alexandre Sarr", team: "WAS", positions: ["C"], rank: 60 }, ...pool];
  assert.equal(resolvePlayer("Alex Sarr", p).player?.player_id, "alexandre-sarr");
  assert.equal(resolvePlayer("Jaren Jackson", p).player?.player_id, "jaren-jackson-jr");
});
