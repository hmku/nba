# 2026-11-01 — weekly matchup review

- New `fantasy review week [--week N] [--save]` (`week-review-0.1.0`, `src/fantasy/inseason/weekreview.py`): per-cat/per-player actual vs week-start projection for both teams (slot-filtered games, fitted noise z), volume effect, decisions/missed moves, P(win) trajectory. Rows `model_runs.kind='week_review'` (schema: dashboard_data.md §8); page `/week/review` (linked from `/week`).
- Saved to prod: `wr-w1-4266f25237a1` (W1 won 6-3 vs Joshua), `wr-w2-4a89aa11e585` (W2 provisional, through 10-31; Monday run will save the final).
- Daily-routine skill step 1.7: Mondays run it for last week after box scores are fetched.
- Limitations: z uses the per-game noise model (understates weekly var ~14-46% for PTS/FG/REB); captures miss intraday lineup changes (W1 Joshua 23 vs 25 games); expected-games is a prior.
- Open: ledger shows a Fears drop approval still "awaiting execution" and stale Shead/Draymond approvals — the next daily run should reconcile.
