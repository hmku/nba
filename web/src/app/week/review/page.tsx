import Link from "next/link";
import type { Metadata } from "next";
import { cachedClient } from "@/lib/supabase";
import { latestByWeek, type ReviewCat, type ReviewPlayer, type WeekReview } from "@/lib/weekReview";
import { CAT_LABEL, fmtCat, heatStyle } from "@/lib/categories";
import { shortTime } from "@/lib/format";
import { Badge, Disclosure, ErrorNote } from "@/components/ui";
import { EmptyState, Panel, SectionTitle } from "@/components/viz";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Week review" };

function fmtDate(d: string): string {
  return new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function signedNum(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  const s = v.toFixed(digits);
  return v > 0 ? `+${s}` : s;
}

const isPct = (c: ReviewCat) => c.cat === "fg_pct" || c.cat === "ft_pct";

/** Over/under cell: counts in stat units, FG%/FT% as makes above expected on the actual attempts. */
function overUnder(c: ReviewCat, side: "me" | "opp"): string {
  return isPct(c) ? `${signedNum(side === "me" ? c.me_makes_above_expected : c.opp_makes_above_expected)} mk` : signedNum(side === "me" ? c.me_over_under : c.opp_over_under);
}

/** z oriented "good for that team" (TO: fewer is better), for the heat colour only. */
function goodZ(c: ReviewCat, z: number | null): number | null {
  return z == null ? null : c.cat === "tov" ? -z : z;
}

export default async function WeekReviewPage({ searchParams }: { searchParams: Promise<{ week?: string }> }) {
  const { week: wq } = await searchParams;
  const { data, error } = await cachedClient(300)
    .from("model_runs")
    .select("run_id,created_at,summary")
    .eq("kind", "week_review")
    .order("created_at", { ascending: false })
    .limit(60);
  const byWeek = latestByWeek((data as { run_id: string; created_at: string; summary: unknown }[]) ?? []);
  const weeks = [...byWeek.keys()].sort((a, b) => b - a);
  const want = wq ? Number(wq) : (weeks[0] ?? null);
  const r = want != null ? (byWeek.get(want) ?? null) : null;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-2 px-1">
        <h1 className="text-xl font-semibold tracking-tight">Week review</h1>
        <Link href={r ? `/week?week=${r.week}` : "/week"} className="text-[13px] text-accent">
          ← This week
        </Link>
      </div>
      <ErrorNote error={error?.message} />
      {weeks.length > 1 && (
        <div className="flex flex-wrap gap-1.5 px-1">
          {weeks.map((w) => (
            <Link
              key={w}
              href={`/week/review?week=${w}`}
              scroll={false}
              className={`rounded-md border px-2 py-0.5 text-[12px] ${w === r?.week ? "border-accent text-accent" : "border-line text-muted"}`}
            >
              Wk {w}
            </Link>
          ))}
        </div>
      )}
      {!r ? (
        <EmptyState icon="↺" title={want != null ? `No review saved for week ${want}` : "No week review yet"}>
          <p>The daily routine saves one on Mondays: fantasy review week --save.</p>
        </EmptyState>
      ) : (
        <Review r={r} />
      )}
    </div>
  );
}

