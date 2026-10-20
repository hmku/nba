# Dashboard data: simulation, advice and lineup rows in `model_runs`

_Status: 2026-10-18. Written for the web agent (dashboard in `web/`). Producer: `fantasy draft simulate`
(kind `draft_sim`) and `fantasy sim preseason` (kind `season_sim`, `params.command = "preseason"`)._

Both live in Supabase table `model_runs` (one row per run):

| column | type | notes |
|---|---|---|
| `run_id` | text PK | `dsim-YYYYmmddHHMMSS-xxxxxx` (draft sim) / `sim-…` (season sim) |
| `kind` | text | `draft_sim` or `season_sim` |
| `model_version` | text | `optimizer-0.2.0` (draft sim) / `season-sim-0.3.0` (season sim) |
| `git_sha` | text | code version |
| `params` | jsonb | run parameters (n_sims, seed, noise, slot, …) |
| `inputs` | jsonb | provenance (projections path + sha256, order, …) |
| `summary` | jsonb | **display payload, schemas below** |
| `created_at` | timestamptz | |

**Which row to show:** latest `created_at` with `kind = 'draft_sim'`; for the season outlook latest
`kind = 'season_sim'` **and** `summary->>'scenario' = 'pre-draft projection'` (other `season_sim` rows are
`compare` / `draft-check` / mid-draft runs with different shapes). Every number is a float unless noted;
probabilities are in [0, 1] (format as %). Player references are `{player_id, name}`; `player_id` is our
slug (e.g. `nikola-jokic`), not a Yahoo id.

---

## 1. `kind = 'draft_sim'` — Monte Carlo mock drafts (optimizer vs best-player-available)

```jsonc
{
  "schema": "draft_sim/1",
  "scenario": "pre-draft simulation",
  "n_sims": 120,                       // int: simulated drafts
  "my_slot": 1,                        // int
  "num_teams": 12, "num_rounds": 13,   // int
  "my_picks": [1, 24, 36, 37, 60, ...],// int[]: my overall pick numbers (13)
  "optimizer_version": "optimizer-0.2.0",
  "noise": 1.0, "seed": 0,
  "strategy_forced": null,              // string|null: strategy forced with --strategy (null = scanned)
  "compute_seconds": 512.3,

  // --- headline: optimizer vs BPA on the SAME simulated rooms (paired) ---
  "optimizer":    {"expected_category_wins_mean": 5.41, "expected_category_wins_sd": 0.12,
                   "matchup_win_prob_mean": 0.78, "matchup_win_prob_sd": 0.05},
  "baseline_bpa": {"expected_category_wins_mean": 5.33, "expected_category_wins_sd": 0.13,
                   "matchup_win_prob_mean": 0.75, "matchup_win_prob_sd": 0.06},
  "edge": {
    "expected_category_wins": {"mean": 0.074, "se": 0.006, "p_optimizer_better": 0.86, "p_tie": 0.02},
    "matchup_win_prob":       {"mean": 0.021, "se": 0.003, "p_optimizer_better": 0.80, "p_tie": 0.02}
    // p_tie = share of sims where both rules gave equally scored (usually identical) rosters
  },
  // legacy flat fields kept for the CLI: edge_category_wins, edge_category_wins_se, first_pick_counts

  "strategy_counts": {"balanced": 118, "punt_ft": 2},   // strategy chosen at my first pick -> count

  // --- who I take at each of my picks (top 5 per pick) ---
  "picks_distribution": [
    {"pick": 1, "round": 1,
     "top": [{"player_id": "nikola-jokic", "name": "Nikola Jokic", "count": 120, "freq": 1.0}, ...],
     "n_distinct": 1},
    {"pick": 24, "round": 2, "top": [...], "n_distinct": 7},
    ...                                 // one entry per element of my_picks
  ],

  // --- availability model (analytic, not simulated): P(the OTHER teams haven't taken him by my pick) ---
  // (ignores that I may have taken him myself at an earlier pick; uses the run's noise multiplier)
  "availability": {
    "picks": [1, 24, 36, 37, 60, 61],  // my first 6 picks (columns)
    "players": [                        // ~40 key targets, sorted by market rank (ADP order)
      {"player_id": "...", "name": "...", "positions": ["PG","SG"], "team": "OKC",
       "adp": 3.2,                       // Yahoo ADP (null if none; then market_rank is model-based)
       "market_rank": 3,                 // int: rank in our market order
       "value": 4.81,                    // our valuation (G-score over replacement, per game)
       "value_rank": 2,                  // int
       "p_available": [1.0, 0.03, 0.0, 0.0, 0.0, 0.0],   // aligned with availability.picks
       "sim_taken_by_me_freq": 0.42      // share of sims in which I drafted him (any pick)
      }, ...
    ]
  },

  // --- my final roster's per-category weekly win probability vs a league-average team ---
  "category_win_probs": {
    "optimizer": {"fg_pct": {"label": "FG%", "mean": 0.61, "p10": 0.52, "p90": 0.69}, "ft_pct": {...}, ...},  // 9 cats
    "baseline_bpa": { ...same shape... }
  },

  // --- three example full rosters (optimizer), by expected category wins ---
  "example_rosters": [
    {"label": "best",   "sim": 17, "expected_category_wins": 5.62, "matchup_win_prob": 0.83, "strategy": "balanced",
     "cat_win_probs": {"fg_pct": 0.64, ...},
     "picks": [{"pick": 1, "round": 1, "player_id": "...", "name": "...", "positions": ["C"]}, ...]},
    {"label": "median", ...},
    {"label": "worst",  ...}
  ],

  "explanations": {                     // short plain-English text per section (render as help/tooltip)
    "overview": "...", "opponents": "...", "expected_category_wins": "...", "matchup_win_prob": "...",
    "edge": "...", "picks_distribution": "...", "availability": "...", "category_win_probs": "...",
    "example_rosters": "...", "caveats": "..."
  },
  "warnings": ["..."]
}
```

