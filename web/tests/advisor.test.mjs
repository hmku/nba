// Advisor channel (ADVISOR_API_TOKEN): auth scoping + suggestion validation. Run: `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, existsSync } from "node:fs";
import {
  ADVISOR_ALLOWED_ENDPOINTS,
  advisorEventPayload,
  advisorRunId,
  authorizeAgentRequest,
  validateAdvisorSuggestion,
  validateReport,
} from "../src/lib/agentPure.ts";

const TOKENS = { agent: "muse-secret-token", advisor: "advisor-secret-token" };
const MUSE = "Bearer muse-secret-token";
const ADV = "Bearer advisor-secret-token";
const auth = (h, m, p, t = TOKENS) => authorizeAgentRequest(h, t, m, p);

// Every authenticated endpoint that exists today, with the methods its route exports.
const ENDPOINTS = [
  ["GET", "/api/agent/state"],
  ["GET", "/api/agent/players"],
  ["GET", "/api/agent/snapshots"],
  ["POST", "/api/agent/report"],
  ["POST", "/api/agent/snapshot"],
  ["POST", "/api/agent/pick"],
];

test("muse token: allowed on every endpoint (unchanged)", () => {
  for (const [m, p] of ENDPOINTS) assert.deepEqual(auth(MUSE, m, p), { ok: true, identity: "muse" }, `${m} ${p}`);
  // and still works when no advisor token is configured
  assert.deepEqual(auth(MUSE, "POST", "/api/agent/pick", { agent: TOKENS.agent }), { ok: true, identity: "muse" });
});

test("advisor token: reads + its own reports only", () => {
  assert.deepEqual(auth(ADV, "GET", "/api/agent/state"), { ok: true, identity: "advisor" });
  assert.deepEqual(auth(ADV, "GET", "/api/agent/players"), { ok: true, identity: "advisor" });
  assert.deepEqual(auth(ADV, "POST", "/api/agent/report"), { ok: true, identity: "advisor" });
  assert.deepEqual(auth(ADV, "GET", "/api/agent/state/"), { ok: true, identity: "advisor" }, "trailing slash");
  assert.deepEqual(auth(ADV, "get", "/api/agent/state"), { ok: true, identity: "advisor" }, "method case");
});

test("advisor token: 403 on snapshot, pick, private snapshots and anything unknown (default deny)", () => {
  for (const [m, p] of [
    ["POST", "/api/agent/snapshot"],
    ["POST", "/api/agent/pick"],
    ["GET", "/api/agent/snapshots"],
    ["GET", "/api/agent/report"],
    ["POST", "/api/agent/state"],
    ["DELETE", "/api/agent/report"],
    ["POST", "/api/agent/approval"],
    ["POST", "/api/agent/action"],
    ["POST", "/api/approvals"],
    ["POST", "/api/agent/reportx"],
    ["POST", "/api/agent/report/../pick"],
  ]) {
    const r = auth(ADV, m, p);
    assert.equal(r.ok, false, `${m} ${p}`);
    assert.equal(r.status, 403, `${m} ${p}`);
    assert.ok(!r.error.includes(TOKENS.advisor), "never echoes the token");
  }
});

test("wrong / missing token → 401; nothing configured → 503; advisor-only config works", () => {
  assert.equal(auth("Bearer nope", "GET", "/api/agent/state").status, 401);
  assert.equal(auth(null, "GET", "/api/agent/state").status, 401);
  assert.equal(auth("advisor-secret-token", "GET", "/api/agent/state").status, 401, "scheme required");
  assert.equal(auth(MUSE, "GET", "/api/agent/state", {}).status, 503);
  assert.equal(auth(ADV, "GET", "/api/agent/state", { advisor: TOKENS.advisor }).identity, "advisor");
  assert.equal(auth(ADV, "POST", "/api/agent/pick", { advisor: TOKENS.advisor }).status, 403);
  assert.equal(auth(MUSE, "GET", "/api/agent/state", { advisor: TOKENS.advisor }).status, 401);
});

test("same value in both env vars → treated as Muse, advisor disabled", () => {
  const same = { agent: "x-token", advisor: "x-token" };
  assert.deepEqual(auth("Bearer x-token", "POST", "/api/agent/pick", same), { ok: true, identity: "muse" });
});

test("allow-list names real route files that export the method", () => {
  for (const ep of ADVISOR_ALLOWED_ENDPOINTS) {
    const [method, path] = ep.split(" ");
    const file = new URL(`../src/app${path}/route.ts`, import.meta.url);
    assert.ok(existsSync(file), `${path} route exists`);
    assert.match(readFileSync(file, "utf8"), new RegExp(`export async function ${method}\\b`));
  }
});

