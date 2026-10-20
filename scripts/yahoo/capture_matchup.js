// Read-only capture of my H2H matchup score in Yahoo league 44495 (one GET with the logged-in session).
// Paste into the browser JS tool while on https://basketball.fantasysports.yahoo.com/nba/44495 ;
// it returns the capture JSON as a string. Save it, then:
//   fantasy league score --capture <file> --json            (validate + score; dry run)
//   fantasy league score --capture <file> --save --json     (model_runs kind matchup_score; dashboard + lineup plan)
// Edit WEEK to read a past week (null = the week Yahoo shows by default, i.e. the current one).
// captured_at = ISO 8601 in UTC with the "Z" offset (new Date().toISOString()).
// Format: {source, captured_at, url, week, teams: [{yahoo_team_id, name, totals: {fgm, fga, fg_pct, ftm, fta,
//          ft_pct, fg3m, pts, reb, ast, stl, blk, tov}, cats_won, games_played, games_remaining}],
//          problems: [...], diagnostics: {...}}
// Parsing (mirrored and unit-tested in Python: fantasy.league.matchup.parse_matchup_html): the score table is the
// first <table> whose header row names >= 6 of the category columns and whose body has >= 2 rows linking to a
// team page /nba/44495/<team id>. Columns are mapped by header label (Yahoo marks display-only stats with "*",
// e.g. "FGM/A*"), never by position. A field that cannot be found stays null and is listed in `problems`.
await (async () => {
  const LEAGUE = 44495, MY_TEAM = 11;
  const WEEK = null;  // e.g. 3 for week 3
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function get(url) {
    let last;
    for (let k = 0; k < 3; k++) {
      try {
        const r = await fetch(url, { credentials: "include", method: "GET", cache: "no-store" });
        if (r.ok) return await r.text();
        last = new Error(`HTTP ${r.status} ${url}`);
      } catch (e) { last = e; }
      await sleep(1500);
    }
    throw last;
  }
  const norm = (s) => (s || "").replace(/\*/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  // header label -> field (keep in sync with HEADER_ALIASES in src/fantasy/league/matchup.py)
  const COLS = [
    [/^fgm ?\/ ?a$/, "fgm_a"], [/^fg ?%$/, "fg_pct"], [/^ftm ?\/ ?a$/, "ftm_a"], [/^ft ?%$/, "ft_pct"],
    [/^(3ptm|3pm|3-ptm|3pt|3ptm?\.)$/, "fg3m"], [/^pts$/, "pts"], [/^reb$/, "reb"], [/^ast$/, "ast"],
    [/^(st|stl)$/, "stl"], [/^blk$/, "blk"], [/^(to|tov)$/, "tov"], [/^(score|cats|cat)$/, "cats_won"],
    [/^(gp|games played)$/, "games_played"],
    [/^(rem|remaining|games left|gl|games remaining|remaining games)$/, "games_remaining"],
  ];
  const CATS = new Set(["fgm_a", "fg_pct", "ftm_a", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]);
  const field = (label) => { const n = norm(label); const hit = COLS.find(([re]) => re.test(n)); return hit ? hit[1] : null; };
  const num = (s) => { const t = (s || "").replace(/,/g, "").trim(); return /^-?\d*\.?\d+$/.test(t) ? Number(t) : null; };
  const pct = (s) => { const v = num(s); return v === null ? null : (v > 1 ? v / 100 : v); };
  const pair = (s) => { const m = (s || "").replace(/,/g, "").match(/(\d+)\s*\/\s*(\d+)/); return m ? [Number(m[1]), Number(m[2])] : [null, null]; };
  const teamRe = new RegExp(`/nba/${LEAGUE}/(\\d+)(?:[/?#]|$)`);
  const cellText = (el) => (el.getAttribute("title") && !el.textContent.trim() ? el.getAttribute("title") : el.textContent).replace(/\s+/g, " ").trim();

  const url = `/nba/${LEAGUE}/matchup?${WEEK ? `week=${WEEK}&` : ""}mid1=${MY_TEAM}`;
  const html = await get(url);
  const d = new DOMParser().parseFromString(html, "text/html");
  const problems = [], seenHeaders = [];
  let best = null;
  for (const table of d.querySelectorAll("table")) {
    const rows = [...table.querySelectorAll("tr")];
    const hdr = rows.find((tr) => tr.querySelector("th") && [...tr.children].filter((c) => CATS.has(field(cellText(c)))).length >= 6);
    if (!hdr) continue;
    const labels = [...hdr.children].map(cellText);
    seenHeaders.push(labels);
    const teamRows = rows.filter((tr) => tr !== hdr && [...tr.querySelectorAll("a[href]")].some((a) => teamRe.test(a.getAttribute("href"))));
    if (teamRows.length >= 2) { best = { labels, teamRows }; break; }
  }
  const teams = [];
  if (!best) {
    problems.push("no score table found (header with >= 6 category columns and >= 2 team rows): logged out or layout change?");
  } else {
    const fields = best.labels.map(field);
    for (const f of [...CATS, "cats_won"]) if (!fields.includes(f)) problems.push(`column for ${f} not found in header ${JSON.stringify(best.labels)}`);
    for (const tr of best.teamRows.slice(0, 2)) {
      const cells = [...tr.children];
      const off = fields.length - cells.length;  // a row shorter than the header is aligned from the right
      if (off) problems.push(`row has ${cells.length} cells, header ${fields.length}: aligned from the right`);
      const a = [...tr.querySelectorAll("a[href]")].find((x) => teamRe.test(x.getAttribute("href")));
      const t = { yahoo_team_id: Number(a.getAttribute("href").match(teamRe)[1]), name: a.textContent.trim(),
                  totals: { fgm: null, fga: null, fg_pct: null, ftm: null, fta: null, ft_pct: null, fg3m: null,
                            pts: null, reb: null, ast: null, stl: null, blk: null, tov: null },
                  cats_won: null, games_played: null, games_remaining: null };
      cells.forEach((c, i) => {
        const f = fields[i + off], s = cellText(c);
        if (!f) return;
        if (f === "fgm_a") [t.totals.fgm, t.totals.fga] = pair(s);
        else if (f === "ftm_a") [t.totals.ftm, t.totals.fta] = pair(s);
        else if (f === "fg_pct" || f === "ft_pct") t.totals[f] = pct(s);
        else if (f === "cats_won" || f === "games_played" || f === "games_remaining") t[f] = num(s);
        else t.totals[f] = num(s);
      });
      for (const [k, v] of Object.entries(t.totals)) if (v === null && !k.endsWith("_pct")) problems.push(`team ${t.yahoo_team_id}: ${k} not parsed`);
      teams.push(t);
    }
    if (!teams.some((t) => t.yahoo_team_id === MY_TEAM)) problems.push(`my team ${MY_TEAM} not among the score rows`);
  }
  // week: the selected week option, else "Week N" in the page heading/title
  let week = WEEK;
  if (!week) {
    const opt = d.querySelector("select[name='week'] option[selected], select#week option[selected]");
    const m = ((opt && opt.textContent) || "").match(/(\d+)/) || (d.title + " " + [...d.querySelectorAll("h1,h2,h3,h4,span,div.Fw-b")]
      .slice(0, 400).map((e) => e.childElementCount ? "" : e.textContent).join(" ")).match(/Week\s+(\d+)/i);
    week = m ? Number(m[1]) : null;
    if (!week) problems.push("week not found on the page (pass --week to fantasy league score)");
  }
  // games played / remaining are not in the score table on every layout: keep short text hints for the next fix
  const hints = [...d.querySelectorAll("body *")].filter((e) => !e.childElementCount && /(remaining|games left|games played)/i.test(e.textContent))
    .slice(0, 12).map((e) => (e.parentElement ? e.parentElement.textContent : e.textContent).replace(/\s+/g, " ").trim().slice(0, 160));
  if (teams.length && teams.every((t) => t.games_remaining === null)) problems.push("games_remaining not in the score table (see diagnostics.remaining_hints)");
  return JSON.stringify({
    source: `Yahoo league ${LEAGUE} matchup page read in the user's logged-in browser (scripts/yahoo/capture_matchup.js, read-only)`,
    captured_at: new Date().toISOString(),
    url,
    week,
    teams,
    problems,
    diagnostics: { score_table_headers: seenHeaders.slice(0, 4), remaining_hints: hints },
  });
})()
