# Dashboard data: simulation, advice, lineup and matchup-score rows in `model_runs`

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

## 4. `kind = 'advice'` — recommendations for the Inbox decision cards (`/inbox`; formerly the Moves view)

_Added 2026-10-20 (advice-0.1.0); 2026-10-20 advice-0.2.0 added `rationale` + `why_now` (additive, schema stays
`advice/1`); 2026-10-20 advice-0.3.0 added the concise-content limits and the metric `key` flag (see "Concise limits"
below). Producer: `fantasy advise post|status` (`src/fantasy/ops/advice.py`, `model_version = advice-0.3.0`).
Readers: `web/src/app/inbox/page.tsx` via `web/src/lib/moves.ts` (parsing) and `web/src/lib/inbox.ts` (join with
approvals and threads, sections); the This week decisions timeline (`web/src/lib/lineup.ts`)._

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
  "title": "Trade Ware + VanVleet for Jamal Murray (Yeet)",   // ≤ 70 chars (0.3.0)
  "status": "idea",                      // idea | awaiting_yes | approved | done | rejected | expired
  "headline": "one line",                // ≤ 120 chars (0.3.0)
  "for_date": "2026-10-21",              // optional, YYYY-MM-DD (ET); lineup plans: the day they are for
  "week": 1,                             // optional fantasy week
  "verdict": "≤ 2 sentences, ≤ 200 chars (0.3.0); the 1st sentence is the card's one-line verdict",
  "requires_user_yes": true,             // policy: add/drops and trades always true
  "metrics": [                           // ordered, ≤ 6 (0.3.0); every number carries an evidence label
    {"label": "ΔP(title), blend + Champagnie refill", "value": 8.6, "se": 0.6, "unit": "pp",
     "delta": true,                      // optional: show a + sign on positive values
     "key": true,                        // optional (0.3.0): one of the ≤ 3 chips on the collapsed card
     "evidence": "simulated",            // fitted | simulated | prior | literature
     "note": "paired, position-aware, seed 0"},
    {"label": "Yeet's ΔP(playoffs)", "value": "+2 to +10", "unit": "pp", "evidence": "simulated"}  // value may be text
  ],
  "scenario_columns": ["name", "refill", "ΔP(title)"],   // optional column order (jsonb does not keep key order)
  "scenarios": [{"name": "Base case", "refill": "Champagnie", "ΔP(title)": "+8.6 ± 0.6 pp"}],  // optional; scalar cells
  "risks": ["…"],                        // ≤ 4, each ≤ 120 chars (0.3.0)
  "links": [{"label": "Trade scan", "path": "docs/research/week1_and_trades_2026-10-20.md"}],  // repo path, /route or URL
  "approval_id": "apr-…",                // optional: the approvals row carrying the user's decision

  // advice-0.2.0: plain-language justification + "why now" (see the rules below the block)
  "rationale": [                         // 1-4 strings, each ≤ 160 chars (0.3.0; 0.2.0 allowed 6 × 600);
    "Champagnie is a 3-and-D forward: threes, steals, blocks, few turnovers — categories we are thin in.",
    "- Bailey (rookie) is the weakest bench slot by every projection set."   // "- " / "• " prefix = bullet
  ],
  "why_now": {
    "before": "At the draft (draft-0.x values, ADP ~150) he ranked below our last picks: ...",  // ≤ 200 chars (0.3.0)
    "changed": [                         // 1-3 items, each ≤ 140 chars (0.3.0): a string or {"text", "kind"?}
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

**Concise limits (advice-0.3.0).** User feedback 2026-10-20: "too much text on the inbox". New or changed content
must fit the collapsed card: `title` ≤ 70 chars, `headline` ≤ 120, `verdict` ≤ 2 sentences and ≤ 200 chars (a
sentence ends at `.`/`!`/`?` + whitespace, or a line break), `rationale` ≤ 4 items each ≤ 160, `why_now.before` ≤ 200,
`why_now.changed` ≤ 3 items each ≤ 140, `risks` ≤ 4 each ≤ 120, `metrics` ≤ 6 (put the rest in `scenarios`) with at
most 3 flagged `key: true` (the card's chips; none flagged → the first 3). `advice.concise_problems(summary)` lists
every violation; `fantasy advise post` refuses new/changed content with ONE usage error naming everything to shorten
("too long for the Inbox card (advice-0.3.0 limits); shorten: title 90 chars > 70; risks[0] 130 chars > 120 …").
Same rule shape as `rationale`/`why_now`: an unchanged re-post of an older long item, `fantasy advise status` on it
(non-closed status) and `validate(strict=False)` only warn, and old rows still render (the 0.2.0 structural caps,
6 × 600 rationale etc., still apply as hard limits). Detail belongs in the linked research note, not the card.

**Inbox card layout** (`web/src/app/inbox/Decisions.tsx`), collapsed by default: type badge + title (1 line) + age →
the verdict's first sentence (`splitVerdict` in `moves.ts`) → ≤ 3 key-metric chips (`keyMetrics`: label, value without
SE, evidence as a coloured dot; full value ± SE and note on hover; legend once at the top of the page) → Approve /
Reject + expiry (pending approval; note behind "add note") or one state line (queued / no approval yet / decided) →
**Details ▸** (headline + the rest of the verdict, **Why** bullets, **What changed** with kind tags and evidence
refs, the full metrics table + scenarios, risks, links, approval request text, posted/updated, ids;
`AdviceDetails.tsx`, a client component that mounts its body on first open so the text is sent once as JSON, not
twice as HTML + RSC markup) and **💬 n** (linked threads + the comment box). Ideas show the verdict on one line;
closed cards sit under a one-line summary row.

### Advice ↔ approval ↔ thread linkage (Inbox, 2026-10-20)

One decision = one card. The Inbox joins (`joinDecisions` / `linkThreads` in `web/src/lib/inbox.ts`):

| link | field | written by |
|---|---|---|
| advice → approval | `summary.approval_id` | `fantasy advise status <id> <status> --approval-id apr-…` (or in the advice JSON) |
| approval → advice | `approvals.payload.advice_id` | `fantasy approval request … --payload '{"advice_id": "<advice_id>", …}'` (**preferred**: set it whenever an approval is requested for a recommendation) |
| thread → decision (explicit) | root event `payload.details.advice_id` (or `details.approval_id`; top-level `payload.advice_id|approval_id` also accepted) | `fantasy intel post --kind recommendation --text "…" --details '{"advice_id": "<advice_id>"}'`; the dashboard's "Comment on this decision" (`POST /api/threads/comment`, report_kind `comment`, `details.advice_id|approval_id`) |
| thread → decision (fallback) | the advice_id or an approval_id appears as a whole token in the root's message, `details` or `evidence` text | anything (e.g. a post saying "see 2026-10-21-champagnie-claim") |

Either join direction suffices; both are applied. An approval that names an advice item not in the newest 300 advice
rows, or an advice item with no approval, still gets its own card. Several approvals on one item: the card's deciding
approval is a pending one, else the one `summary.approval_id` names, else the newest; all are listed. A root post that
names several decisions shows in each card. Replies follow their root (`thread_root`); replies and private chat rows
never link by themselves. Linked roots are removed from the Agent updates feed (de-dup), so a recommendation appears
once.

**Sections** (`groupInbox`): *Needs your yes* = a pending approval, or advice `awaiting_yes` without a decided
approval (soonest expiry first); *Approved · queued* = approval `approved` or advice `approved`, advice not closed;
*Ideas* = advice `idea` (no pending/decided approval) and today's (ET) lineup plan; *Done / rejected* = advice
done/rejected/expired, a rejected/expired approval on open advice, decided approvals without advice, other days' lineup
plans (newest first). Approving on the dashboard is the user's yes for add/drops and trades (user decision
2026-10-20): the next daily run (7:10 ET) or the main session executes it after re-checking Yahoo, then
`fantasy advise status <id> done`.

---

## 5. Threads on agent updates (`agent_events`, no migration)

_Added 2026-10-20. Writers: `POST /api/agent/report` (Muse / advisor tokens), `POST /api/threads/reply` (the user,
passphrase-gated like approvals), `fantasy intel reply|post` (Claude), `POST /api/threads/comment` (the user's new thread on an Inbox decision
card). Readers: Inbox page (`web/src/lib/feed.ts`; threads linked to a decision render inside its card, §4), `GET /api/agent/state` → `recent_updates`, `fantasy intel latest|thread`. How Claude uses
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
(`/inbox?author=advisor&kind=trade_offer`).

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

### `lineup_plan` — `model_version` = the optimizer's (`lineup-0.4.0`; 0.2.0 rows lack the 0.3.0 keys below, pre-0.4.0 rows lack `ratio_bench`), `params` = `LineupParams.to_dict()`
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
                    "p_win_matchup": 0.6, "p_win_matchup_se": 0.004}, …}},  // absent with --sims 0
    // lineup-0.3.0: chance to win each category by the end of the week (Sunday) under the optimized plan,
    // banked totals included. Monte Carlo share of weeks won (tie = 0.5) when sims ran, else analytic Phi(z).
    "cat_win_prob": {"fg_pct": 0.24, "ft_pct": 0.46, "fg3m": 0.89, "pts": 0.56, "reb": 0.61, "ast": 0.51,
                     "stl": 0.80, "blk": 1.0, "tov": 0.41},
    "cat_win_prob_method": "monte_carlo: share of 20000 simulated weeks the category is won (ties 0.5); SE <= 0.0035",
    "cat_win_prob_analytic": {…}            // always present, same keys
  },
  "fixed_point": {"iterations": 3, "converged": true},
  "slots_as_of": {"date": "2026-10-21", "source": "league file captured_at", "current_slots_file": null},
  // lineup-0.3.0: where the banked (already played) totals came from. kind: matchup_score (newest saved row of
  // section 7 for this week, read on or before `date`) | score_file (--score capture) | accumulated_file
  // (--accumulated) | none (banked = 0; `note` says why, e.g. no saved score yet)
  "accumulated_source": {"kind": "matchup_score", "run_id": "ms-w1-2026-10-21-05af63388e1a", "store": "db",
                         "observed_at": "2026-10-21T13:00:00Z", "date_et": "2026-10-21", "week": 1,
                         "score": {"me": 5, "opp": 4, "ties": 0}},
  "accumulated": {"me": {"fgm": 40, "fga": 85, …, "tov": 12}, "opp": {…}},   // raw totals used; null when none
  "category_weights": {"reb": {"my_mean": 160.2, "opp_mean": 151.0, "margin": 9.2, "sd": 14.1, "p_win": 0.74,
                               "weight": 0.023}},   // all 9 cats; weight ~0 = category settled (far ahead/behind)
  // lineup-0.4.0: protect-ratios plan vs the executed default plan -- a SUGGESTION, never applied. null in older rows;
  // {"checked": false, "reason": …} when the plan itself used --protect-ratios. Identical plans: differs false, no MC.
  "ratio_bench": {"checked": true, "differs": true, "suggest": true, "rule": "suggest iff … benches someone and dE[categories won] … > 2 x its paired SE … and >= 0.05 …",
                  "method": "monte_carlo: 20000 paired sims (common random numbers), seed 0",
                  "delta_expected_cats": 0.12, "delta_expected_cats_se": 0.04,   // protect minus default
                  "delta_p_win": 0.03, "delta_p_win_se": 0.01,
                  "benches": [{"date": "2026-10-25", "player_id": "…", "name": "…", "game": "@DET", "p_play": 0.96,
                               "instead_of_slot": "C", "value_per_game": -0.02,
                               "cats_helped": [{"cat": "ft_pct", "per_game": -0.03}],   // his start lowers these
                               "cats_hurt": [{"cat": "reb", "per_game": 0.01}]}],      // benching gives these up
                  "starts": [{"date": "2026-10-25", "player_id": "…", "name": "…", "p_play": 0.96, "slot": "C", "game": "vs NYK"}],   // swapped in
                  "fixed_point": {"iterations": 2, "converged": true},
                  "expected_analytic": {"default": {…}, "protect_ratios": {…}}},
  "warnings": ["MID-WEEK WITHOUT BANKED TOTALS: …"],   // only when accumulated_source.kind = none
  "model_version": "lineup-0.4.0"
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

