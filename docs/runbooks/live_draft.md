# Runbook: live draft (Mon 2026-10-19, 10:00 PM ET)

The agent protocol itself is `.claude/skills/live-draft/SKILL.md`. This page is for the human.

## Roles
- **Local Claude Code + Claude in Chrome** (on your laptop): syncs the Yahoo draft room, runs
  `fantasy draft recommend`, clicks picks, verifies, logs. Cloud sessions can't reach Yahoo.
- **Python engine**: deterministic recommendations (≈3 s per call) from `data/processed/projections_2026_27.csv`.
- **Dashboard `/draft`** (Vercel): board, recommendations, roster profile, agent activity; manual pick
  entry if automation fails.
- **You**: supervise; take over if anything looks wrong.

## One-time setup (today / tomorrow afternoon)
1. `git clone https://github.com/hmku/nba && cd nba && pip install -e ".[dev]"` (Python ≥ 3.11).
2. Database: Supabase → project **fantasy-nba** → Project Settings → Database → *Connection string*
   (Session pooler, URI). `export FANTASY_DB_URL='postgresql://...'` (keep it out of git; put it in `.env` or your shell rc).
   Without it the CLI uses local SQLite and the dashboard won't see live data (`fantasy publish` can copy later).
3. Claude in Chrome: install the Claude extension in Chrome, sign in (same account as Claude Code),
   log in to Yahoo in that Chrome, run `claude --chrome`, check `/chrome` shows Enabled. When Claude first
   opens Yahoo choose "allow all actions on this site" for `basketball.fantasysports.yahoo.com`
   (a permission prompt during a 60 s pick is fatal).
4. Ask Claude: *"Run the draft-prep skill."* It will verify league settings and the **actual draft order**
   (Yahoo has no native third-round reversal — the commissioner types the order in; it may differ from
   1, 24, 36, 37, 60, 61, …), fill Yahoo ADP gaps, rebuild values, and set Yahoo **Pre-Draft Rankings**
   from `fantasy draft queue --top 60` so Yahoo's autopick uses our model if the clock runs out.
5. Rehearse: start a Yahoo **mock draft** and ask Claude to run the live-draft skill in `dry_run` mode
   with draft id `mock-1`, then once more in `live` mode in a mock to test clicking.

## Draft night
1. 21:30 — laptop plugged in, sleep disabled, Chrome open on the draft room, `claude --chrome` in the repo.
2. Say explicitly: *"Run the live-draft skill for the real draft nba.l.44495-2026 in live mode. You are
   authorised to make my picks."* (Without this sentence the agent must stay in dry_run.)
3. Watch `/draft` on the dashboard (phone works). The agent records every pick, recommendation and
   decision; disagreements with the model are highlighted.
4. Optional: `/remote-control` in Claude Code to follow from your phone.

## If something breaks
| Symptom | Action |
|---|---|
| Agent stuck / browser unresponsive | Say "manual mode". Pick in Yahoo yourself using the top of `/draft` recommendations, or run `fantasy draft recommend` in a terminal. |
| Board out of sync | `fantasy draft board` shows `gaps`; record missing picks: `fantasy draft pick "Name" --overall N --source manual` (or use the dashboard Manual panel). |
| Dashboard writes disabled | Needs `SUPABASE_SERVICE_ROLE_KEY` + `DASHBOARD_PASSPHRASE` env vars in Vercel. Use the CLI instead. |
| Wrong pick recorded | `fantasy draft undo N` then re-record. |
| Clock expiring | Yahoo autopicks from your Pre-Draft Rankings (set in setup step 4). |
| Recommendations live-updating from dashboard entries | Run `fantasy draft watch` in a terminal: it recomputes whenever a pick is recorded anywhere. |

## How to read a recommendation
- `score`: projected expected categories won per week (of 9) for the **final roster** if you take this player
  and then make the planned picks (`plan`) — vs a league-average opponent.
- `marginal_value`: score difference to the next-best option (top) / to the top (others).
- `prob_available_next`: chance the player is still there at your next pick — low = take now.
- `strategy`: whether a punt build (e.g. punt FT%) projects better than balanced; scan in `strategy.scan`.
- `cat_impact`: the player's per-category G-scores (positive = helps).
Absolute win probabilities are optimistic (opponent model is an average team); compare options relatively.