`model_runs.inputs` for this kind also has `per_sim`: `[{sim, ecw, bpa_ecw, mwp, bpa_mwp, roster: [player_id x13]}]`
(compact per-simulation results, e.g. for a histogram of `ecw - bpa_ecw`), plus `projections_sha256`,
`order_type`, `custom_rounds`, `my_picks`. `params`: `n_sims, seed, noise, strategy, my_slot, optimizer_params`.
The availability table's player list = the 3 first-pick candidates, then players I drafted at one of my first 6
picks in the sims (most frequent first), then players whose P(available) at one of picks 2-6 is between 5% and
95% (best value first), capped at 40, displayed sorted by `market_rank`.

Category keys (fixed order): `fg_pct, ft_pct, fg3m, pts, reb, ast, stl, blk, tov` (labels FG%, FT%, 3PTM,
PTS, REB, AST, ST, BLK, TO). For `tov` a *higher* win probability is still better (fewer turnovers).

## 2. `kind = 'season_sim'`, `summary.scenario = 'pre-draft projection'` — season outlook

Produced by `fantasy sim preseason`: K full 12-team leagues are drafted in simulation (my team = the
optimizer from slot 1, the other 11 teams = noisy ADP), then the season simulator plays each league
(n_sims split across the K leagues) and results are pooled.

