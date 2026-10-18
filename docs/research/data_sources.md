# Data sources (projections 2026-27)

Last updated: 2026-10-19 (UTC). Owner: data & projections agent.

## Update 2026-10-19: external projections (ESPN, FantasyPros) + full-history bbref for backtests
- **ESPN fantasy API** (unofficial, no auth): `https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/<yyyy>/segments/0/leaguedefaults/1?view=kona_player_info`
  with header `X-Fantasy-Filter` (limit, draft-rank sort, `filterStatsForSourceIds=[1]`, `filterStatsForSplitTypeIds=[0]`).
  Stat set `id = "10<yyyy>"` = ESPN's season projection (per-game `averageStats`: FGM/FGA/FTM/FTA/3PM/PTS/REB/AST/STL/BLK/TO, MIN, GP).
  Past seasons are served too (2018-19 onward; 2019-20 has only 14 rows; **2022-23 is an in-season update and not preseason**).
  `fantasy data fetch-external --source espn [--season 2027]` writes `data/raw/espn/projections_<yyyy_yy>.csv` (+ provenance).
- **FantasyPros consensus** `https://www.fantasypros.com/nba/projections/overall.php`: season totals (no attempts) for 264 players.
  `fantasy data fetch-external --source fantasypros` writes `data/raw/fantasypros/projections_2026_27.csv`.
