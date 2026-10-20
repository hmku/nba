"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { anonClient } from "@/lib/supabase";
import { loadDraftLive, loadDrafts, loadValues, latestRun, slimValues, type DraftLive, type ValuesBundle } from "@/lib/data";
import type { Draft } from "@/lib/types";
import type { PlayerRow } from "@/lib/players";
import { Card, Empty } from "@/components/ui";
import { DraftView } from "./DraftView";

const POLL_MS = 4000;
const VALUES_CHECK_MS = 30000;

interface Props {
  initialDrafts: Draft[];
  initialDraftId: string | null;
  initialLive: DraftLive | null;
  initialValues: ValuesBundle;
  loadError: string | null;
}

export default function DraftClient({ initialDrafts, initialDraftId, initialLive, initialValues, loadError }: Props) {
  const router = useRouter();
  const [drafts, setDrafts] = useState(initialDrafts);
  const [draftId, setDraftId] = useState(initialDraftId);
  const [live, setLive] = useState<DraftLive | null>(initialLive);
  const [values, setValues] = useState<ValuesBundle>(initialValues);
  const [pollError, setPollError] = useState<string | null>(loadError);
  const [lastOk, setLastOk] = useState<string | null>(initialLive?.fetchedAt ?? null);
  const [selected, setSelected] = useState<PlayerRow | null>(null);
  const draftIdRef = useRef(draftId);
  draftIdRef.current = draftId;

  const refreshLive = useCallback(async () => {
    const id = draftIdRef.current;
    if (!id) return;
    try {
      const l = await loadDraftLive(anonClient(), id);
      if (draftIdRef.current !== id) return;
      setLive(l);
      setPollError(l.errors.length ? l.errors.join("; ") : null);
      if (!l.errors.length) setLastOk(l.fetchedAt);
    } catch (e) {
      setPollError(String(e));
    }
  }, []);

  // poll live state
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") refreshLive();
    }, POLL_MS);
    const onVis = () => document.visibilityState === "visible" && refreshLive();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [refreshLive]);

  // check for a new valuation run + new drafts occasionally
  useEffect(() => {
    const t = setInterval(async () => {
      if (document.visibilityState !== "visible") return;
      const db = anonClient();
      const run = await latestRun(db, "valuation").catch(() => null);
      // reload when a new valuation run appears (retry each check while it has no rows yet)
      if (run && run.run_id !== values.run?.run_id) {
        const v = await loadValues(db);
        if (v.values.length && v.run?.run_id !== values.run?.run_id) setValues(slimValues(v));
      }
      const d = await loadDrafts(db);
      if (!d.error) setDrafts(d.drafts);
    }, VALUES_CHECK_MS);
    return () => clearInterval(t);
  }, [values.run?.run_id]);

  const switchDraft = (id: string) => {
    setDraftId(id);
    setLive(null);
    draftIdRef.current = id;
    router.replace(`/draft?id=${encodeURIComponent(id)}`, { scroll: false });
    refreshLive();
  };

  const draft = drafts.find((d) => d.draft_id === draftId) ?? null;

  if (!draft) {
    return (
      <div className="space-y-3">
        <h1 className="text-lg font-semibold">Draft</h1>
        {pollError && <div className="text-sm text-bad">{pollError}</div>}
        <Card title="No drafts">
          <Empty>
            No rows in <code>drafts</code> yet. Create one with <code>fantasy draft init --draft-id …</code>.
          </Empty>
        </Card>
      </div>
    );
  }

  return (
    <DraftView
      draft={draft}
      drafts={drafts}
      live={live}
      values={values}
      pollError={pollError}
      lastOk={lastOk}
      onSwitch={switchDraft}
      onRefresh={refreshLive}
      selected={selected}
      setSelected={setSelected}
    />
  );
}
