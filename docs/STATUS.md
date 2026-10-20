# Project status

_Last updated: 2026-10-20 UTC (post-draft league simulation). Update this file when state or priorities change._

## Where we are
**Phase 1 (draft MVP) — built and verified; the real draft (`nba.l.44495-2026`, Mon 2026-10-19 22:00 ET, slot 1, 3RR) is
under way tonight** (live-draft skill, local session with a browser). Draft results are not recorded here yet: the
drafting session writes `docs/handoffs/<date>-draft.md` afterwards. Draft-path code (`src/`, `web/`) is frozen until then.

| Area | State |
|---|---|
| Projections | 456 players, 3-season weighted per-minute rates + age + GP shrinkage + manual offseason/injury overrides + rookie priors. **Default = 50/50 ours/ESPN per-game blend, GP ours** (`projections-2026-27-0.3.0`, `baseline-0.2.1+blend-0.2.0`, config `data/manual/projection_blend.json`; unblended model in `projections_2026_27.base.csv`, `--blend none` to revert). Yahoo ADP for 293 players and Yahoo positions for the top 250 via Yahoo's public API (`fantasy data fetch-yahoo --apply`); injuries from ESPN API + CBS (`fantasy data fetch-injuries`). Backtest: `docs/research/projection_quality.md`; `docs/handoffs/2026-10-19-blend-adoption.md`, `2026-10-18-data-refresh-v2.md`. |
| Valuation | `valuation-0.3.0`: G-scores (H2H-aware) with makes/attempts for FG%/FT%, availability blended with streaming replacement, replacement-level adjusted; game noise from the one fitted model `noise-var-1.0` (`src/fantasy/model/noise.py`). |
| Draft policy (default) | **`fantasy draft recommend` with no flags = value search + simulated availability, room `mixed`** (`recommend-0.2.0`, `optimizer-0.4.0`, value model `value-model-0.2.0`; user-approved 2026-10-19). Optimizer: rollouts over the final roster's expected category wins vs a league-average opponent; availability from 300 simulated rooms (3 sharp + 8 noisy-ADP drafters) from the current board; 3RR/custom order; positional feasibility; punt-strategy scan. The value search re-ranks the optimizer's top 6 by simulated season outcome. Evidence: +1.9 pp P(playoffs), +1.4 pp P(title) vs the old default in the mixed room (z 8.7 / 4.7, 200 paired simulated drafts, slot 1; fitted, `docs/research/draft_policy.md`); value model 0.2.0 vs 0.1.0: +0.5 pp P(playoffs) (z 2.8), +1.2 pp P(title) (z 3.8) (`docs/research/value_search.md`). Rule: **take `recommendations[0]`** (no score tie-break). Guard: every recommend must show `value_search_status: ok` and `value_model_version: value-model-0.2.0` (`FANTASY_VALUE_MODEL` unset). `draft watch` uses the same policy (shared `Recommender`); ≈2–3 s wall per `recommend --json`. Old behaviour: `--search advise --availability adp`. |
| Draft ops | `draft init/board/pick/undo/recommend/watch/simulate/queue/set-order/prepare-pick/confirm-pick`; txn ledger + locks + policy enforced for picks. `pick --allow-unknown` / explicit `--overwrite` (`--force` alone no longer overwrites); `board` reports `gaps` and `behind`; `prepare-pick` refuses while earlier picks are unrecorded (`behind`, own submitted picks in `behind_mine`) and warns when the latest recommendation is for an older board; `set-order` refuses to re-own recorded picks without `--force` (`reassigned_picks`); `undo` of an empty pick exits 6; `watch` reloads the order. Handoffs: `2026-10-19-draft-review-fixes.md`, `2026-10-19-review2-fixes.md`. |
| Season simulator | `fantasy sim ...` (`season-sim-0.5.0`, skill `season-sim`): fitted priors (`docs/research/sim_calibration.md`), same noise model as the draft. **Post-draft run on the real league** (`sim-20261020033348-d0613f`, 12k sims, real rosters = Yahoo capture, Yahoo's real schedule `data/manual/league_schedule.json`): me 91.9% playoffs / 33.7% title / exp. rank 2.6 (simulated, in-sample optimistic); next Haagen Daz Center 76% / 12%. Results + per-team table: `docs/research/season_simulator.md` "Post-draft league". Dashboard League/Matchup show it. |
| In-season tools | `fantasy inseason lineup`, `stream`, `trade` (`inseason-0.1.0`, `src/fantasy/inseason/`): position-aware daily lineups (crunch ≈ 0 games wks 1-4), streaming ranker on Yahoo FA list, paired trade evaluation. Week-1/trade analysis: `docs/research/week1_and_trades_2026-10-20.md` (top idea Ware + VanVleet -> Jamal Murray, needs approval). |
| Agent ops | Runs/events/decisions, lease locks, approvals, idempotent txn ledger, tasks, `status`, `publish`. Cloud sessions use `--rest` on `run/lock/approval/txn/task` (single-row PostgREST writes, CAS on state; no `approval decide` over REST). `publish` (SQL and `--rest`) and `sqldump` never overwrite ledger/state rows (insert-if-missing). `docs/handoffs/2026-10-18-rest-ledger.md`. |
| Muse / league intel | `POST /api/agent/snapshot` -> `league_snapshots`, reports incl. `chat_digest`; read with `fantasy intel latest|list|show [--rest]`; in-season protocol = `league-loop` skill (THINK side built; snapshot->rosters converter, action queue, lineup/stream/trade tools PLANNED). **Privacy migration `20261019120000_private_league_intel.sql` pending (user action, `docs/USER_TODO.md`).** |
| Yahoo | Read-only OAuth API client (untested against live Yahoo). Picks only via the browser. League settings **all verified**: settings page 2026-10-18 (9 cats, 12 teams, 3 BN + 3 IL, 6 adds/week, daily lineups, playoffs 6 teams weeks 18-20 = Mar 1-21, 2027, re-seeded) and draft order (3RR) + my slot 1 from the draftresults page 2026-10-19 (commit 436c3ba). |
| Dashboard | Next.js on Vercel reading Supabase (see `docs/runbooks/dashboard.md`). `draft_sim` and `season_sim` (`scenario: "pre-draft projection"`) online; schemas in `docs/research/dashboard_data.md`. |
| DB | Supabase `fantasy-nba` (ref `bdzpkdkhlzjzddkptjex`), schema from `src/fantasy/db/schema.py`. Local agents need `FANTASY_DB_URL` (user supplies). |

## Next priorities (see `fantasy task list` for the full backlog)
1. **Tonight:** finish the draft (live-draft skill). Afterwards: `fantasy draft board --json` vs the Yahoo roster,
   `fantasy run end`, handoff `docs/handoffs/<date>-draft.md` (roster, deviations from `recommendations[0]`, issues), commit.
2. **Done 2026-10-20:** post-draft league sim on the real rosters + Yahoo schedule (handoff `2026-10-20-post-draft-league-sim.md`).
   Next: re-run after any roster change (rosters file from a Muse `rosters` snapshot once a converter exists; the draft
   no longer equals the rosters after the first waiver move); ask Muse for a read-only league check; user applies the private-intel migration.
3. Calibration with the real draft (T-016): pick-vs-ADP residuals for the ADP sd prior and the room model (first real sample).
4. P1 in-season: Yahoo roster sync (T-004), daily lineup optimizer (T-005), waivers/streaming (T-006), snapshot ->
   `rosters.json` converter for `league-loop`; then trade evaluator (T-008), backtesting (T-010), scheduled job (T-011).
5. Backlog hygiene: T-007 closed 2026-10-20; T-009 (Monte Carlo draft sim) is built but still `todo` in the DB;
   close T-002/T-014 or record why not.
6. Tech debt: deferred parts of `docs/research/tech_debt.md` (WIP snapshots on main, mypy beyond the CI subset, helper duplicates).
7. Research options, all opt-in and **not adopted** (no draft action needed): `--filler board|available` / `--refine k`
   (`docs/research/optimizer_placeholders.md`: no variant beyond ±0.65 pp), `OptimizerParams(room_self=skip|plan)`
   (`docs/research/draft_policy.md`), `--policy bpa|hybrid|bpa_need|...` (evaluated alternatives; value search then only advises).

## Known limitations
- Pre-draft season projection (96% playoffs / 55% title from slot 1) is the optimistic end: symmetric room 61%/13%, sharp market-informed opponents + market truth 67%/12%, central ≈ 83–89% / 28–35% (`docs/research/optimism_decomposition.md`; computed before the ESPN blend, the calibrated simulator, the unified noise model and the 2026-10-19 draft policy, not recomputed).
- Cloud sessions can now reach bbref / Yahoo public API / ESPN / CBS (stats.nba.com times out, cdn.nba.com 403s at origin).
  Stats still come from the GitHub mirror of Basketball-Reference (verified identical to bbref.com 2026-10-18).
- Opponent model = league-average team; absolute win probabilities are optimistic.
- Variance priors: the season simulator is fitted (`docs/research/sim_calibration.md`). Since 2026-10-19 there is ONE game-noise model, the fitted var(stat, m) = a·m + b·m² in `src/fantasy/model/noise.py`, used by valuation G-scores, the optimizer's category model and the season simulator (since `valuation-0.3.0`, `optimizer-0.3.0` (current 0.4.0), `season-sim-0.5.0`); the constant-CV models and `FANTASY_GAME_CV` are deleted. Draft effect: section 8 of sim_calibration.md. ADP sd and `USAGE` are uncalibrated heuristics.
- Quant review (2026-10-18) open concerns: weekly variance ignores schedule (games/week) variance and
  category correlation → overconfident win probs; GP weighted ~linearly (`stream_recovery=0.5` may
  over-penalize long injuries given 3 IL slots + IL direct adds); punt scan rarely beats balanced under the cat-wins objective
  (candidates are pre-filtered by balanced value); recommendation margins < ~0.03 are noise; sim edge vs BPA is in-sample.

## How to validate work
`pytest` (all offline, synthetic data) · `ruff check src tests` · `cd web && npm run build` ·
end-to-end: `FANTASY_DB_URL=sqlite:///tmp/x.db fantasy draft init --draft-id mock-1 --mock && fantasy draft recommend`.
