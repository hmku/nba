# Handoff 2026-10-18 - dashboard data: persisted draft & pre-draft season simulations (dashboard-data agent, cloud session)

## What was accomplished
- `fantasy draft simulate` now records a `model_runs` row (kind `draft_sim`, model_version = `OPTIMIZER_VERSION`,
  params incl. optimizer params, inputs incl. projections sha256, order, my picks and compact `per_sim` results)
  with a display-ready `summary` (schema `draft_sim/1`): paired optimizer-vs-BPA edge (mean, SE, p_better, p_tie)
  for expected category wins and matchup win prob, strategy counts, top-5 players at each of my picks,
  availability table (~40 targets x my first 6 picks, from the availability model), per-category win-prob
  mean/p10/p90 (optimizer and BPA), best/median/worst example rosters, `explanations`, `warnings`.
  `--save/--no-save` (default save). Text output is now a short headline instead of the whole summary.
- New `fantasy sim preseason` (`src/fantasy/sim/preseason.py`): K simulated full drafts (me = optimizer from my
  slot, others noisy ADP; `simulate_drafts(keep_leagues=True)`), season simulator on each league, pooled by
  slot. Saved as kind `season_sim`, `params.command = "preseason"`, `summary.scenario = "pre-draft projection"`
  (schema `season_sim_preseason/1`). SEs clustered by league (sd of league means / sqrt K). Refuses `--schedule`
  (Yahoo team ids != draft slots).
- Schemas documented field by field in `docs/research/dashboard_data.md` (committed first, then updated).
- Quant-reviewer pass; fixed: clustered SEs (the reviewer's suggested "between/K + within/N" formula double
  counts - test added), explanations now match the engine (symmetric streaming, weekly totals by normal
  approximation, settings-driven weeks/bracket/calendar text), p_tie, availability uses the run's noise.

## Published to Supabase (`fantasy publish --rest`, from `var/dash.db`)
| run_id | kind | what |
|---|---|---|
| `dsim-20261018184857-cc4e33` | draft_sim | 200 sims, seed 0, slot 1, 3RR, noise 1.0 (905 s) |
| `sim-20261018183737-68e5f1` | season_sim | preseason: 20 simulated leagues x 100 seasons = 2000, draft seed 100, season seed 0 (104 s) |

Key numbers (in-sample, optimistic absolute levels):
- Edge vs BPA: +0.104 ± 0.007 expected cat wins/week (optimizer better in 90.5% of sims, 1% ties);
  matchup win prob +0.0086 ± 0.0008. Optimizer 6.24 vs BPA 6.13 exp cat wins vs league-average opponent.
  Strategy: balanced in 200/200. Pick 1 Jokić 100%; pick 24 Holmgren 56% / J. Murray 18.5% / Murphy 10.5%.
  Weakest category: FG% (mean 0.49 win prob), then FT% 0.62.
- Season (pre-draft projection): P(playoffs) 96.6% ± 0.6%, P(bye) 80%, P(title) 54.5% ± 1.6%, exp rank 1.86;
  across leagues P(title) 39-66%. Next best slot P(title) ~5%. Category profile: top in PTS/REB/STL/3PM, FG% rank ~6.4, TO ~5.2.
  This is strongly optimistic (passive ADP opponents, same projections) - stated in `explanations.caveats`.

## Files changed
`src/fantasy/draft/simulate.py`, `src/fantasy/draft/cli.py` (simulate), `src/fantasy/sim/preseason.py` (new),
`src/fantasy/sim/cli.py` (preseason), `tests/test_dashboard_data.py` (new, 7 tests),
`docs/research/dashboard_data.md` (new), `.claude/skills/model-eval/SKILL.md`, `.claude/skills/season-sim/SKILL.md`, `docs/STATUS.md`.

## Tests performed
`python -m pytest -q` all green; `ruff check src tests` clean. Verified both rows via PostgREST GET.

## Problems encountered
- 200 draft sims took 15 min (~4.5 s/sim incl. BPA replay), slower than the ~0.5 s/sim seen with 2 sims.
- `pkill -f "fantasy draft simulate"` killed my own shell (pattern in its command line) - use pids.

## Outstanding work / next steps
- After the draft: `fantasy sim season --draft-id <real>` (or `--league-file` + `--schedule`) supersedes the
  pre-draft projection; the dashboard should prefer a non-preseason season_sim row once it exists.
- Preseason runs the BPA replay per league only to report a noisy `draft_edge_vs_bpa`; could skip it for speed.
- Availability table ignores that I may have taken a player myself earlier (documented).

## Notes for next agent
Dashboard query: latest `kind='draft_sim'`; latest `kind='season_sim' and summary->>'scenario'='pre-draft projection'`.
No schema migration was needed (summary is JSON). No web/ edits.
