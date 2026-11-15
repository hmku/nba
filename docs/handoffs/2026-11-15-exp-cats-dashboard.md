# 2026-11-15 — Dashboard: expected categories won instead of P(win week)

**User decision:** the week outlook is shown/reported as expected categories won (of 9, ties ½, ± SE), not P(win the week).

## Done
- Web (`/week`): score header "Exp. cats won X.X / 9 ± SE" (today's lineup plan MC, else analytic, else Σ category odds, else season sim `exp_cats_won`);
  week-strip tiles = season sim pre-week exp. cats; Other matchups Wk/Szn = exp. cats, sorted toughest (fewest) first;
  ratio-bench flag shows ΔE[cats] only. `WeekOdds.expCats/expCatsSe`, `sumCats`, `catsText/catsNum` (tone ≥5 good, ≤4 bad).
- Week review: `p_win_trajectory[]` gains `exp_cats`/`exp_cats_se` (field name kept); headline "exp. cats a Mon -> b last plan";
  plans with `covers_week: false` now skipped (same rule as the dashboard). Weeks 1–4 re-saved to Supabase.
- Agentic context: CLAUDE.md rule, daily-routine / in-season-ops / season-sim / model-eval skills, dashboard runbook, advisor.md
  (both copies), memory `feedback-exp-cats-not-pwin`. Decision rules (2·SE tests on ΔP(win)/ΔP(playoffs), `helps`) unchanged.
- Unchanged on purpose: per-category P(win), season P(playoffs)/P(title), draft page "P(win a week)", `/api/agent/state` (has both).

## Mistake found → context updated
- Local Bash shells don't inherit `FANTASY_DB_URL`; `fantasy review week` silently read empty SQLite (0 plans) and `review backlog`
  claimed every week needed review. Added to CLAUDE.md "Environments & data".
- Week 1's trajectory still has a 2.68 point from an old plan saved before `covers_week` existed (unflagged); harmless.

## Next
- Agents' past Inbox cards still say P(win); new ones should use ΔE[cats] ± SE.
