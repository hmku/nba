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
Navigation: 4 primary tabs — **This week**, **League**, **Inbox** (pending-approval count badge, polled every 30s),
**Draft** — plus **More** (Players, History, theme toggle), as a 5-column bottom tab bar on phones and a top
bar on desktop. Write unlock ("unlock" / "read-only" / "● unlocked") sits top right. `/` redirects to `/draft`, or to
`/week` once the real (non-mock) default draft is complete (status `complete` or all picks recorded).
Old URLs redirect (307, query kept; the browser keeps the `#anchor`): `/matchup`, `/lineup` → `/week`; `/moves`,
`/activity`, `/approvals`, `/agents` → `/inbox` (`src/lib/routes.ts`, wired in `next.config.ts` `redirects()`).

**Snappiness** (keep these when adding pages): every tab has a `loading.tsx` skeleton (`src/components/Skeleton.tsx`),
so `<Link>`'s default prefetch loads it and a tap switches instantly while the server renders. The tapped tab
highlights on press (optimistic, `useOptimisticPath` in `Nav.tsx`; `aria-current` follows the real path). Selected tab =
`--tab-fg` text on a `--tab-bg` pill + an accent indicator bar (WCAG AA in both themes; tokens in `globals.css`).
The Nav badge counts approvals with a bare PostgREST `HEAD` (no supabase-js in the shared bundle — only `/draft` loads it).
Sim pages read model runs in two steps (`loadSimPicks` in `sim.ts`): a live metadata query picks the run(s), then
the full ~75 KB rows are fetched by `run_id` through `cachedClient(3600)` (Next Data Cache; a run row is immutable
for its id). League player names use `cachedClient(300)`. Approvals, activity, advice and the live draft stay uncached.

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
### `/week` — This week (matchup + lineups on one page)
`src/app/week/{page,WeekLineups,LineupGrid}.tsx`; pure helpers `src/lib/week.ts` (score, odds, games left, opponent lineup)
and `src/lib/lineup.ts` (plan/actual grid, timeline); tests `tests/week.test.mjs`, `tests/lineup.test.mjs`. Week picker =
`src/content/league_weeks.json` (Yahoo's schedule, generated from `data/manual/league_schedule.json` by
`node scripts/league-weeks.mjs`; `tests/lineup.test.mjs` fails when they drift); default = the week containing today (ET),
`?week=N` otherwise. Top to bottom:
- **Week strip**: W1…W20 with the season sim's pre-week P(win) against Yahoo's opponent (H2H-matrix cell, else the weekly row).
- **Score header**: me vs opponent (Yahoo names from the league snapshot), categories won now **W–L(–T)** from the newest
  `model_runs` row of kind `matchup_score` for the week (schema `matchup_score/1`, data side; FG%/FT% recomputed from
  makes/attempts, leaders recomputed from the totals, TO lower wins), "as of <observed_at> ET", **Yahoo live ↗** (Yahoo's
  matchup page with `mid1`/`mid2` = the two Yahoo team ids), games played / total per team (Yahoo's games strip at
  capture time: played + remaining), and **P(win week)** with its label: the `lineup_plan` for today
  (else the latest earlier day of the week; never a later day's plan, and never one with `expected.covers_week: false`,
  whose banked totals predate its date) (Monte Carlo ± SE, else analytic; "simulated, lineup model") when that plan carries `expected.cat_win_prob`,
  else the season sim ("simulated, season sim (pre-week)").
- **Category strip**: per category me | opp totals (leader shaded; FG%/FT% with makes/attempts underneath) and the chance
  to win it by Sunday (plan's `expected.cat_win_prob`, given banked stats; else the season sim's weekly odds, column
  labelled "odds"), plus one "Swing: …" line (30–70% categories). **Before the first capture** (no `matchup_score` row):
  a compact 3×3 (phone) / 1×9 grid of projected odds only.
