# Handoff: data refresh v2 (2026-10-18) — ADP, positions, injuries from direct sources

## Summary
Network policy now allows the sports hosts. Yahoo ADP and Yahoo position eligibility come from
**Yahoo's public draft-analysis API** (no login), and injuries from the **ESPN injuries API**,
cross-checked against CBS. Coverage: **ADP 50 → 293 of 456** projected players (184 with a real
Yahoo average pick, the rest marked "undrafted"). Positions: **Yahoo eligibility for the top 250**.
The Basketball-Reference GitHub mirror matches bbref.com exactly, so the stats were not changed.

## Reachability (2026-10-18 ~14:08 UTC, `curl -sS -m 20 -A 'Mozilla/5.0'`)
| URL | HTTP | Notes |
|---|---|---|
| www.basketball-reference.com/leagues/NBA_2026_per_game.html | 200 | |
| stats.nba.com/stats/leaguedashplayerstats (nba_api headers) | timeout (000) after 30 s | tunnel opens, origin never answers (stats.nba.com is known to drop datacenter IPs) |
| cdn.nba.com/static/json/staticData/scheduleLeagueV2.json | 403 | 403 comes from the **origin** (Akamai); CONNECT itself succeeds |
| basketball.fantasysports.yahoo.com/nba/draftanalysis | 200 | page is rendered by JS; data comes from the API below |
| pub-api-rw.fantasysports.yahoo.com/fantasy/v2/league/478.l.public/players;…;sort=DA_AP;out=draft_analysis?format=json | 200 | **used**: ADP, % drafted, eligible positions, injury status |
| fantasysports.yahooapis.com/fantasy/v2/game/nba | 401 | reachable; needs OAuth |
| www.fantasypros.com/nba/adp/overall.php | 200 | not needed (Yahoo is the primary source) |
| www.rotowire.com/basketball/injury-report.php (+ tables/ JSON) | 200 | JSON gives only 5 rows to non-subscribers, so not used |
| www.cbssports.com/nba/injuries/ | 200 | **used** as a cross-check (90 rows) |
| www.espn.com/nba/injuries | 202, empty body | site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries → 200, **used** (90 rows) |

## What changed
1. **ADP** (`data/manual/adp_yahoo_2026_27.csv`, 300 rows): new command `fantasy data fetch-yahoo
   --apply` (`src/fantasy/data/yahoo_public.py`). Raw API pages are cached in
   `data/raw/yahoo_public/draft_analysis_478.json` with provenance (Yahoo game id 478 = NBA
   2026-27). The file keeps `name,adp,source,as_of` and adds `yahoo_adp,pct_drafted`.
   - **Statistical fix:** Yahoo's average pick only counts drafts in which the player was taken.
     Many fringe players have ADP around 105–123 but are drafted in only 2–5% of leagues (e.g.
     Bronny James: 106.9 at 3%). Feeding those raw numbers to the Normal(adp, sd) availability
     model would wrongly predict they get taken around pick 110. `adp` is now the
     unconditional mean: `pct*yahoo_adp + (1-pct)*168`, where 168 is one round past a 12×13
     draft. Yahoo-listed players with 0% drafted get 168. The two-point mixture is approximated by
     a single Normal; over picks 100–156 the error is small, about 0.1 in availability probability.
   - The 50 old WebSearch values vs Yahoo: median |Δ| 1.6 picks, mean 2.8. The large gaps were
     Kon Knueppel (40.4 → 53.1), Dyson Daniels (59 → 66), Castle (60 → 55), Ja Morant
     (84 → 89), and Ajay Mitchell (118 → 161 effective, because he is drafted in only 14% of
     leagues).
   - Yahoo names with no projection row (fringe, ignored): Bronny James, Aday Mara, Ben Simmons,
     Morez Johnson Jr., Allen Graves, Terrence Shannon Jr., Russell Westbrook (we list him as
     retired; Yahoo shows "Not Active"), Jimmy Butler III / Mark Williams / Donte DiVincenzo
     (these do match after the alias fixes, but long injuries had cut them before the earlier
     override fix). New aliases: `bobby-portis-jr`, `robert-williams-iii`; `slugify` now
     transliterates Cyrillic ё/е (bbref spells it "Egor Dёmin").
   - Players in our value top 150 that Yahoo drafts essentially never (possible late-round
     value): Santi Aldama, Royce O'Neale, Bilal Coulibaly, Tre Jones, Bub Carrington,
     Anfernee Simons, Malik Monk, Keldon Johnson.
