// Moves view readers (shape: `fantasy advise post|status`, schema advice/1). Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  chipValue,
  formatMetric,
  keyMetrics,
  latestAdvice,
  linkHref,
  parseAdvice,
  parseRationale,
  parseWhyNow,
  splitVerdict,
} from "../src/lib/moves.ts";

const base = (over = {}) => ({
  advice_id: "2026-10-20-murray-trade",
  version: 1,
  type: "trade",
  title: "Ware + VanVleet for Murray",
  status: "idea",
  headline: "2-for-1",
  verdict: "Pursue with the Champagnie refill.",
  requires_user_yes: true,
  metrics: [
    { label: "ΔP(title)", value: 8.6, se: 0.6, unit: "pp", delta: true, evidence: "simulated" },
    { label: "share", value: "about half", evidence: "bogus" },
    { label: "no value" },
  ],
  scenarios: [{ "ΔP(title)": "+8.6", name: "base", refill: "Champagnie" }, { name: "alt", extra: [1] }],
  risks: ["health", "", 3],
  links: [{ label: "note", path: "docs/research/x.md" }, { label: "bad" }],
  ...over,
});

const row = (run_id, created_at, summary) => ({ run_id, kind: "advice", created_at, summary });

test("parseAdvice keeps valid metrics, drops junk, orders scenario columns", () => {
  const a = parseAdvice(row("adv-1", "2026-10-20T04:00:00Z", base()));
  assert.equal(a.adviceId, "2026-10-20-murray-trade");
  assert.equal(a.metrics.length, 2);
  assert.equal(a.metrics[0].se, 0.6);
  assert.equal(a.metrics[1].evidence, null); // unknown label -> unlabelled, not trusted
  assert.deepEqual(a.scenarioColumns, ["name", "ΔP(title)", "refill", "extra"]);
  assert.equal(a.scenarios[1].extra, null); // non-scalar cell
  assert.deepEqual(a.risks, ["health"]);
  assert.equal(a.links.length, 1);
  assert.equal(parseAdvice(row("x", null, { status: "idea" })), null); // no title
  assert.equal(parseAdvice(row("x", null, null)), null);
  const cols = parseAdvice(row("adv-1", null, base({ scenario_columns: ["refill", "name"] }))).scenarioColumns;
  assert.deepEqual(cols, ["refill", "name"]);
});

test("latestAdvice takes the highest version per item and counts versions", () => {
  const rows = [
    row("adv-3", "2026-10-20T06:00:00Z", base({ version: 2, status: "awaiting_yes" })),
    row("adv-2", "2026-10-20T05:00:00Z", base({ advice_id: "stream-1", type: "stream", status: "done", requires_user_yes: false })),
    row("adv-1", "2026-10-20T04:00:00Z", base()),
  ];
  const items = latestAdvice(rows);
  assert.equal(items.length, 2);
  const t = items.find((x) => x.adviceId === "2026-10-20-murray-trade");
  assert.equal(t.runId, "adv-3");
  assert.equal(t.status, "awaiting_yes");
  assert.equal(t.versions, 2);
  assert.equal(t.firstCreatedAt, "2026-10-20T04:00:00Z");
  assert.equal(items[0].runId, "adv-3"); // newest first
});

test("formatMetric and linkHref", () => {
  assert.equal(formatMetric({ value: 8.6, se: 0.6, unit: "pp", delta: true }), "+8.6 ± 0.6 pp");
  assert.equal(formatMetric({ value: -3.5, se: 0.4, unit: "pp", delta: true }), "−3.5 ± 0.4 pp");
  assert.equal(formatMetric({ value: 2.31, se: null, unit: null, delta: false }), "2.31");
  assert.equal(formatMetric({ value: "+2 to +10", se: null, unit: "pp", delta: false }), "+2 to +10 pp");
  assert.deepEqual(linkHref("/matchup"), { href: "/matchup", internal: true });
  assert.equal(linkHref("docs/research/x.md").href, "https://github.com/hmku/nba/blob/main/docs/research/x.md");
  assert.equal(linkHref("https://a.b/c").internal, false);
});

test("linkHref: protocol-relative and scheme paths never become internal links", () => {
  assert.deepEqual(linkHref("/"), { href: "/", internal: true });
  for (const p of ["//evil.example/x", "/\\evil.example/x"]) {
    const r = linkHref(p);
    assert.equal(r.internal, false, p);
    assert.ok(r.href.startsWith("https://github.com/hmku/nba/blob/main/"), r.href);
  }
  assert.ok(linkHref("javascript:alert(1)").href.startsWith("https://github.com/"));
  assert.equal(linkHref("./docs/a.md").href, "https://github.com/hmku/nba/blob/main/docs/a.md");
});

