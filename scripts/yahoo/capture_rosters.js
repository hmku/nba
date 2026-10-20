// Read-only capture of all 12 rosters of Yahoo league 44495 (GET requests with the logged-in session).
// Paste into the browser JS tool while on https://basketball.fantasysports.yahoo.com/nba/44495 ;
// it returns the capture JSON as a string. Save it, then:
//   fantasy league import-capture --raw <file> --kind rosters      (resolves our player ids)
//   fantasy league sync --capture data/raw/yahoo/league_rosters_<date>.json [--write]
// captured_at = ISO 8601 in UTC with the "Z" offset (new Date().toISOString()).
// Format: {source, captured_at, pages, notes, teams: {<yahoo team id>: {name, draft_slot: null,
//          players: [{slot, name, player_id: null, team, positions, status, yahoo_player_id}]}}}
// draft_slot is filled by the CLI (previous state's yahoo_team_id, else the league_schedule capture).
await (async () => {
  const LEAGUE = 44495, NUM_TEAMS = 12;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // Yahoo's 30 NBA team codes; no word boundary before the code, so a status tag glued to it
  // ("QGSW - PG,SG" in textContent) still parses.
  const TEAMS = "ATL|BOS|BKN|CHA|CHI|CLE|DAL|DEN|DET|GSW|HOU|IND|LAC|LAL|MEM|MIA|MIL|MIN|NOP|NYK|OKC|ORL|PHI|PHX|POR|SAC|SAS|TOR|UTA|WAS";
  const POS = "(?:PG|SG|G|SF|PF|F|C|Util)";
  const TEAM_POS = new RegExp(`(${TEAMS})\\s*-\\s*(${POS}(?:\\s*,\\s*${POS})*)(?![A-Za-z])`);
  // text nodes joined by spaces (textContent would glue "Curry" and "GSW" together)
  const cellText = (el) => { const w = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT); const out = []; while (w.nextNode()) out.push(w.currentNode.nodeValue.trim()); return out.filter(Boolean).join(" "); };
  // team + Yahoo positions from the player cell even when a status tag (Q/O/NA/...) is shown: try the
  // team/pos span, then the cell's text nodes with the tag and name removed, then the whole row.
  function teamPos(tr, a, tag) {
    const strip = (s) => {
      s = s.split(a.textContent.trim()).join(" ");
      return tag ? s.replace(new RegExp(`(^|\\s)${tag.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=\\s|$)`, "g"), " ") : s;
    };
    const sub = ((a.parentElement.querySelector("span.Fz-xxs, .ysf-player-team-pos") || {}).textContent || "").trim();
    const td = a.closest("td") || a.parentElement;
    for (const s of [sub, strip(cellText(a.parentElement)), strip(cellText(td)), strip(cellText(td).replace(/\s+/g, "")),
                     strip(cellText(tr))]) {
      const m = s.match(TEAM_POS);
      if (m) return { team: m[1].toUpperCase(), positions: m[2].replace(/\s+/g, "") };
    }
    return { team: null, positions: null };
  }
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
    const tp = teamPos(tr, a, tag);
    const pid = (a.getAttribute("href") || "").match(/players\/(\d+)/);
    return {
      slot, name: a.textContent.trim(), player_id: null,
      team: tp.team,
      positions: tp.positions,
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
