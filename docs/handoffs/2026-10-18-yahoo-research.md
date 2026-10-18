# Handoff 2026-10-18 — Yahoo integration research (cloud session)

## Done
- `src/fantasy/league/yahoo_api.py`: read-only httpx Yahoo v2 client (`YahooClient`), pure parsers
  (`parse_league_settings`, `parse_draft_results`, `parse_teams`, `parse_roster`, `parse_players`,
  `parse_game`), and **`settings_from_yahoo(raw) -> dict`** for the lead's `LeagueSettings`
  (keys: num_teams, categories, roster_slots, draft_type, max_weekly_adds, waiver_days,
  playoff_start_week, playoff_teams + extras category_codes, lower_is_better, draft_pick_time,
  waiver_type, uses_faab, playoff_weeks, end_week, season, league_key, scoring_type).
  Non-GET requests raise `YahooReadOnlyError`. 401 → refresh+retry once; 999/429 → `YahooRateLimitError`.
- `src/fantasy/league/yahoo_auth.py`: one-time local OAuth flow. **Lead: wire it** with
  `settings_app.command("yahoo-auth")(yahoo_auth.cli_command)` in `league/cli.py`
  (also runnable as `python -m fantasy.league.yahoo_auth`). Tokens → `~/.config/fantasy/yahoo_oauth.json` (0600).
- Tests: `tests/test_yahoo_api.py` (21 passing) on constructed fixtures `tests/fixtures/yahoo/`
  (game key `470` is a placeholder).
- Docs: `docs/research/yahoo_api.md`, `docs/research/draft_order.md`, `docs/research/browser_integration.md`.

## Key findings
- API cannot make draft picks → live draft must be done in the browser (Claude in Chrome, local session).
- Yahoo has no native 3RR; commissioner hand-customizes round order → **verify actual order** in the
  league's draft-order page before the draft. Expected slot-1 picks: 1, 24, 36, 37, 60, 61, 84, 85,
  108, 109, 132, 133, 156.
- Could not verify league settings: all Yahoo hosts unreachable from cloud (proxy 403 / DNS fail).
  League 44495 settings remain **unverified**.
- 2026-27 NBA game key unknown; resolve via `GET game/nba` (`YahooClient.resolve_league_key(44495)`).

## Next (local session with `claude --chrome`)
1. Verify settings + draft order via browser; optionally `fantasy settings yahoo-auth` and compare
   `settings_from_yahoo` output; record real API responses as fixtures (scrub tokens/GUIDs/emails).
2. Check in a Yahoo mock draft whether `draftresults` updates live; set Pre-Draft Rankings as autopick net.
3. Write ops (lineup PUT, add/drop POST) are documented in yahoo_api.md §4; implement only behind approvals + txn ledger.
