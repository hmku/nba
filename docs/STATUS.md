# Project status

_Last updated: 2026-10-18 (session 1, lead engineer). Update this file when state or priorities change._

## Where we are
**Phase 1 (draft MVP) — built, needs real-world verification before the draft on 2026-10-19 22:00 ET.**

| Area | State |
|---|---|
| Projections | v1: 456 players, 3-season weighted per-minute rates + age + GP shrinkage + manual offseason/injury overrides + rookie priors (`data/processed/projections_2026_27.csv`). **2026-10-18 v2 refresh:** Yahoo ADP for 293 players (184 drafted; effective unconditional ADP) and Yahoo position eligibility for the top 250 via Yahoo's public API (`fantasy data fetch-yahoo --apply`); injuries from ESPN API + CBS (`fantasy data fetch-injuries`); bbref mirror verified identical to bbref.com. See `docs/handoffs/2026-10-18-data-refresh-v2.md`. |
| Valuation | G-scores (H2H-aware) with makes/attempts for FG%/FT%, availability blended with streaming replacement, replacement-level adjusted. |
| Draft optimizer | Rollout optimizer over the final roster's expected category wins vs league-average opponent; ADP availability model; 3RR/custom order; positional feasibility; punt-strategy scan; Monte Carlo sim vs BPA baseline. ≈3 s/recommendation. |
| Draft ops | `draft init/board/pick/undo/recommend/watch/simulate/queue/set-order/prepare-pick/confirm-pick`; txn ledger + locks + policy enforced for picks. |
| Agent ops | Runs/events/decisions, lease locks, approvals, idempotent txn ledger, tasks, `status`, `publish`. |
| Yahoo | Read-only OAuth API client (untested against live Yahoo). Picks only possible via browser. League settings **unverified**. |
| Dashboard | Next.js on Vercel reading Supabase (see `docs/runbooks/dashboard.md`). |
| DB | Supabase `fantasy-nba` (ref `bdzpkdkhlzjzddkptjex`), schema from `src/fantasy/db/schema.py`. Local agents need `FANTASY_DB_URL` (user must supply). |

## Next priorities (see `fantasy task list` for the full backlog)
1. **P0 (before draft, local session with browser):** verify league settings + actual draft order
   (T-001, T-015), fill Yahoo ADP & positions, load Pre-Draft Rankings (T-014), mock-draft rehearsal (T-002),
   configure `FANTASY_DB_URL` (T-013).
2. P1 after the draft: Yahoo roster sync (T-004), daily lineup optimizer (T-005), waivers (T-006), weekly matchup sim (T-007), calibration (T-016).
3. P2: trade evaluator, backtesting, scheduled morning agent.

## Known limitations
- Cloud sessions can now reach bbref / Yahoo public API / ESPN / CBS (stats.nba.com times out, cdn.nba.com 403s at origin).
  Stats still come from the GitHub mirror of Basketball-Reference (verified identical to bbref.com 2026-10-18).
- Opponent model = league-average team; absolute win probabilities are optimistic.
- Variance priors (`GAME_CV`), ADP sd and `USAGE` are uncalibrated heuristics.
- Quant review (2026-10-18) open concerns: weekly variance ignores schedule (games/week) variance and
  category correlation → overconfident win probs; GP weighted ~linearly (`stream_recovery=0.5` may
  over-penalize long injuries given 4 IL slots); punt scan rarely beats balanced under the cat-wins objective
  (candidates are pre-filtered by balanced value); recommendation margins < ~0.03 are noise; sim edge vs BPA is in-sample.

## How to validate work
`pytest` (all offline, synthetic data) · `ruff check src tests` · `cd web && npm run build` ·
end-to-end: `FANTASY_DB_URL=sqlite:///tmp/x.db fantasy draft init --draft-id mock-1 --mock && fantasy draft recommend`.