```jsonc
{
  "schema": "season_sim_preseason/1",
  "scenario": "pre-draft projection",
  "n_sims": 2000, "n_leagues": 20,      // int: season sims in total, simulated drafts (leagues)
  "sims_per_league": 100, "num_teams": 12, "playoff_teams": 6,
  "draft_seed": 100, "season_seed": 0, "noise": 1.0, "strategy_forced": null,
  "my_team": 1,                          // int: my team id = draft slot
  "regular_weeks": [1, ..., 17], "playoff_weeks": [18, 19, 20],
  "compute_seconds": 95.2,

  "my_outlook": {
    "p_playoffs": 0.83, "p_playoffs_se": 0.01,
    "p_bye": 0.45,                       // top-2 seed (first-round bye in a 6-team bracket)
    "p_final": 0.40, "p_title": 0.26, "p_title_se": 0.01,
    "exp_rank": 2.6, "exp_cat_wins": 88.1, "exp_cat_losses": 64.9,   // regular season, 17 weeks x 9 cats
    "final_standing_dist": {"1": 0.30, "2": 0.18, ..., "12": 0.00},   // P(finish k-th), keys "1".."12"
    "across_leagues": {"p_playoffs_min": 0.7, "p_playoffs_max": 0.92, "p_title_min": 0.15, "p_title_max": 0.35},
    "category_profile": {"fg_pct": {"label": "FG%", "rank_mean": 3.1, "z_mean": 0.8}, ...}
      // my weekly category average ranked among the 12 teams (1 = best; TO: fewer = better), and z-score
      // (positive = better than league), averaged over the simulated leagues
  },

  // pooled by draft slot (team id = slot); "Team N" names are placeholders (real teams after the draft)
  "league_table": [
    {"team": 1, "name": "Me (slot 1)", "is_me": true,
     "p_playoffs": 0.83, "p_bye": 0.45, "p_final": 0.40, "p_title": 0.26,
     "exp_cat_wins": 88.1, "exp_rank": 2.6}, ...                      // 12 rows sorted by p_title desc
     // "Team N" for N != my slot is a *draft slot*, pooled over different simulated rosters
  ],

  "leagues": [                           // per simulated league (for drill-down / spread)
    {"league": 0, "draft_seed": 100, "season_seed": 0, "n_sims": 100,
     "my_p_playoffs": 0.86, "my_p_title": 0.28, "my_exp_rank": 2.4, "my_exp_cat_wins": 88.0,
     "my_draft_expected_category_wins": 5.41,   // the draft_sim metric for this roster
     "my_strategy": "balanced",
     "my_roster": [{"player_id": "...", "name": "..."}, ...]}, ...   // 13 players in pick order
  ],
  "draft_edge_vs_bpa": {"expected_category_wins": {"mean": .., "se": .., "p_optimizer_better": ..},
                        "matchup_win_prob": {...}},   // from the K simulated drafts only (small K: noisy)

  "explanations": {"overview": "...", "what_is_simulated": "...", "opponents": "...", "priors": "...",
                   "league_table": "...", "caveats": "..."},
  "warnings": ["..."]
}
```

`sim preseason` refuses `--schedule` (Yahoo schedules are keyed by Yahoo team id, simulated leagues by
draft slot), so pre-draft matchups are always a round robin.

Other `season_sim` rows (`params.command` = `season` / `compare` / `draft-check`) keep the shapes in
`docs/research/season_simulator.md` / `src/fantasy/sim/report.py`; only the post-draft `season` run below has a `scenario`.

## 3. `kind = 'season_sim'`, `params.command = 'season'`, `summary.scenario = 'post-draft: actual rosters'`

Produced by `fantasy sim season --league-file data/manual/league_rosters_2026-27.json --scenario "post-draft: actual rosters"`
(real drafted rosters, team id = draft slot, Yahoo team names). The dashboard's "season run" = newest `season` row whose
scenario is absent or starts with `post-draft` (`web/src/lib/postDraft.ts:isSeasonScenario`); Matchup and League read it.

```jsonc
{
  "scenario": "post-draft: actual rosters", "draft_id": "nba.l.44495-2026",
  "n_sims": 12000, "seed": 0, "regular_weeks": [1..17], "playoff_weeks": [18, 19, 20],
  "matchup_schedule": {"source": "round_robin(12 teams, 17 weeks)",   // or the league_schedule.json source
                       "assumed_weeks": [1, ..., 17],                  // weeks whose pairings are ASSUMED (round robin)
                       "all_assumed": true, "note": "ASSUMPTION: ..."},
  "league_table": [{"team": 1, "name": "Harrison's Best Team", "p_playoffs", "p_playoffs_se", "p_bye", "p_final",
                    "p_title", "p_title_se", "exp_cat_wins", "exp_cat_losses", "exp_rank", "players", "projected_players"}],
  "team_profiles": {"1": {"name", "weekly_mean": {cat}, "rank": {cat}, "z": {cat}, "strengths", "weaknesses",
                          "mean_player_games_per_week"}},
  "my_team": {
    "team": 1, "name", "p_playoffs", "p_title", "exp_rank", "final_standing_dist", ...,
    "weekly_matchups": [{"week", "start", "end", "opponent", "opponent_name", "p_win", "p_tie", "p_loss", "exp_cats_won",
                         "my_player_games", "opp_player_games", "cat_win_probs": {cat}, "opponent_assumed": true}],
    "vs_opponents_all_play": {"4": {"name", "p_win_matchup_any_week", "exp_cats_won", "cat_win_rate": {cat}}},
    "weekly_h2h_matrix": [{"week": 1, "start", "end", "scheduled_opponent": 12, "opponent_assumed": true,
                           "my_player_games": 71.2,
                           "vs": {"2": {"p_win", "p_tie", "exp_cats_won", "opp_player_games", "cat_win_probs": {cat}}, ...}}]
  },
  "warnings": ["... ASSUMED round robin ...", "Week dates derived ..."]
}
```
`inputs.rosters` holds every team's player ids; `inputs.matchup_assumed_weeks` repeats the assumption.

