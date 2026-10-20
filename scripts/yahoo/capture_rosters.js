// Read-only capture of all 12 rosters of Yahoo league 44495 (GET requests with the logged-in session).
// Paste into the browser JS tool while on https://basketball.fantasysports.yahoo.com/nba/44495 ;
// it returns the capture JSON as a string. Save it, then:
//   fantasy league import-capture --raw <file> --kind rosters      (resolves our player ids)
//   fantasy league sync --capture data/raw/yahoo/league_rosters_<date>.json [--write]
// Format: {source, captured_at, pages, notes, teams: {<yahoo team id>: {name, draft_slot: null,
//          players: [{slot, name, player_id: null, team, positions, status, yahoo_player_id}]}}}
// draft_slot is filled by the CLI (previous state's yahoo_team_id, else the league_schedule capture).
await (async () => {
  const LEAGUE = 44495, NUM_TEAMS = 12;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const TEAM_POS = /\b([A-Z]{2,4})\s+-\s+((?:PG|SG|G|SF|PF|F|C|Util)(?:\s*,\s*(?:PG|SG|G|SF|PF|F|C|Util))*)\b/;
  // text nodes joined by spaces (textContent would glue "Curry" and "GSW" together)
  const cellText = (el) => { const w = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT); const out = []; while (w.nextNode()) out.push(w.currentNode.nodeValue.trim()); return out.filter(Boolean).join(" "); };
  async function get(url) {
    let last;
    for (let k = 0; k < 3; k++) {
      try {
        const r = await fetch(url, { credentials: "include", method: "GET" });
        if (r.ok) return await r.text();
        last = new Error(`HTTP ${r.status} ${url}`);
      } catch (e) { last = e; }
      await sleep(1500);
    }
    throw last;
  }
  function parseRow(tr) {
    const a = tr.querySelector("a.name, a[class*='name']");
    if (!a) return null;
    const slot = (tr.querySelector("td")?.textContent || "").trim();
    const tagEl = tr.querySelector(".ysf-player-status, abbr[title], span.F-injury");
    const tag = (tagEl?.textContent || "").trim() || null;
    const sub = ((a.parentElement.querySelector("span.Fz-xxs, .ysf-player-team-pos") || {}).textContent || "").trim();
    // the full player cell contains "NYK - PG,SG" even when a status tag is shown
    const m = sub.match(TEAM_POS) || cellText(a.parentElement).match(TEAM_POS);
    const pid = (a.getAttribute("href") || "").match(/players\/(\d+)/);
    return {
      slot, name: a.textContent.trim(), player_id: null,
      team: m ? m[1].toUpperCase() : null,
      positions: m ? m[2].replace(/\s+/g, "") : null,
      status: tag,
      yahoo_player_id: pid ? Number(pid[1]) : null,
    };
  }
  const teams = {}, problems = [];
  for (let id = 1; id <= NUM_TEAMS; id++) {
    const html = await get(`/nba/${LEAGUE}/${id}`);
    const d = new DOMParser().parseFromString(html, "text/html");
    const name = (d.title.split(" - ")[1] || "").split(" | ")[0].trim();
    const players = [...d.querySelectorAll("table tbody tr")].map(parseRow).filter(Boolean);
    if (!players.length) problems.push(`team ${id}: no rows (logged out or layout change?)`);
    teams[id] = { name, draft_slot: null, players };
    await sleep(300);
  }
  return JSON.stringify({
    source: `Yahoo league ${LEAGUE} team pages read in the user's logged-in browser (scripts/yahoo/capture_rosters.js, read-only)`,
    captured_at: new Date().toISOString(),
    pages: [`/nba/${LEAGUE}/<team_id> for team_id 1..${NUM_TEAMS}`],
    notes: "slot = Yahoo lineup slot at read time; status = Yahoo tag; team/positions null when not parseable; player_id filled by `fantasy league import-capture`",
    problems,
    teams,
  });
})()
