# Runbook: dashboard (web/)

Next.js 15 (App Router, TypeScript, Tailwind v4) dashboard reading Supabase. Deployed on Vercel as
project **fantasy-nba** (team hku-projects), git-connected to `hmku/nba` with root directory `web`.

- Production URL: https://fantasy-nba-ruby.vercel.app (also `fantasy-nba-hku-projects.vercel.app`)
- Vercel Authentication (SSO) is on for `*.vercel.app` URLs: open them while logged in to Vercel
  (team member), or add a custom domain / relax protection in Project → Settings → Deployment Protection.
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
- `/` Overview: active draft (default = most recent non-mock), picks made / on the clock / my next,
  latest model runs by kind + data snapshots (stale ages in amber), agent runs, held locks, pending
  approvals, open tasks by priority, derived warnings. Auto-refresh 20s.
- `/draft` **Draft room** (`?id=<draft_id>` to pick a draft; selector includes mocks). Polls
  Supabase from the browser every 4s (pauses when tab hidden; refreshes on focus); checks for a new
  valuation run every 30s.
  - Header: on the clock, my next pick + picks until, upcoming pick chips, sync status dot.
  - Draft order computed client-side (`src/lib/draftOrder.ts`): `snake` and `snake_3rr`
    (R1 1→N, R2 N→1, R3 N→1, R4 1→N, then alternating).
  - Model recommendations = latest `draft_recommendations` row (highest `as_of_pick`, newest);
    marked **stale** when `as_of_pick` < picks made. Players drafted since are struck through.
  - **Fallback (client-side)** card appears whenever the rec is missing/stale: best available by
    `player_values.value` + 0.25 × value in my 3 weakest categories + 0.4 if the player fills an empty
    G/F/C group; P(avail) is a rough ADP normal approximation.
  - Agent next action: agent_runs whose `task` contains the draft_id → latest `decision` event,
    latest `decisions` row (disagreement highlighted), recent events.
  - Manual pick entry: overall # defaults to the pick on the clock; search available players (or
    "+" in the table / "select" in fallback list); free-text name allowed. POST `/api/draft/pick`
    writes `draft_picks` with `source='manual'` (409 on conflict → confirm overwrite). Undo removes
    the latest pick only if it is manual.
  - My roster with category profile (Σ cat_values, win% from rec payload if present), strengths /
    weaknesses, position counts.
  - Available players from the latest valuation run that has rows (excludes drafted by player_id or
    normalized name), search + position filter (PG/SG/G/SF/PF/F/C), sortable, cat heatmap.
  - Draft board: rounds × slots, my column highlighted, current pick outlined, non-yahoo sources tagged.
- `/agents` run timeline (expandable events with kind icons), locks, decisions (agent ≠ model
  highlighted), transactions. Auto-refresh 15s.
- `/approvals` pending (approve/reject with note → POST `/api/approvals`) and decided.
- `/players` latest valuation + projections joined; toggle category values / per-game projections;
  sortable; row click opens detail drawer.
- `/history` model runs with params/inputs/summary JSON, recommendations over time, phase-3 placeholders.

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
- Red "sync error" dot on /draft: Supabase unreachable or RLS error; message shown under the header.
  The page keeps the last good state. Use the CLI as a fallback.
- Realtime is not used (tables are not in the `supabase_realtime` publication); polling is 4s.
- Manual pick says "Writes disabled": set the two server-only env vars in Vercel and redeploy.
