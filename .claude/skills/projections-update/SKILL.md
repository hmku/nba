---
name: projections-update
description: Update player data and the 2026-27 projections - refresh cached raw sources with provenance, edit the manual override files (injuries, trades, roles, rookies, ADP, positions), rebuild deterministically, sanity-check, test, and version. Use when news changes a player's outlook (injury, trade, role), when asked to refresh/improve projections or ADP/positions, or before the draft / a weekly in-season refresh.
---

# Projections update

Output contract: `data/processed/projections_2026_27.csv` (one row per player, **per-game means**,
columns in `docs/ARCHITECTURE.md`) + `projections_2026_27.meta.json`. **Since 2026-10-19 this default
file is the 50/50 ours/ESPN blend** (per-game stats blended, GP ours; `docs/research/projection_quality.md`),
configured in `data/manual/projection_blend.json`. The unblended model is
`projections_2026_27.base.csv` + `.base.meta.json` (model version, params, input hashes/URLs,
per-player notes); the default meta records `projections_version`, blend weights and the shas of
the base file and `data/raw/espn/projections_2026_27.csv`. Code: `src/fantasy/model/projections.py` (`MODEL_VERSION`,
`Params`, `AGE_CURVE`, `MPG_GROWTH`); ingestion `src/fantasy/data/`. Source notes and licensing:
`docs/research/data_sources.md`. Data handoff: `docs/handoffs/2026-10-18-data-projections.md`.

## Network reality
| Where | Works | Blocked |
|---|---|---|
| Cloud session (curl/python) | environment allowlist (CLAUDE.md): GitHub mirror, PyPI, basketball-reference, Yahoo public pages/API (`fetch-yahoo`), ESPN/CBS (`fetch-injuries`, `fetch-external`), Rotowire, FantasyPros | stats.nba.com (times out), cdn.nba.com (403), Yahoo account pages |
| Cloud WebSearch tool | model-summarised snippets with URLs (news, injuries, ADP) | - |
| User's machine | everything, incl. `fantasy data fetch-nba-api` | - |
Stats come from the GitHub mirror of bbref (verified identical to bbref.com 2026-10-18); news from the injury
fetchers or WebSearch summaries (medium confidence - say so in `source`). Never invent numbers; if you can't source it, leave it blank.

## 1. Raw sources (cached, with provenance)
```bash
fantasy data fetch-bbref --json        # sumitrodatta GitHub mirror -> data/raw/bbref_sumitrodatta/*.csv
                                       # + *.provenance.json (url, fetched_at, method, sha256, rows)
fantasy data fetch-nba-api --seasons 2025-26,2024-25,2023-24 --json   # user's machine only; needs pip install -e ".[nba]"
                                       # -> data/raw/nba_api/ (cross-check only; not consumed by the build)
```
Never edit raw files by hand. Raw data is (c) Sports Reference: personal use, don't republish more than needed.

## 2. Manual overlays (`data/manual/`, `#` lines are comments, matched by name via `canonical_id`)
| File | Columns | Use |
|---|---|---|
| `player_overrides_2026_27.csv` | `name,team,status,injury_note,gp,games_missed,mpg,usage_mult,note,source,gp_scale` | trades/team, injury status (`healthy|out|gtd|out_for_season|unknown`), `games_missed` (gp x (82-n)/82), `mpg` replaces minutes, `usage_mult` scales FGA/FGM/FTA/FTM/3PM/AST/TOV. `gp_scale`: `fitted` = take `gp` literally (use this for new rows), blank/`baseline01` = ×0.90 haircut (old rows). Note the default blend halves `mpg`/`usage_mult` overrides (ESPN is blended after them). Blank = keep baseline. A player with no 2025-26 season MUST be listed here to be projected. |
| `rookies_2026_27.csv` | `name,team,positions,age,gp,gp_sd,mpg,fgm,fga,ftm,fta,fg3m,reb,ast,stl,blk,tov,note` | per-game priors for players without NBA history (low confidence) |
| `inactive_2026_27.csv` | `name,note,source` | retired / overseas / unsigned -> removed |
| `adp_yahoo_2026_27.csv` | `name,adp,source,as_of` | Yahoo ADP (last duplicate wins); blanks fall back to model rank in `market_order` |
| `positions_overrides.csv` | `name,positions,note` | Yahoo eligibility, pipe-separated base positions (`PG|SG`) |
| `league_settings.json` | LeagueSettings JSON | league settings (created by `fantasy settings verify`) |
Rules: always fill `source` (URL/outlet + date, and "via WebSearch" when it was a snippet); prefer
the smallest override that captures the news (e.g. `games_missed` rather than a hand-set `gp`); one
row per player (update in place). Percentages are never inputs - edit makes/attempts.
**In season (since 2026-10-21):** the preseason file is the PRIOR of `fantasy data update-projections`
(`fantasy.model.inseason`), which reads `games_missed` as "expected out for the first n team games of the season" and
ignores those games when updating. So never add an in-season injury as `games_missed` (it would be placed at the season
start and double count the absence the box scores already show): current injuries come in through `status` (daily
`fetch-injuries`) and the in-season update. After any `build-projections` re-run `update-projections`.
News beyond the injury report (trades, waivers, roles, returns, "indefinite"): `fantasy data fetch-news` +
`fantasy data news-digest --new-only` (daily-routine step 1.4b) list new ESPN/Rotowire blurbs per player with keyword
severity, the current override row and `override_stale`; the agent reads them and decides any override edit.

