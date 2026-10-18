# Handoff: data & projections (2026-10-18)

## Accomplished
- `data/processed/projections_2026_27.csv` (450 rows, exact ARCHITECTURE.md contract) + sibling
  `projections_2026_27.meta.json` (model version, params, input file hashes/URLs, per-player notes).
  Regenerate deterministically (no network): `fantasy data build-projections --json`.
- Ingestion (`src/fantasy/data/`): `bbref_github.py` (Basketball-Reference tables via the
  sumitrodatta GitHub mirror, cached in `data/raw/bbref_sumitrodatta/` with provenance JSON),
  `nba_api_ingest.py` (optional stats.nba.com path for the user's machine, extra `nba`),
  `provenance.py`, `ids.py` (`slugify`, `canonical_id` + alias map), `cli.py`
  (`build-projections`, `fetch-bbref`, `fetch-nba-api`, `top`).
- Model (`src/fantasy/model/projections.py`): see decisions below; `zscores()` sanity helper.
- Manual overlays (`data/manual/`): `player_overrides_2026_27.csv` (~95 rows: team changes from
  the 2026 offseason, injuries/status, returning-from-injury players, role tweaks),
  `rookies_2026_27.csv` (11 top 2026 draftees, conservative priors), `inactive_2026_27.csv`
  (retired/overseas), `adp_yahoo_2026_27.csv` (50 ADP observations), `positions_overrides.csv`.
- Tests: `tests/test_ids.py`, `tests/test_projections.py` (14 tests, pass).

## Decisions & rationale
- **Stats source**: bbref/stats.nba/ESPN/Hashtag/FantasyPros are unreachable (container proxy and
  WebFetch DNS). The sumitrodatta GitHub mirror of bbref has complete 2025-26 totals -> primary.
- **Baseline**: per-minute rates with season weights 5/3/2 (2025-26/24-25/23-24) weighted by
  minutes, + 400-minute prior at 85% of position-group (G/F/C) league rate (FG%/FT% at group mean).
  Makes and attempts projected separately; pts = 2*FGM + 3PM + FTM. Age multiplier on production
  (1.08 at 19 -> 1.0 at 27-28 -> 0.89 at 40). MPG = 0.6/0.3/0.1 recency-weighted (seasons with >=10 g),
  + growth for <=24-year-olds under 30 mpg, - for 33+, small-sample shrink toward 16 mpg,
  capped 37.5. GP = season-weighted games shrunk toward 68 (1 season of prior weight), cap 80;
  gp_sd = sqrt(7^2 + (0.6*hist_sd)^2), + 0.15 per injury game missed.
- Players with no 2025-26 season are dropped unless listed in overrides (Haliburton, Lillard,
  Irving, VanVleet re-activated with manual gp/mpg).
- Injuries: `games_missed` scales gp by (82-n)/82; status in {healthy,out,gtd,out_for_season,unknown}.
- **No external projection blend**: no full public 2026-27 projection table was reachable.
  Spot check vs Hashtag Basketball (from a search snippet): Jokic HB 28.4/12.7/10.4/1.6s/0.7b/3.5to
  vs ours 27.2/12.4/9.9/1.5/0.8/3.4; Wembanyama HB 25.6/11.8/3.6/1.1/3.2 vs ours 24.7/11.6/3.5/1.1/3.5.
- **ADP**: only observed values (50 players). Some are cross-platform averages or platform-unspecified
  (see `source` column of `adp_yahoo_2026_27.csv`). `fantasy.model.valuation.market_order`
  already falls back to model rank*1.15+3 for blanks.
- **Positions**: derived from bbref play-by-play position shares (>=10% of weighted minutes) +
  listed position, with ~30 manual Yahoo-style overrides for stars. Medium confidence.

## Sanity check (top 30 by per-game 9-cat z, `fantasy data top --n 30`)
Jokic, SGA, Wembanyama, Doncic, Maxey, Haliburton, Kawhi, A. Davis, D. Mitchell, J. Murray,
Edwards, T. Murphy, Embiid, Tatum, Jalen Johnson, Barnes, Markkanen, Holmgren, KAT, Cade, Kyrie,
D. Daniels, Flagg, Bane, D. White, Amen Thompson, Reaves, Anunoby, Brunson, Mobley.
Per-game z ignores gp; Davis/Embiid/Kawhi/Kyrie fall once games are weighted (valuation does that).
Giannis ranks ~50 per-game because of FT% volume (z_ft -5); market ADP is 6.4.

## Problems / caveats
- Offseason moves, injuries and ADP came from WebSearch *summaries* (no verbatim pages); a few
  conflicts noted (Josh Green MIN vs UTA, Dean Wade CLE vs PHI, Kelly Oubre destination unknown,
  Harden FA status unconfirmed -> kept CLE). Usage/minutes impacts of trades are only roughly
  handled (a handful of `usage_mult`), so role changes are the biggest model error source.
- Rookie lines are hand-set priors, not models.
- `tests/test_ops_status_cli.py::test_db_init_and_publish` failing is the ops agent's, not ours.

## Outstanding / next steps
1. Before the draft: verify ADP + positions for top ~150 in the Yahoo draft room / pre-rank page
   (`basketball.fantasysports.yahoo.com/nba/public_prerank`) and paste into
   `data/manual/adp_yahoo_2026_27.csv` / `positions_overrides.csv`, then rebuild.
2. On the user's machine: `pip install -e .[nba] && fantasy data fetch-nba-api` for a cross-check.
3. If a projections page becomes reachable (Hashtag/FantasyPros), add a blend step (e.g. 50/50 on
   per-minute rates and mpg; record weights in `source`).
4. Calibrate age curve and gp model against historical seasons (backtest 2023-24 -> 2024-25).
