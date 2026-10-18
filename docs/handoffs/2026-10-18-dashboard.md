# Handoff 2026-10-18 — dashboard (web/)

## State
- Next.js 15.5 (App Router, TS, Tailwind v4, Geist fonts) dashboard in `web/`, deployed on Vercel
  project **fantasy-nba** (team hku-projects, `prj_0zk5oOnsrQRHumnwfh4sGqq6pH5l`), git-connected to
  `hmku/nba` `main`, root dir `web`, auto-deploys on pushes that touch `web/`.
- Production: https://fantasy-nba-ruby.vercel.app (Vercel Authentication is on for *.vercel.app —
  open it logged in to Vercel, or change Deployment Protection / add a custom domain).
- Verified on production: `/`, `/draft` (real draft `nba.l.44495-2026` + real valuation run with 450
  players, and `mock-ui-demo`), `/agents`, `/history`, `/api/auth` all render 200 with data.
- Runbook: `docs/runbooks/dashboard.md` (local dev, env vars, pages, API, mock cleanup SQL).

## Needs the user
1. In Vercel → fantasy-nba → Settings → Environment Variables (Production), add
   `SUPABASE_SERVICE_ROLE_KEY` (server-only, NOT `NEXT_PUBLIC_`) and `DASHBOARD_PASSPHRASE`, then redeploy.
   Until then the UI is read-only and shows "Writes disabled" (manual picks, approvals).
2. Unlock on each device: "🔒 unlock" in the nav → passphrase (cookie lasts 30 days). Do this on the
   phone **before** the draft.

## Draft-night notes
- `/draft` defaults to the most recent non-mock draft; selector includes mocks (`?id=` in URL).
- Polls every 4s from the browser (no realtime: tables aren't in the `supabase_realtime` publication).
- On the clock = max recorded pick + 1; gaps below it are shown as a warning (enter them via Manual,
  setting Overall #).
- When no recommendation exists or it's stale (`as_of_pick` < picks made), a clearly labelled
  **fallback (client-side)** ranking is shown.
- Team names come from `drafts.config.team_names` ({"1": "...", ...}); otherwise "Team N".

## Mock data (safe to delete; all `mock-` prefixed)
Draft `mock-ui-demo` (14 picks, 1 rec), runs `mock-ui-valuation` (64 values) / `mock-ui-projections`,
agent runs/events/decisions/approvals/transactions/tasks, lock `mock:yahoo:browser`, snapshot
`mock-snap-bbref`. Cleanup SQL in the runbook. Note: the mock lock and mock pending approval show up on
the Overview until deleted.

## Known issues / follow-ups
- Not tested in a real browser here (sandbox can't reach Supabase); verified via server-rendered HTML
  on Vercel. Client polling uses the same supabase-js read path.
- `/draft` HTML is ~400 KB uncompressed with 450 players (compressed over the wire); fine on mobile but
  could be trimmed further by lazy-loading the available table.
- Available players use the latest valuation run with rows, not the rec's `model_run_id`.
- Agent "next action" relies on `agent_runs.task` containing the draft_id.
- Manual pick writes only `draft_picks`; the optimizer must be re-run (`fantasy draft recommend`) to
  produce a fresh recommendation.
- Phase-3 sections (prediction vs actual) are placeholders.