## 3. Rebuild (deterministic, no network)
```bash
fantasy data build-projections --json          # base model -> .base.csv, then the configured blend -> default csv
fantasy data build-projections --blend none    # default = unblended baseline (emergency fallback)
fantasy data fetch-external --source espn      # refresh ESPN's 2026-27 numbers (cached; --refresh to re-download)
```
JSON: `base` (`model_version`, `rows` ~450 = `Params.max_rows`, `sha256`, `source_counts`:
`bbref_baseline`, `bbref_baseline+manual`, `manual_rookie_prior`), `inputs[]` (path, sha256, url,
fetched_at), `default` (`projections_version`, `model_version` e.g. `baseline-0.2.1+blend-0.2.0`,
`sha256`, `weights`, `inputs` = base + ESPN shas, `ignored_external` = ESPN players we do not project).
Same inputs -> same sha256 (`git diff data/processed` empty). Manual overrides act on the base and so
flow into the blend; blended players have `source` ending in `+blend`. Note an override's per-game
stats get averaged with ESPN's (an injury `games_missed`/`gp` change is not: GP stays ours).

## 4. Sanity checks
```bash
fantasy data top --n 30            # per-game 9-cat z (ignores gp); --json gives {"ok": true, "players": [...]}
git diff --stat data/processed     # what moved
```
- Players you edited: check their row (`grep -i "<name>" data/processed/projections_2026_27.csv`)
  - team, status, gp, mpg moved the way the news implies.
- Unmatched names are silently ignored (see `meta.json` -> `notes`, e.g. "override for unknown player
  ignored"); run the unmatched-name check in the `draft-prep` skill step 2. Long-term injured players
  can also drop out because of the 450-row relevance cut (on 2026-10-18: Jimmy Butler, Mark Williams,
  Donte DiVincenzo, Jordan Miller have overrides but no output row).
- Plausibility: mpg <= 37.5, 0 <= gp <= 80, fgm <= fga, ftm <= fta, fg3m <= fgm, FG% 0.40-0.70 and
  FT% 0.50-0.95 for rotation players, positions non-empty. The `data-auditor` subagent does a full pass.
- Then `fantasy values build --json` (new valuation `model_runs` row) and, if a draft is active,
  `fantasy draft recommend --json` to see the effect.

## 5. Tests & versioning
- `pytest tests/test_projections.py tests/test_ids.py` then full `pytest`, `ruff check src tests`.
  Tests use synthetic data; never hit the network in tests.
- **Blend weights** (`data/manual/projection_blend.json`): a model decision - backtest first
  (`projection_quality.md`), bump `PROJECTIONS_VERSION` in `src/fantasy/model/blend.py`, and get the lead's OK.
- **Data-only changes** (manual CSVs, re-fetch): no version bump; the input hashes in `meta.json` and
  the projections sha256 recorded in each valuation run (`inputs.projections_sha256`) version them.
- **Code/param changes** to the projection model: bump `MODEL_VERSION` in
  `src/fantasy/model/projections.py`, add/adjust a test, and report old vs new top-30 movement in the
  handoff. (Only the valuation step writes `model_runs`; projections are versioned by `meta.json`.)
- Ask the `quant-reviewer` subagent to review non-trivial model changes.

## 6. Record & commit
Commit only `data/manual/<files you changed>`, `data/processed/projections_2026_27.*` (incl. `.base.*`; and code you
own) - others edit `data/` concurrently, so `git status` first and never `git add -A`. Note sources
and confidence in the handoff. If a dashboard/production DB is in use, the new valuation run reaches it
only when `values build` ran against Supabase (or via `fantasy publish`).
