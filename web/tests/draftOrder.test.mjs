// Parity with the Python engine: src/fantasy/draft/order.py and this TS mirror must produce the same
// pick order. The fixture is generated from Python and also checked by tests/test_draft_order.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { customRoundsOf, draftOrder, picksForSlot } from "../src/lib/draftOrder.ts";

const fixture = JSON.parse(readFileSync(new URL("../../tests/fixtures/draft_order_parity.json", import.meta.url), "utf8"));

for (const c of fixture.cases) {
  test(`draft order parity: ${c.name}`, () => {
    const custom = c.custom_rounds ? customRoundsOf({ custom_rounds: c.custom_rounds }, c.num_teams, c.num_rounds) : null;
    if (c.custom_rounds) assert.notEqual(custom, null, "fixture custom_rounds must validate in TS too");
    const order = draftOrder(c.num_teams, c.num_rounds, c.order_type, custom);
    assert.deepEqual(order.map((p) => p.slot), c.slots);
    assert.deepEqual(order.map((p) => p.overall), c.slots.map((_, i) => i + 1));
    const mine = c.slots.flatMap((s, i) => (s === 1 ? [i + 1] : []));
    assert.deepEqual(picksForSlot(1, c.num_teams, c.num_rounds, c.order_type, custom), mine);
  });
}
