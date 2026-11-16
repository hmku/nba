# Handoff 2026-11-16 - weekly context review (scheduled, unattended, repo only)

## Fixed
- `docs/STATUS.md`: latest sim -> `sim-20261115123114-3ca9c8` (85.9 / 23.2 %, exp. rank 3.46); priority 2 roster state as
  of 11-15 (VanVleet back Mon -> infeasible roster, drop via `sim compare --drop`; week 4 6-3 into the last day);
  priority 3 notes week outlook = exp. cats won (user decision 2026-11-15).
- `.claude/skills/weekly-audit/SKILL.md`: "read last week's `weekly_audit_*.md`" — none exists yet; pointed to
  `bias_audit_2026-11-09.md` for the first run (today 09:30).

## Proposed (needs the user)
1. `week-review-0.1.0` not bumped although 9ab8e19 (11-15) changed its output (exp_cats fields, new headline, skips
   `covers_week: false` plans). Model-eval rule says outputs are versioned -> bump to 0.2.0 next time that file is touched.
2. Still open from 11-02/11-09: in-season-ops approvals wording should say "trades" explicitly; two approvals per move;
   daily logs 27-35 lines this week vs the 25-line rule (every day over).
3. Housekeeping: 6 stale worktrees in `.claude/worktrees/`; USER_TODO "CI mypy scope" still open (ci.yml unchanged).

## Checked and fine
- `tests/test_doc_commands.py` green; quoted versions match the code (lineup-0.4.1, season-sim-0.8.1, inseason-0.3.2,
  opportunity-0.1.1, ros-proj-0.6.0+pop-prior-0.1, advice-0.3.0, recommend-0.2.0; older tags mark feature arrival).
- Paths in context exist (unmatched ones are prefixes/templates or outputs the weekly audit creates).
- Scheduled tasks: 6 tasks, descriptions match the context times (daily 07:00, tip-off 18:30 / 20:45 / Sat-Sun 14:30,
  context review Mon 05:30, weekly audit Mon 09:30 ET); prompts name a skill/section; no P(win) wording. No mismatches.
- Exp.-cats decision (11-15) reached CLAUDE.md, daily-routine, dashboard runbook, memory; no stale "P(win the week)"
  reporting language left in skills/runbooks. FANTASY_DB_URL lesson is in CLAUDE.md.
- No unrecorded lessons in the 11-10..11-15 daily logs.

## Tests
`pytest -q` pass, `ruff check src tests` clean, CI mypy clean.
