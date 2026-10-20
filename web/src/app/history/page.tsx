import type { Metadata } from "next";
import Link from "next/link";
import { anonClient } from "@/lib/supabase";
import type { DataSnapshot, DraftRecommendation, ModelRun } from "@/lib/types";
import { Badge, Card, Empty, ErrorNote } from "@/components/ui";
import { fmt, shortTime } from "@/lib/format";
import { Ago } from "@/components/client";
import { RunJson } from "./RunJson";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "History" };

/** Model runs per page. The list carries summary columns only; full params/inputs/summary JSON loads on open. */
const RUNS_PER_PAGE = 25;
/** Summary columns + a few small summary fields (the full JSON is ~75 KB per sim run). */
const RUN_LIST_COLS =
  "run_id,kind,model_version,git_sha,created_at,title:summary->>title,headline:summary->>headline,scenario:summary->>scenario," +
  "status:summary->>status,n_sims:summary->>n_sims,n_players:summary->>n_players,command:params->>command";
const TOP3_COLS = [0, 1, 2]
  .map((i) => `r${i}_name:payload->recommendations->${i}->>name,r${i}_id:payload->recommendations->${i}->>player_id,r${i}_score:payload->recommendations->${i}->score`)
  .join(",");
type RunRow =Omit<ModelRun, "params" | "inputs" | "summary"> & {
  title?: string | null;
  headline?: string | null;
  scenario?: string | null;
  status?: string | null;
  n_sims?: string | null;
  n_players?: string | null;
  command?: string | null;
};

