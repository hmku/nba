import type { Metadata } from "next";
import Link from "next/link";
import { anonClient } from "@/lib/supabase";
import type { DraftRecommendation, ModelRun } from "@/lib/types";
import { Badge, Card, Empty, ErrorNote, JsonView } from "@/components/ui";
import { fmt, shortTime } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "History" };

export default async function HistoryPage({ searchParams }: { searchParams: Promise<{ kind?: string }> }) {
  const { kind } = await searchParams;
  const db = anonClient();
  let q = db.from("model_runs").select("*").order("created_at", { ascending: false }).limit(100);
  if (kind) q = q.eq("kind", kind);
  const [runsR, recsR] = await Promise.all([
    q,
    db
      .from("draft_recommendations")
      .select("rec_id,draft_id,as_of_pick,for_pick,model_run_id,created_at,payload->recommendations,payload->strategy")
      .order("created_at", { ascending: false })
      .limit(100),
  ]);
  const runs = (runsR.data as ModelRun[]) ?? [];
  type RecLite = Omit<DraftRecommendation, "payload"> & {
    recommendations?: { name?: string; player_id?: string; score?: number }[] | null;
    strategy?: { name?: string } | null;
  };
  const recs = (recsR.data as unknown as RecLite[]) ?? [];
  const kinds = ["projections", "valuation", "draft_rec", "week", "trade"];

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold tracking-tight">History</h1>
      <ErrorNote error={runsR.error?.message} />
      <ErrorNote error={recsR.error?.message} />

      <Card
        title="Model runs"
        bodyClassName="p-0"
        right={
          <span className="flex flex-wrap gap-1">
            <Link href="/history" className={!kind ? "text-accent" : ""}>
              all
            </Link>
            {kinds.map((k) => (
              <Link key={k} href={`/history?kind=${k}`} className={kind === k ? "text-accent" : ""}>
                {k}
              </Link>
            ))}
          </span>
        }
      >
        {runs.length === 0 ? (
          <Empty>No model runs.</Empty>
        ) : (
          <ul>
            {runs.map((r) => (
              <li key={r.run_id} className="border-t border-line px-3 py-2 first:border-t-0">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge tone="accent">{r.kind}</Badge>
                  <code className="font-medium">{r.run_id}</code>
                  <span className="text-xs text-muted">v{r.model_version}</span>
                  {r.git_sha && <code className="text-xs text-muted">{r.git_sha.slice(0, 8)}</code>}
                  <span className="ml-auto text-xs text-muted">{shortTime(r.created_at)}</span>
                </div>
                <div className="mt-1 grid gap-1 sm:grid-cols-3">
                  <JsonView value={r.params} label="params" />
                  <JsonView value={r.inputs} label="inputs (provenance)" />
                  <JsonView value={r.summary} label="summary" />
                </div>
              </li>
            ))}
          </ul>
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
