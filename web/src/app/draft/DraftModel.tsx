import Link from "next/link";
import type { DraftSim, SeasonSim } from "@/lib/sim";
import { explanationText } from "@/lib/sim";
import { CATS } from "@/lib/categories";
import { fmt, pct, signed, shortTime } from "@/lib/format";
import {
  CatLabel,
  CatStrip,
  EmptyState,
  Explain,
  Hero,
  MiniHist,
  Panel,
  PctSe,
  ProbTrack,
  SectionTitle,
  ShareBar,
  probText,
} from "@/components/viz";

const AVAIL_SHOWN = 15;

function availTone(p: number | null): React.CSSProperties {
  if (p == null) return {};
  // accent alpha scaled by probability (sequential, one hue)
  return { backgroundColor: `color-mix(in srgb, var(--accent) ${Math.round(p * 55)}%, transparent)` };
}

export default function DraftModel({ dsim, pre }: { dsim: DraftSim | null; pre: SeasonSim | null }) {
  if (!dsim && !pre)
    return (
      <EmptyState icon="∿" title="No simulations published yet">
        Run <code className="num">fantasy draft simulate</code> (optimizer vs best-player-available over simulated drafts)
        and <code className="num">fantasy sim preseason</code> (season outlook), then <code className="num">fantasy publish</code>.
        This page shows the latest <code className="num">draft_sim</code> and pre-draft <code className="num">season_sim</code>{" "}
        runs.
      </EmptyState>
    );
  const ex = dsim?.explanations ?? null;
  const pex = pre?.explanations ?? null;
  return (
    <div className="space-y-3">
      {dsim && <DraftSimSection d={dsim} />}
      {pre ? (
        <SeasonOutlook s={pre} />
      ) : (
        <EmptyState icon="◷" title="No pre-draft season projection yet">
          <code className="num">fantasy sim preseason</code> plays simulated leagues through a whole season to estimate
          P(playoffs) and P(title).
        </EmptyState>
      )}
      {dsim && <DraftSimDetails d={dsim} />}
      {(explanationText(ex, "caveats") || explanationText(pex, "caveats") || dsim?.warnings.length || pre?.warnings.length) && (
        <Panel className="space-y-2 px-4 py-3">
          <div className="text-[13px] font-semibold text-warn">Caveats</div>
          <Explain text={explanationText(ex, "caveats")} />
          {explanationText(pex, "caveats") !== explanationText(ex, "caveats") && <Explain text={explanationText(pex, "caveats")} />}
          {[...(dsim?.warnings ?? []), ...(pre?.warnings ?? [])].map((w, i) => (
            <p key={i} className="text-xs text-warn">
              ⚠ {w}
            </p>
          ))}
          <p className="text-xs text-muted">
            See <Link href="/draft?view=how" className="text-accent">How it works</Link> for the full list.
          </p>
        </Panel>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ headline */

function DraftSimSection({ d }: { d: DraftSim }) {
  const ex = d.explanations;
  const e = d.edgeEcw;
  const sig = e.mean != null && e.se != null && e.se > 0 ? e.mean / e.se : null;
  return (
    <>
      <SectionTitle right={<>{d.nSims ?? "?"} drafts · {shortTime(d.run.created_at)}</>}>Optimizer edge</SectionTitle>
      <Panel className="px-4 py-3">
        <div className="grid grid-cols-2 gap-3">
          <Hero
            label="Edge / week"
            value={
              <>
                {signed(e.mean, 3)}
                {e.se != null && <span className="text-[0.6em] font-normal text-muted"> ±{fmt(e.se, 3)}</span>}
              </>
            }
            sub={
              <>
                expected category wins
                {sig != null && <> · {sig >= 2 ? `${fmt(sig, 0)} SE` : "not significant"}</>}
              </>
            }
            tone={e.mean != null && e.mean > 0 ? "text-good" : undefined}
          />
          <Hero
            label="Optimizer better"
            value={e.pBetter != null ? pct(e.pBetter) : "—"}
            sub="of paired simulated drafts"
          />
        </div>
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="text-[11px] tracking-wider text-muted uppercase">
              <th className="py-1 text-left font-medium" />
              <th className="py-1 text-right font-medium">Optimizer</th>
              <th className="py-1 text-right font-medium">BPA</th>
            </tr>
          </thead>
          <tbody className="num">
            <tr className="border-t border-line">
              <td className="py-1.5 font-sans text-muted">Exp. category wins / 9</td>
              <td className="py-1.5 text-right">{fmt(d.opt.ecw, 2)}</td>
              <td className="py-1.5 text-right text-muted">{fmt(d.bpa.ecw, 2)}</td>
            </tr>
            <tr className="border-t border-line">
              <td className="py-1.5 font-sans text-muted">P(win a week)</td>
              <td className="py-1.5 text-right">{pct(d.opt.mwp, 1)}</td>
              <td className="py-1.5 text-right text-muted">{pct(d.bpa.mwp, 1)}</td>
            </tr>
            {d.edgeMwp.mean != null && (
              <tr className="border-t border-line">
                <td className="py-1.5 font-sans text-muted">Matchup-win edge</td>
                <td className="py-1.5 text-right" colSpan={2}>
                  {signed(d.edgeMwp.mean * 100, 2)} pts
                  {d.edgeMwp.se != null && <span className="text-muted"> ±{fmt(d.edgeMwp.se * 100, 2)}</span>}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="mt-2 space-y-1.5">
          <Explain text={explanationText(ex, "edge", "overview")} />
          <p className="text-xs text-muted">
            BPA = take the highest-valued player available. Both are scored vs a league-average opponent, in-sample.
          </p>
        </div>
      </Panel>
    </>
  );
}

/* ------------------------------------------------------------------ season outlook */

function SeasonOutlook({ s }: { s: SeasonSim }) {
  const m = s.myTeam;
  const g = (k: string) => (m && typeof m[k] === "number" ? (m[k] as number) : null);
  const across = s.acrossLeagues;
  const a = (k: string) => (across && typeof across[k] === "number" ? (across[k] as number) : null);
  const ex = s.explanations;
  const myRow = s.table.find((r) => r.isMe || r.team === s.myTeamId);
  return (
    <>
      <SectionTitle right={<>{s.isPreseason ? "pre-draft projection" : "from rosters"} · {shortTime(s.run.created_at)}</>}>
        Season outlook
      </SectionTitle>
      <Panel className="px-4 py-3">
        <div className="grid grid-cols-3 gap-3">
          <Hero label="Playoffs" value={<PctSe v={g("p_playoffs") ?? myRow?.pPlayoffs ?? null} se={g("p_playoffs_se")} />} />
          <Hero label="Title" value={<PctSe v={g("p_title") ?? myRow?.pTitle ?? null} se={g("p_title_se")} />} />
          <Hero label="Exp. rank" value={fmt(g("exp_rank") ?? myRow?.expRank ?? null, 1)} sub="of 12" />
        </div>
        {(a("p_playoffs_min") != null || a("p_title_min") != null) && (
          <p className="mt-2 text-xs text-muted">
            Across {s.nLeagues ?? "the"} simulated drafts: playoffs {pct(a("p_playoffs_min"))}–{pct(a("p_playoffs_max"))}, title{" "}
            {pct(a("p_title_min"))}–{pct(a("p_title_max"))}.
          </p>
        )}
        {s.standingDist.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-[11px] tracking-wider text-muted uppercase">Final regular-season standing</div>
            <MiniHist bins={s.standingDist.map((b) => ({ label: String(b.rank), p: b.p }))} highlightUpTo={6} />
            <p className="mt-1 text-[11px] text-muted">Highlighted = playoff spots (top 6).</p>
          </div>
        )}
        {Object.keys(s.categoryProfile).length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-[11px] tracking-wider text-muted uppercase">My category rank (1 = best of 12)</div>
            <CatStrip
              cells={Object.fromEntries(CATS.map((c) => [c, { value: s.categoryProfile[c]?.rank ?? null, z: s.categoryProfile[c]?.z ?? null }]))}
            />
          </div>
        )}
        <div className="mt-3 space-y-1.5">
          <Explain text={explanationText(ex, "overview")} />
          <Link href="/league" className="inline-block text-sm text-accent">
            League table & all teams →
          </Link>
        </div>
      </Panel>
    </>
  );
}

/* ------------------------------------------------------------------ details */

function DraftSimDetails({ d }: { d: DraftSim }) {
  const ex = d.explanations;
  const cats = CATS.filter((c) => d.catOpt[c]);
  return (
    <>
      {cats.length > 0 && (
        <>
          <SectionTitle>My category profile</SectionTitle>
          <Panel className="px-4 py-3">
            <div className="mb-2 text-[11px] text-muted">
              <div>Weekly win probability vs a league-average team</div>
              <div className="mt-1 flex items-center gap-3">
                <span className="inline-flex items-center gap-1">
                  <span className="h-2 w-2 rounded-full bg-accent" />
                  mean
                </span>
                <span className="inline-flex items-center gap-1">
                  <span className="h-1.5 w-3 rounded-full bg-accent/35" />
                  p10–p90
                </span>
                {Object.keys(d.catBpa).length > 0 && (
                  <span className="inline-flex items-center gap-1">
                    <span className="h-2.5 w-0.5 rounded bg-muted" />
                    BPA
                  </span>
                )}
              </div>
            </div>
            <ul className="space-y-1.5">
              {cats.map((c) => {
                const b = d.catOpt[c]!;
                const bp = d.catBpa[c];
                return (
                  <li key={c} className="grid grid-cols-[3.6rem_1fr_2.6rem] items-center gap-2 text-[13px]">
                    <CatLabel c={c} />
                    <ProbTrack
                      mean={b.mean}
                      lo={b.p10}
                      hi={b.p90}
                      compare={bp?.mean ?? null}
                      title={`mean ${pct(b.mean)} · p10 ${pct(b.p10)} · p90 ${pct(b.p90)}${bp?.mean != null ? ` · BPA ${pct(bp.mean)}` : ""}`}
                    />
                    <span className={`num text-right ${probText(b.mean)}`}>{pct(b.mean)}</span>
                  </li>
                );
              })}
            </ul>
            <div className="mt-2">
              <Explain text={explanationText(ex, "category_win_probs")} />
            </div>
          </Panel>
        </>
      )}

      {d.picksDist.length > 0 && (
        <>
          <SectionTitle>Who I take at each pick</SectionTitle>
          <Panel>
            <ul className="divide-y divide-line">
              {d.picksDist.map((p) => (
                <li key={p.pick} className="px-4 py-2.5">
                  <div className="flex items-baseline gap-2">
                    <span className="num w-10 shrink-0 text-sm font-semibold">#{p.pick}</span>
                    <span className="min-w-0 flex-1 truncate text-[15px] font-medium">{p.top[0]?.name ?? "—"}</span>
                    <span className="num shrink-0 text-sm">{pct(p.top[0]?.freq ?? null)}</span>
                  </div>
                  <div className="mt-1 ml-12">
                    <ShareBar v={p.top[0]?.freq ?? null} />
                  </div>
                  {p.top.length > 1 && (
                    <div className="mt-1 ml-12 truncate text-xs text-muted">
                      {p.top
                        .slice(1, 4)
                        .map((t) => `${t.name} ${pct(t.freq)}`)
                        .join(" · ")}
                      {p.nDistinct != null && p.nDistinct > 4 ? ` · ${p.nDistinct} different` : ""}
                    </div>
                  )}
                </li>
              ))}
            </ul>
            <div className="border-t border-line px-4 py-2.5">
              <Explain text={explanationText(ex, "picks_distribution")} />
            </div>
          </Panel>
        </>
      )}

      {d.availPlayers.length > 0 && d.availPicks.length > 0 && <AvailTable d={d} />}

      {d.strategyCounts.length > 0 && (
        <>
          <SectionTitle>Strategy scan</SectionTitle>
          <Panel className="px-4 py-3">
            <ul className="space-y-1.5">
              {(() => {
                const tot = d.strategyCounts.reduce((a, b) => a + b.count, 0) || 1;
                return d.strategyCounts.map((s) => (
                  <li key={s.name} className="grid grid-cols-[7rem_1fr_3rem] items-center gap-2 text-[13px]">
                    <span className="truncate">{s.name.replace(/_/g, " ")}</span>
                    <ShareBar v={s.count / tot} tone={s.name === "balanced" ? "accent" : "muted"} />
                    <span className="num text-right">{pct(s.count / tot)}</span>
                  </li>
                ));
              })()}
            </ul>
            <p className="mt-2 text-[13px] leading-snug text-muted">
              Strategy the optimizer settled on at my first pick. A &quot;punt&quot; deliberately gives up one category
              (e.g. FT%) to be stronger in the other eight.
            </p>
          </Panel>
        </>
      )}

      {d.examples.length > 0 && (
        <>
          <SectionTitle>Example rosters</SectionTitle>
          <div className="space-y-2">
            {d.examples.map((r) => (
              <details key={r.label} className="group rounded-xl border border-line bg-panel">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 select-none [&::-webkit-details-marker]:hidden">
                  <span className="text-[10px] text-muted transition-transform group-open:rotate-90">▶</span>
                  <span className="flex-1 text-[15px] font-medium capitalize">{r.label}</span>
                  <span className="num text-xs text-muted">
                    {fmt(r.ecw, 2)} cats · {pct(r.mwp)} wk
                  </span>
                </summary>
                <div className="border-t border-line px-4 py-2">
                  <ol className="divide-y divide-line text-sm">
                    {r.picks.map((p, i) => (
                      <li key={`${p.id}-${i}`} className="flex items-baseline gap-2 py-1.5">
                        <span className="num w-10 shrink-0 text-xs text-muted">{p.pick != null ? `#${p.pick}` : i + 1}</span>
                        <span className="min-w-0 flex-1 truncate">{p.name}</span>
                        {p.positions.length > 0 && <span className="shrink-0 text-xs text-muted">{p.positions.join("/")}</span>}
                      </li>
                    ))}
                  </ol>
                  {Object.keys(r.catWin).length > 0 && (
                    <div className="mt-2 grid grid-cols-3 gap-x-3 gap-y-1 border-t border-line pt-2 text-[12.5px]">
                      {CATS.map((c) => (
                        <div key={c} className="flex items-center justify-between">
                          <CatLabel c={c} />
                          <span className={`num ${probText(r.catWin[c])}`}>{pct(r.catWin[c] ?? null)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {r.strategy && <p className="mt-2 text-xs text-muted">strategy: {r.strategy}</p>}
                </div>
              </details>
            ))}
          </div>
          <Explain text={explanationText(ex, "example_rosters")} />
        </>
      )}
    </>
  );
}

function AvailTable({ d }: { d: DraftSim }) {
  const ex = d.explanations;
  const rows = (list: DraftSim["availPlayers"]) =>
    list.map((p) => (
      <tr key={p.id}>
        <td className="sticky left-0 z-10 max-w-[8.5rem] bg-panel py-1.5 pr-2 pl-3">
          <div className="truncate text-[13.5px] font-medium">{p.name}</div>
          <div
            className="num truncate text-[11px] text-muted"
            title={`ADP ${p.adp ?? "n/a"} · market rank ${p.marketRank ?? "—"} · our value rank ${p.valueRank ?? "—"}`}
          >
            {p.adp != null ? fmt(p.adp, 0) : `~${p.marketRank ?? "?"}`}/{p.valueRank ?? "—"}
            {p.positions.length ? ` ${p.positions.join("/")}` : ""}
          </div>
        </td>
        {d.availPicks.map((k, i) => {
          const v = p.p[i] ?? null;
          return (
            <td key={k} className="num px-1 text-center text-[12.5px]" style={availTone(v)} title={`P(available at #${k}) = ${pct(v, 1)}`}>
              {v == null ? "—" : v >= 0.995 ? "✓" : v < 0.005 ? "·" : pct(v)}
            </td>
          );
        })}
        <td className="num px-2 text-right text-[12px] text-muted">{p.takenByMe != null ? pct(p.takenByMe) : "—"}</td>
      </tr>
    ));
  const head = (
    <thead>
      <tr className="text-[10.5px] tracking-wider text-muted uppercase">
        <th className="sticky left-0 z-10 bg-panel py-1.5 pl-3 text-left font-medium">Player</th>
        {d.availPicks.map((k) => (
          <th key={k} className="num px-1 text-center font-medium">
            #{k}
          </th>
        ))}
        <th className="px-2 text-right font-medium" title="share of simulated drafts in which I drafted him">
          mine
        </th>
      </tr>
    </thead>
  );
  return (
    <>
      <SectionTitle right="P(still on the board)">Availability at my picks</SectionTitle>
      <Panel className="overflow-hidden">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full border-separate border-spacing-0 [&_tbody_td]:border-t [&_tbody_td]:border-line">
            {head}
            <tbody>{rows(d.availPlayers.slice(0, AVAIL_SHOWN))}</tbody>
          </table>
        </div>
        {d.availPlayers.length > AVAIL_SHOWN && (
          <details className="group border-t border-line">
            <summary className="cursor-pointer list-none px-4 py-2.5 text-sm text-accent select-none [&::-webkit-details-marker]:hidden">
              <span className="group-open:hidden">Show all {d.availPlayers.length} players</span>
              <span className="hidden group-open:inline">Hide</span>
            </summary>
            <div className="scroll-thin overflow-x-auto">
              <table className="w-full border-separate border-spacing-0 [&_tbody_td]:border-t [&_tbody_td]:border-line">
                {head}
                <tbody>{rows(d.availPlayers.slice(AVAIL_SHOWN))}</tbody>
              </table>
            </div>
          </details>
        )}
        <div className="border-t border-line px-4 py-2.5">
          <Explain
            text={
              explanationText(ex, "availability") ??
              "Probability the player is still undrafted when my pick comes, from the ADP availability model (Normal around ADP)."
            }
          />
          <p className="mt-1 text-xs text-muted">
            Under each name: ADP / our value rank. ✓ = certain, · = gone. &quot;mine&quot; = how often the optimizer drafted him in
            simulation.
          </p>
        </div>
      </Panel>
    </>
  );
}