---

## 7. `kind = 'matchup_score'` — live H2H score of my current matchup (live-score view, no migration)

_Added 2026-10-20 (matchup-score-0.1.0). Producer: `fantasy league score --capture <file> --save [--rest]`
(`src/fantasy/league/matchup{,_cli}.py`) from `scripts/yahoo/capture_matchup.js` (read-only GET of
`/nba/44495/matchup?week=N&mid1=11`; runbook `docs/runbooks/roster_sync.md` section 3). Consumers: the web live-score
page; `fantasy lineup plan` (banked totals, section 6 `accumulated_source`)._

What Yahoo's matchup page showed at `observed_at`: both teams' raw category totals **for the week so far** (games
already played), the current leader per category and the category score. Not a projection: the end-of-week chance per
category is the latest `lineup_plan`'s `expected.cat_win_prob` (section 6) for the same week, whose
`accumulated_source.run_id` names the score row it started from.

`run_id = ms-w<week>-<date_et>-<sha12>`, idempotent per (week, ET date, content): sha256 of the week, date and both
teams' slot / Yahoo id / totals / games (not names, source or `observed_at`), so re-reading an unchanged score the same
day inserts nothing and keeps the first `observed_at`. `model_version = matchup-score-0.1.0`, `params = {"command":
"league score"}`, `inputs = {capture_file, capture_sha256, content_sha256}`.
**Which row to show:** the newest by `summary.observed_at` (then `created_at`) with `summary.week` = the week shown;
for "now", the newest overall. PostgREST: `kind=eq.matchup_score&order=created_at.desc&limit=20`, then pick by
`observed_at` (rows of an earlier date have earlier `observed_at`; a same-day re-read with changed totals is a new row).