test("rationale and why_now (advice-0.2.0) parse; old rows without them still parse", () => {
  const old = parseAdvice(row("adv-old", null, base()));
  assert.deepEqual(old.rationale, []);
  assert.equal(old.whyNow, null);
  const a = parseAdvice(
    row("adv-new", null, base({
      rationale: ["Champagnie shoots threes and rebounds.", "- Bailey is a raw rookie", "• cheap to try", " ", 4],
      why_now: {
        before: "At the draft our model ranked him below the last picks.",
        changed: ["He started 3 preseason games", { text: "inseason-0.3.0 refill model", kind: "model" }, { text: "x", kind: "bogus" }, "", 7],
        evidence: ["ΔP(title)", 3],
      },
    })),
  );
  assert.deepEqual(a.rationale, [
    { text: "Champagnie shoots threes and rebounds.", bullet: false },
    { text: "Bailey is a raw rookie", bullet: true },
    { text: "cheap to try", bullet: true },
  ]);
  assert.equal(a.whyNow.before, "At the draft our model ranked him below the last picks.");
  assert.deepEqual(a.whyNow.changed, [
    { text: "He started 3 preseason games", kind: null },
    { text: "inseason-0.3.0 refill model", kind: "model" },
    { text: "x", kind: null },
  ]);
  assert.deepEqual(a.whyNow.evidence, ["ΔP(title)"]);
});

test("parseRationale/parseWhyNow edge cases", () => {
  assert.deepEqual(parseRationale("one para"), [{ text: "one para", bullet: false }]);
  assert.deepEqual(parseRationale(null), []);
  assert.equal(parseWhyNow("text"), null);
  assert.equal(parseWhyNow({ before: " ", changed: [] }), null);
  assert.deepEqual(parseWhyNow({ changed: ["c"] }), { before: "", changed: [{ text: "c", kind: null }], evidence: [] });
});

test("keyMetrics: key-flagged metrics (advice-0.3.0) else the first three; old rows without `key` still work", () => {
  const ms = (flags) =>
    parseAdvice(row("a", null, base({ metrics: flags.map((k, i) => ({ label: `m${i}`, value: i, evidence: "prior", ...(k ? { key: true } : {}) })) })))
      .metrics;
  assert.deepEqual(keyMetrics(ms([false, false, false, false, false])).map((m) => m.label), ["m0", "m1", "m2"]);
  assert.deepEqual(keyMetrics(ms([false, true, false, false, true])).map((m) => m.label), ["m1", "m4"]);
  assert.deepEqual(keyMetrics(ms([true, true, true, true])).map((m) => m.label), ["m0", "m1", "m2"]);
  assert.equal(ms([true])[0].key, true);
  assert.equal(parseAdvice(row("a", null, base())).metrics[0].key, false);
  assert.deepEqual(keyMetrics([]), []);
});

test("splitVerdict: first sentence on the card, the rest under Details (no text twice)", () => {
  assert.deepEqual(splitVerdict("Recommended: +1.8 pp title odds. Costs ~1 pp this week."), {
    first: "Recommended: +1.8 pp title odds.",
    rest: "Costs ~1 pp this week.",
  });
  assert.deepEqual(splitVerdict("Keep 8.6 vs. lineup as is"), { first: "Keep 8.6 vs. lineup as is", rest: "" });
  assert.deepEqual(splitVerdict("Line one\nLine two"), { first: "Line one", rest: "Line two" });
  assert.deepEqual(splitVerdict("Do it! \"Now\" or never."), { first: "Do it!", rest: "\"Now\" or never." });
  assert.deepEqual(splitVerdict(null), { first: "", rest: "" });
});

test("chipValue drops the SE and tightens pp/% units", () => {
  assert.equal(chipValue({ value: 1.76, unit: "pp", delta: true }), "+1.76pp");
  assert.equal(chipValue({ value: -0.9, unit: "pp", delta: true }), "−0.9pp");
  assert.equal(chipValue({ value: 0.45, unit: "cat wins", delta: false }), "0.45 cat wins");
  assert.equal(chipValue({ value: "+3.9 / +3.7", unit: "pp", delta: false }), "+3.9 / +3.7pp");
});
