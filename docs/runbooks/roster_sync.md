# Roster sync (Yahoo -> league state)

Keeps `data/manual/league_rosters_2026-27.json` (the `--league-file` of `fantasy sim ...`) equal to
Yahoo: all 12 rosters, lineup slots and injury tags. Read-only on Yahoo. Code: `src/fantasy/league/sync.py`,
CLI `src/fantasy/league/sync_cli.py` (`fantasy league ...`), tests `tests/test_league_sync.py`.

## 1. Capture (local session with browser tools)

Follow the `yahoo-browser` skill (hold the `yahoo:browser` lock, log events). On
`https://basketball.fantasysports.yahoo.com/nba/44495`, paste into the browser's JS tool:

| Script | Reads | Returns |
|---|---|---|
| `scripts/yahoo/capture_rosters.js` | `/nba/44495/<1..12>` (GET) | `{source, captured_at, problems, teams: {<yahoo team id>: {name, draft_slot: null, players: [{slot, name, team, positions, status, yahoo_player_id}]}}}` |
| `scripts/yahoo/capture_free_agents.js` | `/nba/44495/players?status=A&pos=P&sort=OR&sdir=1&count=<offset>`, 25/page, top 150 (edit `N`) | `{..., players: [{name, team, positions, status, yahoo_player_id, ownership, waiver_until, list_rank, yahoo_rank, pct_rostered}]}` |

`captured_at` is ISO 8601 UTC (`...Z`). Team and Yahoo positions are parsed from the player cell
even for status-tagged rows (the tag text is stripped; team codes are matched against Yahoo's 30 codes,
so a tag glued to the code, "QGSW - PG,SG", still parses).
`ownership` is the cell text: `FA` = free agent, `W (Oct 22)` = on waivers until that date.
Check `problems` is empty (non-empty usually means logged out or a Yahoo layout change). Save the
returned string to a file (e.g. in the scratchpad), then normalise it:

```bash
fantasy league import-capture --raw <saved>.json --kind rosters       # -> data/raw/yahoo/league_rosters_<date>.json
fantasy league import-capture --raw <saved>.json --kind free_agents   # -> data/raw/yahoo/free_agents_<date>.json
```

`import-capture` resolves our `player_id`s, fills team/positions where Yahoo shows a status tag
instead, maps team codes (CHA->CHO, PHX->PHO, BKN->BRK), fills `draft_slot`, and lists unresolved
players. It accepts the raw JSON or the JSON string the JS tool hands back. It records
`capture_date` = the US/Eastern date of `captured_at` (the lineup tool refuses stale captures by it;
null if the capture has no `captured_at`) and names the output file by that date.

## 2. Sync

```bash
fantasy league sync --capture data/raw/yahoo/league_rosters_<date>.json --json          # dry run: diff only
fantasy league sync --capture data/raw/yahoo/league_rosters_<date>.json --write --json  # save
```

- Team id in the state = **draft slot** (my team = 1). Yahoo team id -> slot: the capture's
  `draft_slot`, else the previous state's `yahoo_team_id`, else `--slot-map` (default: newest
  `data/raw/yahoo/league_schedule_*.json`).
- Player ids: capture `player_id` if it is a projection id -> previous state's Yahoo-id map -> name
  (`fantasy.draft.names.resolve_player`: diacritics, Cyrillic ё, aliases, Jr./III, "N. Alexander-Walker")
  -> fuzzy match only if the NBA team agrees (`fuzzy_matches`). Otherwise `unknown:<slug>`
  (`unknown_players` = not in projections, simulated at replacement level; `unresolved` = ambiguous
  between projection players — fix by adding an alias in `fantasy.data.ids.ALIASES`). Nobody is dropped.
- `positions_from_projections` (warning): players whose Yahoo positions were missing in the capture
  and were filled from OUR projections (can differ from Yahoo's eligibility; re-capture with the
  current script). Captures from before 2026-10-20's script fix list every status-tagged player here.
- Diff (`diff`): `roster_changed`, `status_changed`, `moves` (player changed fantasy team = trade),
  per team `adds`/`drops` (free agency only), `moved_in`/`moved_out`, `status_changes`, `renamed`;
  `my_team` adds `status_now`, `lineup_changes`, `out_in_starting_slot` (tagged O/INJ/SUSP/NA in a
  starting slot). If the previous state had no status maps, `status_baseline` is false and tags are
  listed under `status_now` instead of as changes.