## 4. `kind = 'advice'` — recommendations for the Moves view (`/moves`)

_Added 2026-10-20 (advice-0.1.0); 2026-10-20 advice-0.2.0 added `rationale` + `why_now` (additive, schema stays
`advice/1`). Producer: `fantasy advise post|status` (`src/fantasy/ops/advice.py`, `model_version = advice-0.2.0`).
Reader: `web/src/app/moves/page.tsx` via `web/src/lib/moves.ts`._

One recommendation (trade idea, add/drop, stream, IL move, daily lineup plan) is an **item** with a stable
`summary.advice_id`. Every change is a new **version**: its own immutable `model_runs` row (`run_id = adv-YYYYmmddHHMMSS-xxxxxx`).
Readers show the newest version per `advice_id` (highest `summary.version`, then `created_at`).

**Why no migration.** `model_runs` already has everything a recommendation needs (typed JSON `summary`/`params`/`inputs`,
`git_sha`, `model_version`, `created_at`), the dashboard already reads it with the anon key, and `fantasy publish`
already copies it. A dedicated `advice` table would buy server-side status filtering and a mutable status column; neither
is needed at a few items per day, and a mutable status would break the `model_runs` convention that rows are never
edited (and `publish`'s upsert-by-run_id would then let a stale local copy overwrite a newer status). Revisit if items
reach thousands or the dashboard needs to *write* status (it does not: it is read-only; approvals stay in `approvals`).

**Status changes are append-only.** `fantasy advise status <advice_id|run_id> <status> [--note] [--approval-id]` copies
the latest summary, sets `status`, bumps `version`, sets `supersedes` to the previous `run_id` and appends to
`status_history`. `fantasy advise post` with content identical to the latest version (ignoring `status` and the
writer-managed keys) is a no-op, so re-running a seed never reverts a status; changed content appends a new version with
the file's status. `approved` requires `--approval-id` or `--note` (where the user said yes); it mirrors a decision and
never authorises a Yahoo write.

### `summary` (schema `advice/1`)
```jsonc
{
  "schema": "advice/1",                 // writer-managed
  "advice_id": "2026-10-20-murray-trade",// writer-managed (default: input file stem); stable across versions
  "version": 2,                          // writer-managed, 1-based
  "supersedes": "adv-…",                 // writer-managed: previous version's run_id (null for v1)
  "status_history": [{"status": "idea", "at": "<iso>", "by": "post"},
                     {"status": "awaiting_yes", "at": "<iso>", "by": "status", "from": "idea", "note": "…"}],

  "type": "trade",                       // trade | add_drop | stream | lineup | il
  "title": "Trade Ware + VanVleet for Jamal Murray (Yeet)",
  "status": "idea",                      // idea | awaiting_yes | approved | done | rejected | expired
  "headline": "one line",
  "for_date": "2026-10-21",              // optional, YYYY-MM-DD (ET); lineup plans: the day they are for
  "week": 1,                             // optional fantasy week
  "verdict": "1-3 lines",
  "requires_user_yes": true,             // policy: add/drops and trades always true
  "metrics": [                           // ordered; every number carries an evidence label
    {"label": "ΔP(title), blend + Champagnie refill", "value": 8.6, "se": 0.6, "unit": "pp",
     "delta": true,                      // optional: show a + sign on positive values
     "evidence": "simulated",            // fitted | simulated | prior | literature
     "note": "paired, position-aware, seed 0"},
    {"label": "Yeet's ΔP(playoffs)", "value": "+2 to +10", "unit": "pp", "evidence": "simulated"}  // value may be text
  ],
  "scenario_columns": ["name", "refill", "ΔP(title)"],   // optional column order (jsonb does not keep key order)
  "scenarios": [{"name": "Base case", "refill": "Champagnie", "ΔP(title)": "+8.6 ± 0.6 pp"}],  // optional; scalar cells
  "risks": ["…"],
  "links": [{"label": "Trade scan", "path": "docs/research/week1_and_trades_2026-10-20.md"}],  // repo path, /route or URL
  "approval_id": "apr-…",                // optional: the approvals row carrying the user's decision

  // advice-0.2.0: plain-language justification + "why now" (see the rules below the block)
  "rationale": [                         // 1-6 strings, each ≤ 600 chars (a bare string = one paragraph);
    "Champagnie is a 3-and-D forward: threes, steals, blocks, few turnovers — categories we are thin in.",
    "- Bailey (rookie) is the weakest bench slot by every projection set."   // "- " / "• " prefix = bullet
  ],
  "why_now": {
    "before": "At the draft (draft-0.x values, ADP ~150) he ranked below our last picks: ...",  // ≤ 800 chars
    "changed": [                         // 1-8 items, each ≤ 400 chars: a string or {"text", "kind"?}
      "Bailey's projected minutes fell after the preseason",
      {"text": "inseason-0.3.0 values bench depth with a refill model", "kind": "model"}
      // kind ∈ news | model | roster | schedule | league | other (optional tag shown on the card)
    ],
    "evidence": ["ΔP(title), blend"]     // optional, ≤ 8: metric labels (from `metrics`) backing the change;
  }                                      //   a label not found in `metrics` is a warning
}
```
`params`: `{"command": "post" | "status", …}` plus anything the input file's `params` holds (status rows:
`from_status`, `to_status`, `note`, `approval_id`). `inputs`: `{"source_file", "content_sha256", …}` (status rows:
`previous_run_id`). An input file is either the bare summary or `{"summary": {...}, "params": {...}, "inputs": {...}}`;
seeds live in `data/advice/`.

