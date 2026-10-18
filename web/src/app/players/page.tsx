import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { loadProjections, loadValues, slimValues } from "@/lib/data";
import { ErrorNote } from "@/components/ui";
import PlayersClient from "./PlayersClient";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Players" };

export default async function PlayersPage() {
  const db = anonClient();
  const [values, proj] = await Promise.all([
    loadValues(db),
    loadProjections(db).catch((e) => ({ run: null, rows: [], error: String(e) })),
  ]);
  // Don't mix mock projections with a real valuation run.
  const mixMock = !!proj.run?.run_id.startsWith("mock-") && !values.run?.run_id.startsWith("mock-");
  const projRun = mixMock ? null : proj.run;
  const projRows = mixMock ? [] : proj.rows;
  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold tracking-tight">Players</h1>
      {values.errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}
      <PlayersClient
        values={slimValues(values)}
        projRun={projRun}
        projections={projRows.map((p) => {
          const ex = (p.extra ?? {}) as Record<string, unknown>;
          return { ...p, extra: { name: ex.name ?? null, team: ex.team ?? null } };
        })}
      />
    </div>
  );
}
