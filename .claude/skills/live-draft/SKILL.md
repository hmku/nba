---
name: live-draft
description: Run the user's live Yahoo draft (or a mock/rehearsal) — sync picks from the Yahoo draft room in the browser, run the optimizer, make picks within the 60s clock, verify, and log everything. Use when asked to draft, rehearse a draft, or recover a draft in progress.
---

# Live draft protocol

Requires a **local** Claude Code session started with `claude --chrome` (Claude in Chrome tools
`mcp__claude-in-chrome__*`), the user logged in to Yahoo, and `fantasy` installed. Cloud sessions
cannot reach Yahoo. Read `docs/runbooks/live_draft.md` once for context.

League: 12 teams, 13 rounds, my slot 1, 60 s per pick. Draft id: `nba.l.44495-2026` (use `mock-<n>` for rehearsals).

## 0. Authorisation
- Making picks in the real draft is authorised **only** in a run started with `--mode live` for that
  draft, which the user has explicitly asked for in this session. Rehearsals: `--mode dry_run` (never click "Draft").
- Within a live draft run you may pick without asking, but **only a player from the current
  recommendation's top 5**, unless you found disqualifying news (injury/suspension/trade) — then re-run
  `recommend` and record why. Never pick someone the model has not seen without logging the reason.

## 1. Setup (do before the draft starts)
```bash
export FANTASY_RUN_ID=$(fantasy run start --agent draft-manager --task "live draft nba.l.44495-2026" --mode live)
fantasy draft init --draft-id nba.l.44495-2026          # idempotent
export FANTASY_DRAFT_ID=nba.l.44495-2026
fantasy lock acquire draft:$FANTASY_DRAFT_ID --ttl 10800 --purpose "live draft"   # exit 3 = someone else is drafting: STOP
fantasy lock acquire yahoo:browser --ttl 10800 --purpose "draft room"
fantasy settings show --json        # check `unverified`
```
- In the browser, open the league's **Draft order** page (League → Draft Results/Draft order, or the draft room's order panel). Yahoo has
  no native 3RR, so read the actual order and store it:
  `fantasy draft set-order --rounds "1,2,...,12;12,...,1;..."` → confirm `my_picks` (expected 1,24,36,37,60,61,...).
  If it differs from the 3RR formula, tell the user.
- Read league Settings in the browser; if categories/roster match, `fantasy settings verify categories roster_slots num_teams scoring_type draft_order_type my_slot --source browser`. If not, edit `data/manual/league_settings.json`, rebuild values, tell the user.
- Safety net: put `fantasy draft queue --top 60` into Yahoo **Pre-Draft Rankings** (autopick uses it if the clock expires).
- `fantasy run event observation "setup complete" --payload '{"my_picks": [...]}'`

## 2. Loop (every pick)
1. **Sync the board.** Read the draft room's pick log. For every pick not yet recorded:
   `fantasy draft pick "<Name as shown>" --overall <N> --source browser --json`
   - exit 2 = unresolved name or conflict → read `candidates`, retry with the exact player_id; if the player
     isn't in projections use `--force` (records `unknown:<name>`) and log an `observation`.
   - `fantasy draft board --json` → `gaps` must be empty before recommending.
2. **When my pick is ≤ 2 picks away**, precompute: `fantasy draft recommend --json` (≈3 s). Read
   `recommendations[0..4]`, `strategy`, `warnings`, `prob_available_next`.
3. **On the clock:** sync again (last picks), re-run `recommend --json`. Choose `recommendations[0]` unless:
   - the player has fresh injury news (check the player card in the draft room) → note it, rerun excluding
     nobody — instead pick the next recommendation and record the disagreement;
   - top options are within ~0.01 score: prefer the one with *lower* `prob_available_next` (it won't come back).
4. **Prepare (one call — logs decision vs model, plans txn, checks policy+lock, marks submitted):**
   `fantasy draft prepare-pick "<Name>" --overall N --rationale "<why>" --json`
   - `action: CLICK` (exit 0) → in the draft room search the exact name, check team/position match
     `team` in the output, click **Draft**, confirm.
   - exit 4 `DO_NOT_CLICK` (dry_run / lock missing) → do not click. exit 5/7 → this pick was already
     submitted: re-read the draft log instead of clicking again. exit 2 → name ambiguous, use a player_id.
5. **Verify independently** in the draft log (my team, pick N, right player), then
   `fantasy draft confirm-pick <txn_id> --evidence "draft log: #N <Name> → my team" --json`
   (records the pick on the board). If not visible after ~10 s, re-read; **never click Draft twice**.
   If someone else got him first: `fantasy draft confirm-pick <txn_id> --failed "taken at #N-1"`, take the next rec.
6. If you deviate from `recommendations[0]`, the rationale must say why (it is stored with `agrees_with_model=false`
   and shown on the dashboard).
7. `fantasy lock renew draft:$FANTASY_DRAFT_ID --ttl 10800` every ~30 min; `fantasy run heartbeat` every few picks.

**Time budget (60 s):** sync ≤15 s, recommend 3 s, decide ≤10 s, execute ≤20 s. If < 15 s remain and you
haven't clicked, click the precomputed top pick if still available. Autopick (Pre-Draft Rankings) is the last resort.

## 3. Failure handling
- Browser tool errors / page unresponsive: reload the draft room once; if still broken tell the user
  "MANUAL MODE" with the current top-3 (they can follow `/draft` on the dashboard, which shows the same recs).
- Pick recorded wrong: `fantasy draft undo <N>` then re-record.
- Never perform any roster action other than drafting. Don't chat in the draft room.

## 4. After the draft
`fantasy draft board --json` (13 players), compare to Yahoo roster, `fantasy run end --summary "..."`,
release locks, write `docs/handoffs/<date>-draft.md` (roster, deviations from model, issues), commit & push.
