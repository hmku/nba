import type { ReactNode } from "react";
import { OPTIMISM } from "@/components/Outlook";
import { pct } from "@/lib/format";

/**
 * "How the model works" — concise prose. Facts are taken from docs/STATUS.md,
 * docs/research/availability_model.md, docs/research/season_simulator.md,
 * docs/handoffs/2026-10-18-optimizer-diagnosis.md, docs/research/optimism_decomposition.md (scenario numbers come
 * from web/src/content/optimism.json) and the module docstrings in src/fantasy/.
 * Keep it in sync when the model changes (bump the versions quoted below).
 */

function Sec({ title, children, open = false }: { title: string; children: ReactNode; open?: boolean }) {
  return (
    <details open={open} className="group rounded-xl border border-line bg-panel">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 select-none [&::-webkit-details-marker]:hidden">
        <span className="text-[10px] text-muted transition-transform group-open:rotate-90">▶</span>
        <h3 className="text-[15px] font-semibold">{title}</h3>
      </summary>
      <div className="space-y-2.5 border-t border-line px-4 pt-3 pb-4 text-[14.5px] leading-relaxed [&_b]:font-semibold [&_code]:rounded [&_code]:bg-panel-2 [&_code]:px-1 [&_code]:text-[12.5px] [&_li]:ml-4 [&_li]:list-disc [&_ul]:space-y-1">
        {children}
      </div>
    </details>
  );
}

