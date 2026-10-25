# Handoff 2026-10-25 - P(title) up / P(playoffs) down anomaly (main session, local)

## What was accomplished
- Diagnosed (subagent, 24k paired sims): no bug. Title gains for some adds = fantasy playoff-week schedule (we're a
  likely bye seed; weeks 19-20 games: UTA 3+3 vs DAL/ATL 4+4) plus noise at 4000 sims (SE ≈ 0.55 pp). "Add with no
  drop hurts" = compare baseline is a replacement-level pickup (IL replacement) and/or the roster cap releases someone.
- `sim compare` output `notes` (report.py `roster_notes`/`compare_notes`); monotonicity-style tests.
- `il_rep_availability` fitted 0.84 (was 0.85 prior; indistinguishable) → season-sim-0.8.1; fit script
  `scripts/il_rep/`, note `docs/research/il_replacement.md`. Quant-reviewed; review trims applied (single home for
  numbers, leaner report, notes formula matches engine).
- Skills: season-sim (title deltas need ≥ 3 seeds × 8000; schedule effect; read `notes`), daily-routine §2 pointer.
- Re-sim `sim-20261025173616-cc4acb`: P(playoffs) 86.9 ± 0.3 %, P(title) 27.6 ± 0.4 % (morning 86.8 / 27.4).

## Context updated (learn from mistakes)
- My first diagnosis (0.74 games share) compared the wrong quantity (full-season share incl. pre-pickup absences);
  recorded in il_replacement.md. Lesson: define the estimand from the engine's use before fitting.

## Not done / next
- `inseason stream` sorts by raw P(title) delta (streamscan.py ~355): noisy ranking; consider sorting by a
  lower-variance metric. Not changed (out of scope).
