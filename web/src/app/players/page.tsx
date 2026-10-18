import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import { loadProjections, loadValues } from "@/lib/data";
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
  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold tracking-tight">Players</h1>
      {values.errors.map((e, i) => (
        <ErrorNote key={i} error={e} />
      ))}
      <PlayersClient values={values} projRun={proj.run} projections={proj.rows} />
    </div>
  );
}
