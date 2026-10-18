# Yahoo Fantasy Sports API v2 — what it can do for NBA (2026-27)

Researched 2026-10-18 for league 44495 ("Thomas's Tip-Top League"). Client: `src/fantasy/league/yahoo_api.py`.

**Verification caveat.** Every `*.yahoo.com` / `*.yahooapis.com` host was unreachable from this
cloud session (container egress proxy returns 403, and the WebFetch tool got DNS `ENOTFOUND` for
developer.yahoo.com, help.yahoo.com, sports.yahoo.com and basketball.fantasysports.yahoo.com). The
official guide (developer.yahoo.com/fantasysports/guide) could not be read directly. Facts below come
from: the source code of the maintained client libraries (downloaded from PyPI: `yahoo_fantasy_api`
2.12.3, `yfpy` 17.0.0, `yahoo_oauth` 2.1.1), the recorded Yahoo responses in
`spilchen/yahoo_fantasy_api` tests, search-engine snippets of the Yahoo docs/help pages, and prior
knowledge. Items marked **(unverified)** need a live check on the user's machine.

## 0. Access policy changed in 2025-2026: self-serve app creation no longer works (updated 2026-10-18)

The old "create an app at developer.yahoo.com/apps/create, tick Fantasy Sports" flow (still in the yfpy
README and most blog posts) is **obsolete**:

