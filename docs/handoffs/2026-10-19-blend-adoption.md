# Handoff 2026-10-19 - blend adopted as default projections (projections agent, cloud session)

## What was accomplished
- **The draft default is now the 50/50 ours/ESPN per-game blend, with GP from our model.** The lead orchestrator
  relayed the user's approval. This session received it as an orchestrator message and did not get it from the user
  directly. Revert with `fantasy data build-projections --blend none` if that approval is wrong.
  - Config: `data/manual/projection_blend.json` `{"weights": {"ours": 0.5, "espn": 0.5}, "gp_weights": null}`, read by default.
  - Plain `fantasy data build-projections` writes the unblended model to `data/processed/projections_2026_27.base.csv`
    (sha `25ef6ca4…`, identical to the old default) and the blend to `data/processed/projections_2026_27.csv`
    (sha `6acf9cce…`, byte-identical to the reviewed `.blend.csv` from the research session). The old `.blend.csv/.meta.json`
    files are deleted because the default now holds the blend.
  - Options: `--blend none` = unblended default, `--blend "ours=..,espn=.."` = other weights, `--blend-output <path>` = blend
    elsewhere while the default stays unblended.
  - Versions: `PROJECTIONS_VERSION = projections-2026-27-0.3.0`, `BLEND_VERSION = blend-0.2.0`; default meta `model_version`
    `baseline-0.2.1+blend-0.2.0`; base model unchanged at `baseline-0.2.1`; valuation bumped to `valuation-0.2.2` (no
    formula change, so the new projections are visible in model_runs). The default meta records weights, gp source, and
    the input shas of the base file and of `data/raw/espn/projections_2026_27.csv` (`d38a49b1…`).
- The rebuild is deterministic: two builds in a row gave the same shas.
- Published to Supabase (REST, from fresh SQLite `var/pub2.db`):
  | run_id | kind | version | notes |
  |---|---|---|---|
  | `val-20261018220411-43f246` | valuation | valuation-0.2.2 | 456 player_values (verified via REST); `inputs.projections_sha256` = 6acf9cce… |
  | `dsim-20261018222430-8e6d01` | draft_sim | optimizer-0.2.0 | 200 sims, seed 0, slot 1, 3RR, noise 1 (1208 s). Edge vs BPA +0.098 (SE 0.007) exp. cat wins; Jokić first pick in 200/200 |
  | `sim-20261018222640-4b5e92` | season_sim | season-sim-0.3.0 | preseason: 20 leagues x 100 = 2000 sims, draft seed 100, season seed 0 (130 s). Me: P(playoffs) 0.951 (0.005), P(title) 0.475 (0.017), exp. rank 2.04 |
  Also published: 456 players, the `nba.l.44495-2026` draft row (snake_3rr, slot 1), and 1 draft_recommendation (pick 1).
- Docs: `.claude/skills/projections-update/SKILL.md` (sections 3 and 5), `docs/research/projection_quality.md` (adopted banner and
  Method CLI), `docs/research/optimism_decomposition.md` (note: the analysis and `web/src/content/optimism.json` predate the blend;
  not recomputed), `docs/STATUS.md` Projections row.

## Verification results
- `fantasy values build --no-save --top 30`: the top 10 are Jokić 9.79, Wembanyama 9.52, SGA 9.13, Dončić 7.41, Haliburton 6.32,
  Maxey 5.91, Tatum 5.50, Edwards 5.42, Jamal Murray 5.24, Donovan Mitchell 5.18.
- Mock draft (fresh SQLite, `mock-1`, slot 1, 3RR; other picks by Yahoo ADP; `recommend` scores are expected category wins):
  pick 1 Jokić 6.074 (SGA 6.011, Wemby 5.990); pick 24 Kawhi Leonard 6.067 (Bam 6.065, D. White 6.040, Holmgren 6.040);
  pick 36 Derrick White 6.040 (Clingan 6.040, Kyrie 6.033, Bane 6.028). These match the research note's blend column exactly.
  At #24 and #36 the top candidates are near ties.

## Decisions & rationale
- I used a config file plus the default path, not a flag, because the draft tools all read `data/processed/projections_2026_27.csv`. The draft-path code is
  unchanged: only the data file differs.
- GP stays ours: ESPN's GP runs about +11 games optimistic (fitted, `projection_quality.md`).

## Files changed
`src/fantasy/model/blend.py`, `src/fantasy/data/cli.py`, `src/fantasy/model/valuation.py` (version only),
`tests/test_external_blend.py` (+3 tests), `data/manual/projection_blend.json`, `data/processed/projections_2026_27.{csv,meta.json,base.csv,base.meta.json}`
(the `.blend.*` files are deleted), and the docs listed above.

## Tests performed
`python -m pytest -q` passed on the merged head (after 0914116). `python -m pytest -q -m slow`: 2 passed. `ruff check src tests`: clean.

## Problems encountered
- The session started on a detached HEAD, so the first push was rejected. I pushed with `HEAD:main` and re-attached local `main`.
- A concurrent commit, 0914116 (one opponent sampler, season-sim-0.3.0), landed while the sims ran. The draft_sim process had already
  loaded the older sampler code. The preseason sim started later and ran season-sim-0.3.0. Both runs used the blended projections.

## Caveats for the next agent
- **Manual overrides are diluted for players ESPN covers.** Overrides (`mpg`, `usage_mult`, rookie priors) act on the base
  file, and the per-game stats are then averaged 50/50 with ESPN. Only GP and `games_missed` stay fully ours. A late
  role or injury change that should apply in full needs either an ESPN refresh (`fantasy data fetch-external --source espn --refresh`)
  or a rethink. Players ESPN does not cover are unaffected.
- Morez Johnson Jr. is still missing from every projection input (see the projections-quality handoff).

## Suggested next steps
- After the draft: refit the age, minutes and team-change biases (see the projections-quality handoff), then re-backtest the blend weight.
- Optionally recompute `optimism.json` / `optimism_decomposition.md` with the blended projections.
