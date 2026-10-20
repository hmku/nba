// Read-only capture of the top-N available players (free agents + waivers) of Yahoo league 44495,
// sorted by Yahoo's overall rank (sort=OR), 25 per page. Paste into the browser JS tool while on
// https://basketball.fantasysports.yahoo.com/nba/44495 ; it returns the capture JSON as a string. Then:
//   fantasy league import-capture --raw <file> --kind free_agents
// Each player: {name, team, positions, status, yahoo_player_id, ownership, waiver_until, list_rank,
//               yahoo_rank, pct_rostered, player_id: null}
// ownership = the cell text as shown: "FA" = free agent, "W (Oct 22)" = on waivers until that date.
await (async () => {
  const LEAGUE = 44495, N = 150, PAGE = 25;
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
  const OWN = /^(FA|W\s*\([^)]*\))$/;
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
  // best-effort column lookup by header text (Yahoo's two-row header; may shift if the layout changes)
  function headerIndex(table, re) {
    const rows = table.querySelectorAll("thead tr");
    if (!rows.length) return -1;
    let i = 0;
    for (const th of rows[rows.length - 1].children) {
      if (re.test(th.textContent.trim())) return i;
      i += Number(th.getAttribute("colspan") || 1);
    }
    return -1;
  }
  const players = [], problems = [];
  for (let off = 0; off < N; off += PAGE) {
    const html = await get(`/nba/${LEAGUE}/players?status=A&pos=P&sort=OR&sdir=1&count=${off}`);
    const d = new DOMParser().parseFromString(html, "text/html");
    const table = [...d.querySelectorAll("table")].find((t) => t.querySelector("a.name, a[class*='name']"));
    if (!table) { problems.push(`offset ${off}: no player table`); break; }
    const iRank = headerIndex(table, /^(Rank|O-Rank|Current)$/i);
    const iRos = headerIndex(table, /^%\s*Ros/i);
    let k = 0;
    for (const tr of table.querySelectorAll("tbody tr")) {
      const a = tr.querySelector("a.name, a[class*='name']");
      if (!a) continue;
      const tds = [...tr.querySelectorAll("td")];
      const tag = (tr.querySelector(".ysf-player-status, abbr[title], span.F-injury")?.textContent || "").trim() || null;
      const tp = teamPos(tr, a, tag);
      const own = tds.map((td) => td.textContent.replace(/\s+/g, " ").trim()).find((t) => OWN.test(t)) || null;
      const wv = own && own.match(/^W\s*\(([^)]*)\)$/);
      const pid = (a.getAttribute("href") || "").match(/players\/(\d+)/);
      const num = (i) => (i >= 0 && tds[i] ? parseFloat(tds[i].textContent.replace(/[^0-9.]/g, "")) : NaN);
      players.push({
        name: a.textContent.trim(),
        team: tp.team,
        positions: tp.positions,
        status: tag,
        yahoo_player_id: pid ? Number(pid[1]) : null,
        ownership: own,
        waiver_until: wv ? wv[1] : null,
        list_rank: off + (++k),
        yahoo_rank: Number.isFinite(num(iRank)) ? num(iRank) : null,
        pct_rostered: Number.isFinite(num(iRos)) ? num(iRos) : null,
        player_id: null,
      });
    }
    if (!k) { problems.push(`offset ${off}: no rows`); break; }
    await sleep(300);
  }
  return JSON.stringify({
    source: `Yahoo league ${LEAGUE} players page read in the user's logged-in browser (scripts/yahoo/capture_free_agents.js, read-only)`,
    captured_at: new Date().toISOString(),
    pages: [`/nba/${LEAGUE}/players?status=A&pos=P&sort=OR&sdir=1&count=<offset> (top ${N} available, ${PAGE}/page)`],
    notes: "list_rank = order in Yahoo's OR sort; yahoo_rank/pct_rostered best-effort from header columns (null if not found); player_id filled by `fantasy league import-capture`",
    problems,
    players,
  });
})()
