// Build web/src/content/optimism.json from the decomposition table in
// docs/research/optimism_decomposition.md (slot-1 pre-draft scenarios).
//   node scripts/optimism.mjs            # rewrite the JSON (run from web/)
// tests/optimism.test.mjs checks the committed JSON still matches the doc.
import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export const DOC_PATH = "docs/research/optimism_decomposition.md";
export const DOC_URL = "https://github.com/hmku/nba/blob/main/docs/research/optimism_decomposition.md";
const TRUTHS = ["ours", "blend", "market"];
const CELL = /([\d.]+)\s*\((\.\d+)\)\s*\/\s*([\d.]+)\s*\((\.\d+)\)/;

/** Parse the "Decomposition table" rows: { id, label, who, ours|blend|market: {p_playoffs, se_playoffs, p_title, se_title} }. */
export function parseTable(md) {
  const start = md.indexOf("## Decomposition table");
  if (start < 0) throw new Error("decomposition table not found");
  const lines = md.slice(start).split("\n");
  const rows = [];
  let inTable = false;
  for (const line of lines) {
    if (line.startsWith("|")) inTable = true;
    else if (inTable) break;
    else continue;
    const cells = line.split("|").slice(1, -1).map((c) => c.trim());
    if (cells.length !== 5 || !CELL.test(cells[2])) continue;
    const name = cells[0].replace(/\*\*/g, "").trim(); // "A A_sym", "B status quo", "C6"
    const m = /^([A-Z])\s+(.*)$/.exec(name);
    const id = m ? (/^\w+$/.test(m[2]) ? m[2] : m[1]) : name;
    const row = { id, label: m && !/^\w+$/.test(m[2]) ? m[2] : id, who: cells[1].replace(/\*\*/g, "") };
    TRUTHS.forEach((t, i) => {
      const c = CELL.exec(cells[2 + i]);
      row[t] = { p_playoffs: +c[1], se_playoffs: +c[2], p_title: +c[3], se_title: +c[4] };
    });
    rows.push(row);
  }
  return rows;
}

export function buildOptimism(md, { sha = null, date = null } = {}) {
  const rows = parseTable(md);
  const get = (id, truth) => {
    const r = rows.find((x) => x.id === id);
    if (!r) throw new Error(`room ${id} missing`);
    return { room: id, truth, ...r[truth] };
  };
  const c3 = get("C3", "blend");
  const c3m = get("C3m", "blend");
  return {
    source: { doc: DOC_PATH, url: DOC_URL, git_sha: sha, date },
    slot: 1,
    baseline: { p_playoffs: 0.5, p_title: 1 / 12 },
    central: {
      label: "A few sharp opponents; truth halfway between our projections and the market",
      rooms: ["C3", "C3m"],
      truth: "blend",
      p_playoffs: [Math.min(c3.p_playoffs, c3m.p_playoffs), Math.max(c3.p_playoffs, c3m.p_playoffs)],
      p_title: [Math.min(c3.p_title, c3m.p_title), Math.max(c3.p_title, c3m.p_title)],
    },
    pessimistic: { label: "11 sharp market-informed opponents; the market is right", ...get("C11m", "market") },
    optimistic: { label: "Status quo: opponents draft by noisy ADP; graded by our projections", ...get("B", "ours") },
    slot_alone: { label: "Symmetric room: all 12 teams draft like us", ...get("A_sym", "ours") },
    table: rows,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = new URL("../../", import.meta.url);
  const md = readFileSync(new URL(DOC_PATH, root), "utf8");
  const [sha, date] = execFileSync("git", ["log", "-1", "--format=%H %cs", "--", DOC_PATH], { cwd: fileURLToPath(root) })
    .toString()
    .trim()
    .split(" ");
  const out = buildOptimism(md, { sha, date });
  writeFileSync(new URL("web/src/content/optimism.json", root), JSON.stringify(out, null, 2) + "\n");
  console.log(`wrote web/src/content/optimism.json (${out.table.length} rooms, doc @ ${sha?.slice(0, 7)})`);
}
