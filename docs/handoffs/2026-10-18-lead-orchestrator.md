# Lead orchestrator state (2026-10-18 ~22:00 UTC) — read this after a context compaction

Role: the lead session orchestrates; agents implement; push straight to main (user-approved); every
quantitative claim must be labelled fitted/sourced/prior (CLAUDE.md Evidence standard).

## In flight
| Work | Where | Expected output | Lead action on completion |
|---|---|---|---|
| Tech-debt fixes (plan in docs/research/tech_debt.md) + 3 bugs from E2E xfails (draft complete status, `--overall 0`, out-of-range overall) | local subagent in worktree `.claude/worktrees/agent-…` | docs/handoffs/2026-10-19-tech-debt-fixes.md | Then launch **two independent review passes** over its diff (draft path; sim/model), fix findings, re-review. Draft-path code freeze 2026-10-19 22:00 UTC. |
| Sim calibration vs 2010–24 box scores (priors vol_sd/stat_sd/fg/ft sds, GAME_CV, correlations, opponent/pace effects, injury spells) | local subagent | docs/research/sim_calibration.md, docs/handoffs/2026-10-18-sim-calibration.md | Relay evidence to user (old → fitted ± SE; % weekly variance from opponent/pace). Any GAME_CV change touching the draft valuation must be reported before adoption. |
| Value-model + rollout draft search (user's AlphaGo idea) | local subagent | docs/research/value_search.md (+ handoff, already partly pushed) | Relay validation table (ADP rooms, symmetric rooms, market truth). Adopt as default only if it beats optimizer z≥2 and doesn't lose in stress tests; else opt-in/advisory. |
| **Draft policy decision** (value-search found plain BPA beats the rollout optimizer when graded by the full season sim; independent reproduction + candidate fixes + `--policy` flag) | local subagent | docs/research/draft_policy.md, docs/handoffs/2026-10-19-draft-policy.md | Relay recommendation to user and get explicit OK before switching the default policy (before 2026-10-19 22:00 UTC freeze). Value search itself: NOT adopted as default; ships as advisory block + `--search value` opt-in. |
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

- Before the draft, in this container: `pip install -e .` from /home/user/nba (editable install currently points at the tech-debt worktree).

## Update 2026-10-18 22:40 UTC
- Blend adoption DONE + verified (Supabase: valuation-0.2.2 456 rows, draft_sim dsim-20261018222430-8e6d01, season_sim sim-20261018222640-4b5e92; tests green on main). Handoff docs/handoffs/2026-10-19-blend-adoption.md.
- Container restart 22:30 killed local agents. Relaunched: tech-debt fixes (continues from worktree WIP 7e193ae "stored order"),
  draft-policy reproduction (resumed), sim calibration (had silently died ~19:36; relaunched from scratchpad/calib partial results).

## Update 2026-10-19 ~03:30 UTC (user asleep; morning summary due)
- Done on main: unified noise model (4bab13f, reviewed), noise doc fixes (1714ab9), review-2 fixes (1caefde),
  draft-review fixes A1-A4/B1/B3/B5 (15e1f74, re-reviewed clean), REST ledger (f2f02ba), Muse intel (4989f44, verified live),
  Supabase MCP pre-approved in .claude/settings.json (user request).
- USER ACTION pending: run supabase/migrations/20261019120000_private_league_intel.sql in Supabase SQL editor
  (MCP DROP statements time out - plugin's own confirmation). Then verify pg_policies.
- Running: M1 manual-panel name resolution + minors (fix agent); draft-policy final (push ~10:00 UTC);
  placeholder experiment; value-model grading.
- Draft-policy INTERIM (W_mixed per user "Mixed", n=200 paired): default optimizer -2.6 pp P(playoffs) vs BPA (z -11);
  culprit = analytic ADP availability model (availability="sim" closes the gap; GreedyCat ~ BPA so objective OK).
  Best: `--search value --availability sim --room mixed`: +1.9 pp P(po), +1.4 pp P(title) vs default (z 8.7/4.7), >= BPA on title in every world, ~2 s.
  Pick #24 changes Kawhi -> Bam Adebayo. NEEDS USER OK before 22:00 UTC freeze to make default.

## Update 2026-10-19 ~16:50 UTC: draft config FINAL (pre-freeze)
- Default policy (user-approved): `--search value --availability sim --room mixed` (862cc74, reviewed), value-model-0.2.0 (c2217ac, reviewed; rollback FANTASY_VALUE_MODEL=value-model-0.1.0), display-only p_next fix (4940316), live-draft guard value_search_status==ok (7f4bd68).
- Room-sim self-pick correction evaluated (1c71257): NOT adopted (worse/tie in W_mixed); stays opt-in `room_self`.
- Placeholder variants (177675e): not adopted, opt-in.
- Remaining USER actions: privacy SQL in Supabase SQL editor; laptop draft-prep + mock rehearsal after 22:00 UTC freeze (check value_search_status ok); `pip install -e .`.
