import Link from "next/link";
import type { Metadata } from "next";
import { anonClient, cachedClient } from "@/lib/supabase";
import { defaultDraftId, loadDraftLive, loadDrafts, loadValues, slimValues } from "@/lib/data";
import { loadSimPicks, parseDraftSim, parseSeasonSim } from "@/lib/sim";
import { ErrorNote } from "@/components/ui";
import DraftClient from "./DraftClient";
import DraftModel from "./DraftModel";
import HowItWorks from "./HowItWorks";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Draft" };

const VIEWS = [
  { key: "room", label: "Live room" },
  { key: "model", label: "Model & sims" },
  { key: "how", label: "How it works" },
] as const;
type View = (typeof VIEWS)[number]["key"];

function SubTabs({ view, id }: { view: View; id?: string }) {
  return (
    <div className="mb-3 flex gap-1 rounded-xl border border-line bg-panel p-1 text-[13.5px]">
      {VIEWS.map((v) => {
        const q = new URLSearchParams();
        if (v.key !== "room") q.set("view", v.key);
        if (id) q.set("id", id);
        const href = `/draft${q.toString() ? `?${q}` : ""}`;
        return (
          <Link
            key={v.key}
            href={href}
            className={`flex-1 rounded-lg px-2 py-1.5 text-center whitespace-nowrap ${
              view === v.key ? "bg-panel-2 font-medium text-fg shadow-sm" : "text-muted hover:text-fg"
            }`}
          >
            {v.label}
          </Link>
        );
      })}
    </div>
  );
}

export default async function DraftPage({ searchParams }: { searchParams: Promise<{ id?: string; view?: string }> }) {
  const { id, view: rawView } = await searchParams;
  const view: View = rawView === "model" || rawView === "how" ? rawView : "room";
  const db = anonClient();

  if (view === "how") {
    const d = await loadSimPicks(db, db, "draft_sim", 1, {});
    return (
      <>
        <SubTabs view={view} id={id} />
        <HowItWorks optimizerVersion={d.latestVersion} />
      </>
    );
  }

  if (view === "model") {
    const rows = cachedClient(3600); // a run row is immutable for its run_id
    const [d, s] = await Promise.all([
      loadSimPicks(db, rows, "draft_sim", 1, { latest: true }),
      loadSimPicks(db, rows, "season_sim", 20, { preseason: true }),
    ]);
    const dsim = parseDraftSim(d.latest);
    const pre = parseSeasonSim(s.preseason);
    return (
      <>
        <SubTabs view={view} id={id} />
        {d.error && <ErrorNote error={d.error} />}
        {s.error && s.error !== d.error && <ErrorNote error={s.error} />}
        <DraftModel dsim={dsim} pre={pre} />
      </>
    );
  }

  // the values bundle doesn't depend on the draft list: start both at once
  const valuesP = loadValues(db);
  const { drafts, error } = await loadDrafts(db);
  const draftId = id && drafts.some((d) => d.draft_id === id) ? id : defaultDraftId(drafts);
  const [live, values] = await Promise.all([draftId ? loadDraftLive(db, draftId) : Promise.resolve(null), valuesP]);
  return (
    <>
      <SubTabs view={view} id={id} />
      <DraftClient
        initialDrafts={drafts}
        initialDraftId={draftId}
        initialLive={live}
        initialValues={slimValues(values)}
        loadError={error ?? null}
      />
    </>
  );
}
