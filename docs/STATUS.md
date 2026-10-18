# Project status

_Last updated: 2026-10-18 (session 1, lead engineer). Update this file when state or priorities change._

## Where we are
**Phase 1 (draft MVP) — built, needs real-world verification before the draft on 2026-10-19 22:00 ET.**

| Area | State |
|---|---|
| Projections | v1: 456 players, 3-season weighted per-minute rates + age + GP shrinkage + manual offseason/injury overrides + rookie priors (`data/processed/projections_2026_27.csv`). **2026-10-18 v2 refresh:** Yahoo ADP for 293 players (184 drafted; effective unconditional ADP) and Yahoo position eligibility for the top 250 via Yahoo's public API (`fantasy data fetch-yahoo --apply`); injuries from ESPN API + CBS (`fantasy data fetch-injuries`); bbref mirror verified identical to bbref.com. See `docs/handoffs/2026-10-18-data-refresh-v2.md`. **2026-10-19: default = 50/50 ours/ESPN per-game blend, GP ours** (`projections-2026-27-0.3.0`, config `data/manual/projection_blend.json`; unblended model in `projections_2026_27.base.csv`, `--blend none` to revert; valuation `valuation-0.2.2`). Backtest: `docs/research/projection_quality.md`; `docs/handoffs/2026-10-19-blend-adoption.md`. |
| Valuation | G-scores (H2H-aware) with makes/attempts for FG%/FT%, availability blended with streaming replacement, replacement-level adjusted. |
| Draft optimizer | Rollout optimizer over the final roster's expected category wins vs league-average opponent; ADP availability model; 3RR/custom order; positional feasibility; punt-strategy scan; Monte Carlo sim vs BPA baseline. `optimizer-0.2.0` (expected-value rollouts): +0.074±0.006 exp. cat wins/week vs BPA over 200 paired sims (in-sample); ≈3–4 s/recommendation. |
| Draft ops | `draft init/board/pick/undo/recommend/watch/simulate/queue/set-order/prepare-pick/confirm-pick`; txn ledger + locks + policy enforced for picks. |
| Agent ops | Runs/events/decisions, lease locks, approvals, idempotent txn ledger, tasks, `status`, `publish`. **2026-10-18: cloud sessions use `--rest` on `run/lock/approval/txn/task` (single-row PostgREST writes, CAS on state; no `approval decide` over REST); `publish --rest` never overwrites ledger/state rows (insert-if-missing).** See `docs/handoffs/2026-10-18-rest-ledger.md`. |
| Yahoo | Read-only OAuth API client (untested against live Yahoo). Picks only possible via browser. League settings **verified 2026-10-18 from the Yahoo settings page** (screenshots; `data/manual/league_settings.json`): 9 cats, 12 teams, 3 BN + **3 IL** (not 4), 6 adds/week, playoffs 6 teams weeks 18-20 = Mar 1-21, 2027 (re-seeded, ties → higher seed). **Still unverified: draft order (3RR) and my slot.** See `docs/handoffs/2026-10-18-league-settings.md`. |
| Dashboard | Next.js on Vercel reading Supabase (see `docs/runbooks/dashboard.md`). Pre-draft data online: `draft_sim` (`fantasy draft simulate`, saved by default) and `season_sim` with `scenario: "pre-draft projection"` (`fantasy sim preseason`); schemas in `docs/research/dashboard_data.md`. |
| DB | Supabase `fantasy-nba` (ref `bdzpkdkhlzjzddkptjex`), schema from `src/fantasy/db/schema.py`. Local agents need `FANTASY_DB_URL` (user must supply). |

## Next priorities (see `fantasy task list` for the full backlog)
1. **P0 (before draft, local session with browser):** verify the actual draft order + my slot
   (T-015; the rest of T-001 is done from screenshots), fill Yahoo ADP & positions, load Pre-Draft Rankings (T-014), mock-draft rehearsal (T-002),
   configure `FANTASY_DB_URL` (T-013).
2. P1 after the draft: Yahoo roster sync (T-004), daily lineup optimizer (T-005), waivers (T-006), weekly matchup sim (T-007), calibration (T-016).
3. P2: trade evaluator, backtesting, scheduled morning agent.
4. Season simulator (`fantasy sim`, `season-sim-0.2.0`, skill `season-sim`) built: playoff weeks 18-20 = Mar 1-21 (verified end date; week 1 assumed Oct 20-Nov 1), re-seeding per settings; after the draft load Yahoo rosters + schedule/week dates as JSON (`--league-file`, `--schedule`), calibrate its priors (`SimParams`) with T-010/T-016; next: waiver/trade evaluation on top of `fantasy sim compare` (see `docs/handoffs/2026-10-18-season-sim.md`).
5. **Value search** (`value-search-0.1.0`, `docs/research/value_search.md`): learned season-outcome model + rollout search; NOT adopted as default (P(playoffs) gain z=1.6 < 2), shipped as an advisory `value_search` block in `recommend` (`--search value` opt-in). **Same 200-draft validation: BPA beats the current optimizer under the full season simulator** (symmetric rooms: -6.1 pp P(playoffs), -3.7 pp P(title), z < -8) - investigate before the draft.
5. Tech debt: the 12-step plan in `docs/research/tech_debt.md` is done before the draft except the deferred parts listed there (WIP snapshots still on main, mypy beyond the CI subset, remaining helper duplicates); drafts now store `config.order_slots` (re-run `fantasy draft init` on older drafts, then publish). See `docs/handoffs/2026-10-19-tech-debt-fixes.md`.

## Known limitations
- Pre-draft season projection (96% playoffs / 55% title from slot 1) is the optimistic end: symmetric room 61%/13%, sharp market-informed opponents + market truth 67%/12%, central ≈ 83–89% / 28–35% (`docs/research/optimism_decomposition.md`).
- Cloud sessions can now reach bbref / Yahoo public API / ESPN / CBS (stats.nba.com times out, cdn.nba.com 403s at origin).
  Stats still come from the GitHub mirror of Basketball-Reference (verified identical to bbref.com 2026-10-18).
- Opponent model = league-average team; absolute win probabilities are optimistic.
- Variance priors: the season simulator is fitted (`season-sim-0.4.0`, `docs/research/sim_calibration.md`); the draft model uses the fitted `GAME_CV` since `valuation-0.2.3` (user decision 2026-10-18; picks 1/24/36 unchanged; `FANTASY_GAME_CV=prior` reverts). ADP sd and `USAGE` are uncalibrated heuristics.
- Quant review (2026-10-18) open concerns: weekly variance ignores schedule (games/week) variance and
  category correlation → overconfident win probs; GP weighted ~linearly (`stream_recovery=0.5` may
  over-penalize long injuries given 3 IL slots + IL direct adds); punt scan rarely beats balanced under the cat-wins objective
  (candidates are pre-filtered by balanced value); recommendation margins < ~0.03 are noise; sim edge vs BPA is in-sample.

## How to validate work
`pytest` (all offline, synthetic data) · `ruff check src tests` · `cd web && npm run build` ·
end-to-end: `FANTASY_DB_URL=sqlite:///tmp/x.db fantasy draft init --draft-id mock-1 --mock && fantasy draft recommend`.
