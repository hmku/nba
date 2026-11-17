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

## Open / suspicious
- **Possible season-sim roster-cap artefact.**
  - Dropping a player with no add raises P(title): drop Ware gives +4.1 ± 0.6 pp, while P(playoffs) moves −1.6 ± 0.4
    (`verdicts/compare-drop-kelel-ware_2026-11-16T2335Z.json`).
  - The base sim releases Leonard in about 54% of sims around 2027-02-13. The cap releases the "lowest-value not Out"
    player, which seems to pick an injured star over Sheppard or Ware.
  - Every title delta from `sim compare` and `stream` that involves our roster count is suspect until this is checked
    (`engine._apply_roster_cap`, `docs/research/il_replacement.md`).
- **In-season file has VanVleet `out`** (11-16). He was cleared today; the next morning's rebuild should fix it.
  Sheppard's projected mpg (23.1) still assumes VanVleet is out.
