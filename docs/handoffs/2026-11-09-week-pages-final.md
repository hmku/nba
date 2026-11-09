# 2026-11-09 — past weeks final on the dashboard

- Bug: `/week?week=N` and the week strip showed the last mid-week Yahoo read (W2 "4–5 as of Nov 1 7:14 AM", ended 3–6) and pre-week odds, because the Monday step saved the review but never the final `matchup_score`.
- Backfilled: final Yahoo reads W1 6–3 (`ms-w1-2026-11-09-44d6e4ba27de`), W2 3–6 (`ms-w2-2026-11-02-5f32af2bbe00`), W3 6–3 (`ms-w3-2026-11-09-aa49bbe09498`); reviews `wr-w1-c632e0e94767`, `wr-w3-49cd659dd208` (W2 final `wr-w2-c1fff5530bef` already there). Read-only Yahoo run `run-a2417c5d1dd0468f9a1cee53e1ed9623`.
- Web: finished weeks show the result (strip "W 6–3"/"L 3–6", header Won/Lost, final totals, no odds); rule final read > week_review > provisional ("4–5?"), dashboard_data.md §7.
- Upkeep: new `fantasy review backlog` (completed weeks missing a final score or complete review); daily-routine 1.7 now runs every day and saves the final score with `league score --save` (context updated: daily-routine skill, in-season-ops map, CLAUDE.md).
