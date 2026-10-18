Constructed (not recorded) Yahoo Fantasy API v2 JSON fixtures for league 44495.

- Shapes follow real recorded responses (fragment lists, `"0".."n-1"` + `count` collections) as seen
  in spilchen/yahoo_fantasy_api test samples (MIT) and yfpy models; values are fabricated.
- Game key `470` is a PLACEHOLDER; the real 2026-27 NBA game key must be resolved via `game/nba`.
- `draftresults_live.json` uses the 3RR order (see docs/research/draft_order.md); whether Yahoo
  lists unmade picks during a live draft is unverified, so the parser accepts either form.
- Replace with real recordings (`fantasy settings yahoo-auth`, then dump responses) when available;
  scrub tokens/GUIDs/emails first.
