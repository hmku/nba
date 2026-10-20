// Moves view readers (shape: `fantasy advise post|status`, schema advice/1). Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatMetric, groupMoves, latestAdvice, linkHref, parseAdvice } from "../src/lib/moves.ts";

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

test("groupMoves sections", () => {
  const mk = (id, over) => parseAdvice(row(id, "2026-10-21T10:00:00Z", base({ advice_id: id, ...over })));
  const items = [
    mk("needs-yes", {}),
    mk("awaiting", { requires_user_yes: false, status: "awaiting_yes" }),
    mk("idea-free", { requires_user_yes: false }),
    mk("approved", { status: "approved" }),
    mk("lineup-today", { type: "lineup", for_date: "2026-10-21", status: "done", requires_user_yes: false }),
    mk("lineup-old", { type: "lineup", for_date: "2026-10-20", status: "idea", requires_user_yes: false }),
    mk("rejected", { status: "rejected" }),
  ];
  const g = groupMoves(items, "2026-10-21");
  const ids = (xs) => xs.map((x) => x.adviceId);
  assert.deepEqual(ids(g.needsYes), ["needs-yes", "awaiting"]);
  assert.deepEqual(ids(g.lineup), ["lineup-today"]);
  assert.deepEqual(ids(g.ideas), ["idea-free", "approved"]);
  assert.deepEqual(ids(g.closed), ["lineup-old", "rejected"]);
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
