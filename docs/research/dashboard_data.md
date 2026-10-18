# Dashboard data: pre-draft simulation rows in `model_runs`

_Status: 2026-10-18. Written for the web agent (dashboard in `web/`). Producer: `fantasy draft simulate`
(kind `draft_sim`) and `fantasy sim preseason` (kind `season_sim`, `params.command = "preseason"`)._

Both live in Supabase table `model_runs` (one row per run):

| column | type | notes |
|---|---|---|
| `run_id` | text PK | `dsim-YYYYmmddHHMMSS-xxxxxx` (draft sim) / `sim-…` (season sim) |
| `kind` | text | `draft_sim` or `season_sim` |
| `model_version` | text | `optimizer-0.2.0` (draft sim) / `season-sim-0.2.0` (season sim) |
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
`docs/research/season_simulator.md` / `src/fantasy/sim/report.py` and have no `scenario` field.
