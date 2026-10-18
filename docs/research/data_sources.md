# Data sources (projections 2026-27)

Last updated: 2026-10-18 (UTC). Owner: data & projections agent.

## Reachability from the agent container (2026-10-18)
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

## Not obtained
- Full-table external 2026-27 projections (Hashtag Basketball, FantasyPros, ESPN): pages unreachable
  from both the container and WebFetch, so no external projection blend in v1.
- Official Yahoo position eligibility: approximated from bbref play-by-play position shares
  (>= 20% of 2024-25/2025-26 weighted minutes, plus listed position). Override in
  `data/manual/positions_overrides.csv`.