- Exit codes: 0 ok; 2 invalid capture/state (`{"ok": false, "code": "invalid_capture"|"usage", ...}`):
  not 12 teams, a roster > 16, unknown slot, a Yahoo/our player id on two rosters, unmapped team.

## 3. Matchup score (banked totals this week)

Same session and lock as section 1. On `https://basketball.fantasysports.yahoo.com/nba/44495`, run
`scripts/yahoo/capture_matchup.js` in the browser JS tool (one GET of `/nba/44495/matchup?mid1=11`; set `WEEK` in
the script for a past week). It returns `{source, captured_at, url, week, teams: [{yahoo_team_id, name, totals:
{fgm, fga, fg_pct, ftm, fta, ft_pct, fg3m, pts, reb, ast, stl, blk, tov}, cats_won, games_played, games_remaining}],
problems, diagnostics}`. Save the returned string as `data/raw/yahoo/matchup_<YYYY-MM-DD>T<HHMM>Z.json` (several
reads a day are normal), then:

```bash
fantasy league score --capture data/raw/yahoo/matchup_<stamp>.json --json          # validate + score (dry run)
fantasy league score --capture data/raw/yahoo/matchup_<stamp>.json --save --json   # model_runs kind matchup_score
#   cloud session: add --rest. Saved HTML instead of the JS: --html page.html --captured-at <ISO time of the read>
```

- **When:** in the morning ET, before the day's lineup plan (`fantasy lineup plan` then picks the newest saved score
  for the week automatically; `accumulated_source` in its output names the row). A read after 12:00 ET on the plan day
  may already include that day's games (the plan warns about double counting); a read from an earlier day misses
  the games since (the plan warns; re-capture).
- **Validation** (exit 2, `code: "invalid_capture"`): exactly two teams; every raw total present and >= 0;
  FGM <= FGA, FTM <= FTA, 3PM <= FGM; my team (Yahoo id -> draft slot via the league state's `yahoo_team_id`, else
  the newest `league_schedule_*.json` capture / `--slot-map`) is one of them; a week (capture, `--week`, or the
  schedule week of the observed ET date). Warnings (row still written): every capture `problems` entry, displayed
  FG%/FT% that disagree with makes/attempts by more than rounding (makes/attempts are used), PTS != 2 FGM + 3PM + FTM,
  Yahoo's "Score" != the computed category score, the page's opponent != the schedule's.
- **`problems` non-empty** after a Yahoo layout change: check `diagnostics.score_table_headers` (the header labels
  the script saw) and extend `COLS` in the script and `HEADER_ALIASES` in `src/fantasy/league/matchup.py` together
  (the Python twin is the tested one: `tests/test_matchup.py`, fixture `tests/fixtures/yahoo/matchup_week1.html`).
  Real layout (verified 2026-10-20; fixtures `matchup_real_pregame.html` / `matchup_real_midweek.html`): before
  any game every cell is "-" / "-/-" = 0 (FG%/FT% null, no problem); the blank-header last column is the categories
  won; games played / remaining come from the strip above the table ("30 Remaining | 0/30 Games Played 0/26 |
  Remaining 26", left = my team = first row; `diagnostics.games_strip` shows the matched text, a remaining that
  is not total - played is a problem). The week is usually not on the page section the parser reads: pass `--week`
  (or let the observed ET date pick it from the schedule) when `problems` says "week not found".
- Idempotent: the same week, ET date and totals is one row (`ms-w<week>-<date>-<sha12>`); a changed score is a new
  row. Schema: `docs/research/dashboard_data.md` section 7.

## Files that change

| File | When |
|---|---|
| `data/raw/yahoo/league_rosters_<date>.json`, `free_agents_<date>.json` | `import-capture` |
| `data/raw/yahoo/matchup_<date>T<HHMM>Z.json` | saved by hand from `capture_matchup.js` (section 3) |
| `data/manual/league_rosters_2026-27.json` | `sync --write` (per team also `yahoo_team_id`, `lineup` {id: slot}, `status` {id: tag}, `yahoo_ids`; `fantasy.sim.league.from_json` ignores these extra keys) |
| `data/manual/league_rosters_2026-27.prev.json` | `sync --write`: the replaced state (one generation; git keeps the history) |

Commit the raw capture and the new state together; then re-run `fantasy sim season --league-file
data/manual/league_rosters_2026-27.json` if rosters changed.
