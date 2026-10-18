// The committed scenario JSON must match the research doc's table and headline text. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildOptimism, DOC_PATH } from "../scripts/optimism.mjs";

const md = readFileSync(new URL(`../../${DOC_PATH}`, import.meta.url), "utf8");
const json = JSON.parse(readFileSync(new URL("../src/content/optimism.json", import.meta.url), "utf8"));
const pct = (v) => Math.round(v * 100);

test("optimism.json matches docs/research/optimism_decomposition.md", () => {
  const fresh = buildOptimism(md, { sha: json.source.git_sha, date: json.source.date });
  assert.deepEqual(json, fresh, "regenerate with `node scripts/optimism.mjs`");
  assert.match(json.source.git_sha ?? "", /^[0-9a-f]{40}$/);
  assert.equal(json.table.length, 9);
});

test("headline numbers agree with the doc's stated ranges (to the doc's rounding)", () => {
  const flat = md.replace(/\s+/g, " ");
  const stated = (lead) => {
    const m = new RegExp(`${lead} \\*\\*(\\d+)–(\\d+)% playoffs and (\\d+)–(\\d+)% title\\*\\*`).exec(flat);
    assert.ok(m, `doc sentence "${lead}" not found`);
    return m.slice(1).map(Number);
  };
  const near = (v, doc) => assert.ok(Math.abs(v * 100 - doc) <= 0.5 + 1e-9, `${v} vs doc ${doc}%`);
  const c = json.central;
  const [cp0, cp1, ct0, ct1] = stated("is roughly");
  near(c.p_playoffs[0], cp0); near(c.p_playoffs[1], cp1); near(c.p_title[0], ct0); near(c.p_title[1], ct1);
  const [rp0, rp1, rt0, rt1] = stated("plausible range of about");
  near(json.pessimistic.p_playoffs, rp0); near(json.optimistic.p_playoffs, rp1);
  near(json.pessimistic.p_title, rt0); near(json.optimistic.p_title, rt1);
  assert.ok(flat.includes(`would make the playoffs ${pct(json.slot_alone.p_playoffs)}% of the time`));
  assert.ok(flat.includes(`win the title ${pct(json.slot_alone.p_title)}%`));
});
