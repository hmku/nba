import Link from "next/link";
import optimism from "@/content/optimism.json";
import { pct } from "@/lib/format";
import { Hero, Panel } from "@/components/viz";

/**
 * Pre-draft season outlook as a scenario range (slot 1), from web/src/content/optimism.json, which
 * scripts/optimism.mjs generates from docs/research/optimism_decomposition.md (tests keep them in sync).
 */
export const OPTIMISM = optimism;
type Key = "p_playoffs" | "p_title";

const shortSha = (optimism.source.git_sha ?? "").slice(0, 7);
const range = (r: number[]) => `${Math.round(r[0] * 100)}–${pct(r[1])}`;

/** Position a label at x% without spilling past either edge. */
function at(x: number): React.CSSProperties {
  const shift = x < 12 ? "0" : x > 88 ? "-100%" : "-50%";
  return { left: `${x}%`, transform: `translateX(${shift})` };
}

function RangeBar({ k, label }: { k: Key; label: string }) {
  const lo = optimism.pessimistic[k] * 100;
  const hi = optimism.optimistic[k] * 100;
  const [c0, c1] = optimism.central[k].map((v) => v * 100);
  const base = optimism.baseline[k] * 100;
  return (
    <div>
      <div className="flex items-baseline justify-between text-[12px]">
        <span className="font-medium">{label}</span>
        <span className="num text-muted">
          central <span className="font-semibold text-fg">{range(optimism.central[k])}</span>
        </span>
      </div>
      <div className="relative mt-0.5 h-3.5 text-[10.5px] text-muted">
        <span className="num absolute whitespace-nowrap" style={at(base)}>
          avg {pct(optimism.baseline[k], base < 10 ? 1 : 0)}
        </span>
      </div>
      <div
        className="relative h-2.5 rounded-full bg-panel-2"
        role="img"
        aria-label={`${label}: pessimistic ${pct(optimism.pessimistic[k], 1)}, central ${range(optimism.central[k])}, status quo ${pct(optimism.optimistic[k], 1)}, average team ${pct(optimism.baseline[k], 1)}`}
      >
        <div className="absolute inset-y-0 rounded-full bg-accent/25" style={{ left: `${lo}%`, width: `${hi - lo}%` }} />
        <div className="absolute inset-y-0 rounded-full bg-accent" style={{ left: `${c0}%`, width: `${Math.max(1, c1 - c0)}%` }} />
        <div className="absolute -inset-y-1 w-0.5 rounded bg-fg/70" style={{ left: `calc(${base}% - 1px)` }} />
      </div>
      <div className="num relative mt-0.5 h-4 text-[11px] text-muted">
        <span className="absolute whitespace-nowrap" style={at(lo)} title={optimism.pessimistic.label}>
          {pct(optimism.pessimistic[k], 1)}
        </span>
        <span className="absolute whitespace-nowrap" style={at(hi)} title={optimism.optimistic.label}>
          {pct(optimism.optimistic[k], 1)}
        </span>
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
      <span className="inline-flex items-center gap-1">
        <span className="h-2 w-4 rounded-full bg-accent/25" />
        pessimistic → status quo
      </span>
      <span className="inline-flex items-center gap-1">
        <span className="h-2 w-3 rounded-full bg-accent" />
        central
      </span>
      <span className="inline-flex items-center gap-1">
        <span className="h-3 w-0.5 rounded bg-fg/70" />
        average team
      </span>
    </div>
  );
}

function SlotLine() {
  const s = optimism.slot_alone;
  return (
    <p className="rounded-lg bg-panel-2 px-3 py-2 text-[13px] leading-snug">
      <span className="font-semibold">What slot #1 alone is worth:</span>{" "}
      <span className="font-semibold">
        {pct(s.p_playoffs)} playoffs · {pct(s.p_title)} title
      </span>{" "}
      <span className="text-muted">
        if all 12 teams drafted exactly like us (average team {pct(optimism.baseline.p_playoffs)} /{" "}
        {pct(optimism.baseline.p_title, 1)}).
      </span>
    </p>
  );
}