export default function HowItWorks() {
  return (
    <div className="space-y-2.5">
      <div className="px-1">
        <h2 className="text-lg font-semibold tracking-tight">How the model works</h2>
        <p className="mt-1 text-[14.5px] leading-relaxed text-muted">
          A pipeline of small, versioned Python models (<code className="num">fantasy</code> CLI). Agents only run
          them and act on their JSON; every output is recorded in <code className="num">model_runs</code> with its
          version, git sha, parameters and input hashes.
        </p>
        <ol className="mt-3 grid grid-cols-2 gap-1.5 text-[12.5px] sm:grid-cols-6">
          {["Projections", "Valuation", "Category-win model", "Draft optimizer", "Draft sim", "Season sim"].map((s, i) => (
            <li key={s} className="flex items-center gap-1.5 rounded-lg border border-line bg-panel px-2 py-1.5">
              <span className="num grid h-5 w-5 shrink-0 place-items-center rounded-full bg-panel-2 text-[11px] text-muted">{i + 1}</span>
              <span className="min-w-0 truncate">{s}</span>
            </li>
          ))}
        </ol>
      </div>

      <Sec title="The league in one paragraph" open>
        <p>
          12 teams, head-to-head over <b>9 categories</b>: FG%, FT%, 3-pointers made, points, rebounds, assists, steals,
          blocks and turnovers (fewer is better). Each Mon–Sun week you play one opponent and win, lose or tie each
          category separately; the season standings count category wins. 13 roster spots (10 starters a day, 3 bench) plus
          3 injured-list slots and 6 free-agent adds per week. The top 6 make the playoffs in weeks 18–20 (Mar 1–21, 2027).
          You draft 1st; the order is a snake with a <b>third-round reversal</b>, so your picks are 1, 24, 36, 37, 60, 61,
          84, 85, … — big gaps, then back-to-back pairs.
        </p>
      </Sec>

      <Sec title="1 · Projections (per-game lines and games played)">
        <ul>
          <li>
            <b>Rates</b>: per-minute stats from the last three seasons weighted 5/3/2 (2025-26, 24-25, 23-24) by minutes,
            shrunk toward the position-group (G/F/C) average with a 400-minute prior; an age curve adjusts production.
            Makes and attempts are projected separately; points = 2·FGM + 3PM + FTM.
          </li>
          <li>
            <b>Minutes</b>: recency-weighted, with growth for young players and decline after 33.
          </li>
          <li>
            <b>Games played</b>: a fitted regression on the last three seasons&apos; games and age (with &quot;ironman&quot;
            terms), trained on 2014–2024 and tested out of sample on 2025 and 2026: RMSE about 2 games better than the old
            rule, which was ~8 games too optimistic. Each player also gets a calibrated uncertainty <code>gp_sd</code>
            (15–24 games, larger for injury-prone players).
          </li>
          <li>
            <b>Injuries &amp; news</b>: ESPN/CBS injury reports and manual overrides (trades, roles, returns from injury,
            rookie priors). A small playoff-availability multiplier (0.98–0.99) covers resting in March.
          </li>
          <li>
            Stats come from a GitHub mirror of Basketball-Reference (verified identical to the site); ADP and position
            eligibility from Yahoo.
          </li>
        </ul>
      </Sec>

      <Sec title="2 · Valuation (G-scores)">
        <p>
          Each player gets a per-category score standardised over the draftable pool (12 × 13 players). Counting stats use
          the expected per-game line (games played × production, with missed games partly back-filled by a streamed
          replacement player). <b>FG% and FT% are computed from makes and attempts</b>: the score is the player&apos;s
          &quot;impact&quot; <code>makes − pool% × attempts</code>, so a 90% shooter on 2 attempts matters less than an 85%
          shooter on 8.
        </p>
        <p>
          Scores are <b>G-scores</b> (Rosenof 2024): the denominator adds week-to-week noise, so volatile categories
          (steals, blocks, 3PM, turnovers) count a little less — right for weekly H2H. The headline value is the sum over
          categories above replacement level. It is used for ranking and the fallback list; actual draft decisions come from
          the optimizer below.
        </p>
      </Sec>

      <Sec title="3 · Category-win model">
        <p>
          A team&apos;s weekly total in each category is approximated as Normal (counting stats sum; percentages are total
          makes / total attempts with delta-method variance). The opponent is a <b>league-average team</b>, built by drafting
          the pool in ADP order, with the spread between league teams added as variance.
        </p>
        <p>
          P(win category) = Φ(difference / combined sd); <b>expected category wins</b> = the sum of the nine probabilities;
          P(win matchup) treats categories as independent. This is the objective the optimizer maximises.
        </p>
      </Sec>

      <Sec title="4 · Draft optimizer (rollouts)">
        <ul>
          <li>
            For each candidate at the current pick, it imagines the rest of <i>my</i> draft: at each of my later picks it adds
            the <b>expected</b> pick — each ranked player weighted by the probability he is the best one still on the board —
            then scores the finished 13-man roster with the category-win model (<code>optimizer-0.2.0</code>).
          </li>
          <li>
            <b>Availability</b>: a player&apos;s draft position ~ Normal(ADP, sd) with sd growing with ADP (2.5 + 0.18·ADP);
            P(still there at pick p | there now) follows. That is the &quot;X% chance he&apos;s still there&quot; on the Draft
            tab.
          </li>
          <li>
            <b>Third-round reversal</b> and custom orders are built in: the rollout knows exactly when I pick next (e.g. 24
            → 36/37), so it can pass on a player who will probably last and take one who won&apos;t.
          </li>
          <li>
            It also checks positional feasibility and scans punt strategies (deliberately giving up a category), keeping the
            best full 9-category team; so far &quot;balanced&quot; wins almost always.
          </li>
          <li>
            In 200 paired simulations it beat best-player-available by <b>+0.074 ± 0.006</b> expected category wins per week
            (in-sample); margins below ~0.03 between two candidates are noise.
          </li>
        </ul>
      </Sec>

      <Sec title="5 · Monte Carlo draft simulation">
        <p>
          Many full drafts are simulated from my slot. The other 11 teams pick by Yahoo ADP plus noise; my picks come from
          the optimizer and, on the <b>same simulated rooms</b>, from a best-player-available baseline. The paired
          difference gives the edge with a proper standard error (sd/√n). It also yields who I tend to take at each pick,
          example rosters, and the spread of my category profile.
        </p>
      </Sec>

      <Sec title="6 · Season simulation">
        <p>
          A Monte Carlo of the whole season (<code>season-sim-0.3.0</code>): every team&apos;s roster, the real 2026-27 NBA
          schedule (games per team per day), week-by-week H2H matchups, a daily 10-starter cap, streaming adds,
          standings and a re-seeded 6-team playoff bracket.
        </p>
        <ul>
          <li>
            <b>Season-level uncertainty</b>: each simulated player draws a volume factor and per-stat factors (log-sd 0.08),
            plus shifts in FG% (±1.5 pts) and FT% (±3 pts).
          </li>
          <li>
            <b>Injuries</b>: games played drawn from the fitted model; 75% of missed games come as one or two injury spells,
            25% as single rest days.
          </li>
          <li>
            Before the draft, <code>fantasy sim preseason</code> drafts several simulated leagues (me = optimizer, others =
            noisy ADP), plays each one out and grades it with our own projections. That is the <b>status-quo scenario</b>,
            the optimistic end ({pct(OPTIMISM.optimistic.p_playoffs, 1)} playoffs / {pct(OPTIMISM.optimistic.p_title, 1)} title for
            slot 1). After the draft it runs on the real rosters.
          </li>
          <li>
            The <b>headline outlook is a scenario range</b> from the optimism decomposition: the same draft seeds replayed
            with 0, 3, 6 or 11 opponents drafting with our optimizer, each league graded under three &quot;truths&quot; (our
            projections, the market&apos;s ADP-implied view, a 50/50 blend). Central estimate (3 sharp opponents, blend):{" "}
            <b>
              {pct(OPTIMISM.central.p_playoffs[0])}–{pct(OPTIMISM.central.p_playoffs[1])} playoffs,{" "}
              {pct(OPTIMISM.central.p_title[0])}–{pct(OPTIMISM.central.p_title[1])} title
            </b>
            ; pessimistic (11 sharp market-informed opponents, market right) {pct(OPTIMISM.pessimistic.p_playoffs)} /{" "}
            {pct(OPTIMISM.pessimistic.p_title)}; slot 1 alone (all teams draft like us){" "}
            {pct(OPTIMISM.slot_alone.p_playoffs)} / {pct(OPTIMISM.slot_alone.p_title)}; average team 50% / 8.3%.{" "}
            <a href={OPTIMISM.source.url} className="text-accent" target="_blank" rel="noreferrer">
              Research note
            </a>
            .
          </li>
          <li>Reproducible: the same seed gives the same answer, and roster changes are compared with common random numbers.</li>
        </ul>
      </Sec>

      <Sec title="Caveats (read before trusting a number)" open>
        <ul>
          <li>
            <b>In-sample</b>: the same projections pick the players and score the results, and simulated opponents draft by
            ADP with no strategy. A team that &quot;beats the market&quot; by our own numbers looks dominant.
          </li>
          <li>
            <b>Absolute levels are optimistic</b> (e.g. ~6.3 of 9 categories, 90% matchup wins vs a league-average team,{" "}
            {pct(OPTIMISM.optimistic.p_playoffs, 1)} playoffs in the status quo). The decomposition shows most of that comes from
            ADP-drafting opponents and from grading with our own projections, which is why the outlook is shown as a range.
            Use the tools for <b>relative</b> comparisons: candidate A vs B, add vs drop, which categories to chase.
          </li>
          <li>
            <b>Uncalibrated priors</b>: game-to-game variance, ADP spread, season-level error, injury-spell structure and
            streaming recovery are informed guesses until backtesting (T-010) and calibration (T-016). P(title) moves ±4–7 pts
            with them; P(playoffs) is robust.
          </li>
          <li>
            Weekly variance ignores schedule variance and category correlation, so single-week win probabilities are
            overconfident. Week dates other than the playoffs (weeks 18–20) are derived until Yahoo&apos;s schedule is
            loaded.
          </li>
          <li>Nobody manages in-season in the simulations: no trades, waiver breakouts or lineup tactics.</li>
        </ul>
      </Sec>
    </div>
  );
}
