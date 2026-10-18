# Your to-do list (human-only actions)

_Maintained by the lead agent. Agents: add items here when something needs the user; tick them off when confirmed._
Draft: **Mon 2026-10-19, 10:00 PM ET.**

## Can do from your phone now
- [x] **League settings screenshots** — received and recorded 2026-10-18 (`data/manual/league_settings.json`):
      categories, roster (3 IL, not 4), trades, waivers, playoffs (weeks 18-20, ending Sun Mar 21, 2027).
  - [ ] **Draft order: user believes 1, 24, 36, 37, … is right (2026-10-18)** — laptop session still confirms on Yahoo before pick 1.
        Previously: — screenshot the Draft Order page if available (Yahoo → League →
        Draft Order / draft room), else it gets checked in the laptop session (draft-prep step 4). Confirm
        your picks are 1, 24, 36, 37, 60, 61, … (3rd-round reversal) and that you pick #1.
- [x] **Clean demo rows in Supabase** — done by agent via PostgREST DELETEs on 2026-10-18 (user-approved;
      170 `mock-` rows across 14 tables + lock `mock:yahoo:browser`; real rows untouched).
- [x] `SUPABASE_SERVICE_ROLE_KEY` set in Vercel (verified live 2026-10-18 15:03 UTC).
- [x] `DASHBOARD_PASSPHRASE` set (writes enabled, verified 2026-10-18 15:10 UTC).
- [ ] Unlock writes on each device you'll use (lock icon in the dashboard nav). Consider a longer passphrase
      (the dashboard is public; changing it needs a `web/` build — ask an agent).
- [ ] **Confirm the dashboard is public**: open https://fantasy.harrisonku.com in a private tab.
- [ ] **Apply for Yahoo API access** (optional, takes weeks, not needed for the draft):
      https://sports.yahoo.com/developer/access/ — "personal read-only analysis tool for my own single league (44495)",
      users: Small, leave Client ID blank. See `docs/research/yahoo_api.md` §0.
- [ ] **Database access for agents — laptop**: Supabase → fantasy-nba → Connect → *Session pooler* URI
      (if you don't know the DB password: Project Settings → Database → *Reset database password*).
      Use it as `FANTASY_DB_URL` on your laptop (step 3 below). Never paste it into chat or commit it.
- [x] **Database access for cloud sessions**: allowed domain + network secret (`apikey`) set; verified by a
      successful `fantasy publish --rest` (2026-10-18 17:52 UTC, valuation-0.2.1, 456 players).

## At your computer (before the draft — ideally Monday afternoon)
1. [ ] Install/update Claude Code and sign in (`/login`, not an API key).
2. [ ] `git clone https://github.com/hmku/nba && cd nba && pip install -e ".[dev]"` (Python ≥ 3.11).
3. [ ] `export FANTASY_DB_URL='<session pooler URI>'` (put it in your shell rc or a git-ignored `.env`); check `fantasy status` works.
4. [ ] Chrome: install the **Claude** extension, sign in with the same account, log in to Yahoo Fantasy.
5. [ ] In the repo run `claude --chrome`; `/chrome` should say Enabled. When Claude first opens Yahoo choose
      "allow all actions on this site" for `basketball.fantasysports.yahoo.com`.
6. [ ] Tell Claude: **"Run the draft-prep skill."** It will (with you watching):
      - verify league settings (categories, roster slots) and the **actual draft order** (Yahoo has no native 3RR —
        confirm your picks are 1, 24, 36, 37, 60, 61, …);
      - fill Yahoo ADP / position eligibility gaps, rebuild projections and values;
      - load our top-60 into Yahoo **Pre-Draft Rankings** (autopick safety net).
7. [ ] **Rehearse**: start a Yahoo mock draft and say "run the live-draft skill in dry_run mode for mock-1",
      then once in live mode in a mock to test clicking.

## Draft night
- [ ] 9:30 PM ET: laptop plugged in, sleep off, Chrome on the draft room, `claude --chrome` in the repo.
- [ ] Say: **"Run the live-draft skill for the real draft nba.l.44495-2026 in live mode. You are authorised to make my picks."**
- [ ] Optional: `fantasy draft watch` in a second terminal (recomputes recs when picks are entered on the dashboard);
      watch https://fantasy.harrisonku.com/draft on your phone. Fallback steps: `docs/runbooks/live_draft.md`.

## Done
- [x] Network allowlist with wildcard domains (2026-10-18).