- Basketball-Reference player pages show a "2026-27 Projection" table (per-36 rates only, from bbref's Marcel-like system). Reachable but not
  ingested. Hashtag Basketball, FantasyData and DARKO were unreachable from the container.
- Licensing: ESPN and FantasyPros content is for personal use with no redistribution. Raw payloads are git-ignored, and only normalised
  numbers are committed. The repo is public, so the user decides (USER_TODO).
- The backtest used the full sumitrodatta `Player Totals.csv` (all seasons), downloaded to the session scratchpad only (not committed).
- Analysis, blend and licensing table: `docs/research/projection_quality.md`. This supersedes "Not obtained → full-table external
  projections" below.

## Update 2026-10-18 (v2): direct sources now reachable
- **Yahoo public draft analysis API** (no login): `https://pub-api-rw.fantasysports.yahoo.com/fantasy/v2/league/478.l.public/players;start=N;count=25;sort=DA_AP;out=draft_analysis?format=json`
  -> ADP, % drafted, eligible positions, injury status. `fantasy data fetch-yahoo --apply`; raw in `data/raw/yahoo_public/`.
- **ESPN injuries API**: `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries` (status, comment, est. return date);
  **CBS** `https://www.cbssports.com/nba/injuries/` as cross-check. `fantasy data fetch-injuries`; raw in `data/raw/espn/`, `data/raw/cbs/`.
- basketball-reference.com reachable; the sumitrodatta mirror matched bbref.com exactly for 2025-26 totals (top 200), so it stays primary.
- stats.nba.com: CONNECT ok but origin never responds (timeout); cdn.nba.com: origin 403. Rotowire injury JSON truncated to 5 rows for non-subscribers.
- Details: `docs/handoffs/2026-10-18-data-refresh-v2.md`. The reachability table below is historical.

## Reachability from the agent container (2026-10-18, first session — superseded)
| Source | curl/python in container | WebFetch tool | Notes |
|---|---|---|---|
| basketball-reference.com | blocked | DNS failure (ENOTFOUND) | |
| stats.nba.com | blocked | n/a | `fantasy data fetch-nba-api` works on the user's machine |
| hashtagbasketball / fantasypros / espn / wikipedia | blocked | DNS failure | |
| raw.githubusercontent.com, pypi, api.github.com | OK | github.com pages OK | GitHub API requires repo allowlist for some calls |
| WebSearch tool | n/a | OK | returns model-summarized snippets with source URLs (used for offseason news/injuries/ADP) |

## Sources used

### 1. Basketball-Reference player tables (via GitHub mirror) - PRIMARY STATS
- Upstream: https://github.com/sumitrodatta/bball-reference-datasets (`Data/` folder). This is the
  scraper behind the Kaggle dataset "NBA Stats (1947-present)" by Sumitro Datta; it republishes
  Basketball-Reference tables. Includes complete 2025-26 regular season (bbref season 2026).
- Files (raw.githubusercontent.com, fetched 2026-10-18T13:36Z, httpx GET):
  - `Player Totals.csv` -> `data/raw/bbref_sumitrodatta/player_totals.csv` (seasons 2022-2026, 3694 rows)
  - `Player Play By Play.csv` -> `player_play_by_play.csv` (position-minute shares, used for eligibility)
  - `Player Career Info.csv` -> `player_career_info.csv` (birth dates)
- Provenance sidecars: `*.provenance.json` (url, fetched_at, method, sha256, rows, upstream_rows).
- Refresh: `fantasy data fetch-bbref`.
- Licensing/ToS: Basketball-Reference data is (c) Sports Reference LLC; their terms allow personal,
  non-commercial use and restrict bulk redistribution. The GitHub mirror has no explicit license.
  We cache a small filtered subset for personal fantasy use only; do not republish.

### 2. Offseason transactions, injuries, rookies, ADP - WebSearch summaries (MANUAL)
Encoded by hand in `data/manual/*.csv` with a `source` column. Fetched 2026-10-18 via the WebSearch
tool (summaries of the pages below). These are *model-summarized* snippets, not verbatim tables:
treat as medium confidence and re-verify key players on Yahoo before/during the draft.
- Trades/FA: Yahoo Sports trade tracker (https://sports.yahoo.com/nba/article/2026-nba-offseason-trade-tracker-deal-details-analysis-220745003.html),
  NBA.com (https://www.nba.com/news/lamelo-ball-trade-timberwolves-2026, https://www.nba.com/news/reports-julius-randle-traded-to-nets-on-eve-of-draft),
  ABC/ESPN on LeBron to PHI (https://abc7news.com/19569047/), SI lineup rankings
  (https://www.si.com/nba/ranking-all-30-projected-nba-starting-lineups-from-worst-to-best).
- 2026 draft: https://www.yardbarker.com/nba/articles/2026_nba_draft_first_round_results/s1_14822_43987657,
  https://fadeawayworld.net/2026-nba-draft-full-results-complete-first-round-selections
- Injuries: https://www.covers.com/sport/basketball/nba/injuries (Oct 17, 2026), https://www.espn.com/nba/injuries,
  https://www.nbcsports.com/fantasy/basketball/news/2026-27-fantasy-basketball-kyrie-irving-tyrese-haliburton-among-big-names-returning-from-injury
- ADP: https://slamdex.com/tools/adp/ (Yahoo/ESPN/Fantrax ADP table), https://fantasysixpack.net/2026-27-fantasy-basketball-adp-values/,
  https://sports.yahoo.com/articles/2026-27-fantasy-basketball-adp-074600811.html

## ADP coverage
50 players have an ADP value (`data/manual/adp_yahoo_2026_27.csv`). Most are Yahoo-specific; some are
slamdex cross-platform averages or CBS (platform unspecified) and are labelled as such in `source`.
Additional pages seen: https://fantasygoats.guru/guides/top-150, https://www.cbssports.com/fantasy/basketball/news/fantasy-basketball-rankings-2026-27-sleepers-breakouts-and-busts-from-proven-nba-computer-model/,
https://www.nba.com/news/nba-fantasy-expectations-for-top-rookies, https://www.fantasyalarm.com/articles/nba/fantasy-basketball-draft-advice/2026-27-fantasy-baseball-adp-fallers/197132

## Not obtained
- Full-table external 2026-27 projections (Hashtag Basketball, FantasyPros, ESPN): pages unreachable
  from both the container and WebFetch, so no external projection blend in v1.
- Official Yahoo position eligibility: approximated from bbref play-by-play position shares
  (>= 10% of 2024-25/2025-26 weighted minutes, plus listed position). Override in
  `data/manual/positions_overrides.csv`.