**`rationale` / `why_now` rule (advice-0.2.0).** They are *required* for actionable open items — `type` in
{`trade`, `add_drop`, `stream`} with `status` in {`idea`, `awaiting_yes`} — but only when **new or changed content is
posted** (a new item, or a re-post whose content hash differs): `fantasy advise post` then fails with a usage error.
Chosen over a per-item version flag because the content hash already tells old from new: an unchanged re-post of an item
recorded before 0.2.0 stays a no-op (with a warning), `fantasy advise status` on such an item records the status and
warns, and `advice.validate(..., strict=False)` (used for the pre-0.2.0 seeds in tests) downgrades the error to a
warning. Lineup and IL plans, and decided items (approved/done/rejected/expired), may omit both. `rationale` is written
for a quant who knows little basketball (basketball context welcome); `why_now.before` says what we believed or decided
earlier and why (draft-time model/values/ADP/roster need); `changed` lists what is new since (news, model versions,
roster/schedule facts, other teams' moves). Numbers in these texts follow the evidence standard: put them in `metrics`
with a label and reference them from `why_now.evidence`. Readers treat both as optional: rows without them render as
before.

**Moves card layout.** Header → title → headline → verdict → **Why** (rationale) and **What changed** (`before`, then the
`changed` list with kind tags and the evidence refs) → metrics → scenarios → risks → links. On narrow screens (< 640 px)
a long explanation (`isLongWhy`: > 2 rationale items, > 3 changes, or > 420 chars) collapses to a two-line teaser that
expands on tap; on wider screens it is always open.

**Moves page sections** (`groupMoves`): *Needs your yes* = status `awaiting_yes`, or `requires_user_yes` with status
`idea`; *Today's lineup* = type `lineup` with `for_date` = today ET (or none) and not rejected/expired; *Done / rejected*
= done/rejected/expired plus lineup plans for other days; *Ideas* = the rest (incl. approved, not yet done).

---

## 5. Threads on agent updates (`agent_events`, no migration)

_Added 2026-10-20. Writers: `POST /api/agent/report` (Muse / advisor tokens), `POST /api/threads/reply` (the user,
passphrase-gated like approvals), `fantasy intel reply|post` (Claude). Readers: Activity page
(`web/src/lib/feed.ts`), `GET /api/agent/state` → `recent_updates`, `fantasy intel latest|thread`. How Claude uses
them: `docs/integrations/threads.md`._

**Update (thread root)** = any visible `agent_events` row that is a report (`payload.report_kind` set: Muse reports
incl. `trade_offer`, `advisor_suggestion`, Claude posts `update|recommendation|trade_offer|question|other`) or one of
Claude's notable events (`kind` ∈ decision | verification | error). Private group-chat rows (`chat_digest`,
`chat_note`) are never answerable: replies are public.

