# Draft order: third-round reversal (3RR) for a 12-team Yahoo league, slot #1

## The rule
Third-round reversal: round 1 runs 1→12, round 2 runs 12→1 (normal snake), **round 3 runs 12→1
again** (instead of 1→12), and from then on the snake keeps alternating, so every round from 3 on is
the mirror of a standard snake: R4 1→12, R5 12→1, R6 1→12, … The team picking last in R1 picks first
in R3 ([Substack/Bleav](https://bleavinfantasy.substack.com/p/why-third-round-reversal-drafts-are);
[4for4 mailbag](https://www.4for4.com/2016/preseason/4for4-mailbag-preseason-week-4) spells out
"1–12, 12–1, 12–1, 1–12, 12–1, then regular snake"; [Fantasy Index](https://fantasyindex.com/2014/07/16/fantasy-news/third-round-reversal-drafts)
notes that "all of the remaining rounds are flipped" vs. a plain snake, which is the same thing).

Generic formula (round r, slot s, n = 12): forward rounds are r = 1 and even r ≥ 4; reverse rounds are
r = 2 and odd r ≥ 3. Overall pick = 12·(r−1) + (s if forward else 13 − s).

## Slot #1 (the user) — 13 rounds (10 starters + 3 BN; IL slots are not drafted)

| Round | Direction | Overall pick | Round.pick | Picks until next turn |
|---|---|---|---|---|
| 1 | 1→12 | **1** | 1.01 | 22 |
| 2 | 12→1 | **24** | 2.12 | 11 |
| 3 | 12→1 (reversal) | **36** | 3.12 | 0 (back-to-back) |
| 4 | 1→12 | **37** | 4.01 | 22 |
| 5 | 12→1 | **60** | 5.12 | 0 |
| 6 | 1→12 | **61** | 6.01 | 22 |
| 7 | 12→1 | **84** | 7.12 | 0 |
| 8 | 1→12 | **85** | 8.01 | 22 |
| 9 | 12→1 | **108** | 9.12 | 0 |
| 10 | 1→12 | **109** | 10.01 | 22 |
| 11 | 12→1 | **132** | 11.12 | 0 |
| 12 | 1→12 | **133** | 12.01 | 22 |
| 13 | 12→1 | **156** | 13.12 | — |

Picks: 1, 24, 36, 37, 60, 61, 84, 85, 108, 109, 132, 133, 156. (Matches the prompt's expectation and
`tests/test_yahoo_api.py::test_parse_draft_results_live_partial`.) Note the cost of 3RR for slot 1:
the R2→R3 gap is only 11 picks instead of 22 and the R3 pick is 36 not 25; from R3 on the turns come
as back-to-back pairs (36/37, 60/61, …) with 22-pick gaps — plan pairs, not singles.

## How Yahoo implements it — important
Yahoo has **no native "3RR" option**. Yahoo's draft settings offer only *snaking* or *non-snaking*
rotation; a commissioner who wants 3RR must use **Commissioner → Draft & Keeper → Edit Draft Order →
Custom Order → Continue → customize each round** (desktop only) and hand-enter round 3 (and every
later round) ([Yahoo help SLN6347](https://help.yahoo.com/kb/SLN6347.html),
[SLN6196](https://help.yahoo.com/kb/SLN6196.html)). Consequences:

1. Hand entry can be wrong or partial (e.g. only R3 flipped and R4+ left as default snake, which would
   make R4 12→1 and give slot 1 picks 1, 24, 36, 48, 49, 72, 73, …). **Verify the actual order** on the
   league's draft-order page / draft room before 10pm ET on 2026-10-19 and, if available, via
   `league/{lk}/draftresults` (may list slot `team_key`s pre-draft) — then feed the observed order to
   `fantasy draft init` rather than assuming the formula.
2. Some leagues instead run 3RR through an external tool and paste the order (one 2024-25 Yahoo
   league did this via FantasyNerds — [ResetEra thread](https://www.resetera.com/threads/yahoo-2024-2025-nfl-fantasy-football-play-or-die-league-season-11-say-hello-to-the-bad-guy.918837/)).

## Yahoo defaults relevant to the draft (for unverified settings)
* Default basketball league: H2H 9-cat (FG%, FT%, 3PTM, PTS, REB, AST, ST, BLK, TO); roster PG, SG, G,
  SF, PF, F, C, C, Util, Util + 3 BN + IL slots (Yahoo help "Default league settings in Fantasy
  Basketball", [SLN6919](https://help.yahoo.com/kb/SLN6919.html), lists 3 IL; an AOL/Yahoo 101 guide
  lists 2). Our league has **3 IL** and no IL+ (verified from the Yahoo settings page screenshot,
  2026-10-18; the earlier recollection of 4 IL was wrong). IL slots are not drafted, so the draft is
  still 13 rounds. Eligibility: IL = status INJ/O (and on some settings GTD/DTD); injured free agents
  can be added directly into an IL slot in this league.
* Live draft pick clock default 60 s (league confirmed 60 s).
* **Timeout behaviour**: if a manager doesn't pick within the time limit (or leaves/misses the draft),
  **Autopick** picks for them: highest-ranked available player by the manager's **Pre-Draft Rankings**
  (pre-ranks), falling back to Yahoo's default ranking, respecting position needs — starters are filled
  before bench ([Yahoo help SLN6163 "Pre-rank players to prepare for Autopick Drafts"](https://help.yahoo.com/kb/SLN6163.html),
  [SLN6230 Live Standard Draft](https://help.yahoo.com/kb/SLN6230.html)). Whether the in-room
  *queue* takes precedence over pre-ranks on timeout is not documented; assume pre-ranks.
  Players on the "Do Not Draft" list are skipped. Once autopick kicks in, Yahoo may keep auto-drafting
  until the manager returns (re-enter the room / untick autopick).
* **Mitigation**: before the draft, set Pre-Draft Rankings on Yahoo to the engine's board top ~60 so a
  missed clock (browser hiccup) still takes a sensible player.
