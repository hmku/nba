# 2026-11-16 Rival overperformance + draft adverse selection + pickup check

**Answer:** Ed (4-0) leads the league on players beating draft projections (+0.8 z/g; Coward, Bam, Dosunmu and Ajay
Mitchell gained minutes). Waiver Warrior's gains come from pickups. Our drafted players are at −1.3 z/g.
Model-vs-ADP disagreement predicts minutes loss (−0.19 ± 0.05 mpg per 10 spots, n=270), and we bought the most
disagreement. No pickup clears the `helps` rule tonight.

## Done
- `docs/research/adverse_selection_2026-11-16.md`: regressions, out-of-sample check, news check of our roster, recommendation.
- **Context updated:** `.claude/skills/draft-prep/SKILL.md` now carries the adverse-selection check: research role
  news for picks more than 30 spots ahead of ADP.
- **Task T-030** (priority 3): add a draft-time market-minutes prior and a preseason role source before the next draft.
- **No in-season model change:** the signal fades as the minutes model catches up (11-09 checkpoint −0.03 ± 0.06).
  T-027 is the in-season lever.
- **Stream check for 2026-11-17** (`inseason stream`, FA capture 11-16 13:42Z, adds-used 1, no `--accumulated`):
  recommend `[]`, week_only `[]`.
  - Week-only gains come only from dropping Leonard (+0.3 ΔE[cats]), at about −2 pp P(title). Not done.
  - +Mamukelashvili (waiver, Nov 19) for Sheppard: P(title) +3.5 ± 0.8 pp, P(playoffs) −0.9 ± 0.5.

## Sim bug fixed: season-sim-0.9.0 (per-game lineup and release order)
- **Cause:** the engine ordered roster rows by season `value`, which charges projected absences. Leonard (gp 27.4,
  season value 0.71 = our lowest; per-game 2nd best) was last in lineup priority and was the player the roster cap
  released on his return (54% of sims).
- **Fix:** `compute_values` adds `value_per_game`; `engine.priority_values` is used for row order in the engine and in
  `inseason/possim.py`. `tradeeval.pre_trade_league` no longer picks an Out player as the fallback drop.
- **Evidence** (4000 paired sims): drop Ware with no add went from P(title) +4.1 ± 0.6 pp to −0.7 ± 0.5 pp, and
  P(playoffs) from −1.6 ± 0.4 to −3.6 ± 0.4. The cap now releases Queen.
- **Season re-sim** (12k, same rosters as tonight's 0.8.1 run `sim-20261116234434-82fdc3`):
  `sim-20261117040918-777268`, P(playoffs) 84.7 → 85.7%, P(title) 19.2 → 22.0%.
- **Week plan:** not re-run. `lineup plan` has its own per-game ranking and does not use the changed code.
- **Checks:** quant-reviewer OK; new test `test_roster_cap_keeps_injured_star_releases_worst_per_game` (fails under
  the old order); pytest, ruff and mypy green.
- **Context updated:** `season-sim` skill (sanity check: a drop with no add must not raise P(title) or P(playoffs)),
  `il_replacement.md`, `season_simulator.md`, version strings in STATUS / ARCHITECTURE / in-season-ops / league-loop.
- **Follow-ups:** T-031 (trade screen and lineup command priority; Out/IL players among default drop candidates).

## Open
- **Leonard's in-season gp rate (27.4 of 82) looks pessimistic** against news of a late-November return. Check
  ros-avail and the manual override.
- **In-season file has VanVleet `out`** (11-16). He was cleared today; the next morning's rebuild should fix it.
  Sheppard's projected mpg (23.1) still assumes VanVleet is out.
