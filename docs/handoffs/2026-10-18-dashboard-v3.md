# Handoff 2026-10-18 — dashboard v3 (4 tabs, model & sims, matchup, league)

## What changed (web/ only; backend untouched)
- **Navigation**: Draft · Matchup · League · Activity (+ More: Players, History, theme). Activity tab shows a
  pending-approval count badge (anon query every 30s). `/` now redirects: `/draft` before/during the draft,
  `/matchup` once the real default draft is complete. The old "Team" attention page is gone: approvals → badge,
  freshness / stuck runs / expired locks → a health strip at the top of Activity. `/agents`, `/approvals` still redirect.
- **Draft** has a segmented control: *Live room* (unchanged DraftClient: polling, stale/fallback rule, 3RR/custom
  order, manual pick sheet, board/available expanders) · *Model & sims* (`?view=model`) · *How it works* (`?view=how`).
  Model & sims renders the latest `draft_sim` row and the latest pre-draft `season_sim` row
  (`summary.scenario = 'pre-draft projection'`) per `docs/research/dashboard_data.md`; explainer text comes from the
  rows' `explanations`. How it works is static prose sourced from STATUS / research notes / module docstrings.
- **Matchup** (`/matchup`): reads the newest `season_sim` with `params.command = 'season'` and no scenario
  (`fantasy sim season`, real or mid-draft rosters): week strip, P(win) with W/T/L bar, expected cats, player-games,
  per-category table + edge bars, chase/safe/concede rule of thumb. Until such a run exists: an empty state
  explaining what appears after the draft + Yahoo sync. NOTE: the preseason run has no weekly matchups, so the
  "week-1 projection" appears only once someone runs `fantasy sim season` (e.g. mid/post-draft with
  `--complete-rosters`) and publishes.
- **League** (`/league`): projected standings (exp. rank, P(playoffs), P(title)), category heatmap (post-draft:
  every team's rank/z from `team_profiles`; pre-draft: my category strip), rosters from `draft_picks` by `team_slot`
  with `drafts.config.team_names`. Pre-draft it shows the preseason projection labelled "pre-draft projection"
  (teams = draft slots).
- New code: `src/lib/sim.ts` (defensive parsers; aliases tolerated), `src/components/viz.tsx` (HTML/CSS chart
  primitives: ProbTrack, EdgeBar, ShareBar, MiniHist, CatStrip, CatLabel, EmptyState, ExplainMore), category family
  colours as CSS vars in `globals.css`, `CAT_COLOR`/`CAT_GLOSS`/`fmtCat` in `lib/categories.ts`.

- **Theme** (user request): auto = light 07:00–19:00 local, dark otherwise; toggle in More cycles auto → light →
  dark (localStorage). All tabs checked in light mode (`ui2/light-*.png`).
- **Activity** empty state explains what will appear (mock agent rows are being deleted from Supabase).

## Verification
- `npm run lint`, `npm run build` green.
- Rendered against the scratchpad mock PostgREST (`mock/server.js` + `mock/sims.json`; `MOCK_POST=1` adds a
  post-draft `season` run). sims.json holds REAL rows produced locally by `fantasy draft simulate --sims 8` and
  `fantasy sim preseason --leagues 3 --sims 300` (SQLite), plus an earlier `fantasy sim season --complete-rosters`
  run. 390×844 Playwright screenshots in the session scratchpad `ui2/` — no console/page errors.

## Gaps / follow-ups
- Production Supabase had no `draft_sim` / `season_sim` rows at the time of the push → Model & sims, League and
  Matchup show their empty states until the backend publishes (`fantasy publish`).
- Matchup "what to do" is a heuristic on category win probabilities; streaming-add suggestions need T-004/T-006.
  If a producer adds `weekly_matchups[].categories{cat:{me_mean,opp_mean}}` or `recommendations[]`, they render
  automatically; otherwise totals are season-average weekly means (labelled).
- Model view isn't polled (server-rendered on navigation); reload to see a newer sim.
- How it works quotes `optimizer-0.2.0` / `season-sim-0.2.0` and the +0.074 ± 0.006 edge — update with model changes.
