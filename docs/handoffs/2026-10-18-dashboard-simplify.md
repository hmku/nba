# Handoff 2026-10-18 — dashboard simplification (web/)

## What changed (user: "too much going on"; mostly on a phone; draft 2026-10-19 10pm ET)
- **Navigation**: 3 tabs — Draft, Team (`/`), Activity (`/activity`, agents + approvals merged) — as a
  phone bottom tab bar / desktop top bar; "More" menu → Players, History, theme. Sidebar removed.
  `/agents` and `/approvals` now redirect to `/activity`.
- **/draft** rebuilt mobile-first: one primary card (on the clock · my next pick; the ONE top pick large
  with reason and P(still there); 2 compact alternatives; one subtle source/stale line; "Why / all
  options" expander with category impact, scores, strategy). "✎ Enter a pick" opens a bottom sheet (same
  POST/DELETE `/api/draft/pick` logic, 409 overwrite confirm, undo). Agent status is a one-line
  expander. Roster = compact list + 9 coloured category chips (bars removed). Draft board (recent picks;
  full grid behind a second tap) and Available players are collapsed and lazily rendered.
- **Team** (`/`): active-draft card, pending-approval count, one agent line, one freshness line
  (⚠ only when stale), expired locks only if any. Model-run / snapshot tables moved to History; open
  tasks, held-lock list, recent-agents list and the warnings card removed from the default view.
- **Activity**: pending approvals pinned with Approve/Reject; runs one line each, expand for
  events/decisions/txns; disagreements tinted amber; past approvals + orphan records collapsed.
- Unchanged: data contracts (`lib/data.ts`, types), API routes, passphrase unlock, polling (4s / 30s),
  3RR/custom order logic (`lib/draftOrder.ts`), fallback ranking (`lib/fallback.ts`) and the
  stale-rec rule (`as_of_pick >= picks made && for_pick >= on-the-clock`). One behavioural nuance: if a
  fresh rec's players are all taken, the fallback becomes primary (labelled).

## Verification
- `npm run lint` and `npm run build` green (clean `.next`).
- Supabase is blocked from this container (proxy 403), so screenshots used a local mock PostgREST
  (scratchpad `mock/server.js`) serving rows copied from production via the Supabase MCP
  (`mock-ui-demo` draft + real pending draft + real valuation run, timestamps shifted to "now").
  Rendered with Playwright/Chromium at 390×844; no console/page errors.
- Note: running `next dev` and `next build` concurrently in `web/` corrupts `.next`
  ("Unexpected end of JSON input" during "Collecting page data") — stop dev and `rm -rf .next` first.

## Follow-ups
- Activity tab has no pending-approval badge in the nav (Team shows the count).
- Writes still need `SUPABASE_SERVICE_ROLE_KEY` + `DASHBOARD_PASSPHRASE` in Vercel (see USER_TODO);
  unlock on the phone before the draft.