**Reply** = one `agent_events` row, `kind = 'note'`:
```jsonc
{
  "report_kind": "reply",
  "reply_to": "advisor-ev-…",      // the update OR a reply in its thread
  "thread_root": "muse-ev-…",       // writer-computed: reply_to's root (threads are flat under the root)
  "stance": "disagree",             // agree | disagree | comment | question
  "text": "≤ 2000 chars",           // also copied to agent_events.message
  "evidence": [{"claim": "…", "value": -0.031, "se": 0.008, "label": "simulated", "command": "…"}],  // optional, ≤ 10
  "author": "advisor", "reported_by": "advisor", "source": "advisor"   // set by the writer; informational only
}
```
**Author = the run the row is filed under**, never the payload: `muse-YYYY-MM-DD` → Muse, `advisor-…` → advisor,
`user-…` (agent `user`, inserted as `succeeded`) → the user, everything else (incl. `claude-YYYY-MM-DD`, agent
`claude`, inserted as `succeeded`) → Claude. The `user`/`claude` daily runs are log containers, so they never show as
stuck and the stale-run reaper skips them. Event ids: `muse-ev-<hex>`, `advisor-ev-<hex>`, `user-ev-<hex>`,
`claude-ev-<hex>`.

**Validation** (`validateReply` / `replyTarget` in `web/src/lib/agentPure.ts`; `intel.reply` in Python): stance in
the list, text 1-2000 chars, evidence labelled, body ≤ 12,000 chars of JSON, `reply_to` matches
`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$`, exists (404) and is not private (403). Rate limits: Muse 60/h and advisor 20/h
(shared with their reports), user 30/h. The advisor token's endpoint scope is unchanged (`POST /api/agent/report`
was already allowed); its allowed report kinds are now `advisor_suggestion` and `reply`.

**`trade_offer` report** (Muse) `details`: `{from_team, to_team, from_gives: [names], to_gives: [names], status?:
proposed|countered|accepted|rejected|vetoed|completed|expired|cancelled, involves_me?, seen_on?, expires_at?, note?}`
(teams and both lists required; not both lists empty).

**Why no migration.** A reply is append-only, small, and needs the same RLS as the other agent logs (public read
except chat content), which `agent_events` already has; `payload->>thread_root` is filterable with PostgREST. A
`threads` table or a `parent_event_id` column would only buy an index-backed join, irrelevant at tens of rows a day.
Revisit (`create index on agent_events ((payload->>'thread_root'))`) if replies reach thousands.

**Feed** (`buildFeed`, `web/src/lib/feed.ts`): cards for reports, advisor suggestions, digests (only after the
unlock), Claude's notable events, finished non-passive runs with a `summary` (e.g. daily-routine summaries), and one
card with Muse's newest snapshot per kind (unlock only). Replies hang under `thread_root` (flat, oldest first, with
"↳ re X" when they answer a reply); a thread whose root is outside the query window is fetched by id. Order: newest
activity (root or reply) first. Filters: author (started or replied in the thread) and kind, as URL params
(`/activity?author=advisor&kind=trade_offer`).

---

## 6. `kind = 'lineup_plan'` / `'lineup_actual'` — daily lineups for the Lineup view (`/lineup`, no migration)

