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
| `AGENT_API_TOKEN` | **server-only**, user must add in Vercel | bearer token for the external-agent API `/api/agent/*` (Meta Muse); unset → those routes return 503 |

Without the two server-only vars the UI shows "Writes disabled (read-only)" and all write
buttons are disabled; reads keep working. After adding them in Vercel, **redeploy** (env vars
only apply to new deployments).

## Pages
Navigation: 4 primary tabs — **Draft**, **Matchup**, **League**, **Activity** (pending-approval count badge, polled
every 30s) — as a bottom tab bar on phones and a top bar on desktop; **More** holds Players, History and the theme
toggle. Write unlock ("unlock" / "read-only" / "● unlocked") sits top right. `/` redirects to `/draft`, or to
`/matchup` once the real (non-mock) default draft is complete (status `complete` or all picks recorded).
`/agents` and `/approvals` redirect to `/activity`.

**Theme**: default *auto* = light 07:00–19:00 in the viewer's local time, dark otherwise (inline `<head>` script
avoids a flash; `ThemeAuto` re-evaluates every 10 min and on tab focus). More › Theme cycles auto → light → dark,
persisted in `localStorage.theme` (absent = auto).

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
  G-score valuation, category-win model, rollout optimizer + simulated-room availability (analytic ADP model as the older alternative) + value search + 3RR, draft sim, season sim, caveats.
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
    clock; search covers drafted players too, flagged "drafted #N"; a typed name is resolved server-side like
    `/api/agent/pick` — ambiguous/unknown → candidates, an unlisted name only after "Record as unlisted";
    409 `reason: pick_taken` → confirm overwrite, `duplicate_player` → never; undo latest manual pick). "+" in the
    available table / "pick" in the fallback list open the sheet preselected.
  - Agent one-liner (only when an agent run's `task` contains the draft_id) → expands to latest
    decision event, latest `decisions` row (disagreement highlighted amber), recent events.
  - **Your roster**: compact list + 9 category chips (green = strength, red = weakness; model weekly win%
    ≥60%/≤40% when the rec payload has `team_profile`, else top/bottom 3 of Σ cat values).
  - **Draft board** (collapsed): your upcoming picks, the last 8 picks, and "Full board grid" (rounds ×
    slots, my column highlighted, current pick outlined, non-yahoo sources tagged).
  - **Available players** (collapsed, lazily rendered): search + position filter, sortable, cat heatmap.
  - Draft order: read from `drafts.config.order_slots` (team slot of every overall pick, written by Python's
    `fantasy draft init|set-order`; re-run `draft init` on an older draft to backfill it). Fallback when absent
    or invalid: computed client-side (`src/lib/draftOrder.ts`, mirrors `fantasy/draft/order.py`): `snake`,
    `snake_3rr` and `custom` (`drafts.config.custom_rounds`, honoured whenever valid).
### `/matchup`
Latest `season_sim` row with `params.command = 'season'` and no `scenario` or a `post-draft…` scenario (i.e. `fantasy sim
season` on real or mid-draft rosters; post-draft schema: `dashboard_data.md` section 3; pages read the newest 60
`season_sim` rows so what-if `compare` runs cannot crowd it out) → `summary.my_team.weekly_matchups[]` (`week, start, end, opponent, opponent_name, p_win, p_tie,
p_loss, exp_cats_won, my_player_games, opp_player_games, cat_win_probs{cat}`; optional `categories{cat:{p_win,
me_mean, opp_mean}}` and `recommendations[]` strings are rendered if a future producer adds them). Week strip
(P(win) per week; `?week=N`), header (week, dates, this week/next up, me vs opponent; names from
`drafts.config.team_names` of the sim's draft), P(win) + W/T/L bar, expected cats, player-games each side, per-category
table (totals = this week's projection if present, else each team's `team_profiles[t].weekly_mean` labelled "avg /
week"), "What to do" (chase 30–70%, safe ≥80%, concede ≤20%; streaming adds pending T-004/T-006), season P(playoffs)/P(title).
**Empty state** (no such run, i.e. before the draft) explains what will appear and links to the pre-draft projection.
Post-draft additions: an amber "Opponents are assumed" banner while `summary.matchup_schedule.assumed_weeks` is non-empty
(header says "assumed opponent (round robin)"); **vs every opponent** table for the selected week from
`my_team.weekly_h2h_matrix` (P(win) this week, season-average P(win) from `vs_opponents_all_play`, 9 category-odds heat
cells; toughest first); tapping a team opens `?week=N&opp=T` = that week against that team (what-if, e.g. Yahoo's real
opponent). Names: `drafts.config.team_names`, else the sim's own team names.

### `/league`
Uses the post-draft `season` run if it is newer than the pre-draft projection (badge "post-draft · actual rosters"),
else the pre-draft projection (labelled "pre-draft projection"; teams shown as draft slots, "Me" highlighted). Standings
columns: exp. rank, P(playoffs), P(bye) (top-2 seed), P(title); an amber note while the matchup schedule is assumed. Projected standings (exp. rank,
P(playoffs) with bar, P(title); ± SE in tooltips); category heatmap from `team_profiles` (rank 1–12, colour = z) or,
pre-draft, my category strip from `my_outlook.category_profile`; rosters from `draft_picks` by `team_slot` (default
draft, or the sim's draft post-draft; names from `drafts.config.team_names`), else the sim's rosters, else a pre-draft
note with one simulated roster for me.

- `/agent` (public, not in the nav): instructions for Meta Muse — see "External-agent API" below.
- `/activity`: health strip (age of latest projections / values / draft sim / season sim, ⚠ if stale → History;
  stuck "running" runs with heartbeat >10m and expired locks shown in amber); with no runs, an explanatory empty
  state (runs, decisions vs model, approvals, transactions); pending approvals pinned at top (approve/reject with note → POST `/api/approvals`),
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

## External-agent API (`/api/agent/*`, bearer token)
For the user's outside assistant **Meta Muse** (reads the league's Messenger group chat). Instructions for Muse:
public page **`/agent`** (source `web/src/content/muse.md`, copied verbatim to `docs/integrations/muse.md`; a test
keeps them identical). Auth: `Authorization: Bearer $AGENT_API_TOKEN` (sha256 + `timingSafeEqual`; 401 + 400 ms delay on
mismatch; 503 `{"error":"AGENT_API_TOKEN not configured"}` when unset). All responses JSON with `ok`. Reads use the
anon client; writes need `SUPABASE_SERVICE_ROLE_KEY` (503 otherwise). Code: `src/lib/agentPure.ts` (pure, unit-tested by
`npm test`), `src/lib/agentApi.ts` (server-only), `src/app/api/agent/**`.
- `GET /api/agent` — public machine-readable endpoint list.
- `GET /api/agent/state` — active **non-mock** draft (newest not complete; `mock-*`/`is_mock` never exposed): my slot,
  on the clock (= highest recorded + 1, as on /draft), my upcoming/all picks (custom order honoured), last 12 picks,
  gaps; latest rec top 5 + `stale` (same freshness rule as /draft, or all top picks taken); my roster; season outlook
  (newest of post-draft `season` / pre-draft projection: P(playoffs), P(title), current week P(win)); pending approvals
  count; last 10 agent events. `ok:false` (HTTP 200) with `errors` when some reads failed.
- `GET /api/agent/players?q=&limit=&available=1` — terms match team/position exactly or name/id substring.
- `POST /api/agent/pick {overall?, player, note?, allow_unknown?}` — records an observed pick, `source='external_agent'`.
  Same player at same pick → 200 `unchanged`; pick or player already recorded → 409 (never overwrites); name resolution
  mirrors `fantasy/draft/names.py` plus Yahoo "V. Wembanyama" and unique ≥5-char substrings; ambiguous/unknown → 422
  with `candidates`; `allow_unknown` records `unknown:<canonical id>` exactly like `fantasy draft pick --allow-unknown` (same id on both sides,
  so the same unknown player is caught as a duplicate). Default `overall` = lowest
  unrecorded pick. Also logs an `observation` (`payload.report_kind = "observed_pick"`) in the muse run.
- `POST /api/agent/snapshot {kind, payload, week?, observed_at?, source_url?, summary?}` — stores a read-only
  observation of a Yahoo league page in `league_snapshots` (validated, hashed, deduped on kind+week+payload sha;
  size and hourly limits in `src/lib/agentPure.ts`). `GET /api/agent/snapshots?kind=&since=&limit=&payload=1` — what
  has been sent. Agents read them with `fantasy intel ... [--rest]`.
- `POST /api/agent/report {kind, message, details?, source?}` — kinds `draft_order|trade|rule|injury_news|schedule|
  chat_note|other`; message trimmed to 2000 chars, details ≤ 8000 chars JSON. Upserts the day's run
  `muse-YYYY-MM-DD` (US/Eastern; agent `muse`, mode `dry_run`, status `running`, heartbeat refreshed) and inserts an
  `agent_events` row kind `observation`, `event_id muse-ev-<hex>`, payload `{report_kind, source, details, reported_by}`.
- Rate limit: ≥ 60 muse events in the last hour (reports + observed picks, counted in the DB) → 429.
- Activity shows a "League intel from Muse" list (kind badge + source badge) and badges on those events in the
  timeline; the muse run is excluded from the "stuck running" warning (it only heartbeats on reports).
- Revoke: change/delete `AGENT_API_TOKEN` in Vercel and trigger a build (push touching `web/`).

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
