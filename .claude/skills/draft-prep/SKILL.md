---
name: draft-prep
description: Day-of-draft preparation checklist for league 44495 - refresh projections, fill the Yahoo ADP/position gaps from Yahoo's pages into data/manual CSVs, verify league settings and the ACTUAL draft order (Yahoo has no native 3RR) with `fantasy draft set-order`, rebuild values, run `fantasy draft simulate`, load `fantasy draft queue` into Yahoo Pre-Draft Rankings, and rehearse in a Yahoo mock draft. Use when asked to "prepare for the draft", "run draft-prep", before any real draft, or when tasks T-001/T-002/T-014/T-015 are in play.
---

# Draft prep (day of the draft)

Real draft: **Mon 2026-10-19 22:00 ET**, draft id `nba.l.44495-2026`, 12 teams, 13 rounds, my slot 1,
60 s/pick, H2H 9-cat. Steps 3-4 and 7-8 need a **local** `claude --chrome` session (`yahoo-browser`
skill); steps 1-2, 5-6 work anywhere. Tick off the tasks as you go: T-001 (settings), T-015 (order),
T-014 (pre-ranks), T-002 (rehearsal), T-013 (DB URL).

## 0. Setup
```bash
fantasy status                                    # anything to reconcile? (agent-run skill)
echo "${FANTASY_DB_URL:+db set}"                  # must be the Supabase URL for the dashboard (T-013); never print it
export FANTASY_RUN_ID=$(fantasy run start --agent draft-prep --task "pre-draft checklist" --mode dry_run)
fantasy lock acquire yahoo:browser --ttl 3600 --purpose "draft prep reads" --json   # local session only
```

## 1. Refresh projections (no network needed)
```bash
fantasy data build-projections --json   # -> rows (~450), sha256, source_counts, inputs[] with hashes
fantasy data top --n 30                 # sanity: Jokic, SGA, Wembanyama, Doncic near the top
```
Deterministic from `data/raw/` + `data/manual/`; if `git diff data/processed` is empty nothing changed.
Re-check news for top-150 players (injuries, trades since 2026-10-18) and encode changes per the
`projections-update` skill (`data/manual/player_overrides_2026_27.csv`).

## 2. Close the ADP / position gap
Only ~50 players have ADP (`data/manual/adp_yahoo_2026_27.csv`); blanks fall back to
`model rank * 1.15 + 3` in `fantasy.model.valuation.market_order`, which makes the availability
model (`prob_available_next`) unreliable after ~round 4. Positions are medium confidence.

**Where to read (local browser):** Yahoo Draft Analysis / ADP page, the league's Players page, or the
draft room / mock draft room player list (shows Yahoo rank, ADP and positions). See `yahoo-browser`
for URLs (most are unverified patterns). Read-only: no lock beyond `yahoo:browser` needed.

**How to write:**
- ADP -> `data/manual/adp_yahoo_2026_27.csv`, columns `name,adp,source,as_of`, e.g.
  `Jalen Duren,58.3,Yahoo draft analysis (browser) 2026-10-19,2026-10-19`.
  Update an existing row in place rather than adding a duplicate (if duplicated, the **last** row wins).
- Positions -> `data/manual/positions_overrides.csv`, columns `name,positions,note`, pipe-separated base
  positions only (`PG|SG`, `SF|PF`, `C`; never G/F/Util), e.g. `Jalen Johnson,SF|PF,Yahoo players page 2026-10-19`.
- Lines starting with `#` are comments. Names are matched by `fantasy.data.ids.canonical_id`
  (ascii slug + `ALIASES` in `src/fantasy/data/ids.py`); accents/periods don't matter, but a name the
  slug can't match is **silently ignored** (noted in `projections_2026_27.meta.json` -> `notes`).
- Prioritise players ranked ~1-180 (13 rounds x 12 = 156 picks). Record `source` honestly.

