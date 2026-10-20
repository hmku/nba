import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CATS, CAT_LABEL, CAT_COLOR, CAT_GLOSS } from "../src/lib/categories.ts";

// Fixture generated from Python src/fantasy/league/settings.py CATEGORIES (tech_debt F16: the web keeps its own copy).
const fx = JSON.parse(readFileSync(new URL("../../tests/fixtures/categories_parity.json", import.meta.url), "utf8"));

test("categories: keys and order equal Python's CATEGORIES", () => {
  assert.deepEqual(CATS, fx.categories.map((c) => c.key));
});

test("categories: labels equal Python's", () => {
  for (const c of fx.categories) assert.equal(CAT_LABEL[c.key], c.label, c.key);
});

test("categories: every category has a colour and a gloss", () => {
  for (const k of CATS) {
    assert.ok(CAT_COLOR[k], k);
    assert.ok(CAT_GLOSS[k], k);
  }
});
