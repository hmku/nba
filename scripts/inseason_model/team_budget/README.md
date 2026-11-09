# T-022 / T-023 backtest (team minute budget, population prior), 2026-11-09

Rolling-origin backtest on 2024-25 and 2025-26 with the production `inseason.update_projections` at 5 checkpoints
(first game −1 day, +13, +27, +55, +97 days). Outputs go to `var/team_budget_bt/` (not committed).
Results: `docs/research/minutes_model.md` ("Team minute budget") and `docs/research/inseason_updates.md`
("Players without a projection row").

```bash
cd scripts/inseason_model/team_budget
python prep.py      # production-like priors: project_core(T) on bbref T-3..T-1 (repo totals start 2022), 450 rows,
                    # 50/50 blend with ESPN preseason projections, ESPN's preseason team
python fitpop.py    # population prior constants per season (pop.json)
python bt.py        # base runs (+ T-023 n0 multipliers 0.25/0.5/1/2, early pre-budget grid)
python bt2.py       # preseason budget grid with leave-one-season-out u, league-wide control
python bt3.py       # T-023: n0 multipliers 0.02-0.25 x availability share 0.40/0.5/0.6
python ev.py t022b  # T-022 tables;  python ev.py t023 / t023b  # T-023 tables
```
