// The dashboard reads the pick order Python stored with the draft (config.order_slots) and only falls
// back to the TS mirror. Fixture: tests/fixtures/draft_order_parity.json (generated from Python).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { orderForDraft, pickInfoForDraft, picksForDraft, storedOrderOf } from "../src/lib/draftOrder.ts";

const fixture = JSON.parse(readFileSync(new URL("../../tests/fixtures/draft_order_parity.json", import.meta.url), "utf8"));

for (const c of fixture.cases) {
  const base = { num_teams: c.num_teams, num_rounds: c.num_rounds, order_type: c.order_type };
  const cfg = c.custom_rounds ? { custom_rounds: c.custom_rounds } : {};

  test(`stored order is used and equals the mirror: ${c.name}`, () => {
    const stored = { ...base, config: { ...cfg, order_slots: c.slots } };
    const fallback = { ...base, config: cfg };
    assert.deepEqual(orderForDraft(stored), orderForDraft(fallback));
    assert.deepEqual(orderForDraft(stored).map((p) => p.slot), c.slots);
    const mine = c.slots.flatMap((s, i) => (s === 1 ? [i + 1] : []));
    assert.deepEqual(picksForDraft(stored, 1), mine);
    assert.equal(pickInfoForDraft(stored, c.slots.length).slot, c.slots.at(-1));
  });
}

test("stored order wins over the formula (single source of truth)", () => {
  const N = 2, R = 2;
  const d = { num_teams: N, num_rounds: R, order_type: "snake", config: { order_slots: [2, 1, 2, 1] } };
  assert.deepEqual(picksForDraft(d, 1), [2, 4]);
  assert.equal(pickInfoForDraft(d, 1).slot, 2);
});

test("invalid stored order falls back to the TS mirror", () => {
  const base = { num_teams: 2, num_rounds: 2, order_type: "snake" };
  for (const bad of [[1, 2, 3], [1, 1, 2, 1], "x", null, [1, 2, 2, 1, 1]]) {
    assert.equal(storedOrderOf({ order_slots: bad }, 2, 2), null);
    assert.deepEqual(picksForDraft({ ...base, config: { order_slots: bad } }, 1), [1, 4]);
  }
});
