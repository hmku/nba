# Handoff 2026-10-18 - season-simulator calibration (research agent, cloud session)

The previous agent was killed by a container restart at about 19:36 UTC. This session continued its scratch work.
I checked and reused `prep.py`, `game_noise.py`, `proj_backtest.py` and `team_effects.py`, and ran `spells.py` and
the remaining analyses.

## What was accomplished
- **`docs/research/sim_calibration.md`:** every simulator prior is fitted on 2010-24 box scores and bbref totals,
  with SEs. That covers season projection error (out of sample, hold-out 2025-26), game-to-game variance and
  within-game correlation, opponent/pace/home/rest/blowout/positional effects at game, player-week and team-week
  level, and injury spell structure.
- **`season-sim-0.4.0`** (simulator only):
  - `src/fantasy/sim/calibration.py` holds the fitted constants.
  - `SimParams` defaults:
    - season error by projected mpg tier (`vol_sd`, `fg_pct_sd`, `ft_pct_sd` = None → `ERROR_BY_MPG`);
    - per-stat `stat_sds`;
    - `game_var="fitted"` (var = a·m + b·m²);
    - `noise_corr=True`.
  - 0.3.0 is reproducible with `vol_sd=0.08, stat_sd=0.08, fg_pct_sd=0.015, ft_pct_sd=0.03, game_var="cv", noise_corr=False`.
- **The draft model is unchanged.** `fantasy.model.lines` now has `GAME_CV_PRIOR` (the default), `GAME_CV_FITTED`
  and the `FANTASY_GAME_CV=fitted` opt-in, which is read at import and recorded in valuation params. Effect of the
  opt-in:
  - the top 60 is the same set (Spearman 0.997, max move 4);
  - the picks at 1, 24 and 36 are unchanged (Jokić, Kawhi, D. White).

## Key findings (numbers in the research note)
- **Season error.** The priors were 1.3-2.6× too small.
  - vol_sd: 0.08 → 0.207 (0.006) for rostered players (0.15 / 0.25 / 0.33 by mpg tier).
  - FG%: 0.015 → 0.025 (0.001).
  - FT%: 0.03 → 0.039 (0.002).
  - Stat-specific sds range from 0.05 (REB, STL) to 0.37 (3PM).
- **GAME_CV.** It is right for 30+ mpg stars and too small otherwise, because variance is about proportional to
  the mean (STL/BLK are Poisson).
- **Opponent effects.** The opponent explains < 0.5% of player-week and ≈ 0% of team-week variance. Positional
  matchups add nothing out of sample. Games played explains 18-53% of team-week variance.
- **Injury spells.** The `rest_share`/`two_spell_prob` priors reproduce the real run-length mix and the weekly
  missed-games variance (0.79 vs 0.85 ± 0.04). Kept.
- **Effect on outputs.**
  - Weekly matchup probabilities move about 10% closer to 50/50.
  - Pre-draft, my P(title) goes from 0.465 to 0.390 and P(playoffs) from 0.954 to 0.939.

## Decisions & rationale
- The season error is tiered by mpg, not pooled. Errors differ 2× between tiers with tight SEs.
- `noise_corr` is on. The correlation is fitted (SE ≤ 0.01), it moves the simulator's given-games category
  correlation from 0.06 to 0.12 (empirical 0.14), and its effect is small but has the same sign in every test.
- `symmetric.PERTURB_DEFAULT` is not changed. It models opponents' belief divergence, not truth error.
- The fitted GAME_CV is not the draft default (freeze); the lead should ask the user, and the evidence says it does
  not matter for the picks.

## Files changed
`src/fantasy/sim/{calibration.py (new), engine.py, __init__.py}`, `src/fantasy/model/lines.py` (opt-in only),
`src/fantasy/model/valuation.py` (records `game_cv` only when the opt-in is set), `tests/test_sim_calibration.py`
(new, 8 tests), `docs/research/{sim_calibration.md (new), season_simulator.md}`, `docs/STATUS.md` (one line),
`.claude/skills/{season-sim,model-eval}/SKILL.md`.

## Tests performed
- `python -m pytest -q`: all passed.
- `ruff check src tests`: clean.

## Caveats for the next agent
- The season error was fitted on the baseline projections. The blend is about 5-7% more accurate, so the
  simulator's error is slightly conservative.
- Absolute P(win matchup) calibration still needs real fantasy results (T-016, in-season).
- Scratch scripts and data are in the session scratchpad `calib/` and are not committed. The sources are public
  GitHub raw URLs listed in the note.
- Other agents had uncommitted changes in the working tree (`src/fantasy/cli.py`, `ops/intel*`, db schema). I did
  not commit them.