function Caveats() {
  return (
    <details className="group">
      <summary className="cursor-pointer list-none text-[13px] text-accent select-none [&::-webkit-details-marker]:hidden">
        <span className="group-open:hidden">Caveats ▸</span>
        <span className="hidden group-open:inline">Caveats ▾</span>
      </summary>
      <ul className="mt-1.5 space-y-1 text-[12.5px] leading-snug text-muted [&_li]:ml-4 [&_li]:list-disc">
        <li>
          This range was computed on {optimism.source.date} ({shortSha}), before the ESPN blend, the calibrated
          simulator, the unified noise model and tonight&apos;s draft policy (value search + simulated availability). It
          has not been recomputed; the Model &amp; sims tab has the current-code projection.
        </li>
        <li>
          &quot;The market is right&quot; is built by rescaling each player&apos;s volume to match Yahoo ADP order; it keeps
          our category mix, percentages and games played, so even that scenario agrees with us on every player&apos;s
          category profile.
        </li>
        <li>
          How noisy &quot;sharp&quot; opponents are (and how far ADP drafters stray from ADP) is a guess, not fitted; it
          moves the title odds by several points.
        </li>
        <li>
          Every number comes from the season simulator. Its game-to-game noise and season-level projection error are
          now fitted (docs/research/sim_calibration.md); streaming and opponent sharpness are still priors. Neither our projections nor the market is the truth; the 50/50 blend is a convenience.
        </li>
      </ul>
    </details>
  );
}

function ScenarioTable() {
  const cell = (c: { p_playoffs: number; p_title: number }) => (
    <span className="whitespace-nowrap">
      {pct(c.p_playoffs)} <span className="text-muted">/</span> {pct(c.p_title)}
    </span>
  );
  return (
    <details className="group">
      <summary className="cursor-pointer list-none text-[13px] text-accent select-none [&::-webkit-details-marker]:hidden">
        <span className="group-open:hidden">All scenarios ▸</span>
        <span className="hidden group-open:inline">All scenarios ▾</span>
      </summary>
      <div className="scroll-thin mt-2 overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[10.5px] tracking-wider text-muted uppercase">
              <th className="py-1 pr-2 text-left font-medium">Who drafts how</th>
              <th className="px-1 text-right font-medium">Ours</th>
              <th className="px-1 text-right font-medium">Blend</th>
              <th className="pl-1 text-right font-medium">Market</th>
            </tr>
          </thead>
          <tbody className="num">
            {optimism.table.map((r) => (
              <tr key={r.id} className="border-t border-line align-top">
                <td className="py-1.5 pr-2 font-sans leading-tight">
                  <span className="font-medium">{r.label}</span>
                  <div className="text-[11px] text-muted">{r.who}</div>
                </td>
                <td className="px-1 py-1.5 text-right">{cell(r.ours)}</td>
                <td className="px-1 py-1.5 text-right">{cell(r.blend)}</td>
                <td className="py-1.5 pl-1 text-right">{cell(r.market)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1.5 text-[11px] text-muted">
          P(playoffs) / P(title) for slot 1. Columns = whose view of the players is true when the season is played: our
          projections, the market&apos;s (ADP), or a 50/50 blend.
        </p>
      </div>
    </details>
  );
}

/** Headline pre-draft outlook: central estimate, range bar, slot value, explanation, caveats. */
export function ScenarioOutlook({ compact = false }: { compact?: boolean }) {
  const c = optimism.central;
  const lo = optimism.pessimistic;
  const hi = optimism.optimistic;
  return (
    <Panel className="space-y-3 px-4 py-3">
      <div className="grid grid-cols-2 gap-3">
        <Hero label="Playoffs" value={range(c.p_playoffs)} sub="central estimate" />
        <Hero label="Title" value={range(c.p_title)} sub="central estimate" />
      </div>
      <div className="space-y-2">
        <RangeBar k="p_playoffs" label="P(playoffs)" />
        <RangeBar k="p_title" label="P(title)" />
        <Legend />
      </div>
      <SlotLine />
      <p className="text-[13px] leading-snug text-muted">
        The single-number projection ({pct(hi.p_playoffs, 1)} / {pct(hi.p_title, 1)}) is the <b className="text-fg">optimistic end</b>:
        it assumes the other 11 teams draft by Yahoo ADP plus noise, and it grades every roster with our own projections,
        so it rewards us for disagreeing with the market. With a few sharp opponents and the truth halfway between our
        projections and the market, slot 1 is {range(c.p_playoffs)} playoffs / {range(c.p_title)} title. If all 11 draft
        sharply with the market&apos;s view and the market is right: {pct(lo.p_playoffs)} / {pct(lo.p_title)}, still
        better than an average team.
      </p>
      {!compact && <ScenarioTable />}
      <Caveats />
      <p className="text-[11px] text-muted">
        Slot 1; 60–100 simulated drafts × 500 seasons per scenario.{" "}
        <a href={optimism.source.url} className="text-accent" target="_blank" rel="noreferrer">
          Research note
        </a>{" "}
        <span className="num">
          @{shortSha} · {optimism.source.date}
        </span>
        {compact && (
          <>
            {" · "}
            <Link href="/draft?view=model" className="text-accent">
              Model & sims →
            </Link>
          </>
        )}
      </p>
    </Panel>
  );
}