_Added 2026-10-20 (lineup-record-0.1.0). Producers: `fantasy lineup plan --save [--rest]` (plan),
`fantasy lineup record`, `fantasy league sync --write --record-lineup`, `fantasy lineup backfill --write` (actual);
code `src/fantasy/lineup/record{,_cli}.py`. Reader: `web/src/app/week/page.tsx` (This week) via `web/src/lib/lineup.ts` + `web/src/lib/week.ts`._

Both are immutable `model_runs` rows, **idempotent per (date, content hash)**: `run_id = lp-<date>-<sha12>` /
`la-<date>-<sha12>` (sha256 of the canonical summary; for actuals of `{date, team, source, slots, txn_id}` only, so
re-reading the same Yahoo state later inserts nothing). Re-saving identical content is a no-op (`inserted: false`).
**Which row to show:** plans — the newest `lineup_plan` with `summary.date = D`, else the newest earlier plan whose
`week_plan` covers D; actuals — the newest `lineup_actual` (by `created_at`) with `summary.date = D`. All dates are ET
calendar dates. Filter with `summary->>date` (PostgREST: `summary->>date=gte.2026-10-20`).

**Why no migration.** Same reasons as advice (section 4): typed JSON, versioning and provenance columns exist, the anon key
already reads `model_runs`, `fantasy publish` copies it, and a few rows per day need no index. A dedicated `lineups`
table would only buy a `(date, kind)` index; revisit when rows reach thousands.

### `lineup_plan` — `model_version` = the optimizer's (`lineup-0.2.0`), `params` = `LineupParams.to_dict()`
(`rest_share` with `rest_share_label: "prior"`, tag priors, n_sims, seed, locked), `inputs` = the plan's input files with
sha256 + `content_sha256`.
```jsonc
{
  "schema": "lineup_plan/1",
  "date": "2026-10-21", "week": 1, "week_start": "2026-10-20", "week_end": "2026-10-25",
  "my_team": 1, "opponent": {"team_id": 12, "name": "Joshua's Excellent Team"},
  "players": {"nikola-jokic": {"name": "Nikola Jokić", "positions": ["C"], "nba_team": "DEN", "status": null,
              "current_slot": "C", "games_left": 4, "value_per_game": 0.31}},          // every roster player once
  "slots": [{"slot": "PG", "player_id": "jeremiah-fears", "has_game": true, "p_play": 0.96, "value": 0.0123,
             "game": "@ORL"}],                  // target slots for `date`, all players (starters, BN, IL), Yahoo order
  "bench_with_game": [{"player_id": "brandin-podziemski", "name": "…", "game": "vs LAL"}],
  "decide_at_tipoff": [{"player_id": "…", "name": "…", "status": "Q", "p_play": 0.5, "slot": "BN",
                        "swap_with": "reed-sheppard", "swap_with_name": "Reed Sheppard", "note": "benched: if confirmed …",
                        "game": "vs LAL", "deadline": "before Reed Sheppard's tip-off (@ORL)"}],   // deadline: plain text
  "il_moves": [{"player_id": "…", "name": "…", "status": "O", "from": "BN", "to": "IL"}],
  "changes_vs_current": [{"player_id": "…", "name": "…", "from": "BN", "to": "PG"}],
  "today": {"players_with_game": 11, "started_with_game": 10, "unused_starts": 1, "unused_players": ["…"], "empty_slots": 0},
  "week_totals": {"players_with_game": 40, "planned_starts": 36, "slot_days": 50, "unused_starts": 4, "opponent_planned_starts": 38},
  "week_plan": [{"date": "2026-10-21", "slots": [/* as `slots` */], "players_with_game": 11, "started_with_game": 10,
                 "starting_slots": 10, "unused_starts": 1, "unused_players": ["…"], "empty_slots": 0,
                 "decide_at_tipoff": ["names"]}],             // date .. week_end, always saved (even without --week)
  "games": {"nikola-jokic": {"2026-10-22": "vs PHI"}},       // every game of every roster player this week; absent = no game
  "expected": {
    "method": "analytic normal approximation …", "availability_assumption": "healthy players play … (PRIOR) …",
    "evidence": "simulated (model output; availability and tag P(plays) are PRIORS)",
    "optimized": {"expected_cats": 4.92, "p_win_matchup": 0.62},          // analytic
    "naive_season_value": {…}, "static_current_lineup": {…},
    "monte_carlo": {"n_sims": 20000, "seed": 0, "plans": {"optimized": {"expected_cats": 4.9, "expected_cats_se": 0.01,
                    "p_win_matchup": 0.6, "p_win_matchup_se": 0.004}, …}}   // absent with --sims 0
  },
  "fixed_point": {"iterations": 3, "converged": true},
  "slots_as_of": {"date": "2026-10-21", "source": "league file captured_at", "current_slots_file": null},
  "warnings": ["MID-WEEK WITHOUT --accumulated: …"],
  "model_version": "lineup-0.2.0"
}
```
Game markers come from the cached NBA schedule (`data/raw/bbref_schedule/nba_2027_games.csv`, no tip times, hence the
plain-text deadline).