* **Oct 2025**: new apps can only pick Fantasy Sports **Read**, not Read/Write ([yfpy #79](https://github.com/uberfastman/yfpy/issues/79)).
* **Jun 2026**: a commenter on #79 reports the `oob` redirect is also gone for new apps (legacy apps still worked). Unconfirmed by Yahoo, so for a new app assume `oob` fails.
* **Jul 2026**: Yahoo started returning `403 "This application is not authorized to perform this action"` on **every**
  fantasy endpoint for unapproved and legacy apps. Token issue and refresh still succeed, so the failure shows up only on API calls.
  The create-app form **no longer offers a Fantasy Sports permission**. Yahoo's guide now redirects to an
  application program ([yfpy #84](https://github.com/uberfastman/yfpy/issues/84), [#85](https://github.com/uberfastman/yfpy/issues/85)).
* Application form: https://sports.yahoo.com/developer/access/ (read 2026-10-18). It says: *"The Yahoo
  Fantasy Sports API currently provides read access only. Write access is not available at this time."*
  Each application is reviewed by hand, and vague ones are closed without a reply. You must describe the product, the data
  you need and the user base, **"including where access is limited to personal or single league use"**, so
  personal use is an accepted category. Fields include Expected Users (pick Small, < 1,000) and an optional Client ID
  ("New users without a YDN account can leave this blank, access will be provisioned after approval").
* What applicants reported in #84: about 1–8 weeks from applying to working access (one person applied Jul 31 and was working Sep 26). Steps were:
  approval email → DocuSign "API Access and Use Agreement" → a "Developer Application Confirmation Form"
  asking for Client ID + App ID. **Apps created before Fantasy access was enabled on the account could not
  be fixed.** Applicants had to create a **new** app after enablement and submit its Client ID. The "access is live" emails
  sometimes arrived before calls worked, so check with a real call (e.g. `game/nba`). A personal single-league
  applicant was approved through the same process; there is no separate hobbyist track.
* Consequences for us: **no write ops via the API at all** (lineups/adds/drops/trades must go through the
  browser: see the `yahoo-browser` skill). API access cannot be counted on for the 2026-10-19 draft.

**Steps for the user now:** (1) Apply at sports.yahoo.com/developer/access with your Yahoo account. Product:
"personal analysis tool for my own single league (44495), read-only". Data: league settings, rosters,
players, stats, transactions, matchups. Users: Small / 1. Leave Client ID blank unless you already have a YDN app. (2) Wait
for approval, sign the DocuSign agreement, and only **then** create a new app at developer.yahoo.com/apps/create
(Confidential/"Web Application" type, Redirect URI `https://localhost:8080`, Fantasy Sports Read if offered).
Submit its Client ID on the confirmation form. (3) Run
`python -m fantasy.league.yahoo_auth --redirect-uri https://localhost:8080` and paste the full
dead-page URL. The helper takes `code=` from it. (4) If every call still returns 403, the backend has not caught up yet. Wait, or reply
to the approval email.

## 1. Auth: OAuth 2.0 (authorization-code + refresh token)

| Item | Value |
|---|---|
| App registration | Apply first at https://sports.yahoo.com/developer/access/ (§0). After approval, create a new app at https://developer.yahoo.com/apps/create/ (signed in as the league member) |
| Application type | Confidential client ("Web Application"), which is what working 2026 reports used. "Installed Application" was the old default |
| Redirect URI field | required, must be **https**; register `https://localhost:8080` (our helper's default since 2026-10-18; pass `--redirect-uri oob` only for a legacy oob app) |
| Permissions | **Fantasy Sports → Read** (Read/Write is no longer granted; see §0) |
| Authorize URL | `https://api.login.yahoo.com/oauth2/request_auth?client_id=…&redirect_uri=https://localhost:8080&response_type=code&language=en-us` |
| Token URL | `POST https://api.login.yahoo.com/oauth2/get_token`, HTTP Basic `client_id:client_secret`, form `grant_type=authorization_code&code=…&redirect_uri=https://localhost:8080` |
| Refresh | same URL, `grant_type=refresh_token&refresh_token=…&redirect_uri=https://localhost:8080` |
| Access token lifetime | `expires_in: 3600` (1 h) |
| Refresh token | long-lived (no documented expiry; invalidated if the user revokes the app or changes password). Yahoo may return a new one on refresh — persist it. |
| `oob` | Used by `yahoo_oauth` 2.1.1 (Dec 2024) and yfpy 17 (Sep 2025), and still works for legacy apps. It is reportedly removed for new apps (Jun 2026, yfpy #79). For a new app, use `https://localhost:8080` and copy `code=` from the (dead) redirected URL. Our helper accepts the full URL. The `redirect_uri` in the token/refresh calls must match the one used to authorize. |

Our implementation: `fantasy settings yahoo-auth` (lead wires `fantasy.league.yahoo_auth.cli_command`;
also `python -m fantasy.league.yahoo_auth`) → `~/.config/fantasy/yahoo_oauth.json` (0600). Env vars
`YAHOO_CLIENT_ID` / `YAHOO_CLIENT_SECRET` / `YAHOO_REFRESH_TOKEN` override the file; `YAHOO_OAUTH_FILE`
overrides the path. Tokens are never logged or printed. **Run auth locally** (needs a browser; cloud
sessions cannot reach Yahoo at all).

## 2. Keys

* Base URL `https://fantasysports.yahooapis.com/fantasy/v2/`, append `?format=json` (default is XML).
* **game_key**: numeric id per sport-season. Known NBA keys: 2022-23 = **418** (recorded sample
  `league_teams.json` is `418.l.15944` on basketball.fantasysports), 2023-24 = **428** (yfpy
  quickstart comment "NBA - 2023"); 2024-25 = 454 and 2025-26 = 466 from memory **(unverified)**.
  **2026-27 key: not found in any reachable source.** Resolve at runtime: `GET game/nba` returns the
  *current season's* game (`game_key`, `season`) — `YahooClient.resolve_league_key(44495)` does this.
  Alternative: `games;game_codes=nba;seasons=2026`.
* **league_key** = `<game_key>.l.<league_id>` → `<game_key>.l.44495`. Game code may substitute for the
  game key in `game/` resources; for league keys always use the numeric form.
* **team_key** = `<league_key>.t.<team_id>`; **player_key** = `<game_key>.p.<player_id>`.

## 3. Read endpoints we use (all GET, implemented)

| Need | Path | Notes |
|---|---|---|
| Current NBA game | `game/nba` | `game_key`, `season`, `is_live_draft_lobby_active` |
| League settings | `league/{lk}/settings` | `draft_type` (live/self/autopick), `is_auction_draft`, `draft_pick_time`, `draft_time`, `roster_positions[]` (`position`,`count`), `stat_categories.stats[]` (`stat_id`, `display_name`, `sort_order` 0 = lower better, `is_only_display_stat` for FGM/A, FTM/A), `max_weekly_adds` (absent = unlimited), `waiver_type` (`R` = rolling list, `FR` = FAAB+rolling), `waiver_time` (days), `waiver_rule`, `uses_faab`, `playoff_start_week`, `num_playoff_teams`, `trade_end_date`, `trade_ratify_type`; league meta has `num_teams`, `end_week`, `draft_status` (`predraft`/`draft`/`postdraft`) |
| Draft results | `league/{lk}/draftresults` | `draft_result{pick, round, team_key, player_key[, cost]}` |
| Teams | `league/{lk}/teams` | `is_owned_by_current_login` identifies the user's team; `draft_position`, `waiver_priority`, managers |
| User's teams | `users;use_login=1/games;game_keys=nba/teams` | not wrapped yet |
| Roster | `team/{tk}/roster;date=YYYY-MM-DD` | per-player `selected_position`, `eligible_positions`, `status` (INJ/O/GTD/DTD), `is_editable` |
| Players | `league/{lk}/players;start=N;count≤25;status=A|FA|W|T;position=PG;sort=OR|AR|NAME|<stat_id>;search=…;out=draft_analysis,percent_owned,ownership` | 25 max per page (paginate with `start`). `eligible_positions` lists Yahoo eligibility **including G/F/Util**. |
| ADP | `…/players;…/draft_analysis` (or `out=draft_analysis`) | `average_pick`, `average_round`, `average_cost`, `percent_drafted`, `preseason_*` variants; `"-"` when undrafted. Yahoo-wide, not league-specific. |
| Ownership | `percent_owned` (`value`, `delta`), `ownership` (`ownership_type` freeagents/waivers/team, `owner_team_key`, `waiver_date`) | |
| Stats | `league/{lk}/players;player_keys=…/stats;type=season|lastweek|lastmonth|date;date=…` | not wrapped yet |
| Scoreboard / matchups | `league/{lk}/scoreboard;week=N`, `team/{tk}/matchups;weeks=…` | not wrapped yet |
| Transactions log | `league/{lk}/transactions;types=add,drop,trade;count=…` | reading other teams' moves |

NBA stat_ids used for 9-cat: 5 FG%, 8 FT%, 10 3PTM, 12 PTS, 15 REB, 16 AST, 17 ST, 18 BLK, 19 TO
(display-only 9004003 FGM/A, 9007006 FTM/A). Mapping in `NBA_STAT_IDS`.

### Does draftresults update during a live draft?
Draft results are a plain resource with no push/stream; libraries poll it. Community draft trackers
poll `draftresults` during drafts and picks appear as they are made **(unverified for 2026; test it in
a mock draft before relying on it)**; assume possible lag of several seconds from caching. Before
the draft the collection is empty or lists slots without `player_key`; the parser handles both. **The
browser draft room remains the source of truth during the live draft** (60 s/pick leaves no room for
API lag/rate-limit surprises); the API is a cross-check and a post-draft reconciliation tool.

## 4. Write endpoints (exist; NOT implemented — out of scope, documented for later)

All writes use **XML bodies** (`Content-Type: application/xml`) even when reading JSON, and require
the app to have **Read/Write** permission. Patterns from `yahoo_fantasy_api/team.py`:

* **Lineup** `PUT team/{tk}/roster`
  ```xml
  <fantasy_content><roster><coverage_type>date</coverage_type><date>2026-10-21</date>
    <players><player><player_key>{gk}.p.6014</player_key><position>PG</position></player></players>
  </roster></fantasy_content>
  ```
  (NBA uses `coverage_type=date` — daily lineups.)
* **Add / drop / add-drop / waiver claim** `POST league/{lk}/transactions`
  ```xml
  <fantasy_content><transaction><type>add/drop</type>   <!-- add | drop | add/drop -->
    <!-- <faab_bid>7</faab_bid> only in FAAB leagues -->
    <players>
      <player><player_key>{gk}.p.X</player_key><transaction_data><type>add</type>
        <destination_team_key>{tk}</destination_team_key></transaction_data></player>
      <player><player_key>{gk}.p.Y</player_key><transaction_data><type>drop</type>
        <source_team_key>{tk}</source_team_key></transaction_data></player>
    </players></transaction></fantasy_content>
  ```
  Adding a player on waivers creates a waiver claim (pending until clear). Edit/cancel claims:
  `PUT`/`DELETE transaction/{transaction_key}`.
* **Trades**: propose `POST league/{lk}/transactions` with `type=pending_trade`, `trader_team_key`,
  `tradee_team_key`, `trade_note`, and per-player `source_team_key`/`destination_team_key`; accept/
  reject `PUT transaction/{key}` with `<action>accept|reject</action>`; commissioner allow/disallow and
  voting also via `PUT transaction/{key}`.

## 5. NOT supported by the API

* **Making draft picks** — there is no draft-room/pick endpoint. Every maintained client
  (`yahoo_fantasy_api`, `yfpy`, the Go SDK `n-ae/yahoo-fantasy-sports-api-go`) only *reads*
  `draftresults`; the Go SDK explicitly lists no draft-pick support; Yahoo help describes picks only
  through the live draft room, and RotoWire's Yahoo draft assistant is a one-way sync ("you still
  need to make your draft picks in your draft room"). **Draft picks must be made in the browser.**
* Pre-draft rankings / draft queue editing, keeper designation, league settings changes,
  commissioner tools (draft order), chat/messages, notifications/webhooks. Read-only for other
  users' private data beyond what the league exposes.
* Yahoo projections / rankings values are not exposed beyond `sort=OR` ordering and `draft_analysis`.

## 6. Rate limits & ToS

* No published quota. Throttling surfaces as **HTTP 999 "Request denied"** (yfpy handles status 999
  explicitly). Community lore puts the limit in the low thousands of requests/hour per app/user (unverified);
  we throttle to ≥ 0.25 s between requests and raise `YahooRateLimitError` (no blind retries). Batch
  with `player_keys=` (up to 25) and `out=` sub-resources.
* Yahoo Developer Network API Terms of Use apply: personal, non-commercial use, no scraping beyond the
  API, keep credentials secret, respect rate limits, attribute data ("Data provided by Yahoo! and
  STATS"). Automating the *website* UI is a separate matter (Yahoo ToS discourage automated access);
  we only drive it via the user's own browser with the user in the loop (Claude in Chrome), for the
  user's own team.

## 7. Libraries (checked on PyPI 2026-10-18)

| Library | Latest | Status | Notes |
|---|---|---|---|
| `yahoo_fantasy_api` (spilchen) | 2.12.3, 2026-04-03 | maintained | read + writes (add/drop, claims, lineup PUT, trades). Depends on `yahoo_oauth`, `requests`. Returns loosely-parsed dicts. |
| `yfpy` (uberfastman) | 17.0.0, 2025-09-14 | maintained, GPL-3.0 | read-only, rich typed models, NFL-first; `.env` token storage; heavy. GPL is a licence consideration. |
| `yahoo_oauth` | 2.1.1, 2024-12-17 | slow but alive | OAuth helper used by both; `oob` redirect; stores JSON file; uses `rauth`/`requests`. |

**Recommendation: raw `httpx`** (already a dependency) with our own ~300-line client: no new deps,
typed parsers we control, read-only by construction, token handling that never logs secrets, and
`httpx.MockTransport` tests. If/when we need writes, port the XML builders above (or vendor
`yahoo_fantasy_api`'s, MIT) behind an approval gate (`fantasy approval`) and the txn ledger.

## Sources

* PyPI JSON API: yfpy, yahoo-fantasy-api, yahoo-oauth (versions/dates above); sdists inspected locally.
* [spilchen/yahoo_fantasy_api](https://github.com/spilchen/yahoo_fantasy_api) — `team.py` XML builders, `yhandler.py`, recorded JSON samples in `yahoo_fantasy_api/tests/`.
* [uberfastman/yfpy](https://github.com/uberfastman/yfpy) — README app-registration steps, models, 999 handling.
* [n-ae/yahoo-fantasy-sports-api-go](https://github.com/n-ae/yahoo-fantasy-sports-api-go) — read-only; game keys static ≤2025, discovered for 2026.
* [Yahoo Fantasy Sports API guide](https://developer.yahoo.com/fantasysports/guide/) (unreachable from session; search snippets: league key `game_id.l.league_id`, games collection by game code/season).
* [Yahoo help: Live Standard Draft](https://help.yahoo.com/kb/SLN6230.html), [RotoWire draft assistant](https://www.rotowire.com/baseball/draft-assistant) (picks only in draft room).