test("every authenticated /api/agent route goes through the scoped gate", () => {
  const dir = new URL("../src/app/api/agent/", import.meta.url);
  const subs = readdirSync(dir, { withFileTypes: true }).filter((d) => d.isDirectory());
  assert.ok(subs.length >= 6);
  for (const d of subs) {
    const src = readFileSync(new URL(`${d.name}/route.ts`, dir), "utf8");
    assert.match(src, /\b(requireAgent|authorizeAgent)\(req\)/, `${d.name} uses requireAgent/authorizeAgent`);
    assert.doesNotMatch(src, /process\.env\.(AGENT|ADVISOR)_API_TOKEN/, `${d.name} must not check tokens itself`);
  }
  // no other part of the app accepts the advisor token
  const lib = readFileSync(new URL("../src/lib/agentApi.ts", import.meta.url), "utf8");
  assert.match(lib, /ADVISOR_API_TOKEN/);
});

const good = () => ({
  report_kind: "advisor_suggestion",
  category: "add_drop",
  title: "Stream a C on Thursday",
  summary: "Our C slot is empty Thursday; adding X for Y raises expected blk.",
  evidence: [
    { claim: "P(win week) +2.1pp", value: 0.021, se: 0.006, label: "simulated", command: "fantasy sim matchup --week 2 --json" },
    { claim: "X averages 1.8 blk", value: 1.8, label: "fitted" },
  ],
  proposed_change: "add X, drop Y",
  confidence: "medium",
  urgency: "high",
  links: ["https://fantasy.harrisonku.com/moves"],
});

test("advisor suggestion: valid body; identity fields set by the server, client claims dropped", () => {
  const v = validateAdvisorSuggestion({ ...good(), source: "muse", reported_by: "muse", agent: "claude", extra: 1 });
  assert.equal(v.ok, true);
  assert.equal(v.value.category, "add_drop");
  assert.equal(v.value.evidence[0].se, 0.006);
  assert.ok(!("source" in v.value) && !("extra" in v.value));
  const ev = advisorEventPayload(v.value);
  assert.equal(ev.payload.source, "advisor");
  assert.equal(ev.payload.reported_by, "advisor");
  assert.equal(ev.payload.report_kind, "advisor_suggestion");
  assert.equal(ev.message, "[add_drop] Stream a C on Thursday");
  assert.equal(validateAdvisorSuggestion({ ...good(), report_kind: undefined, kind: "advisor_suggestion" }).ok, true);
});

test("advisor suggestion: rejects other report kinds, bad enums, missing evidence, oversize", () => {
  const bad = (patch) => validateAdvisorSuggestion({ ...good(), ...patch });
  assert.match(bad({ report_kind: "chat_digest" }).errors.join(), /report_kind/);
  assert.match(bad({ report_kind: "draft_order" }).errors.join(), /report_kind/);
  assert.match(bad({ category: "yahoo" }).errors.join(), /category/);
  assert.match(bad({ evidence: [] }).errors.join(), /at least one/);
  assert.equal(bad({ category: "process", evidence: [] }).ok, true, "process ideas need no numbers");
  assert.match(bad({ evidence: [{ claim: "x", label: "vibes" }] }).errors.join(), /label/);
  assert.match(bad({ evidence: [{ label: "prior" }] }).errors.join(), /claim/);
  assert.match(bad({ evidence: [{ claim: "x", label: "prior", se: { a: 1 } }] }).errors.join(), /se/);
  assert.match(bad({ confidence: "very" }).errors.join(), /confidence/);
  assert.match(bad({ urgency: undefined }).errors.join(), /urgency/);
  assert.match(bad({ links: ["javascript:alert(1)"] }).errors.join(), /https/);
  assert.match(bad({ title: "" }).errors.join(), /title/);
  assert.match(bad({ proposed_change: "x".repeat(20001) }).errors.join(), /proposed_change/);
  assert.match(bad({ summary: "x".repeat(50000) }).errors.join(), /too large/);
  assert.equal(validateAdvisorSuggestion([]).ok, false);
});

test("Muse cannot post advisor_suggestion through its own validator", () => {
  assert.equal(validateReport({ kind: "advisor_suggestion", message: "x" }).ok, false);
});

test("advisorRunId is per US/Eastern day", () => {
  assert.equal(advisorRunId(new Date("2026-10-20T03:30:00Z")), "advisor-2026-10-19");
});

test("docs/integrations/advisor.md is identical to the /advisor page source", () => {
  const a = readFileSync(new URL("../src/content/advisor.md", import.meta.url), "utf8");
  const b = readFileSync(new URL("../../docs/integrations/advisor.md", import.meta.url), "utf8");
  assert.equal(a, b);
  assert.match(a, /ADVISOR_TOKEN|<ADVISOR_TOKEN>/);
});
