# Handoff 2026-10-25 — why week-1 P(win) went 0.39 -> 0.98 (cloud, read-only analysis)

- User asked whether the jump from Sat 0.39-0.42 to Sun 0.979 (lineup plans `lp-2026-10-24-6e08eba986a4`,
  `lp-2026-10-25-eeb4fca553c6`) means the probabilities are broken. Answer: no bug found; Saturday was a big day.
- Sat (Oct 24) box scores vs the 10-24 in-season projections (8 games for us, 7 for Joshua; matches the Yahoo score diffs
  `ms-w1-2026-10-24-938a42b842e4` -> `ms-w1-2026-10-25-95ddadb672af`): us PTS 151 vs 121 proj, REB 69 vs 53, BLK 10 vs 6,
  3PM 20, FT 23/23, FG 54/104; Joshua ~on projection (PTS 117 vs 123, REB 42 vs 45, BLK 2 vs 4.5). Jokić 44 min/35 pts,
  Reid 23, Ware 19/13, Sheppard 19. Score went 5-4 -> 7-2 (REB and AST flipped; BLK/FT/3PM became locks).
- Sunday 0.98 is consistent: 4 cats ~locked (FT, 3PM, BLK, STL ≥0.98), need 1 of REB .76 / TO .91 / AST .26 / PTS .19.
- Known overconfidence (week-1 review: team-week variance ≈1.7× the noise model, sd ≈1.3×): inflating sd 1.3-1.5× moves
  Sat ~0 pp and Sun 0.98 -> ~0.95-0.96 (independent-cats approximation). Still pending calibration (T-016), no model change.
