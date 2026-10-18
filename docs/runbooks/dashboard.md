# Runbook: dashboard (web/)

Next.js 15 (App Router, TypeScript, Tailwind v4) dashboard reading Supabase. Deployed on Vercel as
project **fantasy-nba** (team hku-projects), git-connected to `hmku/nba` with root directory `web`.

- Production URL: https://fantasy.harrisonku.com (custom domain, Vercel DNS)
- Fallback: https://fantasy-nba-ruby.vercel.app (also `fantasy-nba-hku-projects.vercel.app`)
- Vercel Authentication (SSO) is on for `*.vercel.app` URLs: open them while logged in to Vercel
  (team member); the custom domain is exempt ("all except custom domains"). Or relax protection in Project → Settings → Deployment Protection.
- Every push to `main` that touches `web/` deploys to production (ignored build step:
  `git diff --quiet HEAD^ HEAD -- .`).

## Local dev
```bash
cd web
cp .env.example .env.local   # public values are pre-filled; add server-only ones to test writes
npm install
npm run dev                  # http://localhost:3000
npm run lint && npm run build  # what CI runs (plus npm ci)
```
The public Supabase URL/publishable key are also hardcoded as fallbacks in `src/lib/env.ts`.

## Environment variables
| Name | Where | Purpose |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | public (set in Vercel) | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | public (set in Vercel) | publishable key; RLS = read-only SELECT |
| `SUPABASE_SERVICE_ROLE_KEY` | **server-only**, user must add in Vercel | enables writes (manual picks, approvals) |
| `DASHBOARD_PASSPHRASE` | **server-only**, user must add in Vercel | passphrase to unlock writes (httpOnly cookie, 30 days) |

Without the two server-only vars the UI shows "Writes disabled (read-only)" and all write
buttons are disabled; reads keep working. After adding them in Vercel, **redeploy** (env vars
only apply to new deployments).

## Pages
Navigation: 4 primary tabs — **Draft**, **Matchup**, **League**, **Activity** (pending-approval count badge, polled
every 30s) — as a bottom tab bar on phones and a top bar on desktop; **More** holds Players, History and the theme
toggle. Write unlock ("unlock" / "read-only" / "● unlocked") sits top right. `/` redirects to `/draft`, or to
`/matchup` once the real (non-mock) default draft is complete (status `complete` or all picks recorded).
`/agents` and `/approvals` redirect to `/activity`.

Model-run readers live in `src/lib/sim.ts` (defensive: missing fields → section hidden, never a crash); shapes per
`docs/research/dashboard_data.md`. Charts are dependency-free HTML/CSS primitives in `src/components/viz.tsx`
(category colour = family: shooting %, scoring, big-man, guard, TO — always next to a text label).

### `/draft` — three sub-views (segmented control at the top; `?view=`)
- **Live room** (default, `?id=<draft_id>`): unchanged draft room (see below).
- **Model & sims** (`?view=model`): latest `model_runs` row of kind `draft_sim` and the latest `season_sim` whose
  `summary.scenario = 'pre-draft projection'` (or `params.command = 'preseason'`). Sections: optimizer-vs-BPA edge
  (mean ± paired SE, "N SE"/"not significant", P(optimizer better), exp. category wins & P(win a week) for both);
  season outlook (P(playoffs)/P(title) ± SE, exp. rank, range across simulated drafts, final-standing histogram,
  my category ranks strip); category profile (mean dot, p10–p90 band, BPA tick per category); who I take at each of
  my picks (top player + share bar, next 3); availability table (P(available) at my first 6 picks, sticky name
  column, first 15 then "show all"); strategy scan; example rosters (best/median/worst); caveats + warnings. Text
  comes from the rows' own `explanations` ("What these numbers mean" expanders).
- **How it works** (`?view=how`): static explainer (`src/app/draft/HowItWorks.tsx`) — league basics, projections,
  G-score valuation, category-win model, rollout optimizer + ADP availability + 3RR, draft sim, season sim, caveats.
  Facts come from STATUS / research notes; update it when model versions change.