**Rebuild and check matches:**
```bash
fantasy data build-projections --json
python - <<'EOF'
import sys; sys.path.insert(0, "src")
import pandas as pd
from fantasy.model.projections import load_manual
ids = set(pd.read_csv("data/processed/projections_2026_27.csv").player_id)
for f in ["adp_yahoo_2026_27.csv", "positions_overrides.csv", "player_overrides_2026_27.csv"]:
    m = load_manual(f); print(f, len(m), "unmatched:", [n for n, p in zip(m["name"], m["player_id"]) if p not in ids])
print("ADP coverage:", pd.read_csv("data/processed/projections_2026_27.csv").adp.notna().sum())
EOF
```
Fix unmatched names (spelling or add an alias in `ids.py` - that's src/, coordinate with the data owner).
Note: players can also be unmatched because they fell outside the 450-row cut (e.g. long-term injured).
Commit the manual CSVs + `data/processed/*` (handoff skill) so the dashboard and others see them.

## 3. Verify league settings (T-001)
In the browser open the league Settings page and compare with `fantasy settings show --json`
(`categories`, `roster_slots`, `num_teams`, `scoring_type`, `draft_order_type`, `my_slot`,
`seconds_per_pick`, `draft_datetime`). Log the observed values as an `observation` event.
- All match: `fantasy settings verify categories roster_slots num_teams scoring_type draft_order_type my_slot --source browser --json`
  (writes `data/manual/league_settings.json`; `unverified` should become `[]`; commit that file).
- Something differs: create the file if needed
  `python -c "from fantasy.league.settings import load_settings, save_settings; print(save_settings(load_settings()))"`,
  edit the values in `data/manual/league_settings.json`, then `verify` the fields, rebuild values
  (step 5) and **tell the user** (it changes the model). Drafted rounds = non-IL roster slots (13 expected).
- Do this **before** `fantasy draft init` for the real draft: a draft record snapshots num_teams/slot/order.

## 4. Record the ACTUAL draft order (T-015)
Yahoo has no native third-round reversal; the commissioner hand-enters each round, so it may be wrong
or partial. Read the full order (all 13 rounds) from the draft order page / draft room order panel.
```bash
fantasy draft init --draft-id nba.l.44495-2026 --json              # idempotent; NOT --mock
fantasy draft set-order --draft-id nba.l.44495-2026 --rounds "1,2,...,12;12,...,1;12,...,1;1,...,12;..." --json
#   or --rounds @order.json with [[...13 lists of 12 slots...]]
```
- Each round lists **draft slots** (a team's round-1 position) in pick order. If Yahoo shows team
  names, map name -> round-1 slot first.
- Must contain exactly 13 rounds, each a permutation of slots 1..12 (`{"ok": false, "error": "invalid --rounds: custom order needs 13 rounds, got N"}`, exit 2).
- Output: `my_picks` (expected `[1,24,36,37,60,61,84,85,108,109,132,133,156]`) and
  `matches_3rr_formula`. If `false`, tell the user immediately (classic mistake: only R3 flipped ->
  `1,24,36,48,49,72,...`) - they may want the commissioner to fix it; re-run set-order after any fix.
- Log an `observation` with the order you read.

## 5. Values
```bash
fantasy values build --json     # model_version, params (kappa=1 G-scores, stream_recovery 0.5), top[]
```
Creates a `model_runs` row (shows in `fantasy status` model freshness). `recommend` uses default
params (unless given `--kappa/--punt`) and reuses/creates the matching valuation run itself, so
`--punt/--kappa` here are for analysis only - punting during the draft is decided by `recommend`'s strategy scan.
Unknown `--punt` names exit 2.

## 6. Simulate
```bash
fantasy draft simulate --sims 20 --seed 1 --draft-id nba.l.44495-2026 --json     # ~4 s per sim
```
Read `summary`: `optimizer.expected_category_wins_mean` vs `baseline_bpa.expected_category_wins_mean`,
`edge_category_wins` (optimizer minus best-player-available; expect >= ~0, small at n=20),
`first_pick_counts`, `strategy_counts`. Report these to the user; don't overinterpret absolute win
probabilities (opponent = league-average team). With `--draft-id` (or `FANTASY_DRAFT_ID`) `simulate` uses
that draft's slot/teams and the custom order stored by `set-order` (`custom_order: true` in the output);
without it, the formula order from settings. Pass `--slot` to test other slots.
`model-eval` skill covers sensitivity runs.

## 7. Pre-Draft Rankings safety net (T-014)
```bash
fantasy draft queue --top 60 --draft-id nba.l.44495-2026 --json   # queue[]: rank, player_id, name, team, positions
```
In the browser (Edit Pre-Draft Rankings) put these 60 in order. This edits Yahoo state (not the
roster): do it only because the user asked for draft prep, in this run, holding `yahoo:browser`; log
each batch as `browser_action`. Verify by re-reading the saved list and diffing against `queue`
(names/order); record the diff as `verification`. Yahoo autopick uses pre-ranks if the clock expires.
Re-export after big changes (new ADP, settings fix).

## 8. Rehearsal in a Yahoo mock draft (T-002)
Yahoo mock drafts are usually a plain snake with a random slot and may have different settings - read
them from the mock room. Always use a **separate mock draft id with `--mock`** and export it:
without `--mock` the record is treated as a real draft and becomes the default for every `draft`
command (`Default: latest non-mock draft`). `draft init` now refuses `mock*` ids without `--mock` and
non-mock ids not shaped `nba.l.<digits>-<year>` (exit 2); every draft command's JSON has `draft_id` - check it.
```bash
fantasy draft init --draft-id mock-1 --mock --slot <your mock slot> --json
export FANTASY_DRAFT_ID=mock-1
fantasy draft set-order --rounds "$(python3 -c "n=12;r=13;print(';'.join(','.join(map(str,range(1,n+1) if i%2==0 else range(n,0,-1))) for i in range(r)))")" --json
```
Then run the `live-draft` skill with `--mode dry_run` (never click Draft; Yahoo will autopick for you,
record those picks too). Measure: sync time per pick, `recommend` latency, name-resolution failures
(exit 2), gaps. Optionally a second mock in `--mode live` to test clicking. End with
`fantasy run end`, `unset FANTASY_DRAFT_ID`.

## 9. Finish
Release `yahoo:browser`, `fantasy run end --summary "settings verified: ..., order: ..., ADP rows: N, pre-ranks loaded: yes/no, mock: ..."`,
`fantasy task done T-001 / T-015 / T-014 / T-002` as applicable, handoff note + commit (`handoff` skill).
Tell the user anything unresolved (settings mismatch, order mismatch, FANTASY_DB_URL missing).
