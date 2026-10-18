// Draft lifecycle rule parity with Python (tests/fixtures/draft_status_parity.json). Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { draftStatusFor } from "../src/lib/draftStatus.ts";

const fixture = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/draft_status_parity.json", import.meta.url), "utf8"),
);

test("draftStatusFor matches fantasy.draft.store.status_for", () => {
  assert.ok(fixture.cases.length >= 10);
  for (const c of fixture.cases) {
    assert.equal(draftStatusFor(c.current, c.n_picks, c.n_total), c.expected, JSON.stringify(c));
  }
});
