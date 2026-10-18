# Lead orchestrator state (2026-10-18 ~22:00 UTC) — read this after a context compaction

Role: the lead session orchestrates; agents implement; push straight to main (user-approved); every
quantitative claim must be labelled fitted/sourced/prior (CLAUDE.md Evidence standard).

## In flight
| Work | Where | Expected output | Lead action on completion |
|---|---|---|---|
| Tech-debt fixes (plan in docs/research/tech_debt.md) + 3 bugs from E2E xfails (draft complete status, `--overall 0`, out-of-range overall) | local subagent in worktree `.claude/worktrees/agent-…` | docs/handoffs/2026-10-19-tech-debt-fixes.md | Then launch **two independent review passes** over its diff (draft path; sim/model), fix findings, re-review. Draft-path code freeze 2026-10-19 22:00 UTC. |
| Sim calibration vs 2010–24 box scores (priors vol_sd/stat_sd/fg/ft sds, GAME_CV, correlations, opponent/pace effects, injury spells) | local subagent | docs/research/sim_calibration.md, docs/handoffs/2026-10-18-sim-calibration.md | Relay evidence to user (old → fitted ± SE; % weekly variance from opponent/pace). Any GAME_CV change touching the draft valuation must be reported before adoption. |
| Value-model + rollout draft search (user's AlphaGo idea) | local subagent | docs/research/value_search.md (+ handoff, already partly pushed) | Relay validation table (ADP rooms, symmetric rooms, market truth). Adopt as default only if it beats optimizer z≥2 and doesn't lose in stress tests; else opt-in/advisory. |
| Blend adoption (50/50 ours+ESPN projections, user-approved) + republish values/draft_sim/preseason to Supabase | cloud session `session_016oxnNqceQcNe5X73q49MaK` | docs/handoffs/2026-10-19-blend-adoption.md | Check-in scheduled 22:26 UTC (trigger trig_01RmiJtv6QeJqFoAAPfFZY9B): verify Supabase rows, tests; tell user picks 1/24/36. If blocked on a permission prompt, tell the user to approve. |

## Done today (see docs/handoffs/*)
Projections v2 + fitted GP model + Yahoo ADP/positions + ESPN injuries; league settings verified from screenshots
(3 IL; playoffs wk 18–20 end Mar 21) except draft order (user believes 1,24,36,37…); optimizer 0.2.0;
season simulator; optimism decomposition (dashboard now shows 83–89%/28–35% central range); dashboard v3
(Draft/Matchup/League/Activity, light/dark auto) at https://fantasy.harrisonku.com; Muse API; E2E tests;
Supabase REST publish (network secret) ; demo rows removed.

## Waiting on the user (docs/USER_TODO.md is the source of truth)
- Make the GitHub repo private (ESPN/FantasyPros personal-use data committed) — tonight.
- Meta Muse: AGENT_API_TOKEN in Vercel → then ask lead to trigger a web/ build; give Muse /agent URL + token.
- Laptop setup (FANTASY_DB_URL, Claude in Chrome), draft-prep skill, mock rehearsal **after Mon 6pm ET freeze**.
- Draft night (Mon 10pm ET): Plan A = local `claude --chrome` runs live-draft skill; Plan B = user picks in Yahoo,
  records all picks via dashboard Draft → "✎ Enter a pick" (unlock with passphrase), laptop runs `fantasy draft watch`.

## Gotchas
- This container's editable install of `fantasy` may point at an agent worktree: use `PYTHONPATH=src` (pytest already does).
- This container cannot reach Supabase Postgres or REST; use the Supabase MCP for reads, cloud sessions for REST writes.
- Vercel skips builds unless `web/` changed (Redeploy too) — trigger env-var changes with a web/ commit.
- Stop hook requires committing/pushing everything: commit WIP snapshots, but never commit `.claude/worktrees/` (ignored).
