---
name: in-season-ops
description: PLACEHOLDER for Phase 2 (in-season management after the 2026-10-19 draft) - states honestly what does not exist yet (Yahoo roster sync, lineup optimizer, waiver/streaming optimizer, trade evaluator; matchup/season simulation now exists via the season-sim skill) and what an agent may do today (read-only inspection and reporting), plus the approval policy for adds/drops/trades. Use when asked to set a lineup, pick up/drop players, evaluate a trade, check the matchup, or run a daily/morning check.
---

# In-season operations (Phase 2 - mostly NOT built yet)

## What does NOT exist yet (do not pretend otherwise)
| Capability | Task | State on 2026-10-18 |
|---|---|---|
| Yahoo roster sync (my roster, all rosters, FAs, transactions -> DB) | T-004 | not built. `src/fantasy/league/yahoo_api.py` has read-only parsers (`parse_roster`, `parse_players`, `parse_teams`) but no sync command and no OAuth tokens yet (T-003). |
| Daily lineup optimizer | T-005 | not built. No `fantasy lineup ...` command. |
| Waiver / streaming optimizer | T-006 | not built. |
| Weekly H2H matchup simulation | T-007 | **built (MVP)**: `fantasy sim season|matchup|compare` — see `.claude/skills/season-sim/SKILL.md`. Needs Yahoo rosters + schedule JSON post-draft. |
| Trade evaluator | T-008 | not built. |
| Scheduled morning agent | T-011 | not built. |
| NBA schedule / games-per-week data | - | ingested from basketball-reference (`fantasy sim schedule`, `data/raw/bbref_schedule/`); fantasy week calendar unverified vs Yahoo. |
Check `fantasy task list --area season` and `docs/STATUS.md` - if any of these became `done`, a newer
skill or runbook should replace this placeholder; update this file.

## What an agent MAY do today
- **Read-only inspection** (local `claude --chrome` session, `yahoo-browser` skill, inside a `dry_run`
  run): read my roster, the matchup page, standings, injury tags, the league transactions log, free
  agents; record findings as `observation` events.
- **Analysis with existing tools**: `fantasy values build --json` for rest-of-season per-player
  category values (season-long, not schedule-aware), `fantasy data top`, projections lookups. Be
  explicit that these ignore schedule, games remaining and the current matchup.
- **Report** to the user: injuries on my roster, players in IL-eligible status not in IL slots, empty
  starting slots on game days, obvious streaming candidates *as suggestions*, with the caveats above.
- **Build the missing pieces** (T-004..T-008) as engineering work, with tests - claim the task first.

## What an agent may NOT do (yet)
- No lineup changes, adds, drops, claims or trades on Yahoo without an explicit user instruction for
  that specific action in this session. There is no optimizer to justify them.
- Never accept/propose/counter trades or message other managers.

## Approval policy (applies now and after Phase 2 lands)
`src/fantasy/ops/policy.py` + `CLAUDE.md` safety rules:
- `lineup`: allowed in a `live` run holding `yahoo:roster` (no approval) - once a lineup optimizer
  exists. Until then only on explicit user request.
- `add`, `drop`, `add_drop`, `trade` (and any unknown kind): **always** need an approved human approval:
  ```bash
  export FANTASY_RUN_ID=$(fantasy run start --agent season-ops --task "..." --mode live)  # only if user authorised live
  fantasy lock acquire yahoo:roster --ttl 300 --purpose "add X drop Y" --json
  fantasy txn plan add_drop --payload '{"add":"<player_id>","drop":"<player_id>","faab":null}' --summary "Add X / drop Y: <why, numbers>" --json
  fantasy approval show <approval_id> --wait 600 --json    # exit 8 if still pending; exit 0 -> read approval.status
  ```
  Then the txn recipe in the `agent-run` skill (mark submitted -> act -> verify on the transactions
  page -> mark verified). The approval summary must state the expected category impact and the
  downside (who we lose). The user approves on the dashboard `/approvals` or in chat; never run
  `fantasy approval decide` yourself.
- League limits (unverified, `fantasy settings show`): `max_weekly_adds` 6, waivers 2 days, continual
  rolling list. Check remaining adds before proposing.

## When Phase 2 is built
Replace this file with the real protocol (sync -> optimize -> plan txns -> approval if needed ->
execute -> verify -> record), keeping the policy section.
