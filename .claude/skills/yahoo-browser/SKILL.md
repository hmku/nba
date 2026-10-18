---
name: yahoo-browser
description: How to operate Yahoo Fantasy (league 44495) safely in the user's Chrome via Claude in Chrome - prerequisites, the yahoo:browser lock, read-before-write, independent verification after every action, evidence without secrets, login/2FA handling, known league URLs, and logging browser_action/verification events. Use whenever you need to read or change anything on basketball.fantasysports.yahoo.com (settings, draft order, draft room, pre-draft rankings, rosters, players, transactions).
---

# Operating Yahoo in the browser

Python never touches Yahoo's UI; you do, in the user's real, logged-in Chrome, with the user able to
watch. The Yahoo API client (`src/fantasy/league/yahoo_api.py`) is read-only and cannot draft.
Background: `docs/research/browser_integration.md`, `docs/research/draft_order.md`.

## Prerequisites (check before promising anything)
- **Local** Claude Code session started with `claude --chrome` (or `/chrome` -> Enabled), Claude in
  Chrome extension installed and signed in to the same claude.ai account. Cloud sessions
  (`CLAUDE_CODE_REMOTE=true`) have no browser access to Yahoo: say so and stop.
- Tools are `mcp__claude-in-chrome__*`. If they are deferred, load them with one ToolSearch call
  (`select:` the ones you need, or keyword "claude-in-chrome"). If the `chrome-browser` skill is
  available, read it first for tab handling and dialog caveats.
- Site permission: the user should have chosen "allow all actions on this site" for
  `basketball.fantasysports.yahoo.com` (a permission prompt during a 60 s draft clock is fatal).
- Work in **your own tab** (tab group of this session); don't navigate the user's other tabs.

## Safety rules
1. **Lock**: `fantasy lock acquire yahoo:browser --ttl 1800 --purpose "<what>" --json` before driving
   the browser (exit 3 = another agent is driving: stop and report). Renew for long sessions; release
   at the end. Roster-changing writes also need `yahoo:roster` (or `draft:<id>` for picks) - see `agent-run`.
2. **Read before write**: read the current state of the page (roster, draft log, settings) and
   compare with what the ledger expects *before* any click that changes something.
3. **Writes only inside policy**: a write (draft pick, lineup, add/drop, trade, pre-draft rankings)
   needs a run whose mode and approvals allow it (`agent-run` skill). Reading is always fine in a
   `dry_run` run. Never click buttons unrelated to the task (no chat, no messages to other managers,
   no settings edits, no trade offers, no league joins).
4. **Verify after every write by an independent re-read**: reload / open the page that shows the
   result (draft log, roster page, league transactions) and confirm the exact player/slot. A success
   toast is not verification. Then `fantasy run event verification "..."` and mark the txn.
5. **Never retry blindly**: if a click's outcome is unclear (timeout, page reload, tool error), re-read
   state first. A txn in `submitted` is reconciled, not re-clicked.
6. **Evidence without secrets**: evidence = page name/URL path, the visible text you relied on
   (player, team, pick no.), timestamp. Never store cookies, tokens, session URLs with crumbs,
   e-mail addresses or screenshots of account menus in events, commits or docs. If you take a
   screenshot/GIF for the user, don't commit it.
7. **Login / 2FA / CAPTCHA / "Are you sure?" dialogs**: stop and ask the user to handle it in the
   browser (you cannot and must not enter credentials). JS alert/confirm dialogs block the extension:
   ask the user to dismiss them.
8. If the extension disconnects ("Receiving end does not exist", "No tab available"): ask the user to
   run `/chrome` -> Reconnect, then re-read state before continuing.

## Logging pattern (inside a run)
```bash
fantasy run event browser_action "open settings page" --payload '{"path":"/nba/44495/settings"}'
fantasy run event observation "settings: 12 teams, H2H cats, 9 cats ..." --payload '{...parsed values...}'
fantasy run event browser_action "clicked Draft: Jamal Murray" --payload '{"overall":24}'
fantasy run event verification "draft log shows #24 Jamal Murray -> my team" --payload '{"path":"draft room log"}'
```

## League 44495 pages
Base: `https://basketball.fantasysports.yahoo.com/nba/44495`. Only the base URL is documented in
this repo; the rest are Yahoo's usual patterns and are **UNVERIFIED** - navigate from the league home
menu if a URL 404s, then update this table (and say so in your handoff).

| Page | URL (pattern) | Status | Use |
|---|---|---|---|
| League home | `/nba/44495` | referenced in docs | standings, menu, my team link |
| Settings | `/nba/44495/settings` | unverified | categories, roster slots, draft type/time, waivers, trades, playoffs |
| Draft results / order | `/nba/44495/draftresults` | unverified | pre-draft: may show order; post-draft: all picks |
| Draft room | "Enter Live Draft" button on league home (opens a draftclient window) | unverified | live draft, pick log, player list with ADP/rank |
| Pre-Draft Rankings | league home -> My Team -> "Edit Pre-Draft Rankings" (pattern `/nba/44495/<team_id>/prerank`) | unverified | autopick safety net |
| Draft analysis (ADP) | `https://basketball.fantasysports.yahoo.com/nba/draftanalysis` | unverified | Yahoo ADP / avg round / % drafted |
| Public pre-rankings | `https://basketball.fantasysports.yahoo.com/nba/public_prerank` | mentioned in data handoff, unverified | Yahoo default ranks |
| Players | `/nba/44495/players` (filters: status, position, stat type) | unverified | positions eligibility, ownership, free agents |
| My team | `/nba/44495/<team_id>` (team_id 1..12; find via league home) | unverified | roster, lineup |
| Transactions | `/nba/44495/transactions` | unverified | add/drop/trade log for verification |
| Matchup | `/nba/44495/matchup` | unverified | current H2H matchup |
| Mock draft lobby | Yahoo Fantasy Basketball -> "Mock Draft" (lobby) | unverified | rehearsals |

Commissioner-only pages (Edit Draft Order etc.) are not ours to change.

## Reading tips
- Prefer page text / accessibility tree reads over screenshots (faster, parseable). Copy names exactly
  as shown; the CLI resolves names (`fantasy draft pick` exit 2 lists `candidates`).
- Yahoo positions on player cards are the eligibility to store (e.g. `PG,SG` -> `PG|SG` in our CSVs).
- Injury tags on Yahoo: `INJ`, `O`, `GTD`, `DTD`, `NA`, `SUSP` - record them as observations; injury
  news can change a decision (re-run the model, log the rationale).

## Failure handling
- Page won't load / layout unexpected: reload once, then navigate from league home. Still broken:
  report to the user with what you saw; switch to manual mode for time-critical tasks (draft).
- Something changed that you didn't intend: stop all writes, record an `error` event with what you
  observed, tell the user immediately, mark the txn `failed` (with evidence) if one was involved.
- At the end: `fantasy lock release yahoo:browser --json` (`run end` also releases it, but free the browser as soon as you are done).