- **Lineups**: **Day** (default) with day chips, or **Week** (my plan-vs-Yahoo grid below). Day view = my lineup next to the
  opponent's (two columns on ≥md; **Me | Them** tabs on phones). Mine = the grid day (✓ Yahoo / ◌ plan / ⚠ mismatch, see
  Grid below); on a day with neither plan nor Yahoo read (e.g. a future week) my roster from the league snapshot. Theirs =
  a `lineup_actual` row with `summary.team` = their slot if the data side records one, else
  `src/content/league_state.json` (labelled "their lineup as of last capture (<time>)"; future days keep those slots with
  that day's game markers from `src/content/nba_schedule.json`). Under each column **Games left** = remaining scheduled
  games (today included) of rostered players not in an IL slot and not tagged O/INJ/NA/SUSP (mine from the plans' `games`
  map, else the schedule by NBA team; theirs from the schedule), plus Yahoo's `games_remaining` when the score row has it.
  **Day notes** under the lineups: decide-at-tip-off items and IL moves of the picked day's plan.
- **Decisions**: the 3 newest timeline items (lineup/IL txns with verification evidence, approvals, Inbox recommendations, logged
  `decisions`) + "Show all N".
- **Other matchups** (collapsed): the old "vs every opponent" table from `my_team.weekly_h2h_matrix` (week P(win), season
  average from `vs_opponents_all_play`, 9 category-odds cells); tapping a team opens `?week=N&opp=T`, a what-if view (score
  hidden, odds and their lineup for that team, banner with a link back).
- **How these numbers are made** (collapsed): score source/time, odds source (plan run id, method, n sims, availability
  assumption — or season sim run id, n sims), games-left definition, where their lineup comes from, season P(playoffs) /
  P(title), plan + sim warnings. Keep explanatory text here, not on the page body.
- **Grid** (Week view; `LineupGrid.tsx`, controlled by the page's day state): rows PG, SG, G, SF, PF, F, C, C, Util, Util,
  BN×3, IL×3; columns = days. Per day the newest `lineup_actual` of my team (rows without `summary.team` count as mine) is
  merged with the plan (`lineup_plan` made that day, else the newest earlier plan's `week_plan` entry): ✓ Yahoo shows the
  planned player (or Yahoo was read and there is no plan), ⚠ Yahoo differs, ◌ planned only. Desktop shows the whole week,
  phones one day with ‹ › / swipe. A one-line "Week so far: used/possible starts · unused · mismatch days" appears only
  when something is off.
- **Speed**: one `Promise.all` round: season sim (`loadSimPicks`, full row via `cachedClient(3600)`), lineup rows for past
  days via `cachedClient(300)` and today/future live, the week's `matchup_score` rows (live; cached once the week is over),
  transactions / approvals / advice / decisions live — column lists only, windowed by the week's dates. The league snapshot
  and NBA schedule are bundled JSON (no query).
- **League snapshot freshness**: `scripts/league-state.mjs` builds `src/content/league_state.json` (every team's players with
  Yahoo slot + injury tag from `data/manual/league_rosters_2026-27.json`, names/NBA teams from the projections CSV) and
  `src/content/nba_schedule.json` (from `data/raw/bbref_schedule/nba_2027_games.csv`). It runs as `prebuild` (keeps the
  committed files when the inputs are missing). Vercel only deploys pushes that touch `web/`, so after
  `fantasy league sync --write` the daily routine should run `cd web && node scripts/league-state.mjs` and commit the
  changed JSON — that refreshes the opponent's lineup and triggers the deploy.

### `/league`
Uses the post-draft `season` run if it is newer than the pre-draft projection (badge "post-draft · actual rosters"),
else the pre-draft projection (labelled "pre-draft projection"; teams shown as draft slots, "Me" highlighted). Standings
columns: exp. rank, P(playoffs), P(bye) (top-2 seed), P(title); an amber note while the matchup schedule is assumed. Projected standings (exp. rank,
P(playoffs) with bar, P(title); ± SE in tooltips); category heatmap from `team_profiles` (rank 1–12, colour = z) or,
pre-draft, my category strip from `my_outlook.category_profile`; rosters from `draft_picks` by `team_slot` (default
draft, or the sim's draft post-draft; names from `drafts.config.team_names`), else the sim's rosters, else a pre-draft
note with one simulated roster for me.

- `/agent` (public, not in the nav): instructions for Meta Muse — see "External-agent API" below.
- `/inbox` — **Inbox** (2026-10-20: merges the former Moves and Activity tabs so a recommendation appears ONCE, with
  its explanation next to the Approve / Reject buttons). Top to bottom, auto-refresh 15s:
  1. amber warnings only when needed: stuck "running" runs (heartbeat >10m; the passive `muse`/`advisor`/`user`/
     `claude` daily runs excluded) and expired locks;
  2. **Needs your yes** — one card per decision (`src/lib/inbox.ts` joins advice items and approvals both ways:
     `summary.approval_id` / `payload.advice_id`; schema `docs/research/dashboard_data.md` §4 "Advice ↔ approval ↔
     thread"). Under the heading ONE policy line: *"Approve = your yes; the next daily run executes it after
     re-checking Yahoo."* (user decision 2026-10-20). Cards are **collapsed by default** (user feedback 2026-10-20:
     "too much text"): type badge + title (1 line on desktop, 2 on phones, full title on hover) + age; the verdict's
     **first sentence** only (`splitVerdict`; the rest moves into Details, so no text renders twice); up to 3
     **key-metric chips** (`key: true` metrics, else the first three; evidence = coloured dot, legend once next to the
     page title; value ± SE and the note on hover); the passphrase-gated **Approve / Reject** (POST `/api/approvals`;
     an optional note behind "add note") with the expiry ("expires in 20h", amber once past). Advice awaiting a yes
     with no approval row yet says so in one line (no buttons). Then two toggles: **Details ▸** (headline + rest of
     the verdict, **Why** bullets, **What changed**, the full metrics table + scenarios, risks, links, the approval
     request text, posted/updated, ids; a client component `AdviceDetails.tsx` that mounts its body on first open, so
     the text travels once as JSON in the RSC payload instead of twice as markup) and **💬 n** (every thread linked to
     the decision — posts that name its advice_id or approval_id, with replies and per-thread Reply — plus the
     **Comment** box when unlocked: POST `/api/threads/comment` `{advice_id|approval_id, stance, text}` → a new user
     thread linked by `details`, shown to Claude in `fantasy intel latest` → `open_threads`). Approval-only cards (no
     advice item) show the approval summary as the title and the payload under Details. Sorted by soonest expiry, then
     newest. "Nothing needs your yes right now." when empty;
  3. **Approved · queued** (only when non-empty): you said yes (approval approved, or advice `approved`) and the move
     is not `done` yet;
  4. **Ideas**: advice with status `idea` (and today's lineup plan): the same card with a one-line verdict snippet and
     the chips, Details and 💬 collapsed;
  5. **Agent updates**: the threaded feed (one panel, a hairline between updates; header "🔒 unlock to see Muse's private
     snapshots and chat digests" when locked); threads shown inside a decision card are left out
     (and out of the filter counts). ONE feed (`src/lib/feed.ts`, schema in `docs/research/dashboard_data.md` §5) of Muse reports
     (incl. `trade_offer` cards: who gives what), advisor suggestions (dashed card: category/urgency/confidence,
     labelled evidence, proposed change), Claude's decisions / verifications / errors and finished run summaries,
     Claude's posts, and, after the passphrase unlock only, group-chat digests (badge "private") and one card with
     Muse's newest Yahoo snapshot per kind (each collapsed). Each update: author (coloured name: Muse / Advisor /
     Claude / You), kind, age (event id on hover), body clamped to 2 lines with "more" (`LongText`: rendered once,
     CSS `:has()` toggle), raw payloads behind "payload"/"details" (`LazyJson`, mounted on open, ≤ 1200 chars),
     evidence lists collapsed to "n evidence items", logged decisions as one line ("lineup · agrees · chose …") + the
     rationale, and its **thread** collapsed to "💬 n replies · stance tally" (replies oldest first with stance agree ✓
     / disagree ✕ / question ? / comment …, "↳ re X" when answering a reply; open inside a decision card's 💬).
     **Paging** (`src/lib/feedPage.ts`, tested in `tests/feedPage.test.mjs`): newest *posted* first, 12 cards per
     page, "← Newer" / "Older →" links carrying a `(timestamp, key)` cursor (`?before=` / `?after=`, base64url;
     `?at=` = the page starting at one update), so pages stay put while new updates arrive; "Jump to newest" on
     older pages; filters ride along. A reply never moves its thread: a root on page k shows all its replies
     however new, and page 1 lists "New replies on earlier updates" (newest 5, each linking to `?at=<root>#<id>`).
     The decision sections and the snapshot card show on the newest page only (older pages show a "N decisions need
     your yes → newest page" link instead). Filter chips by author (started or
     replied in) and by kind, as URL params (`?author=advisor&kind=trade_offer`; a chip resets to the newest page),
     with thread counts over the newest 1000 feed rows (shown "n+" beyond that). When unlocked, a **Reply** button on
     every non-private card posts `{reply_to, stance, text}` to `POST /api/threads/reply` (passphrase cookie, author
     always "user", 30/h shared with comments). Locked: read-only, with a one-line note that private intel needs the unlock;
  6. **Done / rejected** (collapsed): advice done/rejected/expired, decided approvals without open advice and other
     days' lineup plans, newest first, one line each (status, title, time) expanding to the compact card; links to
     **This week → Decisions** (`/week#decisions`, the week-by-week timeline with Yahoo verification);
  7. **System**: model freshness strip (→ History), the agent run timeline **on request** (link → `?timeline=1#runs`,
     streamed via Suspense: one line per run: status dot, agent, task, status, age; "≠ model" and error counts; tap
     for summary, decisions, every event with payloads capped at 600 chars of JSON, transactions, ids), held locks,
     and past approvals / decisions / transactions not tied to a listed run.
  Anchors: each decision card's id is its advice_id (approval-only cards: the approval_id), and every approval id on
  a card is an anchor too, so `/inbox#<advice_id>` and `/inbox#apr-…` work (the This week timeline links there).
  Speed: all independent queries run in one `Promise.all`, explicit column lists (no `select("*")` on the big
  tables). Round 1 reads, besides the system tables and approvals: the newest 300 advice rows, the candidate thread
  roots for the cards (≤ 200 report rows with an explicit `details.advice_id|approval_id` or id-shaped text in
  message/details/evidence: PostgREST `or` + POSIX `match`, then matched exactly in JS), a light index of the newest 1000 feed rows (ids, kinds,
  thread links; facets + "who replied where"), the cursor window of roots (~2 pages, full columns; light columns
  when an author filter needs a 1000-row window), finished run summaries by `ended_at`, the newest 100 replies in
  full and a run-summary count. Round 2 runs only for what is still missing (light-read roots, older replies incl.
  the cards' threads, decision cards older than the newest 100), so the usual page is one round. Local `next start` against production
  Supabase (anon, 2026-10-20, one Inbox load): 0.16 s / 318 KB HTML → **106 KB** after the concise redesign (same
  data, same day: collapsed cards, Details/payloads mounted on open instead of rendered twice as HTML + RSC markup,
  short CSS classes for repeated Inbox elements in `globals.css`, 12 feed cards per page) (was Activity 0.11 s / 153 KB + Moves as a
  separate page). Older Activity numbers (median of 10, 2026-10-20): before 0.22 s / 358 KB HTML, RSC (client tab switch) 0.23 s / 180 KB →
  after 0.075 s / 158 KB, RSC 0.056 s / 84 KB (`?timeline=1`: 0.12 s / 451 KB). Cursor paging (median of 7, same
  day, more data): `?n=` version 0.105 s / 184 KB → 0.11 s / 153 KB (RSC 77 KB); `?author=claude` 0.09 s → 0.17 s
  (two rounds); `?timeline=1` 0.19 s / 468 KB.
  Pure readers: `src/lib/inbox.ts` (join, grouping, thread linkage, feed de-dup; `tests/inbox.test.mjs`),
  `src/lib/moves.ts` (advice parsing; `tests/moves.test.mjs`), `src/lib/feed.ts` / `feedPage.ts`.
- `/players` (More): latest valuation + projections joined; toggle category values / per-game
  projections; sortable; row click opens detail drawer.
- `/history` (More): latest model run per kind + data snapshots per source (freshness, moved from the
  overview), model runs, recommendations over time (top 3 read via JSON paths, not the full payload), phase-3
  placeholders. Model runs: 25 per page (`?page=N`, "← Newer / Older →", kind chips incl. every kind seen), the list
  reads summary columns only (id, kind, version, git sha, time + a one-line blurb from `summary->>title|headline|
  scenario|status|n_sims|n_players`, `params->>command`); each run's params / inputs / summary expanders load on
  open from `GET /api/history/run?id=` (anon key, same public `model_runs` rows; cached 1 h — a run row is immutable
  for its id), with a skeleton while loading. Before: ~100 full rows in hidden expanders = 2.86 MB HTML; after
  165 KB (local `next start` vs production Supabase, median of 7, 2026-10-20: 0.16 s → 0.10 s).

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
- `POST /api/agent/report {kind, message, details?, source?}` — kinds `draft_order|trade|trade_offer|rule|injury_news|schedule|
  chat_note|chat_digest|other`; message trimmed to 2000 chars, details ≤ 8000 chars JSON. Upserts the day's run
  `muse-YYYY-MM-DD` (US/Eastern; agent `muse`, mode `dry_run`, status `running`, heartbeat refreshed) and inserts an
  `agent_events` row kind `observation`, `event_id muse-ev-<hex>`, payload `{report_kind, source, details, reported_by}`.
- Rate limit: ≥ 60 muse events in the last hour (reports + observed picks, counted in the DB) → 429.
- `report_kind: "reply"` (Muse and advisor tokens) `{reply_to, stance, text, evidence?}` — a thread reply (see
  `docs/integrations/threads.md`, schema `docs/research/dashboard_data.md` §5): author from the token, `reply_to` must
  exist (404) and not be chat content (403); filed as `kind 'note'` under the caller's daily run. `kind: "trade_offer"`
  (Muse) needs `details {from_team, to_team, from_gives, to_gives, status?}`.
- `GET /api/agent/state` also returns `recent_updates` (newest 15 replyable updates with their replies) and
  `event_id` on `recent_events`.
- The Inbox shows Muse's reports in the "Agent updates" feed (author + kind + source badges) and badges on those
  events in the timeline; the muse run is excluded from the "stuck running" warning (it only heartbeats on reports).
- `POST /api/threads/reply {reply_to, stance, text}` — the user's reply (passphrase cookie like `/api/approvals`;
  author "user", run `user-YYYY-MM-DD`).
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

## Install as an app (PWA)
The dashboard is an installable web app ("Fantasy Desk"); it opens full-screen at `/` (→ This week after the draft).
- **iPhone/iPad (Safari only):** open https://fantasy.harrisonku.com → Share button → *Add to Home Screen* → Add.
  Unlock writes once inside the installed app (it has its own cookie jar, separate from Safari).
- **Android (Chrome):** open the URL → ⋮ menu → *Install app* (or *Add to Home screen*) → Install.
- **Desktop Chrome/Edge:** open the URL → install icon at the right of the address bar (or ⋮ → *Cast, save and share* →
  *Install page as app*; Edge: ⋯ → *Apps* → *Install this site as an app*).

How it works: `web/src/app/manifest.ts` (→ `/manifest.webmanifest`), icons in `web/public/icons/` generated from
`web/src/app/icon.svg` by `node web/scripts/make-icons.mjs` (re-run + commit if the SVG changes), Apple metadata in
`layout.tsx`, and a minimal service worker `web/public/sw.js` (registered in production only). The SW caches **only**
`/_next/static/*` and `/icons/*`; pages always come from the network (offline → `/offline.html`), and `/api/*`,
Supabase and all other requests are never intercepted or cached, so data is always live. To force-update the SW,
bump `VERSION` in `sw.js`. Safe areas (notch / home indicator) are handled via `viewportFit: "cover"` +
`env(safe-area-inset-*)` padding in `Nav.tsx` and the main container.

## Troubleshooting
- Red "sync error" dot on /draft: Supabase unreachable or RLS error; message shown as one red line at the top.
  The page keeps the last good state. Use the CLI as a fallback.
- Realtime is not used (tables are not in the `supabase_realtime` publication); polling is 4s.
- Manual pick says "Writes disabled": set the two server-only env vars in Vercel and redeploy.