function runBlurb(r: RunRow): string {
  return [
    r.title ?? r.headline ?? r.scenario,
    r.status,
    r.n_sims && `${r.n_sims} sims`,
    r.n_players && `${r.n_players} players`,
    r.command && `cmd: ${r.command}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function historyHref(kind: string | null | undefined, page = 1): string {
  const q = new URLSearchParams();
  if (kind) q.set("kind", kind);
  if (page > 1) q.set("page", String(page));
  const s = q.toString();
  return `${s ? `/history?${s}` : "/history"}#runs`;
}

export default async function HistoryPage({ searchParams }: { searchParams: Promise<{ kind?: string; page?: string }> }) {
  const sp = await searchParams;
  const kind = typeof sp.kind === "string" && sp.kind ? sp.kind.slice(0, 40) : null;
  const pageNo = Math.min(1000, Math.max(1, Math.floor(Number(sp.page)) || 1));
  const db = anonClient();
  const from = (pageNo - 1) * RUNS_PER_PAGE;
  // one extra row tells whether an older page exists
  let q = db.from("model_runs").select(RUN_LIST_COLS).order("created_at", { ascending: false }).order("run_id").range(from, from + RUNS_PER_PAGE);
  if (kind) q = q.eq("kind", kind);
  const [runsR, recsR, snapsR, kindsR] = await Promise.all([
    q,
    db
      .from("draft_recommendations")
      .select(`rec_id,draft_id,as_of_pick,for_pick,model_run_id,created_at,strategy_name:payload->strategy->>name,${TOP3_COLS}`)
      .order("created_at", { ascending: false })
      .limit(100),
    db.from("data_snapshots").select("snapshot_id,source,rows,fetched_at").order("fetched_at", { ascending: false }).limit(200),
    db.from("model_runs").select("run_id,kind,model_version,created_at").order("created_at", { ascending: false }).limit(200),
  ]);
  const latestByKind = new Map<string, ModelRun>();
  for (const r of (kindsR.data as ModelRun[]) ?? []) if (!latestByKind.has(r.kind)) latestByKind.set(r.kind, r);
  const latestBySource = new Map<string, DataSnapshot>();
  for (const s of (snapsR.data as DataSnapshot[]) ?? []) if (!latestBySource.has(s.source)) latestBySource.set(s.source, s);
  const FRESH: Record<string, number> = { projections: 36 * 3600, valuation: 36 * 3600, draft_rec: 3600 };
  const fetched = (runsR.data as unknown as RunRow[]) ?? [];
  const runs = fetched.slice(0, RUNS_PER_PAGE);
  const hasOlder = fetched.length > RUNS_PER_PAGE;
  type RecLite = Omit<DraftRecommendation, "payload"> & {
    recommendations?: { name?: string; player_id?: string; score?: number }[] | null;
    strategy?: { name?: string } | null;
  };
  // only the top 3 of each recommendation list (the full payload is ~15 KB per row)
  const recs: RecLite[] = ((recsR.data as unknown as Record<string, unknown>[]) ?? []).map((r) => ({
    ...(r as unknown as RecLite),
    recommendations: [0, 1, 2]
      .filter((i) => r[`r${i}_name`] != null || r[`r${i}_id`] != null)
      .map((i) => ({
        name: (r[`r${i}_name`] as string | null) ?? undefined,
        player_id: (r[`r${i}_id`] as string | null) ?? undefined,
        score: typeof r[`r${i}_score`] === "number" ? (r[`r${i}_score`] as number) : undefined,
      })),
    strategy: r.strategy_name ? { name: String(r.strategy_name) } : null,
  }));
  const kinds = [...new Set(["projections", "valuation", "draft_rec", "week", "trade", ...latestByKind.keys()])];

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold tracking-tight">History</h1>
      <ErrorNote error={runsR.error?.message} />
      <ErrorNote error={recsR.error?.message} />

      <div className="grid gap-3 md:grid-cols-2">
        <Card title="Latest model run by kind" bodyClassName="p-0">
          {latestByKind.size === 0 ? (
            <Empty>No model runs.</Empty>
          ) : (
            <table className="dense w-full text-sm">
              <tbody>
                {[...latestByKind.values()].map((r) => (
                  <tr key={r.kind}>
                    <td className="font-medium">{r.kind}</td>
                    <td className="truncate text-xs text-muted">{r.model_version}</td>
                    <td className="text-right">
                      <Ago ts={r.created_at} staleAfter={FRESH[r.kind] ?? 86400} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Data snapshots (latest by source)" bodyClassName="p-0">
          {latestBySource.size === 0 ? (
            <Empty>No snapshots.</Empty>
          ) : (
            <table className="dense w-full text-sm">
              <tbody>
                {[...latestBySource.values()].map((s) => (
                  <tr key={s.source}>
                    <td className="max-w-[12rem] truncate font-medium" title={s.source}>
                      {s.source}
                    </td>
                    <td className="num text-right text-xs text-muted">{s.rows ?? "—"} rows</td>
                    <td className="text-right">
                      <Ago ts={s.fetched_at} staleAfter={3 * 86400} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card
        id="runs"
        className="scroll-mt-16"
        title="Model runs"
        bodyClassName="p-0"
        right={
          <span className="flex flex-wrap gap-x-2 gap-y-1">
            <Link href={historyHref(null)} className={!kind ? "text-accent" : ""}>
              all
            </Link>
            {kinds.map((k) => (
              <Link key={k} href={historyHref(k)} className={kind === k ? "text-accent" : ""}>
                {k}
              </Link>
            ))}
          </span>
        }
      >
        {runs.length === 0 ? (
          <Empty>{pageNo > 1 ? "No older model runs." : "No model runs."}</Empty>
        ) : (
          <ul>
            {runs.map((r) => {
              const blurb = runBlurb(r);
              return (
                <li key={r.run_id} className="border-t border-line px-3 py-2 first:border-t-0">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <Badge tone="accent">{r.kind}</Badge>
                    <code className="min-w-0 break-all font-medium">{r.run_id}</code>
                    <span className="text-xs text-muted">v{r.model_version}</span>
                    {r.git_sha && <code className="text-xs text-muted">{r.git_sha.slice(0, 8)}</code>}
                    <span className="ml-auto text-xs text-muted">{shortTime(r.created_at)}</span>
                  </div>
                  {blurb && <p className="mt-0.5 line-clamp-2 text-xs text-muted">{blurb}</p>}
                  <div className="mt-1 grid gap-1 sm:grid-cols-3">
                    <RunJson runId={r.run_id} field="params" label="params" />
                    <RunJson runId={r.run_id} field="inputs" label="inputs (provenance)" />
                    <RunJson runId={r.run_id} field="summary" label="summary" />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {(pageNo > 1 || hasOlder) && (
          <nav aria-label="Model run pages" className="flex items-center gap-2 border-t border-line px-3 py-2 text-xs">
            {pageNo > 1 ? (
              <Link href={historyHref(kind, pageNo - 1)} className="text-accent hover:underline" rel="prev">
                ← Newer
              </Link>
            ) : (
              <span className="text-muted">Newest</span>
            )}
            <span className="mx-auto text-muted">
              page {pageNo}
              {runs.length > 0 && ` · runs ${from + 1}–${from + runs.length}`}
            </span>
            {hasOlder ? (
              <Link href={historyHref(kind, pageNo + 1)} className="text-accent hover:underline" rel="next">
                Older →
              </Link>
            ) : (
              <span className="text-muted">Oldest</span>
            )}
          </nav>
        )}
      </Card>

      <Card title="Draft recommendations over time" bodyClassName="p-0">
        {recs.length === 0 ? (
          <Empty>No recommendations.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="dense w-full text-sm">
              <thead>
                <tr className="text-left">
                  <th>Created</th>
                  <th>Draft</th>
                  <th className="text-right">As of</th>
                  <th className="text-right">For</th>
                  <th>Top 3</th>
                  <th>Strategy</th>
                </tr>
              </thead>
              <tbody>
                {recs.map((r) => (
                  <tr key={r.rec_id}>
                    <td className="whitespace-nowrap text-xs text-muted">{shortTime(r.created_at)}</td>
                    <td className="text-xs">
                      <Link href={`/draft?id=${encodeURIComponent(r.draft_id)}`} className="text-accent">
                        {r.draft_id}
                      </Link>
                    </td>
                    <td className="num text-right">{r.as_of_pick}</td>
                    <td className="num text-right">{r.for_pick ?? "—"}</td>
                    <td className="text-xs">
                      {(r.recommendations ?? []).slice(0, 3).map((x, i) => (
                        <span key={i} className="mr-2 whitespace-nowrap">
                          {x.name ?? x.player_id} <span className="num text-muted">{fmt(x.score, 1)}</span>
                        </span>
                      ))}
                    </td>
                    <td className="text-xs text-muted">{r.strategy?.name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid gap-3 md:grid-cols-2">
        <Card title="Prediction vs actual (phase 3)">
          <Empty>
            Placeholder: once the season starts, weekly category projections vs. actual box-score totals will be charted here
            (calibration of category win probabilities, projection error by stat).
          </Empty>
        </Card>
        <Card title="Decision quality (phase 3)">
          <Empty>Placeholder: agent-vs-model disagreements and their realized outcomes.</Empty>
        </Card>
      </div>
    </div>
  );
}