2. **Positions** (`data/manual/positions_overrides.csv`): Yahoo `eligible_positions` for the top
   250 in Yahoo ADP order (column `source`). All 34 old hand-set rows were inside that set and
   were replaced. Notable corrections: Wembanyama is **C only** (we had PF|C), SGA/Maxey/LaMelo/
   Murray/Kyrie/Fox are **PG only**, Barnes gains C, Siakam is PF|C, Edwards is PG|SG, and
   Kawhi/Durant/Jaylen Brown gain SG or PF. 69 players' eligibility changed in the processed
   file.
3. **Injuries** (`data/manual/player_overrides_2026_27.csv`): new command `fantasy data
   fetch-injuries` (`src/fantasy/data/injuries.py`) caches `data/raw/espn/injuries.json` and
   `data/raw/cbs/injuries.html`. Expected games missed is derived from ESPN's estimated return date
   (82 games spread evenly from 2026-10-20 to 2027-04-11). Per-player decisions are recorded in
   `docs/research/injury_refresh_2026-10-18.py` (`DECIDE`). The rule: when the report gives an
   explicit timeline, use it; when ESPN's date is only a generic "not ready for start"
   placeholder, take the mean of the ESPN-implied and the prior estimate. Each row's source
   column names ESPN (update date, estimated return), whether CBS lists the player, and the
   prior source.
   - **Games missed changed:** Butler 52→49, Mark Williams 45→50, Sharpe 50→60, Jordan Miller 41→63,
     Moody 40→34, Porziņģis 25→17, Ingram 25→17, Beal 15→12, Strawther 25→22, Coby White 6→3,
     Knueppel 2→1, Claxton 2→1, Isaiah Stewart gtd 3 → out 2.
   - **Newly out:** Maxime Raynaud 7, Johnny Furphy 35, Noah Penda 14, Konan Niederhauser 9,
     Cardwell 6, Vukcevic 6, Jamir Watkins 78.
   - **Newly day-to-day for the opener** (status `gtd`, 0 games missed, informational only):
     Bam Adebayo, Chet Holmgren, Franz Wagner, Dejounte Murray, AJ Dybantsa, Josh Hart,
     Jrue Holiday, Anthony Black, Cason Wallace, Collin Sexton, Lively, plus about 30 fringe
     players.
   - Kept: DiVincenzo 65 (ESPN's Apr 2 is a placeholder; the player expects to return this
     season), Nurkić 8, Strus 12. No player we mark out/gtd is missing from ESPN's list.
     Haliburton, Lillard, Kyrie and VanVleet are not on the injury report, which agrees with
     our "healthy".
4. **Teams:** Yahoo vs our teams for 293 matched players: only 6 differed, all fringe. Set
   from Yahoo: Kelly Oubre Jr. IND (destination was unknown before), Dean Wade PHI (resolves the
   CLE/PHI conflict), Bruce Brown NYK, John Konchar NYK, Ziaire Williams LAL, Brandon Williams
   GSW.
5. **Stats:** a direct bbref `NBA_2026_totals.html` fetch compared with the mirror for the top
   200 by ADP (187 have 2025-26 seasons; the other 13 are rookies or missed the whole season).
   G, MP, PTS, TRB, AST, STL, BLK, TOV, 3P, FGA and FTA are all **identical**, and 582 of 583
   players overlap. There is no reason to switch, so the page was not committed (bbref ToS).

## Validation
`fantasy data build-projections` → 456 rows. `fantasy data top --n 40` and `fantasy values
build --no-save --top 40` look sane (Jokić, SGA, Wemby, Luka, Maxey on top).
`fantasy draft recommend` on a mock draft works. `pytest` passes (new: `tests/test_yahoo_public.py`,
`tests/test_injuries.py`), and `ruff check src tests` is clean.

## Caveats
- Yahoo public ADP comes from public and mock leagues that lean on Yahoo's default rankings and
  autodraft. It is the best available proxy for a Yahoo room, but humans in league 44495 can
  deviate from it. `pct_drafted` is computed over all those leagues.
- The 168 "undrafted" slot and the Normal approximation are priors. Calibrate them with real
  draft results (T-016).
- ESPN return dates are estimates. GTD players carry no games-missed penalty.
- Yahoo eligibility can change in-season. Re-run `fantasy data fetch-yahoo --apply` on draft day
  (it overwrites both files; positions for players outside the top 250 keep any `manual` rows).

## Next steps
1. Draft day (2026-10-19, before 22:00 ET): `fantasy data fetch-yahoo --apply && fantasy data
   fetch-injuries`, review injury diffs (edit overrides by hand), then `fantasy data
   build-projections`.
2. Still needed locally (browser): verify league settings and draft order (T-001/T-015) and load
   Pre-Draft Rankings (T-014).
3. Optional: blend an external projection set (FantasyPros/Hashtag are reachable now).