function Review({ r }: { r: WeekReview }) {
  const tone = r.final.result === "W" ? "good" : r.final.result === "L" ? "bad" : "neutral";
  return (
    <>
      <Panel className="px-4 py-3">
        <div className="flex items-center gap-2 text-[12px] text-muted">
          <span>
            Week {r.week} · {fmtDate(r.weekStart)} – {fmtDate(r.weekEnd)}
          </span>
          <Badge tone={tone}>
            {r.final.score.me}-{r.final.score.opp}
            {r.final.score.ties ? `-${r.final.score.ties}` : ""}
          </Badge>
          {!r.final.complete && (
            <Badge tone="warn" title={`basis: ${r.final.basis}`}>
              provisional · through {fmtDate(r.final.through)}
            </Badge>
          )}
        </div>
        <p className="mt-1.5 text-[15px] leading-snug font-semibold">{r.headline}</p>
        {r.bullets.length > 0 && (
          <ul className="mt-2 space-y-1 text-[13px] leading-snug">
            {r.bullets.map((b) => (
              <li key={b} className="flex gap-1.5">
                <span className="text-muted">•</span>
                <span>{b}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <SectionTitle right={`vs ${r.opponent.name ?? "opponent"} · actual vs projection for the games played`}>Categories</SectionTitle>
      <Panel className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead className="text-[11px] text-muted">
            <tr className="border-b border-line">
              <th className="px-2 py-1.5 text-left font-medium">Cat</th>
              <th className="px-2 py-1.5 text-right font-medium">Us</th>
              <th className="px-2 py-1.5 text-right font-medium">Them</th>
              <th className="px-2 py-1.5 text-right font-medium" title="our actual minus projection for the games that counted">
                Us ±
              </th>
              <th className="px-2 py-1.5 text-right font-medium" title="z from the fitted game-noise model">
                z
              </th>
              <th className="px-2 py-1.5 text-right font-medium">Them ±</th>
              <th className="px-2 py-1.5 text-right font-medium">z</th>
            </tr>
          </thead>
          <tbody>
            {r.categories.map((c) => (
              <tr key={c.cat} className="border-b border-line/60 last:border-0">
                <td className="px-2 py-1.5 font-medium">{CAT_LABEL[c.cat] ?? c.label}</td>
                <td className={`num px-2 py-1.5 text-right ${c.winner === "me" ? "font-semibold text-good" : ""}`}>{fmtCat(c.cat, c.final_me)}</td>
                <td className={`num px-2 py-1.5 text-right ${c.winner === "opp" ? "font-semibold text-bad" : ""}`}>{fmtCat(c.cat, c.final_opp)}</td>
                <td className="num px-2 py-1.5 text-right" title={`proj ${fmtCat(c.cat, c.me_proj_given_games)}`}>
                  {overUnder(c, "me")}
                </td>
                <td className="num px-2 py-1.5 text-right" style={heatStyle(goodZ(c, c.me_z))}>
                  {signedNum(c.me_z)}
                </td>
                <td className="num px-2 py-1.5 text-right" title={`proj ${fmtCat(c.cat, c.opp_proj_given_games)}`}>
                  {overUnder(c, "opp")}
                </td>
                <td className="num px-2 py-1.5 text-right" style={heatStyle(goodZ(c, c.opp_z))}>
                  {signedNum(c.opp_z)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="border-t border-line px-3 py-2 text-[11.5px] leading-snug text-muted">
          Games counted: us {r.me.games_counted} vs {fmtNum(r.me.games_expected_covered_days)} expected at week start; them{" "}
          {r.opp.games_counted} vs {fmtNum(r.opp.games_expected_covered_days)}. Colour = good (green) / bad (red) for that team.
        </p>
      </Panel>

      <div className="grid gap-3 sm:grid-cols-2">
        <Performers title="Us" t={r.me} />
        <Performers title={r.opponent.name ?? "Them"} t={r.opp} />
      </div>

      {r.pWin.length > 0 && (
        <Disclosure summary="P(win) during the week" right={`${pctText(r.pWin[0].p_win)} → ${pctText(r.pWin[r.pWin.length - 1].p_win)}`}>
          <ul className="divide-y divide-line text-[12.5px]">
            {r.pWin.map((p) => (
              <li key={`${p.created_at}`} className="flex justify-between px-4 py-1.5">
                <span className="text-muted">{shortTime(p.created_at)}</span>
                <span className="num">
                  {pctText(p.p_win)}
                  {p.method !== "monte_carlo" && <span className="text-muted"> (analytic)</span>}
                </span>
              </li>
            ))}
          </ul>
        </Disclosure>
      )}

      <Disclosure
        summary="Decisions and missed moves"
        right={r.decisions.available ? `${r.decisions.transactions.length} txns · ${r.decisions.missed.length} missed` : "ledger not read"}
      >
        <div className="space-y-2 px-4 py-3 text-[12.5px] leading-snug">
          {r.decisions.missed.length > 0 && (
            <div>
              <div className="mb-1 text-[11px] font-semibold tracking-wide text-warn uppercase">Missed / failed</div>
              <ul className="space-y-1">
                {r.decisions.missed.map((m, i) => (
                  <li key={i}>
                    <Badge tone="warn">{m.type.replace(/_/g, " ")}</Badge> <span>{m.what}</span>
                    {m.detail && <span className="text-muted"> — {m.detail}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <div className="mb-1 text-[11px] font-semibold tracking-wide text-muted uppercase">Transactions</div>
            <ul className="space-y-1">
              {r.decisions.transactions.map((t, i) => (
                <li key={i} className="flex gap-2">
                  <span className="shrink-0 text-muted">{shortTime(t.at)}</span>
                  <span className="min-w-0">
                    {t.kind}: {t.what} <span className="text-muted">({t.status})</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {!r.decisions.available && <p className="text-muted">Ledger not read: {r.decisions.error}</p>}
        </div>
      </Disclosure>

      <Disclosure summary="How this was made">
        <div className="space-y-1 px-4 py-3 text-[12px] leading-snug text-muted">
          <p>
            Score: Yahoo matchup reads ({r.final.basis}). Stats: ESPN box scores for players in an active slot each day (Yahoo roster reads); per-player
            analysis through {r.analysisTo ?? "—"}.
          </p>
          <p>
            Projections as of week start: {r.projectionSource.kind} {r.projectionSource.path} {r.projectionSource.commit ?? ""}
            {r.projectionSource.data_as_of ? ` (data as of ${r.projectionSource.data_as_of})` : ""}. z: fitted game-noise model (understates weekly variance
            for PTS/FGA/REB by ~15-45%, so |z| is a bit large). Expected games: prior-based estimate.
          </p>
          {r.warnings.map((w) => (
            <p key={w} className="text-warn">
              {w}
            </p>
          ))}
          <p>
            {r.modelVersion} · {r.runId} · {shortTime(r.createdAt)}
          </p>
        </div>
      </Disclosure>
    </>
  );
}

function fmtNum(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(0);
}

function pctText(p: number): string {
  return `${Math.round(p * 100)}%`;
}

function Performers({ title, t }: { title: string; t: WeekReview["me"] }) {
  const line = (p: ReviewPlayer) => p.top.map((x) => `${signedNum(x.delta, x.cat.endsWith("_pct") ? 1 : 0)} ${x.cat.endsWith("_pct") ? `${CAT_LABEL[x.cat]} mk` : CAT_LABEL[x.cat]}`).join(", ");
  return (
    <Panel className="px-4 py-3 text-[12.5px]">
      <div className="mb-1.5 text-[12px] font-semibold">{title}</div>
      {[
        ["Over", t.top_over, "text-good"] as const,
        ["Under", t.top_under, "text-bad"] as const,
      ].map(([label, list, cls]) => (
        <div key={label} className="mb-1">
          <span className={`text-[11px] font-semibold uppercase ${cls}`}>{label}</span>
          <ul>
            {list.map((p) => (
              <li key={p.player_id} className="flex justify-between gap-2">
                <span className="min-w-0 truncate">
                  {p.name} <span className="text-muted">({p.games} g)</span>
                </span>
                <span className="num shrink-0 text-muted">{line(p)}</span>
              </li>
            ))}
            {!list.length && <li className="text-muted">—</li>}
          </ul>
        </div>
      ))}
    </Panel>
  );
}