### `lineup_actual` — `model_version = lineup-record-0.1.0`, `params.command` = the producing command, `inputs` =
`{content_sha256, txn_id, capture_file}`.
```jsonc
{
  "schema": "lineup_actual/1",
  "date": "2026-10-22", "team": 1,
  "source": "yahoo_verified",              // yahoo_verified: server re-read after a lineup change | yahoo_capture: roster capture
  "observed_at": "2026-10-20T04:48:27Z",   // when Yahoo was read (capture captured_at / txn verified_at / --observed-at)
  "how": "txn txn-0e73… verified by a server re-read of /nba/44495/11/team?&date=2026-10-22",
  "txn_id": "txn-0e73…",                   // the lineup txn this verifies (null for captures)
  "capture_file": null,                    // capture path for yahoo_capture rows
  "slots": [{"slot": "C", "player_id": "nikola-jokic", "name": "Nikola Jokić", "status": null,
             "positions": "C", "nba_team": "DEN", "game": "vs PHI"}],   // optional keys when known; Yahoo order
  "complete": true,                        // every rostered player has a slot (false = partial read; null = roster unknown)
  "games_known": true,                     // markers were computed (absent marker = no game) — false without the schedule
  "derived_from": {"base_run_id": "la-2026-10-21-…", "base_date": "2026-10-21", "changes": 2},  // backfill only (below)
  "warnings": ["player 'X' not on my roster (kept by name, player_id null)"]
}
```
`slots` input to `fantasy lineup record --slots` may be `{player_id | yahoo_player_id | name: slot}`,
`{slot: player}` (single-slot labels) or `[{slot, player_id|name|yahoo_player_id}]`; slot labels normalise
(`Util2` → `Util`, `bn` → `BN`). `--source yahoo_verified` requires `--txn` or `--how`.

**Backfill** (`fantasy lineup backfill --from D0 --to D1 [--write] [--rest]`, dry run by default): one row per roster capture
in range (`data/raw/yahoo/league_rosters_<date>.json`, or `--capture`) and per **verified** lineup txn whose `payload.date`
is in range. A txn's slots come from its verification evidence (`slots` / `server_slots` / `final_slots` / `after` /
`lineup`); without one, its `payload.changes` (`[{player|player_id|name, from, to}]`) are replayed on the newest complete
row for a date ≤ the txn's (Yahoo carries an edited lineup forward to later unedited days), processing txns by date then
time, and the row records `derived_from`. A change whose `from` disagrees with the base is skipped with the reason
(never guessed). 2026-10-20..22 seed (main session, local, `FANTASY_DB_URL` = production):
`fantasy lineup backfill --from 2026-10-20 --to 2026-10-22 --json` (check the dry run), then the same with `--write`.

**Grid semantics** (`buildDay` in `web/src/lib/lineup.ts`): within a slot label (two C, two Util, three BN/IL rows) a
player in both plan and actual shares a row; the rest pair in order. ✓ verified = Yahoo shows the planned player (or no plan
exists), ⚠ mismatch = Yahoo shows someone else or nobody, ◌ planned = no Yahoo read that day (or not covered by a partial
read). Starts possible = players with a game who are not tagged out, capped at the starting slots; unused = possible − started.
