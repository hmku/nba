# Week 1, lineup crunch, streaming and trade scan (2026-10-20)

_In-season analyst, cloud session, 2026-10-20. Analysis only: nothing was done on Yahoo, nothing was planned in
the txn ledger. Tools: `src/fantasy/inseason/` (`inseason-0.1.0`; CLI `fantasy inseason lineup|stream|trade`),
season simulator `season-sim` (fitted priors, `docs/research/sim_calibration.md`)._

**Labels.** *Simulated* = output of the season simulator (fitted parameters; ± is the Monte Carlo/paired SE
only, not model error). *Schedule* = counted from the NBA schedule (basketball-reference, `data/raw/bbref_schedule`).
*Prior* = an assumption, not fitted.

## Inputs
| Input | Source |
|---|---|
| Rosters | `draft_picks` for `nba.l.44495-2026` (Supabase REST, 156/156), identical to the Yahoo roster capture `data/raw/yahoo/league_rosters_2026-10-20.json` (checked: no differences). Team ids = draft slots (me = 1). |
| League calendar and pairings | `data/raw/yahoo/league_schedule_2026-10-20.json` (Yahoo ids mapped to draft slots). Week 1 = **Tue Oct 20 to Sun Oct 25** (6 days); week 7 (Nov 30 to Dec 13) and week 17 (Feb 15-28) are two weeks long. |
| Free agents | `data/raw/yahoo/free_agents_2026-10-20.json` (Yahoo's top 150 available; all on waivers until **Oct 22**). |
| Injuries | ESPN injuries API read 2026-10-20 plus the Yahoo status tags in the captures. |
| Projections | `data/processed/projections_2026_27.csv` (default 50/50 blend). |

Yahoo team ids differ from draft slots (Yahoo: me = 11). Team 11 (draft slot) is now called "SCOTTIER BARNEY" on Yahoo.

## 1. Roster diagnosis

### Category profile (simulated, 8000 sims, real Yahoo schedule)
Weekly category ranks among the 12 teams (1 = best; for TO, 1 = fewest):

| FG% | FT% | 3PM | PTS | REB | AST | STL | BLK | TO |
|---|---|---|---|---|---|---|---|---|
| 9 | 5 | 2 | **1** | **1** | 2 | **1** | 2 | 9 |

- **Strengths:** REB, STL, PTS, 3PM, AST, BLK. Jokić alone is worth +2.5 AST and +2.2 REB in G-score units.
- **Weaknesses:** FG% and TO. FG% suffers because five of our guards are inefficient shooters (per-player G-scores in
  FG%: VanVleet −0.74, Sheppard −0.54, Fears −0.51, Bridges −0.37, Bailey −0.35). TO is the price of Jokić (−1.23),
  Queen (−0.77), Fears (−0.61) and Brunson (−0.49).
- **Swing categories** (our win rate closest to 50% averaged over all 11 opponents in week 1): TO (0.54), BLK (0.55),
  FT% (0.57), AST/PTS (0.54-0.55). FG% (0.37) is usually lost. REB, STL and 3PM (0.64-0.69) are usually won.
- Season outlook (simulated): **P(playoffs) 0.920 ± 0.003, P(title) 0.328 ± 0.005**. Haagen Daz Center is
  second at 0.77 / 0.12. The simulator is optimistic for us (other teams never manage their rosters; see the
  `season-sim` caveats). Read these as relative odds.

### Lineup crunch: position-aware daily lineups (schedule)
Method: for each day, take the players whose NBA team plays, then the maximum matching of players to the 10
starting slots (PG, SG, G, SF, PF, F, C, C, Util, Util), filled greedily in order of projected value. This is
optimal (the matchable sets form a transversal matroid). "Lost to positions" = min(players with a game, 10) minus
the number who can start. Code: `fantasy.inseason.lineup`.

| Fantasy week | Player-games | Starts | Lost to the 10-slot count | **Lost to positions** | Monte Carlo (gp/82 availability, prior): lost to positions |
|---|---|---|---|---|---|
| 1 (Oct 20-25) | 36 | 34 | 2 | **0** | 0.00 |
| 2 (Oct 26-Nov 1) | 42 | 41 | 1 | **0** | 0.00 |
| 3 (Nov 2-8) | 45 | 40 | 5 | **0** | 0.04 |
| 4 (Nov 9-15) | 48 | 47 | 1 | **0** | 0.08 |

**Conclusion: the three C-only players cost about zero games in weeks 1-4.** The C, C and both Util slots absorb
Jokić, Clingan and Ware. PF and F take Reid/Queen and Bridges. Kawhi can play SG/SF/PF, so the frontcourt
always fits. The real cost is the plain count: 13 players for 10 slots, so 9 healthy games sit on the bench over
27 days, 5 of them in week 3 (two 12-13-game days). Every other team loses a similar 5-10 games to the count.
Running the season simulator with these position-aware lineups (`PositionAwareSimulator`) changes our
P(title) by −0.6 pp (0.328 vs 0.335 for the position-blind simulator) and P(playoffs) by 0.0 pp.

The crunch that does matter is for **streaming**: Util is usually full of bigs, so a streamer only starts if
he fits a guard or wing slot that is empty that day. The streaming ranker accounts for this.

### Games per player (schedule; all healthy)
| Player | Pos | NBA | Wk 1 (Oct 20-25) | Wk 2 | Wk 3 | Wk 4 |
|---|---|---|---|---|---|---|
| Nikola Jokić | C | DEN | 2 | 3 | 3 | 3 |
| Kawhi Leonard | SG/SF/PF | TOR | 3 | 3 | 3 | 4 |
| Jalen Brunson | PG | NYK | 3 | 3 | 3 | 4 |
| Donovan Clingan | C | POR | 2 | 3 | 4 | 3 |
| Kel'el Ware | C | MIL | 3 | 3 | 4 | 4 |
| Naz Reid | PF/C | CHO | 3 | 4 | 3 | 3 |
| Miles Bridges | PF | PHO | 2 | 2 | 4 | 4 |
| Fred VanVleet | PG | HOU | 3 | 4 | 3 | 4 |
| Brandin Podziemski (Q, back) | PG/SG | GSW | 3 | 4 | 3 | 3 |
| Derik Queen | PF/C | NOP | 3 | 3 | 4 | 4 |
| Reed Sheppard | PG/SG | HOU | 3 | 4 | 3 | 4 |
| Jeremiah Fears | PG/SG | NOP | 3 | 3 | 4 | 4 |
| Ace Bailey | SF/PF | UTA | 3 | 3 | 4 | 4 |

Week 1 day by day: Tue 1 game, **Wed 11**, Thu 1, **Fri 11**, Sat 9, Sun 3. On Wednesday and Friday one healthy
player sits (the lowest-value one: Bailey or Fears).

## 2. Week 1

### Real opponent: Joshua's Excellent Team (draft slot 12, Yahoo team 5)
Simulated (8000 sims, position-aware): **P(win) 0.728, P(tie) 0.024, P(loss) 0.248**; expected categories won
5.45 of 9. Expected player-games: us 36.9 vs them 33.4 (both include the simulator's modelled streaming, see the caveats).

| Category | FG% | FT% | 3PM | PTS | REB | AST | STL | BLK | TO |
|---|---|---|---|---|---|---|---|---|---|
| P(we win) | 0.25 | 0.56 | 0.87 | 0.72 | 0.77 | 0.61 | 0.81 | **0.55** | 0.25 |

Joshua's team is efficient and careful with the ball (rank 2 in FG% and TO) but weak in STL, 3PM and PTS. The
matchup turns on **BLK and FT%** (≈55%), then AST. FG% and TO are probably lost, so don't chase them this week.
Their roster: Haliburton, D. Mitchell, J. Williams, Zubac, A. Davis, M. Bridges, J. Allen, Jaquez, Morant,
J. Green, A. Gordon, B. Lopez, Vučević.

### Odds against every team in week 1 (simulated, all-play, Oct 20-25)
| Team (slot) | P(win) | P(tie) | Exp cats | Their games |
|---|---|---|---|---|
| **Joshua's Excellent Team (12), actual opponent** | **0.728** | 0.024 | 5.45 | 33.4 |
| Justin's Groovy Team (2) | 0.704 | 0.024 | 5.46 | 33.9 |
| Thomas's Dandy Team (3) | 0.681 | 0.027 | 5.26 | 33.6 |
| Haagen Daz Center (4) | 0.558 | 0.029 | 4.79 | 36.9 |
| jesse's Phenomenal Team (5) | 0.672 | 0.028 | 5.25 | 34.1 |
| Yeet (6) | 0.760 | 0.021 | 5.68 | 33.2 |
| Andrew's Awesome Team (7) | 0.572 | 0.029 | 4.97 | 38.6 |
| Ed's Extraordinary Team (8) | 0.440 | 0.031 | 4.40 | 37.9 |
| Jeffrey's Great Team (9) | 0.576 | 0.026 | 4.96 | 37.8 |
| Christian's Bad Team (10) | 0.641 | 0.027 | 5.20 | 35.6 |
| SCOTTIER BARNEY (11) | 0.610 | 0.033 | 5.16 | 37.0 |

Weeks 2-4 against the scheduled opponents: Andrew's (7) 0.773, Justin's (2) 0.713, Christian's (10) 0.831.

### Streaming (schedule + per-game G-scores; Yahoo's real free-agent list)
Every free agent is on waivers until **Oct 22**, so the first possible pickup plays Thu Oct 22 at the earliest.
Only Oct 22-25 is left in week 1, and those days have just 2 games for most teams. Our roster is full (13 of 13, no
one Out, so the IL can't be used), so **every add is an add/drop, which needs the user's approval**.

Ranking method (`fantasy inseason stream`): window gain = Σ over days of the per-game value of the players who
actually start (position-aware lineup), with the candidate added and the lowest per-game player
(**Jeremiah Fears**, 0.23) dropped, minus the same sum for the current roster.

| Window | Top candidates (gain in per-game G units; games/starts) | Flags |
|---|---|---|
| Oct 22-25 (rest of week 1) | Dereck Lively II C DAL +1.37 (2/2); Grayson Allen SG/SF CHO +0.70 (2/2); Deandre Ayton C WAS +0.31 | Lively Q (foot, ESPN DTD); Allen Q (knee, DTD) |
| Oct 26-Nov 1 (week 2) | Lively +2.31 (3/3); G. Allen +2.13 (4/4); Robert Williams C POR +1.61 (3/3); Mitchell Robinson C BOS +1.39 (4/4) | R. Williams and Robinson healthy |

**But the drop costs more than the stream earns.** Making the swap permanent (the simulator plays the rest of the
season; paired, 4000 sims):

| Add / drop Fears | ΔP(playoffs) | ΔP(title) | Δ category wins |
|---|---|---|---|
| Dereck Lively II | −0.035 ± 0.004 | −0.051 ± 0.006 | −2.18 ± 0.07 |
| Grayson Allen | −0.022 ± 0.003 | −0.036 ± 0.006 | −1.07 ± 0.07 |
| Robert Williams | −0.028 ± 0.004 | −0.042 ± 0.006 | −1.58 ± 0.07 |

**Recommendation: no add/drop in week 1.** Stream only into a slot that is genuinely free, for example when a
player goes Out long enough for the IL (Out / Out-for-Season only) or a trade turns 2 players into 1 (section 3).
If the user wants to stream anyway, the order is Allen (healthier long-term than Lively), then Robinson/Williams for
week 2. That still needs approval.

## 3. Trade scan
Method: 884 candidates from the actual rosters, screened by paired simulation (400 sims each, position-aware).
The candidates were 1-for-1 swaps within ±0.6/0.8 projected value, and 2-for-1s giving one surplus big plus one
other player for a player valued between the better of the two (+0.3) and their sum (−0.5). The finalists were
re-run at **6000 paired sims on the real Yahoo schedule** (`evaluate_trades`). In a 2-for-1 we drop to 12
players and refill with a free agent. The partner goes to 14 and drops its lowest-value player. Refill
assumption (prior): *optimistic* = we get the best projected free agent still on Yahoo's list (Julian Champagnie,
SAS SF/PF, currently on waivers). *Conservative* = the 12th-best (Santi Aldama), i.e. everyone else's claims land first.
"Naive value" is the sum of projected values, roughly how a manager reading rankings would judge it.

| Trade (we give -> we get) | Partner | Us ΔP(playoffs) | Us ΔP(title) | Us Δcat wins | Conservative refill: ΔP(po) / ΔP(title) | Partner ΔP(playoffs) | Naive value give / get |
|---|---|---|---|---|---|---|---|
| **Ware + VanVleet -> Jamal Murray** | Yeet (6) | **+0.016 ± 0.003** | **+0.086 ± 0.006** | +0.86 ± 0.09 | −0.005 ± 0.003 / **+0.060 ± 0.006** | +0.034 ± 0.006 | 5.52 / 4.98 |
| Clingan + VanVleet -> Jamal Murray | Yeet (6) | +0.009 ± 0.003 | +0.051 ± 0.006 | +0.81 ± 0.09 | −0.011 / +0.028 | +0.034 | 5.75 / 4.98 |
| **Ware + Bailey -> Derrick White** | Haagen Daz (4) | +0.011 ± 0.003 | +0.043 ± 0.006 | +0.68 ± 0.08 | −0.014 / +0.021 | +0.027 | 4.44 / 3.93 |
| Reid + VanVleet -> Scottie Barnes | SCOTTIER BARNEY (11) | +0.012 ± 0.003 | +0.028 ± 0.006 | +0.63 ± 0.09 | −0.014 / +0.012 | +0.033 | 5.45 / 4.74 |
| Reid + Bailey -> Desmond Bane | Christian's (10) | +0.004 ± 0.003 | +0.020 ± 0.006 | +0.58 ± 0.08 | −0.018 / −0.004 | +0.060 | 4.37 / 3.87 |
| Kawhi -> Mikal Bridges (1-for-1) | Joshua's (12) | +0.011 ± 0.003 | −0.024 ± 0.006 | +0.40 ± 0.07 | n/a | −0.011 | 3.92 / 3.18 |
| Kawhi + Reid -> Donovan Mitchell (counter-example) | Joshua's (12) | −0.009 ± 0.004 | −0.026 ± 0.006 | −1.17 ± 0.10 | −0.036 / −0.054 | +0.107 | 7.22 / 4.85 |
| Reid -> Nickeil Alexander-Walker | Yeet (6) | +0.003 ± 0.003 | +0.013 ± 0.006 | −0.14 ± 0.06 | n/a | +0.001 | 3.30 / 3.18 |
| Fears -> DeMar DeRozan | Justin's (2) | −0.003 ± 0.003 | −0.006 ± 0.005 | +0.06 ± 0.06 | n/a | +0.014 | 1.09 / 1.19 |

Reading it:
- **Best idea: Kel'el Ware + Fred VanVleet for Jamal Murray (Yeet).** It consolidates two bench-level players
  into a 23-point guard who shoots well (projected 48% FG, 88% FT, 2.8 3PM, 6.6 AST). It also removes VanVleet,
  our worst FG% drag. P(title) rises under both refill assumptions (+8.6 / +6.0 pp, both > 9 SE). P(playoffs) is
  +1.6 pp with the optimistic refill and −0.5 pp (noise level) with the conservative one. **Why Yeet would say
  yes:** by rankings they get more (5.52 vs 4.98). By category fit, Yeet ranks 12th in STL, 11th in BLK and 11th
  in AST, and Ware (BLK) and VanVleet (AST, STL) target exactly those. Their own simulated P(playoffs) rises by
  3.4 pp. Murray and Jokić both play for Denver, so they share an identical schedule (no extra games, no
  conflicts either).
- **Second: Ware + Ace Bailey for Derrick White (Haagen Daz).** White is efficient and low on turnovers (BLK and
  STL for a guard). The partner is our main rival (2nd in P(title)), so helping them is a real cost. Their
  P(title) barely moves (+0.3 pp).
- 1-for-1s don't help: with roster sizes fixed, the category-fit gains are within noise, or they trade
  P(playoffs) for P(title) (Kawhi -> Mikal Bridges).
- The title gain is consistently larger than the playoff gain in the 2-for-1s. The likely mechanism is that
  consolidation raises the quality of the 10 starters, which matters most in the three single-elimination
  playoff weeks against the strongest teams. **This mechanism has not been verified** (it is a simulator output;
  no real-league validation of P(win matchup) exists yet).

**Every trade needs the user's approval**, and Yahoo adds commissioner review with a 2-day veto window. Nothing
has been proposed. If the user wants to pursue one, the order is: user approval -> a local session proposes on
Yahoo (yahoo-browser skill, txn ledger, `trade` kind) -> after acceptance, claim the refill free agent (an add,
also approval).

## 4. Recommendations (plain language)
1. **Set the lineup daily; no roster moves in week 1.** We're favourites against Joshua's team (73%). Wednesday and
   Friday have more players than slots; bench Fears/Bailey on those days. Podziemski is questionable for
   Wednesday: if he's ruled out, start the healthy guard instead (Sheppard).
2. **Consider offering Ware + VanVleet for Jamal Murray to Yeet** (simulated +6 to +9 pp P(title)). Needs approval.
3. **Don't drop Fears/Bailey for a streamer.** Permanent swaps cost 2-4 pp of P(playoffs). Revisit when an IL slot
   opens or after a 2-for-1.

## Caveats
- Projections and the simulator are the same family the draft used: optimistic for our team; opponents never
  trade, stream or adjust lineups. The modelled streaming (≈6 extra games/week, a prior) is not available to us
  in practice while the roster is full.
- ± values are Monte Carlo SEs (paired where stated) and exclude projection/model error.
- Availability in the lineup Monte Carlo is iid per game (prior). The season simulator uses spells (fitted).
- Streamers' per-game values are opponent-agnostic G-scores. For a specific matchup, weight BLK/FT% (week 1).
- ESPN/Yahoo injury tags are as of 2026-10-20 03:25-ish UTC. Podziemski Q (back); league-wide Outs that matter for
  trades: Claxton (team 9, 2 weeks), Butler (team 2, Jan-Feb), Porziņģis (team 6, indefinite), Ingram (team 10),
  Nurkić (team 5, 4 weeks).

## Reproduce
```bash
fantasy inseason lineup --league-file rosters.json --start 2026-10-20 --end 2026-11-15 [--sims 4000] --json
fantasy inseason stream --league-file rosters.json --start 2026-10-22 --end 2026-10-25 \
    --free-agents data/raw/yahoo/free_agents_2026-10-20.json --json
fantasy inseason trade --league-file rosters.json --schedule schedule.json --partner 6 \
    --give "Kel'el Ware,Fred VanVleet" --get "Jamal Murray" --sims 6000 [--refill-skip 11] --json
```
`rosters.json` = draft-slot league file (as `data/manual/league_rosters_2026-27.json`). `schedule.json` = the Yahoo
capture with Yahoo ids mapped to draft slots (the other agent's `data/manual/league_schedule.json` is meant for
this).
