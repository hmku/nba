# 2026-11-08 — matchup capture fix + late scheduled runs

**Answer:** `capture_matchup.js` works again (verified live, week 3: 7-2, problems []); the late 11-07 runs were the Mac
asleep, which needs a setting on the Mac (USER_TODO), not code.

- **Cause:** since ~11-06 Yahoo's default matchup view (`/matchup?mid1=11`) renders the score table client-side; the
  server HTML has no FGM/A. The **Totals** view (`?date=totals&week=N&mid1=11&mid2=<opp>`, linked from that page) still
  serves the old table and games strip unchanged.
- **Fix:** the script follows the Totals link when no score table is found (`url` = page read); week falls back to the
  link's `week=`. Python twin `parse_matchup_html` records `diagnostics.totals_href` and says "client-rendered ...
  read the Totals view" in `problems`. Real fixtures + 2 tests in `tests/test_matchup.py`.
- Capture `data/raw/yahoo/matchup_2026-11-08T0336Z.json` kept but **not saved** as a score row (SA@POR in progress;
  daily-routine §6 rule). Sunday's morning run captures the banked total.
- **Scheduler:** 11-07 07:11 daily and 14:30 / 18:30 checks started no session; the daily caught up at 21:33 ET.
  App keep-awake is on but only holds while a session works. USER_TODO item added.
- Context updated: `docs/runbooks/roster_sync.md` §3 (Totals fallback, new fixtures), script header comment.
- Run `run-9233207f787c4334825d02ebf436565b` (dry_run, read-only). pytest / ruff / mypy green.