### `summary` (schema `matchup_score/1`)
```jsonc
{
  "schema": "matchup_score/1",
  "week": 1,                                  // fantasy week (Mon-Sun; week 1 = Tue 10-20 .. Sun 10-25)
  "date_et": "2026-10-22",                    // US/Eastern date of observed_at
  "observed_at": "2026-10-22T14:00:00Z",      // when Yahoo was read (capture captured_at), ISO UTC
  "source": "Yahoo league 44495 matchup page read in the user's logged-in browser (…capture_matchup.js, read-only)",
  "url": "/nba/44495/matchup?mid1=11",        // null for saved HTML
  "capture_file": "data/raw/yahoo/matchup_2026-10-22.json",
  "me": {
    "slot": 1, "yahoo_team_id": 11, "name": "Harrison's Best Team",     // slot = our team id (draft slot)
    "totals": {"fgm": 120, "fga": 250, "ftm": 40, "fta": 50, "fg3m": 30, "pts": 310, "reb": 130, "ast": 70,
               "stl": 20, "blk": 10, "tov": 35},                         // raw, ints (floats only if Yahoo shows them)
    "fg_pct": 0.48, "ft_pct": 0.8,            // makes / attempts (4 decimals); null when no attempts yet
    "games_played": 16, "games_remaining": 14        // from the strip above the score table; null = not found
  },
  "opp": { /* same shape; slot null if the Yahoo id is unknown (a warning says so) */ },
  "categories": [                             // the 9 scoring categories in Yahoo order
    {"cat": "fg_pct", "me": 0.48, "opp": 0.4583, "leader": "me"},     // % from makes/attempts, compared at 3 decimals
    {"cat": "ft_pct", "me": 0.8, "opp": 0.75, "leader": "me"},        //   (Yahoo's display precision)
    {"cat": "fg3m", "me": 30, "opp": 25, "leader": "me"},
    {"cat": "pts", …}, {"cat": "reb", …}, {"cat": "ast", …}, {"cat": "stl", "me": 20, "opp": 20, "leader": "tie"},
    {"cat": "blk", …},
    {"cat": "tov", "me": 35, "opp": 30, "leader": "opp"}             // TO: fewer is better
  ],                                          // me/opp null (and leader "tie") for a % with no attempts yet
  "score": {"me": 5, "opp": 3, "ties": 1},    // categories currently led / trailed / tied (sum 9)
  "yahoo_cats_won": {"me": 5, "opp": 3},      // Yahoo's own "Score" column (null if not shown); mismatch = warning
  "warnings": ["capture: games_remaining not in the score table"],  // capture problems + cross-checks (displayed % vs
                                              //  makes/attempts, PTS = 2 FGM + 3PM + FTM, Yahoo score, schedule opponent)
  "model_version": "matchup-score-0.1.0"
}
```
Display notes: show `observed_at` ("as of 10:00 ET") — Yahoo updates during games, the row does not. Format % to 3
decimals like Yahoo. Evidence label: **observed** (read from Yahoo, not modelled); the per-category chance by Sunday is
**simulated** (`lineup_plan.expected.cat_win_prob`, availability priors apply).