Live room details:
- `/draft` **Draft room**, mobile-first (`?id=<draft_id>`; small selector at the top includes mocks).
  Polls Supabase from the browser every 4s (pauses when tab hidden; refreshes on focus); checks for a
  new valuation run every 30s. Top to bottom:
  - One-line sync status (green/red dot + age); poll errors as one red line; sync gaps as one amber line.
  - **Primary card**: "On the clock: #N (Team) · your next pick #M in K picks" (or "You're on the clock",
    "Draft not started", "Draft complete"); the ONE top pick large (name, team, positions, reason,
    "X% chance he's still there at your pick #P"); the next 2 alternatives as compact rows; one subtle
    line saying where it came from (model as of pick X / stale → client-side fallback / no rec).
    "Why / all options" expands to the full list with category-impact strips, score/value/marginal/ADP,
    strategy, payload warnings, rec/run ids; in fallback mode it shows the fallback formula + list
    (with "pick" buttons) and the last (stale) model rec underneath.
  - Model rec = latest `draft_recommendations` row; **fresh** iff `as_of_pick >= picks made` and
    `for_pick >= on-the-clock`. Otherwise (or if every rec'd player is taken) the **fallback
    (client-side)** ranking is primary: value + 0.25 × value in my 3 weakest categories + 0.4 if the
    player fills an empty G/F/C group; P(avail) is a rough ADP normal approximation. For model recs the %
    is the payload's `prob_available_next` (my pick *after* `for_pick`); for fallback it is P(available
    at my next pick).
  - **✎ Enter a pick** button opens a bottom sheet (manual pick: overall # defaults to the pick on the
    clock, search available or free text, 409 → confirm overwrite, undo latest manual pick). "+" in the
    available table / "pick" in the fallback list open the sheet preselected.
  - Agent one-liner (only when an agent run's `task` contains the draft_id) → expands to latest
    decision event, latest `decisions` row (disagreement highlighted amber), recent events.
  - **Your roster**: compact list + 9 category chips (green = strength, red = weakness; model weekly win%
    ≥60%/≤40% when the rec payload has `team_profile`, else top/bottom 3 of Σ cat values).
  - **Draft board** (collapsed): your upcoming picks, the last 8 picks, and "Full board grid" (rounds ×
    slots, my column highlighted, current pick outlined, non-yahoo sources tagged).
  - **Available players** (collapsed, lazily rendered): search + position filter, sortable, cat heatmap.
  - Draft order computed client-side (`src/lib/draftOrder.ts`, mirrors `fantasy/draft/order.py`):
    `snake`, `snake_3rr` and `custom` (`drafts.config.custom_rounds`, honoured whenever valid).
### `/matchup`
Latest `season_sim` row with `params.command = 'season'` and no `scenario` (i.e. `fantasy sim season` on real or
mid-draft rosters) → `summary.my_team.weekly_matchups[]` (`week, start, end, opponent, opponent_name, p_win, p_tie,
p_loss, exp_cats_won, my_player_games, opp_player_games, cat_win_probs{cat}`; optional `categories{cat:{p_win,
me_mean, opp_mean}}` and `recommendations[]` strings are rendered if a future producer adds them). Week strip
(P(win) per week; `?week=N`), header (week, dates, this week/next up, me vs opponent; names from
`drafts.config.team_names` of the sim's draft), P(win) + W/T/L bar, expected cats, player-games each side, per-category
table (totals = this week's projection if present, else each team's `team_profiles[t].weekly_mean` labelled "avg /
week"), "What to do" (chase 30–70%, safe ≥80%, concede ≤20%; streaming adds pending T-004/T-006), season P(playoffs)/P(title).
**Empty state** (no such run, i.e. before the draft) explains what will appear and links to the pre-draft projection.

### `/league`
Uses the post-draft `season` run if it is newer than the pre-draft projection, else the pre-draft projection
(labelled "pre-draft projection"; teams shown as draft slots, "Me" highlighted). Projected standings (exp. rank,
P(playoffs) with bar, P(title); ± SE in tooltips); category heatmap from `team_profiles` (rank 1–12, colour = z) or,
pre-draft, my category strip from `my_outlook.category_profile`; rosters from `draft_picks` by `team_slot` (default
draft, or the sim's draft post-draft; names from `drafts.config.team_names`), else the sim's rosters, else a pre-draft
note with one simulated roster for me.

- `/activity`: health strip (age of latest projections / values / draft sim / season sim, ⚠ if stale → History;
  stuck "running" runs with heartbeat >10m and expired locks shown in amber), pending approvals pinned at top (approve/reject with note → POST `/api/approvals`),
  then the agent run timeline, one line per run (status dot, agent, task, status, age; "≠ model" and
  error counts flagged; runs with a disagreement tinted amber). Tap to expand: summary, decisions,
  events (kind icons, payloads), transactions, ids. Held locks as one line; past approvals and
  decisions/transactions not tied to a listed run under a collapsed expander. Auto-refresh 15s.
- `/players` (More): latest valuation + projections joined; toggle category values / per-game
  projections; sortable; row click opens detail drawer.
- `/history` (More): latest model run per kind + data snapshots per source (freshness, moved from the
  overview), model runs with params/inputs/summary JSON, recommendations over time, phase-3 placeholders.

## API routes (server-only, service key)
- `GET /api/auth` → `{writesEnabled, authed, reason}`; `POST {passphrase}` sets cookie; `DELETE` logs out.
- `POST /api/draft/pick {draft_id, overall_pick, player_id?, player_name, overwrite?}`;
  `DELETE /api/draft/pick?draft_id=&overall_pick=` (manual rows only).
- `POST /api/approvals {approval_id, decision: approved|rejected, note?}` (only pending rows).

## Mock data
UI demo rows are prefixed `mock-` (draft `mock-ui-demo`, runs `mock-ui-valuation` /
`mock-ui-projections`, `mock-run-*`, `mock-ev-*`, `mock-dec-*`, `mock-appr-*`, `mock-txn-*`,
`mock-T-*`, lock `mock:yahoo:browser`, snapshot `mock-snap-bbref`). Delete with:
```sql
delete from draft_recommendations where rec_id like 'mock-%';
delete from draft_picks where draft_id like 'mock-%';
delete from drafts where draft_id like 'mock-%';
delete from player_values where run_id like 'mock-%';
delete from projections where run_id like 'mock-%';
delete from model_runs where run_id like 'mock-%';
delete from agent_events where event_id like 'mock-%';
delete from decisions where decision_id like 'mock-%';
delete from transactions where txn_id like 'mock-%';
delete from approvals where approval_id like 'mock-%';
delete from locks where resource like 'mock:%';
delete from agent_runs where run_id like 'mock-%';
delete from tasks where task_id like 'mock-%';
delete from data_snapshots where snapshot_id like 'mock-%';
```

## Troubleshooting
- Red "sync error" dot on /draft: Supabase unreachable or RLS error; message shown as one red line at the top.
  The page keeps the last good state. Use the CLI as a fallback.
- Realtime is not used (tables are not in the `supabase_realtime` publication); polling is 4s.
- Manual pick says "Writes disabled": set the two server-only env vars in Vercel and redeploy.
